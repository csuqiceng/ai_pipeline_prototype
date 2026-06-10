"""将语音文本转换为确定性动作或大模型规划。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .atomic_memory import AtomicMemory
from .atomic_parser import AtomicParser
from .atomic_resolver import AtomicResolver
from .assistant_knowledge_base import AssistantKnowledgeBase
from .command_parser import CommandParseError, parse_command
from .dashboard_query_specs import dashboard_query_specs, match_dashboard_query_spec
from .deepseek_client import DeepSeekClient
from .models import QueryRecord
from .nlp_normalization import NlpNormalizer
from .voice_wake_words import configured_wake_words, strip_wake_word


SYSTEM_ACTION_ALIASES = {
    "报警复位": "alarm_reset",
    "复位": "alarm_reset",
    "暂停": "sys_pause",
    "继续": "sys_resume",
    "恢复": "sys_resume",
    "取消当前动作": "sys_cancel",
    "取消当前任务": "sys_cancel",
    "停止当前动作": "sys_cancel",
    "停止当前任务": "sys_cancel",
    "急停": "sys_estop",
}

@dataclass(frozen=True)
class VoiceNlpAction:
    """语音文本解析出的单个可执行动作。"""
    action_type: str
    target: str | None
    source: str
    raw_text: str
    reason: str

    def to_preview_dict(self) -> dict[str, str]:
        """处理相关数据。"""
        return {
            "actionType": self.action_type,
            "target": self.target or "",
            "source": self.source,
            "rawText": self.raw_text,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class VoiceNlpPlan:
    """语音文本解析出的动作序列和说明。"""
    actions: tuple[VoiceNlpAction, ...]
    source: str
    raw_text: str
    reason: str
    normalized_text: str = ""
    semantic_level: int = 0
    semantic_label: str = "未识别层"
    response_deadline_ms: int = 500
    requires_precheck: bool = False
    requires_confirmation: bool = False
    priority: str = "normal"
    tokens: tuple[str, ...] = ()
    nlp_engine: str = "rule"
    atomic_records: dict[str, QueryRecord] = field(default_factory=dict)
    flow_draft: dict[str, Any] = field(default_factory=dict)

    def to_preview_dict(self) -> dict[str, object]:
        """处理相关数据。"""
        return {
            "source": self.source,
            "engine": self.nlp_engine,
            "rawText": self.raw_text,
            "normalizedText": self.normalized_text or self.raw_text,
            "reason": self.reason,
            "semanticLevel": self.semantic_level,
            "semanticLabel": self.semantic_label,
            "responseDeadlineMs": self.response_deadline_ms,
            "requiresPrecheck": self.requires_precheck,
            "requiresConfirmation": self.requires_confirmation,
            "priority": self.priority,
            "tokens": list(self.tokens),
            "actions": [action.to_preview_dict() for action in self.actions],
            "atomicRecords": {key: record.to_dict() for key, record in self.atomic_records.items()},
            "flowDraft": dict(self.flow_draft),
        }


class VoiceNlpAdapter:
    """语音文本到指令计划的适配器。"""
    def __init__(
        self,
        table: dict[str, QueryRecord],
        flow_names: Iterable[str],
        tokenizer: Callable[[str], Iterable[str]] | None = None,
        atomic_memory: AtomicMemory | None = None,
        normalizer: NlpNormalizer | None = None,
        flow_phrase_aliases: dict[str, list[dict[str, Any]]] | None = None,
        knowledge_base: AssistantKnowledgeBase | None = None,
    ) -> None:
        """初始化对象。"""
        self.table = table
        self.flow_names = tuple(sorted(str(name) for name in flow_names))
        self._external_deepseek_client = None
        self._diagnostic_callback: Callable[[str, str, str], None] | None = None
        self._runtime_context_provider: Callable[[], str] | None = None
        self._tokenizer = tokenizer
        self.atomic_memory = atomic_memory or AtomicMemory()
        self.atomic_parser = AtomicParser()
        self.atomic_resolver = AtomicResolver(self.atomic_memory)
        self.normalizer = normalizer or NlpNormalizer(enable_pinyin=False)
        self.flow_phrase_aliases = flow_phrase_aliases if flow_phrase_aliases is not None else self._load_flow_phrase_aliases()
        self.knowledge_base = knowledge_base or AssistantKnowledgeBase.load()
        self._pending_flow_draft_payload: dict[str, Any] | None = None
        self._pending_flow_missing_gesture: str | None = None

    def set_deepseek_client(self, client) -> None:
        """设置大模型客户端。"""
        self._external_deepseek_client = client

    def set_diagnostic_callback(self, callback: Callable[[str, str, str], None]) -> None:
        """设置诊断回调，用于把大模型回退原因写入 GUI 日志。"""
        self._diagnostic_callback = callback

    def set_runtime_context_provider(self, provider: Callable[[], str] | None) -> None:
        """设置运行时上下文提供器，用于让问答接上当前 GUI 会话状态。"""
        self._runtime_context_provider = provider

    def parse(
        self,
        text: str,
        *,
        use_deepseek: bool = False,
        chat_delta_callback: Callable[[str], None] | None = None,
    ) -> VoiceNlpPlan:
        """解析相关数据。"""
        normalization = self.normalizer.normalize(text)
        normalized = normalization.text.strip()
        if not normalized:
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "rule", text, "输入为空"),),
                source="rule",
                raw_text=text,
                reason="输入为空",
            )

        followup_plan = self._parse_pending_flow_followup(normalized, raw_text=text)
        if followup_plan is not None:
            return followup_plan

        if self._is_coded_emergency(normalized):
            return self._build_plan(
                actions=(VoiceNlpAction("system", "sys_estop", "rule", text, "命中三段式应急编码"),),
                source="rule",
                raw_text=text,
                reason="命中三段式应急编码",
            )

        query_target = self._match_dashboard_query(normalized, self._tokenize(normalized))
        if query_target:
            return self._build_plan(
                actions=(VoiceNlpAction("query", query_target, "rule", text, "命中看板查询规则"),),
                source="rule",
                raw_text=text,
                reason="命中看板查询规则",
            )

        if self._is_wakeless_rest_command(normalized):
            rest_plan = self._parse_with_atomic(normalized, raw_text=text)
            if rest_plan is not None:
                return rest_plan

        command_text = self._strip_wake_word(normalized)
        if command_text is None:
            knowledge_plan = self._answer_chat_with_knowledge_base(normalized, raw_text=text)
            if knowledge_plan is not None:
                return knowledge_plan
            if use_deepseek and self._looks_like_explanatory_question(normalized):
                chat_plan = self._answer_chat_with_deepseek(
                    normalized,
                    raw_text=text,
                    chat_delta_callback=chat_delta_callback,
                )
                if chat_plan is not None:
                    return chat_plan
            if not self._looks_like_control_text(normalized):
                if use_deepseek:
                    chat_plan = self._answer_chat_with_deepseek(
                        normalized,
                        raw_text=text,
                        chat_delta_callback=chat_delta_callback,
                    )
                    if chat_plan is not None:
                        return chat_plan
                return self._build_plan(
                    actions=(VoiceNlpAction("unknown", None, "rule", text, "闲聊或咨询，未触发控制动作"),),
                    source="rule",
                    raw_text=text,
                    reason="闲聊或咨询，未触发控制动作",
                )
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "rule", text, "生产指令缺少“小正或小兵”唤醒词，未执行"),),
                source="rule",
                raw_text=text,
                reason="生产指令缺少“小正或小兵”唤醒词，未执行",
            )
        if self._is_empty_command_after_wake(command_text):
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "rule", text, "已听到唤醒词，请补充具体指令。"),),
                source="rule",
                raw_text=text,
                reason="已听到唤醒词，请补充具体指令。",
            )

        complex_flow_plan = self._parse_complex_flow_draft(command_text, raw_text=text, use_deepseek=use_deepseek)
        if complex_flow_plan is not None:
            return complex_flow_plan

        atomic_plan = self._parse_with_atomic(command_text, raw_text=text)
        if atomic_plan is not None:
            return atomic_plan

        rule_plan = self._parse_with_rules(command_text)
        if rule_plan.actions and rule_plan.actions[0].action_type != "unknown":
            return self._build_plan(
                actions=rule_plan.actions,
                source=rule_plan.source,
                raw_text=text,
                reason=rule_plan.reason,
            )

        if use_deepseek:
            if self._looks_like_chat_question(command_text) or self._looks_like_explanatory_question(command_text):
                knowledge_plan = self._answer_chat_with_knowledge_base(command_text, raw_text=text)
                if knowledge_plan is not None:
                    return knowledge_plan
                chat_plan = self._answer_chat_with_deepseek(
                    command_text,
                    raw_text=text,
                    chat_delta_callback=chat_delta_callback,
                )
                if chat_plan is not None:
                    return chat_plan
            deepseek_result = self._parse_with_deepseek(command_text)
            if deepseek_result is not None:
                return deepseek_result

        return self._build_plan(actions=rule_plan.actions, source=rule_plan.source, raw_text=text, reason=rule_plan.reason)

    def _build_plan(
        self,
        *,
        actions: tuple[VoiceNlpAction, ...],
        source: str,
        raw_text: str,
        reason: str,
        atomic_records: dict[str, QueryRecord] | None = None,
        requires_confirmation: bool | None = None,
        flow_draft: dict[str, Any] | None = None,
    ) -> VoiceNlpPlan:
        tokens = self._tokenize(raw_text)
        nlp_engine = "jieba_rule" if tokens else source
        metadata = self._semantic_metadata(actions, raw_text=raw_text, reason=reason)
        return VoiceNlpPlan(
            actions=actions,
            source=source,
            raw_text=raw_text,
            reason=reason,
            semantic_level=metadata["semantic_level"],
            semantic_label=metadata["semantic_label"],
            response_deadline_ms=metadata["response_deadline_ms"],
            requires_precheck=metadata["requires_precheck"],
            requires_confirmation=bool(metadata["requires_confirmation"] if requires_confirmation is None else requires_confirmation),
            priority=metadata["priority"],
            tokens=tokens,
            nlp_engine=nlp_engine,
            atomic_records=atomic_records or {},
            flow_draft=flow_draft or {},
        )

    def _tokenize(self, text: str) -> tuple[str, ...]:
        tokenizer = self._tokenizer
        if tokenizer is None:
            try:
                import jieba  # type: ignore

                tokenizer = jieba.lcut
            except Exception:
                return ()
        try:
            return tuple(str(token).strip() for token in tokenizer(text) if str(token).strip())
        except Exception:
            return ()

    @staticmethod
    def _semantic_metadata(
        actions: tuple[VoiceNlpAction, ...],
        *,
        raw_text: str,
        reason: str,
    ) -> dict[str, object]:
        first = actions[0] if actions else None
        action_type = getattr(first, "action_type", "unknown") if first else "unknown"
        target = getattr(first, "target", None) if first else None
        if action_type == "system" and target == "sys_estop":
            return {
                "semantic_level": 5,
                "semantic_label": "应急安全层",
                "response_deadline_ms": 100,
                "requires_precheck": False,
                "requires_confirmation": False,
                "priority": "high",
            }
        if action_type == "system":
            return {
                "semantic_level": 4,
                "semantic_label": "系统管理层",
                "response_deadline_ms": 2000,
                "requires_precheck": False,
                "requires_confirmation": target not in {"sys_pause", "sys_resume", "sys_cancel"},
                "priority": "normal",
            }
        if action_type == "chat":
            return {
                "semantic_level": 1,
                "semantic_label": "闲聊咨询层",
                "response_deadline_ms": 5000,
                "requires_precheck": False,
                "requires_confirmation": False,
                "priority": "normal",
            }
        if action_type in {"template", "flow", "atomic_template"}:
            return {
                "semantic_level": 3,
                "semantic_label": "常规生产执行层",
                "response_deadline_ms": 2000,
                "requires_precheck": True,
                "requires_confirmation": True,
                "priority": "normal",
            }
        if action_type == "flow_draft":
            return {
                "semantic_level": 3,
                "semantic_label": "流程草案编排层",
                "response_deadline_ms": 2000,
                "requires_precheck": True,
                "requires_confirmation": True,
                "priority": "normal",
            }
        if action_type == "clarification":
            return {
                "semantic_level": 1,
                "semantic_label": "澄清确认层",
                "response_deadline_ms": 1000,
                "requires_precheck": False,
                "requires_confirmation": False,
                "priority": "normal",
            }
        if action_type == "memory":
            return {
                "semantic_level": 4,
                "semantic_label": "系统管理层",
                "response_deadline_ms": 1000,
                "requires_precheck": False,
                "requires_confirmation": False,
                "priority": "normal",
            }
        if action_type == "query":
            return {
                "semantic_level": 2,
                "semantic_label": "工艺查询层",
                "response_deadline_ms": 5000,
                "requires_precheck": False,
                "requires_confirmation": False,
                "priority": "normal",
            }
        if action_type == "unknown" and "小正" not in reason and "未执行" not in reason:
            return {
                "semantic_level": 1,
                "semantic_label": "闲聊咨询层",
                "response_deadline_ms": 1000,
                "requires_precheck": False,
                "requires_confirmation": False,
                "priority": "normal",
            }
        return {
            "semantic_level": 0,
            "semantic_label": "未识别层",
            "response_deadline_ms": 500,
            "requires_precheck": False,
            "requires_confirmation": False,
            "priority": "normal",
        }

    @staticmethod
    def _strip_wake_word(text: str) -> str | None:
        compact = text.strip()
        command = strip_wake_word(compact)
        if command is not None:
            return command or ""
        for wake_word in configured_wake_words():
            index = compact.find(wake_word)
            if index <= 0:
                continue
            prefix = compact[:index].strip(" ，,。:：")
            command = compact[index + len(wake_word):].lstrip(" ，,。:：")
            if command and VoiceNlpAdapter._allows_embedded_wake_prefix(prefix):
                return command
        return None

    @staticmethod
    def _allows_embedded_wake_prefix(prefix: str) -> bool:
        compact = re.sub(r"\s+", "", prefix or "")
        if not compact or len(compact) > 20:
            return False
        return any(
            keyword in compact
            for keyword in (
                "请",
                "帮",
                "麻烦",
                "直接",
                "编写",
                "生成",
                "创建",
                "新建",
                "我要",
                "我想",
                "能不能",
                "可以",
            )
        )

    @staticmethod
    def _is_empty_command_after_wake(text: str) -> bool:
        compact = re.sub(r"[\s，,。:：.!！?？、；;]+", "", text or "")
        return not compact

    @staticmethod
    def _is_coded_emergency(text: str) -> bool:
        return bool(re.match(r"^\s*(?:急停|紧急停止)\s+[A-Za-z0-9_-]{3,16}\s+(?:急停|紧急停止)\s*$", text))

    @staticmethod
    def _is_wakeless_rest_command(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return bool(
            re.fullmatch(r"(?:机械手|机器人)?(?:休息|休息了|去休息|回去休息)", compact)
            or re.fullmatch(r"(?:回到|回|到|去|移动到)?(?:默认)?休息姿态", compact)
            or re.fullmatch(r"(?:回到|回|到|去|移动到)?(?:0位|零位)", compact)
        )

    def _looks_like_control_text(self, text: str) -> bool:
        compact = text.replace(" ", "")
        if any(keyword in compact for keyword in SYSTEM_ACTION_ALIASES):
            return True
        if self._match_flow_name(text):
            return True
        try:
            parse_command(text, self.table)
            return True
        except CommandParseError:
            return False

    @staticmethod
    def _match_dashboard_query(text: str, tokens: Iterable[str] = ()) -> str | None:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return None
        command_like = compact
        command_like = strip_wake_word(command_like) or command_like
        if re.fullmatch(r"(速度|速)?-?\d+(?:\.\d+)?%", command_like):
            return None
        if re.fullmatch(r"步长-?\d+(?:\.\d+)?(毫米|mm|度|°)", command_like, flags=re.IGNORECASE):
            return None
        lowered = compact.lower()
        if any(keyword in lowered for keyword in ("支持哪些原子命令", "支持哪些二次原子", "二次原子函数能力", "原子命令能力", "原子函数清单")):
            return "atomic_capabilities"
        spec = match_dashboard_query_spec(command_like)
        if spec is not None:
            return spec.board_key
        token_set = {str(token).strip().lower() for token in tokens if str(token).strip()}
        query_words = {"查", "查询", "看看", "看一下", "状态", "看板", "现在"}
        if not (token_set & query_words):
            return None
        if {"安全", "范围"} <= token_set:
            return "safety_boundary"
        for query_spec in dashboard_query_specs():
            alias_tokens = {alias.lower() for alias in query_spec.aliases}
            split_alias_tokens = {word for alias in query_spec.aliases for word in re.findall(r"[\w\u4e00-\u9fff]+", alias.lower())}
            if token_set & (alias_tokens | split_alias_tokens):
                return query_spec.board_key
        return None

    def _parse_with_rules(self, text: str) -> VoiceNlpPlan:
        """解析相关数据。"""
        actions = self._parse_multiple_with_rules(text)
        if actions:
            reason = "命中多动作规则" if len(actions) > 1 else actions[0].reason
            return VoiceNlpPlan(actions=tuple(actions), source="rule", raw_text=text, reason=reason)

        action = self._parse_single_with_rules(text)
        return VoiceNlpPlan(actions=(action,), source="rule", raw_text=text, reason=action.reason)

    def _parse_with_atomic(self, command_text: str, *, raw_text: str) -> VoiceNlpPlan | None:
        unsupported_reason = self._unsupported_complex_atomic_reason(command_text)
        if unsupported_reason is not None:
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "atomic_rule", command_text, unsupported_reason),),
                source="atomic_rule",
                raw_text=raw_text,
                reason=unsupported_reason,
            )
        parts = self._split_atomic_parts(command_text)
        if len(parts) > 1:
            actions: list[VoiceNlpAction] = []
            records: dict[str, QueryRecord] = {}
            reasons: list[str] = []
            requires_confirmation = False
            for part in parts:
                step_plan = self._parse_with_atomic(part, raw_text=f"小正，{part}")
                if step_plan is None or not step_plan.actions:
                    return None
                action = step_plan.actions[0]
                if action.action_type not in {"atomic_template", "memory"}:
                    return None
                if action.action_type == "memory" and str(action.target or "").startswith("position_save:"):
                    return None
                actions.append(
                    VoiceNlpAction(
                        action.action_type,
                        action.target,
                        action.source,
                        part,
                        action.reason,
                    )
                )
                records.update(getattr(step_plan, "atomic_records", {}) or {})
                reasons.append(str(getattr(step_plan, "reason", "") or action.reason))
                requires_confirmation = requires_confirmation or bool(getattr(step_plan, "requires_confirmation", False))
            return self._build_plan(
                actions=tuple(actions),
                source="atomic_rule",
                raw_text=raw_text,
                reason="；".join(reason for reason in reasons if reason) or "命中多原子动作规则",
                atomic_records=records,
                requires_confirmation=requires_confirmation,
            )

        elements = self.atomic_parser.parse(f"小正，{command_text}")
        resolved = self.atomic_resolver.resolve(elements)
        if resolved.kind == "template":
            record = resolved.params.get("record")
            if not isinstance(record, QueryRecord):
                return None
            action = VoiceNlpAction(
                "atomic_template",
                record.query_key,
                "atomic_rule",
                command_text,
                resolved.reason,
            )
            return self._build_plan(
                actions=(action,),
                source="atomic_rule",
                raw_text=raw_text,
                reason=resolved.reason,
                atomic_records={record.query_key: record},
                requires_confirmation=resolved.requires_confirmation,
            )
        if resolved.kind in {"memory", "query"}:
            action_type = "query" if resolved.kind == "query" else "memory"
            target = resolved.target
            if resolved.kind == "memory" and resolved.target == "position_save":
                position_name = resolved.params.get("position_name")
                if position_name:
                    target = f"position_save:{position_name}"
            action = VoiceNlpAction(
                action_type,
                target,
                "atomic_rule",
                command_text,
                resolved.reason,
            )
            return self._build_plan(
                actions=(action,),
                source="atomic_rule",
                raw_text=raw_text,
                reason=resolved.reason,
                requires_confirmation=resolved.requires_confirmation,
            )
        return None

    @staticmethod
    def _unsupported_complex_atomic_reason(command_text: str) -> str | None:
        compact = re.sub(r"\s+", "", command_text or "")
        if not compact:
            return None
        if re.search(r"(?:循环|重复|反复|连续)\d+(?:次|遍|回)", compact):
            return "暂不支持循环/重复类原子组合命令，请拆成单步确认后执行。"
        if any(keyword in compact for keyword in ("同时", "并行", "一起执行")) or re.search(r"一边.+一边", compact):
            return "暂不支持并行类原子组合命令，请拆成顺序动作。"
        if any(keyword in compact for keyword in ("如果", "假如", "当")) and any(keyword in compact for keyword in ("就", "则", "再")):
            return "暂不支持条件判断类原子组合命令，请先查询状态，再下达明确动作。"
        if any(keyword in compact.lower() for keyword in ("func11", "函数11")) or any(
            keyword in compact for keyword in ("连续路径", "连续轨迹", "轨迹", "插补", "路径经过")
        ):
            return "暂不支持 Func11 连续插补/轨迹类原子命令，请拆成单点动作或使用已验证流程。"
        return None

    @staticmethod
    def _split_atomic_parts(command_text: str) -> list[str]:
        parts = [
            part.strip(" ，,。.；;")
            for part in re.split(r"(?:然后|接着|之后|并且)", command_text or "")
            if part.strip(" ，,。.；;")
        ]
        return parts

    def _parse_complex_flow_draft(self, command_text: str, *, raw_text: str, use_deepseek: bool) -> VoiceNlpPlan | None:
        if not self._looks_like_complex_flow(command_text):
            return None
        payload = self._complex_flow_payload_from_deepseek(command_text) if use_deepseek else None
        if payload is None:
            payload = self._local_complex_flow_payload(command_text)
        if payload is None:
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "flow_draft", command_text, "复杂流程描述未能结构化，请拆成新建流程、添加步骤、确认流程。"),),
                source="flow_draft",
                raw_text=raw_text,
                reason="复杂流程描述未能结构化，请拆成新建流程、添加步骤、确认流程。",
            )
        draft_result = self._build_complex_flow_draft(payload)
        if draft_result is None:
            return None
        draft, missing = draft_result
        if missing:
            missing_text = str(missing[0])
            reason = f"需要补充动作映射：{missing_text}"
            self._pending_flow_draft_payload = payload
            self._pending_flow_missing_gesture = missing_text
            return self._build_plan(
                actions=(VoiceNlpAction("clarification", f"gesture_mapping:{missing_text}", "flow_draft", command_text, reason),),
                source="flow_draft",
                raw_text=raw_text,
                reason=reason,
                flow_draft=draft,
            )
        flow_name = str(draft.get("flow_name") or "未命名流程")
        reason = f"已生成流程草案：{flow_name}，共{len(draft.get('expanded_steps', []))}步，等待确认保存/执行。"
        return self._build_plan(
            actions=(VoiceNlpAction("flow_draft", flow_name, "flow_draft", command_text, reason),),
            source="flow_draft",
            raw_text=raw_text,
            reason=reason,
            requires_confirmation=True,
            flow_draft=draft,
        )

    def _parse_pending_flow_followup(self, text: str, *, raw_text: str) -> VoiceNlpPlan | None:
        if not self._pending_flow_draft_payload or not self._pending_flow_missing_gesture:
            return None
        alias = self._parse_gesture_mapping_answer(text)
        if alias is None:
            return None
        gesture = self._pending_flow_missing_gesture
        self.flow_phrase_aliases[gesture] = alias
        payload = self._pending_flow_draft_payload
        draft_result = self._build_complex_flow_draft(payload)
        if draft_result is None:
            return None
        draft, missing = draft_result
        if missing:
            self._pending_flow_missing_gesture = str(missing[0])
            reason = f"还需要补充动作映射：{missing[0]}"
            return self._build_plan(
                actions=(VoiceNlpAction("clarification", f"gesture_mapping:{missing[0]}", "flow_draft", raw_text, reason),),
                source="flow_draft",
                raw_text=raw_text,
                reason=reason,
                flow_draft=draft,
            )
        self._pending_flow_draft_payload = None
        self._pending_flow_missing_gesture = None
        flow_name = str(draft.get("flow_name") or "未命名流程")
        reason = f"已补充{gesture}映射并生成流程草案：{flow_name}，共{len(draft.get('expanded_steps', []))}步，等待确认保存/执行。"
        return self._build_plan(
            actions=(VoiceNlpAction("flow_draft", flow_name, "flow_draft", raw_text, reason),),
            source="flow_draft",
            raw_text=raw_text,
            reason=reason,
            requires_confirmation=True,
            flow_draft=draft,
        )

    @staticmethod
    def _parse_gesture_mapping_answer(text: str) -> list[dict[str, Any]] | None:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return None
        joint = re.search(r"J([1-6])", compact, flags=re.IGNORECASE)
        if joint and "正反" in compact:
            axis_no = int(joint.group(1)) - 1
            label = f"J{joint.group(1)}"
            return [
                {"command": f"{label}正转", "func_id": 106, "axis_no": axis_no, "direction": 1},
                {"command": f"{label}反转", "func_id": 106, "axis_no": axis_no, "direction": -1},
            ]
        virtual = re.search(r"R([XYZ])", compact, flags=re.IGNORECASE)
        if virtual and "正反" in compact:
            axis_label = f"R{virtual.group(1).lower()}"
            axis_no = {"rx": 9, "ry": 10, "rz": 11}[axis_label.lower()]
            display = axis_label[0].upper() + axis_label[1].lower()
            return [
                {"command": f"{display}正转", "func_id": 107, "axis_no": axis_no, "direction": 1},
                {"command": f"{display}反转", "func_id": 107, "axis_no": axis_no, "direction": -1},
            ]
        return None

    @staticmethod
    def _looks_like_complex_flow(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        if re.match(r"^(执行|开始|运行)流程", compact) and not any(word in compact for word in ("先", "再", "然后", "坐标", "小流程")):
            return False
        has_flow = "流程" in compact or "小流程" in compact
        has_sequence = any(word in compact for word in ("先", "再", "然后", "接着", "之后"))
        has_repeat = bool(re.search(r"(?:循环|重复|反复)?\d+(?:次|遍|回)", compact))
        has_pose = "xyzrxryrz" in compact.lower() or "坐标" in compact or bool(re.search(r"-?\d+(?:\.\d+)?[，,]\s*-?\d+", text or ""))
        has_home_sequence = bool(re.search(r"home(?:位)?", compact, flags=re.IGNORECASE)) and has_sequence and has_repeat
        has_gesture = "点头" in compact
        has_virtual_repeat = bool(re.search(r"(上移|上升|下移|下降|前进|后退|左移|右移)\d+(?:\.\d+)?(?:mm|毫米)", compact, re.IGNORECASE))
        return (has_flow and (has_sequence or has_repeat or has_pose)) or (has_home_sequence and (has_gesture or has_virtual_repeat))

    def _complex_flow_payload_from_deepseek(self, text: str) -> dict[str, Any] | None:
        try:
            client = self._external_deepseek_client or DeepSeekClient.from_env()
            prompt = self._build_complex_flow_deepseek_prompt(text)
            self._emit_diagnostic("DeepSeek流程草案", "开始", "复杂流程描述进入结构化草案解析")
            payload = client.parse_json(prompt)
            if not isinstance(payload, dict):
                self._emit_diagnostic("DeepSeek流程草案", "失败", "未返回 JSON 对象")
                return None
            if str(payload.get("intent", "")).strip() != "create_flow":
                self._emit_diagnostic("DeepSeek流程草案", "失败", "返回 intent 不是 create_flow")
                return None
            self._emit_diagnostic("DeepSeek流程草案", "成功", str(payload.get("reason", "已生成草案")))
            return payload
        except Exception as exc:
            self._emit_diagnostic("DeepSeek流程草案", "失败", f"{type(exc).__name__}: {exc}")
            return None

    def _build_complex_flow_deepseek_prompt(self, text: str) -> str:
        aliases = sorted(self.flow_phrase_aliases)
        return (
            "你负责把复杂中文口语机械手指令转换为流程草案 JSON。\n"
            "你不能输出寄存器写入，不能创造实际轴映射；未知动作只保留 gesture 文本。\n"
            "只允许返回 JSON 对象，schema 如下：\n"
            '{'
            '"intent":"create_flow",'
            '"flowName":"流程名",'
            '"positions":[{"name":"位置名","pose":[x,y,z,rx,ry,rz]}],'
            '"steps":[{"type":"move_position","position":"位置名"},'
            '{"type":"gesture_repeat","gesture":"动作原文","angleDeg":15,"repeat":3}],'
            '"reason":"一句原因"'
            '}\n'
            f"已配置动作别名: {json.dumps(aliases, ensure_ascii=False)}\n"
            f"用户输入: {text}"
        )

    def _local_complex_flow_payload(self, text: str) -> dict[str, Any] | None:
        compact = re.sub(r"\s+", "", text or "")
        if not self._looks_like_complex_flow(compact):
            return None
        flow_name = "未命名流程"
        match = re.search(r"(?:叫|名叫|命名为)?([\u4e00-\u9fa5A-Za-z0-9_]+)的?小?流程", compact)
        if match:
            flow_name = match.group(1).strip("，,。")
        pose = self._extract_pose6(compact)
        has_home_step = bool(re.search(r"home(?:位)?", compact, flags=re.IGNORECASE))
        home_pose = pose or (self._lookup_flow_position_pose("home") if has_home_step else None)
        positions = [{"name": "home", "pose": home_pose}] if home_pose else []
        gesture = self._match_configured_flow_gesture(compact)
        if not gesture:
            gesture_match = re.search(r"(小臂[^，,。]*?点头|[^，,。]*?点头)", compact)
            if gesture_match:
                gesture = gesture_match.group(1)
        virtual_motion = self._extract_virtual_repeat_step(compact)
        angle = self._extract_number_before_unit(compact, "度") or 0.0
        repeat_match = re.search(r"(\d+)(?:次|遍|回)", compact)
        repeat = int(repeat_match.group(1)) if repeat_match else 1
        if not positions and not gesture and virtual_motion is None:
            return None
        steps: list[dict[str, Any]] = []
        if has_home_step:
            steps.append({"type": "move_position", "position": "home"})
        if gesture:
            steps.append({"type": "gesture_repeat", "gesture": gesture, "angleDeg": angle, "repeat": repeat})
        if virtual_motion is not None:
            virtual_motion["repeat"] = repeat
            steps.append(virtual_motion)
        return {"intent": "create_flow", "flowName": flow_name, "positions": positions, "steps": steps, "reason": "本地规则生成流程草案"}

    def _lookup_flow_position_pose(self, name: str) -> list[float] | None:
        registry = getattr(self.atomic_memory, "position_registry", None)
        if registry is not None:
            try:
                entry = registry.get(name)
            except Exception:
                entry = None
            pose = getattr(entry, "pose", None) if entry is not None else None
            if pose is not None:
                return [self._float_value(value, 0.0) for value in pose]
        pose = self.atomic_memory.get_position(name)
        if pose is not None:
            return [self._float_value(value, 0.0) for value in pose]
        record = self.table.get(name)
        params = getattr(record, "params", None)
        if isinstance(params, dict):
            keys = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
            if all(key in params for key in keys):
                return [self._float_value(params.get(key), 0.0) for key in keys]
        return None

    def _match_configured_flow_gesture(self, text: str) -> str:
        compact = re.sub(r"\s+", "", text or "")
        for gesture in sorted(self.flow_phrase_aliases, key=len, reverse=True):
            name = re.sub(r"\s+", "", str(gesture or ""))
            if name and name in compact:
                return str(gesture)
        return ""

    def _build_complex_flow_draft(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]] | None:
        flow_name = str(payload.get("flowName") or payload.get("flow_name") or "").strip()
        if not flow_name:
            flow_name = "未命名流程"
        positions = self._normalize_draft_positions(payload.get("positions"))
        position_map = {item["name"]: item["pose"] for item in positions}
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return None
        expanded: list[dict[str, Any]] = []
        missing: list[str] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                continue
            step_type = str(raw_step.get("type") or "").strip()
            if step_type == "move_position":
                position = str(raw_step.get("position") or "").strip()
                pose = position_map.get(position)
                if pose is None:
                    missing.append(f"position:{position}")
                    continue
                expanded.append(self._flow_move_step(len(expanded) + 1, position, pose))
            elif step_type in {"gesture", "gesture_repeat"}:
                gesture = str(raw_step.get("gesture") or raw_step.get("text") or "").strip()
                alias = self.flow_phrase_aliases.get(gesture)
                if not alias:
                    missing.append(gesture or "未知动作")
                    continue
                repeat = max(1, min(20, self._int_value(raw_step.get("repeat"), 1)))
                angle = abs(self._float_value(raw_step.get("angleDeg", raw_step.get("angle_deg")), 0.0))
                for _ in range(repeat):
                    for alias_step in alias:
                        expanded.append(self._flow_gesture_step(len(expanded) + 1, alias_step, angle, gesture))
            elif step_type == "virtual_repeat":
                repeat = max(1, min(20, self._int_value(raw_step.get("repeat"), 1)))
                axis_no = self._int_value(raw_step.get("axis_no"), 0)
                distance = abs(self._float_value(raw_step.get("distance_mm"), 0.0))
                direction = -1 if self._float_value(raw_step.get("direction"), 1.0) < 0 else 1
                label = str(raw_step.get("label") or "相对移动").strip()
                if axis_no <= 0 or distance <= 0:
                    missing.append(label)
                    continue
                for _ in range(repeat):
                    expanded.append(self._flow_virtual_step(len(expanded) + 1, axis_no, direction * distance, label))
        draft = {
            "flow_name": flow_name,
            "positions": positions,
            "raw_steps": raw_steps,
            "expanded_steps": expanded,
            "missing": missing,
            "safe_to_execute": False,
        }
        return draft, missing

    @staticmethod
    def _extract_virtual_repeat_step(text: str) -> dict[str, Any] | None:
        match = re.search(r"(上移|上升|下移|下降|前进|后退|左移|右移)(\d+(?:\.\d+)?)(?:mm|毫米)", text, re.IGNORECASE)
        if not match:
            return None
        label = match.group(1)
        axis_no, direction = {
            "前进": (6, 1),
            "后退": (6, -1),
            "左移": (7, -1),
            "右移": (7, 1),
            "上移": (8, 1),
            "上升": (8, 1),
            "下移": (8, -1),
            "下降": (8, -1),
        }[label]
        return {
            "type": "virtual_repeat",
            "label": label,
            "axis_no": axis_no,
            "direction": direction,
            "distance_mm": float(match.group(2)),
        }

    def _normalize_draft_positions(self, raw_positions: object) -> list[dict[str, Any]]:
        positions: list[dict[str, Any]] = []
        if not isinstance(raw_positions, list):
            return positions
        for item in raw_positions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            pose = item.get("pose")
            if not name or not isinstance(pose, list) or len(pose) != 6:
                continue
            positions.append({"name": name, "pose": [self._float_value(value, 0.0) for value in pose]})
        return positions

    def _flow_move_step(self, step_id: int, position: str, pose: list[float]) -> dict[str, Any]:
        x, y, z, rx, ry, rz = pose
        speed = float(self.atomic_memory.current_speed)
        return {
            "step_id": step_id,
            "action": "移动",
            "func_id": 108,
            "position_name": position,
            "description": f"移动到{position}",
            "params": {
                "target_x": x,
                "target_y": y,
                "target_z": z,
                "target_rx": rx,
                "target_ry": ry,
                "target_rz": rz,
                "spd_pct": speed,
                "acc_pct": speed,
                "dec_pct": speed,
                "stop_cmd": 0,
                "fuzzy_pos": 0,
                "fuzzy_spd": 1,
                "fuzzy_acc": 1,
                "fuzzy_dec": 1,
                "move_type": 0,
            },
        }

    def _flow_virtual_step(self, step_id: int, axis_no: int, pos_val: float, label: str) -> dict[str, Any]:
        speed = float(self.atomic_memory.current_speed)
        return {
            "step_id": step_id,
            "action": label,
            "func_id": 107,
            "description": f"{label}{abs(pos_val):g}mm",
            "params": {
                "axis_no": axis_no,
                "pos_val": pos_val,
                "spd_pct": speed,
                "acc_pct": speed,
                "dec_pct": speed,
                "fuzzy_pos": 0,
                "fuzzy_spd": 1,
                "fuzzy_acc": 1,
                "fuzzy_dec": 1,
                "stop_cmd": 0,
            },
        }

    def _flow_gesture_step(self, step_id: int, alias_step: dict[str, Any], angle: float, gesture: str) -> dict[str, Any]:
        axis_no = self._int_value(alias_step.get("axis_no"), 0)
        direction = -1 if self._float_value(alias_step.get("direction"), 1.0) < 0 else 1
        func_id = self._int_value(alias_step.get("func_id"), 107 if axis_no >= 6 else 106)
        pos_val = direction * angle
        speed = float(self.atomic_memory.current_speed)
        return {
            "step_id": step_id,
            "action": str(alias_step.get("command") or gesture),
            "func_id": func_id,
            "description": f"{gesture}:{alias_step.get('command', '')}",
            "params": {
                "axis_no": axis_no,
                "pos_val": pos_val,
                "spd_pct": speed,
                "acc_pct": speed,
                "dec_pct": speed,
                "fuzzy_pos": 0,
                "fuzzy_spd": 1,
                "fuzzy_acc": 1,
                "fuzzy_dec": 1,
                "stop_cmd": 0,
            },
        }

    @staticmethod
    def _extract_pose6(text: str) -> list[float] | None:
        lower = text.lower()
        if "xyzrxryrz" in lower:
            tail = lower.split("xyzrxryrz", 1)[1]
        elif "坐标" in lower:
            tail = lower.split("坐标", 1)[1]
        else:
            tail = lower
        values = re.findall(r"-?\d+(?:\.\d+)?", tail)
        if len(values) < 6:
            return None
        return [float(value) for value in values[:6]]

    @staticmethod
    def _extract_number_before_unit(text: str, unit: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*" + re.escape(unit), text)
        return float(match.group(1)) if match else None

    @staticmethod
    def _float_value(value: object, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int_value(value: object, default: int) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _load_flow_phrase_aliases() -> dict[str, list[dict[str, Any]]]:
        path = Path(__file__).resolve().parent.parent / "data" / "flow_phrase_aliases.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        aliases = payload.get("aliases") if isinstance(payload, dict) else None
        if not isinstance(aliases, dict):
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for key, value in aliases.items():
            if isinstance(value, list):
                result[str(key)] = [dict(item) for item in value if isinstance(item, dict)]
        return result

    def _parse_single_with_rules(self, text: str) -> VoiceNlpAction:
        """解析相关数据。"""
        system_action = self._match_system_action(text)
        if system_action:
            return VoiceNlpAction("system", system_action, "rule", text, "命中系统动作规则")

        flow_name = self._match_flow_name(text)
        if flow_name:
            return VoiceNlpAction("flow", flow_name, "rule", text, "命中流程规则")

        try:
            parsed = parse_command(text, self.table)
            return VoiceNlpAction("template", parsed.query_key, "rule", text, "命中模板规则")
        except CommandParseError:
            return VoiceNlpAction("unknown", None, "rule", text, "未命中模板、流程或系统动作")

    def _parse_multiple_with_rules(self, text: str) -> list[VoiceNlpAction]:
        """解析相关数据。"""
        parts = [part.strip(" ，,。.；;") for part in re.split(r"(?:然后|接着|再|之后|并且)", text) if part.strip(" ，,。.；;")]
        if len(parts) <= 1:
            return []
        actions: list[VoiceNlpAction] = []
        for part in parts:
            action = self._parse_single_with_rules(part)
            if action.action_type == "unknown":
                return []
            actions.append(action)
        return actions

    def _parse_with_deepseek(self, text: str) -> VoiceNlpPlan | None:
        """解析大模型。"""
        try:
            client = self._external_deepseek_client or DeepSeekClient.from_env()
            prompt = self._build_deepseek_prompt(text)
            self._emit_diagnostic("DeepSeek解析", "开始", "本地规则未命中，正在调用在线AI辅助识别")
            payload = client.parse_json(prompt)
            if not payload:
                self._emit_diagnostic("DeepSeek解析", "失败", "未返回可解析 JSON，已回退本地规则")
                return None
            reason = str(payload.get("reason", "")).strip() or "DeepSeek解析"
            raw_actions = payload.get("actions")
            actions: list[VoiceNlpAction] = []
            if isinstance(raw_actions, list):
                for item in raw_actions:
                    action = self._validate_deepseek_action(item, text, reason)
                    if action is None:
                        self._emit_diagnostic("DeepSeek解析", "失败", "返回动作未通过本地白名单校验，已回退本地规则")
                        return None
                    actions.append(action)
            else:
                action = self._validate_deepseek_action(payload, text, reason)
                if action is None:
                    self._emit_diagnostic("DeepSeek解析", "失败", "返回动作未通过本地白名单校验，已回退本地规则")
                    return None
                actions.append(action)
            if not actions:
                self._emit_diagnostic("DeepSeek解析", "失败", "未返回有效动作，已回退本地规则")
                return None
            summary = "、".join(f"{action.action_type}:{action.target or '-'}" for action in actions)
            self._emit_diagnostic("DeepSeek解析", "成功", f"{summary} | {reason}")
            return self._build_plan(actions=tuple(actions), source="deepseek", raw_text=text, reason=reason)
        except Exception as exc:
            self._emit_diagnostic("DeepSeek解析", "失败", f"{type(exc).__name__}: {exc}；已回退本地规则")
            return None

    def _answer_chat_with_deepseek(
        self,
        text: str,
        *,
        raw_text: str,
        chat_delta_callback: Callable[[str], None] | None = None,
    ) -> VoiceNlpPlan | None:
        try:
            client = self._external_deepseek_client or DeepSeekClient.from_env()
            prompt = self._build_deepseek_chat_prompt(text)
            self._emit_diagnostic("DeepSeek问答", "开始", "非控制类问题进入资料问答")
            if chat_delta_callback is not None and hasattr(client, "generate_chat_stream"):
                parts = []
                for chunk in client.generate_chat_stream(prompt, system_prompt=self._deepseek_chat_system_prompt()):
                    delta = self._sanitize_chat_delta(str(chunk or ""))
                    if not delta:
                        continue
                    parts.append(delta)
                    chat_delta_callback(delta)
                answer = "".join(parts)
            elif hasattr(client, "generate_chat"):
                answer = client.generate_chat(prompt, system_prompt=self._deepseek_chat_system_prompt())
            else:
                answer = client.generate(prompt)
            answer = self._sanitize_chat_answer(str(answer or ""))
            if not answer:
                self._emit_diagnostic("DeepSeek问答", "失败", "未返回有效回答，已回退本地提示")
                return None
            self._emit_diagnostic("DeepSeek问答", "成功", answer[:120])
            return self._build_plan(
                actions=(VoiceNlpAction("chat", None, "deepseek_chat", raw_text, answer),),
                source="deepseek_chat",
                raw_text=raw_text,
                reason=answer,
            )
        except Exception as exc:
            self._emit_diagnostic("DeepSeek问答", "失败", f"{type(exc).__name__}: {exc}；已回退本地提示")
            return None

    def _answer_chat_with_knowledge_base(self, text: str, *, raw_text: str) -> VoiceNlpPlan | None:
        answer = self.knowledge_base.best_answer(text, min_score=40)
        if not answer:
            return None
        return self._build_plan(
            actions=(VoiceNlpAction("chat", None, "knowledge_base", raw_text, answer),),
            source="knowledge_base",
            raw_text=raw_text,
            reason=answer,
        )

    @staticmethod
    def _sanitize_chat_answer(text: str) -> str:
        answer = re.sub(r"\s+", " ", text or "").strip()
        if len(answer) > 500:
            answer = answer[:497].rstrip() + "..."
        return answer

    @staticmethod
    def _sanitize_chat_delta(text: str) -> str:
        without_thinking = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
        return without_thinking

    @staticmethod
    def _deepseek_chat_system_prompt() -> str:
        return (
            "你是机械手自然语言交互系统的问答助手。"
            "你可以在内部完成意图判断、资料匹配和安全边界检查，但最终回复只输出给用户看的结论；"
            "不要输出思考过程、推理链、草稿、分析步骤或<think>标签；"
            "只回答身份、能力、使用方式、当前系统资料相关问题；"
            "不要生成控制动作，不要声称已经执行机械手动作；"
            "回答应简洁、中文、面向现场操作员。"
        )

    def _build_deepseek_chat_prompt(self, text: str) -> str:
        flow_names = "、".join(self.flow_names) or "暂无已登记流程"
        template_count = len(self.table)
        sample_templates = []
        for key in sorted(self.table)[:12]:
            record = self.table[key]
            sample_templates.append(f"{record.query_key}: Func{record.func_num} {record.description or record.keywords}")
        local_context = (
            "本地资料：\n"
            "- 系统名称：机械手自然语言交互系统。\n"
            "- 主要能力：自然语言理解、状态/看板查询、位置示教、流程草案创建、多轮澄清、安全预检、确认后执行、报警建议。\n"
            "- 安全原则：DeepSeek 只回答问题或生成草案，不直接控制机械手；执行前仍需本地白名单、安全预检和确认。\n"
            f"- 已加载模板数量：{template_count}。\n"
            f"- 已登记流程：{flow_names}。\n"
            f"- 模板示例：{'；'.join(sample_templates) if sample_templates else '暂无模板示例'}。\n"
        )
        knowledge_context = self.knowledge_base.prompt_context(text)
        if knowledge_context:
            local_context += f"{knowledge_context}\n"
        runtime_context = ""
        if self._runtime_context_provider is not None:
            try:
                runtime_context = str(self._runtime_context_provider() or "").strip()
            except Exception:
                runtime_context = ""
        if runtime_context:
            local_context += f"- 当前会话上下文：{runtime_context}\n"
        return (
            f"{local_context}\n"
            "请基于上面的本地资料回答用户问题。"
            "如果问题超出资料范围，请说明只能基于当前系统资料回答。\n"
            f"用户问题：{text}"
        )

    @staticmethod
    def _looks_like_chat_question(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        keywords = (
            "你是谁",
            "你是什么",
            "你能做什么",
            "有什么功能",
            "功能",
            "怎么用",
            "使用方法",
            "帮助",
            "你好",
            "您好",
            "介绍一下",
        )
        return any(keyword in compact for keyword in keywords)

    @staticmethod
    def _looks_like_explanatory_question(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        query_intents = ("看下", "看一下", "看看", "查询", "查一下", "想看", "有哪些", "什么", "怎么", "如何")
        explanation_targets = ("命令", "模板", "指令", "功能", "说法", "用法", "参数", "坐标", "位置库")
        return any(intent in compact for intent in query_intents) and any(target in compact for target in explanation_targets)

    def _emit_diagnostic(self, action: str, result: str, detail: str) -> None:
        """发出诊断信息。"""
        if self._diagnostic_callback is None:
            return
        try:
            self._diagnostic_callback(action, result, detail)
        except Exception:
            pass

    def _validate_deepseek_action(self, payload: object, raw_text: str, default_reason: str) -> VoiceNlpAction | None:
        """校验大模型。"""
        if not isinstance(payload, dict):
            return None
        action_type = str(payload.get("actionType", "")).strip().lower()
        target = str(payload.get("target", "")).strip() or None
        reason = str(payload.get("reason", "")).strip() or default_reason
        if action_type == "system" and target in SYSTEM_ACTION_ALIASES.values():
            return VoiceNlpAction("system", target, "deepseek", raw_text, reason)
        if action_type == "flow" and target in self.flow_names:
            return VoiceNlpAction("flow", target, "deepseek", raw_text, reason)
        if action_type == "template" and target in self.table:
            return VoiceNlpAction("template", target, "deepseek", raw_text, reason)
        if action_type == "unknown":
            return VoiceNlpAction("unknown", None, "deepseek", raw_text, reason)
        return None

    def _build_deepseek_prompt(self, text: str) -> str:
        """构建大模型。"""
        template_context = self._deepseek_template_context()
        flow_names = "、".join(self.flow_names) or "无"
        system_names = (
            "alarm_reset=报警复位；"
            "sys_pause=暂停当前系统；"
            "sys_resume=继续/恢复运行；"
            "sys_cancel=停止当前任务/取消当前动作；"
            "sys_estop=急停，仅三段式应急编码命中后才应返回"
        )
        return (
            "你负责把自然语言归类到现有 Qt 控制系统动作。\n"
            "这是安全相关工业控制系统，你只能做意图分类，不能创造新动作、不能输出解释性正文。\n"
            "调用你表示本地强规则未能确定动作，请根据下方白名单选择最接近的已有目标；若不确定必须返回 unknown。\n"
            "target 必须与白名单中的模板 query_key、流程名或系统动作 key 完全一致。\n"
            "如果用户输入包含顺序动作（例如“先...再...”或“然后...”），请输出 actions 数组，保持执行顺序。\n"
            "只允许返回以下 JSON 之一：\n"
            '{'
            '"actionType":"template|flow|system|unknown",'
            '"target":"目标名称",'
            '"reason":"一句原因"'
            '}\n'
            "或：\n"
            '{'
            '"actions":['
            '{"actionType":"template|flow|system|unknown","target":"目标名称","reason":"一步原因"}'
            '],'
            '"reason":"整体原因"'
            '}\n'
            f"可用模板清单(JSON数组，每项含 query_key/func_num/keywords/description/safety_level/params):\n{template_context}\n"
            f"可用流程: {flow_names}\n"
            f"系统动作目标: {system_names}\n"
            "匹配优先级: 明确模板 > 明确流程 > 系统动作 > unknown。\n"
            "如果用户只是查询状态、聊天、缺少必要目标、目标不在白名单中，返回 actionType=unknown。\n"
            "如果无法确定，返回 actionType=unknown。\n"
            f"用户输入: {text}"
        )

    def _deepseek_template_context(self, *, max_chars: int = 12000) -> str:
        """构建 DeepSeek 可用模板上下文，限制长度避免提示词过大。"""
        rows: list[dict[str, object]] = []
        for key in sorted(self.table):
            record = self.table[key]
            rows.append(
                {
                    "query_key": record.query_key,
                    "func_num": record.func_num,
                    "keywords": record.keywords,
                    "description": record.description,
                    "safety_level": record.safety_level,
                    "params": self._deepseek_param_summary(record.params),
                }
            )
        text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            return text
        truncated: list[dict[str, object]] = []
        used = 2
        for row in rows:
            item = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            extra = len(item) + (1 if truncated else 0)
            if used + extra + 32 > max_chars:
                break
            truncated.append(row)
            used += extra
        return json.dumps(truncated, ensure_ascii=False, separators=(",", ":")) + "\n[模板清单因长度限制已截断]"

    @staticmethod
    def _deepseek_param_summary(params: dict[str, Any], *, max_items: int = 12) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for index, key in enumerate(sorted(params)):
            if index >= max_items:
                summary["..."] = "truncated"
                break
            value = params[key]
            if isinstance(value, (int, float, str, bool)) or value is None:
                summary[key] = value
            else:
                summary[key] = str(value)
        return summary

    def _match_system_action(self, text: str) -> str | None:
        """处理系统。"""
        compact = text.replace(" ", "")
        for keyword, action_key in SYSTEM_ACTION_ALIASES.items():
            if keyword in compact:
                return action_key
        return None

    def _match_flow_name(self, text: str) -> str | None:
        """处理流程。"""
        compact = text.strip()
        match = re.search(r"(执行|开始|运行)?流程[:：]?\s*(.+)", compact)
        if match:
            candidate = match.group(2).strip()
            if candidate in self.flow_names:
                return candidate
        for flow_name in self.flow_names:
            if flow_name and re.search(r"(执行|开始|运行)" + re.escape(flow_name), compact):
                return flow_name
        return None
