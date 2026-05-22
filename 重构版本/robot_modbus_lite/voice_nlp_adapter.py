"""将语音文本转换为确定性动作或大模型规划。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from .command_parser import CommandParseError, parse_command
from .deepseek_client import DeepSeekClient
from .models import QueryRecord


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

WAKE_WORDS = ("小正", "小郑", "校正")


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
    semantic_level: int = 0
    semantic_label: str = "未识别层"
    response_deadline_ms: int = 500
    requires_precheck: bool = False
    requires_confirmation: bool = False
    priority: str = "normal"
    tokens: tuple[str, ...] = ()
    nlp_engine: str = "rule"

    def to_preview_dict(self) -> dict[str, object]:
        """处理相关数据。"""
        return {
            "source": self.source,
            "engine": self.nlp_engine,
            "rawText": self.raw_text,
            "reason": self.reason,
            "semanticLevel": self.semantic_level,
            "semanticLabel": self.semantic_label,
            "responseDeadlineMs": self.response_deadline_ms,
            "requiresPrecheck": self.requires_precheck,
            "requiresConfirmation": self.requires_confirmation,
            "priority": self.priority,
            "tokens": list(self.tokens),
            "actions": [action.to_preview_dict() for action in self.actions],
        }


class VoiceNlpAdapter:
    """语音文本到指令计划的适配器。"""
    def __init__(
        self,
        table: dict[str, QueryRecord],
        flow_names: Iterable[str],
        tokenizer: Callable[[str], Iterable[str]] | None = None,
    ) -> None:
        """初始化对象。"""
        self.table = table
        self.flow_names = tuple(sorted(str(name) for name in flow_names))
        self._external_deepseek_client = None
        self._diagnostic_callback: Callable[[str, str, str], None] | None = None
        self._tokenizer = tokenizer

    def set_deepseek_client(self, client) -> None:
        """设置大模型客户端。"""
        self._external_deepseek_client = client

    def set_diagnostic_callback(self, callback: Callable[[str, str, str], None]) -> None:
        """设置诊断回调，用于把大模型回退原因写入 GUI 日志。"""
        self._diagnostic_callback = callback

    def parse(self, text: str, *, use_deepseek: bool = False) -> VoiceNlpPlan:
        """解析相关数据。"""
        normalized = text.strip()
        if not normalized:
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "rule", text, "输入为空"),),
                source="rule",
                raw_text=text,
                reason="输入为空",
            )

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

        command_text = self._strip_wake_word(normalized)
        if command_text is None:
            if not self._looks_like_control_text(normalized):
                return self._build_plan(
                    actions=(VoiceNlpAction("unknown", None, "rule", text, "闲聊或咨询，未触发控制动作"),),
                    source="rule",
                    raw_text=text,
                    reason="闲聊或咨询，未触发控制动作",
                )
            return self._build_plan(
                actions=(VoiceNlpAction("unknown", None, "rule", text, "生产指令缺少“小正”唤醒词，未执行"),),
                source="rule",
                raw_text=text,
                reason="生产指令缺少“小正”唤醒词，未执行",
            )

        if use_deepseek:
            deepseek_result = self._parse_with_deepseek(command_text)
            if deepseek_result is not None:
                return deepseek_result

        plan = self._parse_with_rules(command_text)
        return self._build_plan(actions=plan.actions, source=plan.source, raw_text=text, reason=plan.reason)

    def _build_plan(
        self,
        *,
        actions: tuple[VoiceNlpAction, ...],
        source: str,
        raw_text: str,
        reason: str,
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
            requires_confirmation=metadata["requires_confirmation"],
            priority=metadata["priority"],
            tokens=tokens,
            nlp_engine=nlp_engine,
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
        if action_type in {"template", "flow"}:
            return {
                "semantic_level": 3,
                "semantic_label": "常规生产执行层",
                "response_deadline_ms": 2000,
                "requires_precheck": True,
                "requires_confirmation": True,
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
        for wake_word in WAKE_WORDS:
            if compact.startswith(wake_word):
                command = compact[len(wake_word):].lstrip(" ，,。:：")
                return command or ""
        return None

    @staticmethod
    def _is_coded_emergency(text: str) -> bool:
        return bool(re.match(r"^\s*(?:急停|紧急停止)\s+[A-Za-z0-9_-]{3,16}\s+(?:急停|紧急停止)\s*$", text))

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
        lowered = compact.lower()
        patterns = (
            ("communication_faults", ("通讯", "通信", "连接", "ethercat", "ecat")),
            ("action_feasibility", ("能不能执行", "可以执行", "可执行", "能发指令", "能动", "能不能动")),
            ("safety_boundary", ("安全吗", "安全不安全", "当前位置安全", "半径", "高度", "边界", "软限位", "关节限位")),
            ("motion_limits", ("速度", "加速度", "减速度", "超限")),
            ("process_preview", ("预演到哪", "预演进度", "流程预演", "流程到哪", "执行到哪")),
            ("process_adaptation", ("到不到", "能到", "运动规划", "奇异", "姿态")),
            ("device_status", ("设备状态", "现在状态", "当前状态", "系统状态")),
        )
        for target, keywords in patterns:
            if any(keyword.lower() in lowered for keyword in keywords):
                return target
        token_set = {str(token).strip().lower() for token in tokens if str(token).strip()}
        query_words = {"查", "查询", "看看", "看一下", "状态", "看板", "现在"}
        if not (token_set & query_words):
            return None
        token_targets = (
            ("communication_faults", {"通讯", "通信", "连接", "ethercat", "ecat", "故障"}),
            ("action_feasibility", {"可行", "执行", "能动", "动作", "通道"}),
            ("safety_boundary", {"安全", "边界", "范围", "限位", "半径", "高度"}),
            ("motion_limits", {"速度", "加速度", "减速度", "超限", "极限"}),
            ("process_preview", {"预演", "流程", "进度", "步骤"}),
            ("process_adaptation", {"工艺", "适配", "规划", "奇异", "姿态", "fstatus"}),
            ("device_status", {"设备", "系统", "状态", "当前"}),
        )
        for target, board_words in token_targets:
            if token_set & {word.lower() for word in board_words}:
                return target
        return None

    def _parse_with_rules(self, text: str) -> VoiceNlpPlan:
        """解析相关数据。"""
        actions = self._parse_multiple_with_rules(text)
        if actions:
            reason = "命中多动作规则" if len(actions) > 1 else actions[0].reason
            return VoiceNlpPlan(actions=tuple(actions), source="rule", raw_text=text, reason=reason)

        action = self._parse_single_with_rules(text)
        return VoiceNlpPlan(actions=(action,), source="rule", raw_text=text, reason=action.reason)

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
            return self._build_plan(actions=tuple(actions), source="deepseek", raw_text=text, reason=reason)
        except Exception as exc:
            self._emit_diagnostic("DeepSeek解析", "失败", f"{type(exc).__name__}: {exc}；已回退本地规则")
            return None

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
        template_names = "、".join(sorted(self.table))
        flow_names = "、".join(self.flow_names) or "无"
        system_names = "alarm_reset、sys_pause、sys_resume、sys_cancel、sys_estop"
        return (
            "你负责把自然语言归类到现有 Qt 控制系统动作。\n"
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
            f"可用模板: {template_names}\n"
            f"可用流程: {flow_names}\n"
            f"系统动作目标: {system_names}\n"
            "如果无法确定，返回 actionType=unknown。\n"
            f"用户输入: {text}"
        )

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
