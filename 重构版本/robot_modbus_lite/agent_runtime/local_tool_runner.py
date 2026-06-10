from __future__ import annotations

import re
from typing import Any

from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent, CommandUnderstandingResult
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator, AgentOrchestratorResult
from robot_modbus_lite.agent.service import RestrictedAgentResult
from robot_modbus_lite.agent_tools.tool_result import ToolResult
from robot_modbus_lite.models import QueryRecord

from .local_tool_registry import LocalToolRegistry
from .memory_normalizer import apply_active_memory_to_text
from .session_state import SessionState
from .tool_calling_agent import LocalToolSpec


class LocalToolCallingRunner:
    """Conservative local tool-calling runner used when LangChain is not configured."""

    def __init__(self, registry: LocalToolRegistry) -> None:
        self.registry = registry

    def __call__(
        self,
        text: str,
        session_state: SessionState,
        tool_specs: tuple[LocalToolSpec, ...],
    ) -> AgentOrchestratorResult:
        raw_text = str(text or "")
        route_text = raw_text
        applied_memories: tuple[dict[str, Any], ...] = ()
        if self.registry.memory_store is not None:
            normalized = apply_active_memory_to_text(self.registry.memory_store, raw_text)
            route_text = normalized.text
            applied_memories = normalized.applied
        vote = _feedback_vote(raw_text)
        if vote:
            result = self.registry.call(
                "record_feedback_vote",
                interaction_id=str(session_state.last_interaction_id or session_state.thread_id),
                target_type=_feedback_target_type(session_state),
                target_id=_feedback_target_id(session_state),
                vote=vote,
                note=raw_text,
            )
            return _feedback_result(
                result,
                raw_text=raw_text,
                tool_name="record_feedback_vote",
                kind="feedback_vote_recorded" if result.ok else "feedback_vote_rejected",
            )
        if _looks_like_cancel_execution(route_text):
            draft_id = _pending_confirm_draft_id(session_state)
            if not draft_id:
                return _confirm_result(
                    ToolResult.failure(
                        state="confirm_not_found",
                        message="当前没有待确认计划，不能取消执行。",
                        code="CONFIRM_NOT_FOUND",
                    ),
                    raw_text=raw_text,
                    tool_name="query_pending_confirm",
                    kind="confirm_rejected",
                )
            result = self.registry.call("cancel_pending_plan", draft_id=draft_id)
            return _confirm_result(
                result,
                raw_text=raw_text,
                tool_name="cancel_pending_plan",
                kind="confirm_cancelled" if result.ok else "confirm_rejected",
            )
        if _looks_like_confirm_execution(route_text) or (
            _pending_confirm_draft_id(session_state) and _looks_like_positive_pending_ack(route_text)
        ):
            draft_id = _pending_confirm_draft_id(session_state)
            if not draft_id:
                return _confirm_result(
                    ToolResult.failure(
                        state="confirm_not_found",
                        message="当前没有待确认计划，不能确认执行。",
                        code="CONFIRM_NOT_FOUND",
                    ),
                    raw_text=raw_text,
                    tool_name="query_pending_confirm",
                    kind="confirm_rejected",
                )
            result = self.registry.call("confirm_pending_plan", draft_id=draft_id)
            return _confirm_result(
                result,
                raw_text=raw_text,
                tool_name="confirm_pending_plan",
                kind="confirm_result" if result.ok else "confirm_rejected",
            )
        if _looks_like_followup_execute(route_text):
            return _confirm_result(
                ToolResult.failure(
                    state="confirm_not_found",
                    message="当前没有待确认计划，不能执行刚刚提到的命令。请先创建运动草案并完成确认。",
                    code="CONFIRM_NOT_FOUND",
                ),
                raw_text=raw_text,
                tool_name="query_pending_confirm",
                kind="followup_rejected",
            )
        if _is_waiting_flow_name(session_state):
            flow_name = _extract_flow_name(route_text)
            result = self.registry.call(
                "set_flow_name",
                draft=session_state.current_flow_draft,
                flow_name=flow_name,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="set_flow_name",
                session_state=session_state,
            )
        if _is_waiting_flow_step_params(session_state):
            result = self.registry.call(
                "answer_flow_clarification",
                text=route_text,
                draft=session_state.current_flow_draft,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="answer_flow_clarification",
                session_state=session_state,
            )
        if _is_editing_flow(session_state) and _looks_like_query_flow(route_text):
            result = self.registry.call(
                "query_current_flow_draft",
                draft=session_state.current_flow_draft,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="query_current_flow_draft",
                session_state=session_state,
            )
        if _is_editing_flow(session_state) and _looks_like_flow_context_followup(route_text):
            result = self.registry.call(
                "query_current_flow_draft",
                draft=session_state.current_flow_draft,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="query_current_flow_draft",
                session_state=session_state,
            )
        if _is_editing_flow(session_state) and _looks_like_edit_flow_params(route_text):
            result = self.registry.call(
                "edit_flow_draft_params",
                text=route_text,
                draft=session_state.current_flow_draft,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="edit_flow_draft_params",
                session_state=session_state,
            )
        if _is_editing_flow(session_state) and _looks_like_save_flow(route_text):
            result = self.registry.call(
                "save_flow_draft",
                draft=session_state.current_flow_draft,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="save_flow_draft",
                session_state=session_state,
            )
        if _is_editing_flow(session_state) and _looks_like_append_flow_step(route_text):
            result = self.registry.call(
                "append_flow_step",
                step_text=route_text,
                draft=session_state.current_flow_draft,
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="append_flow_step",
                session_state=session_state,
            )
        if _looks_like_flow_creation(route_text):
            result = self.registry.call(
                "start_flow_draft",
                text=route_text,
                flow_name=_extract_inline_flow_name(route_text),
            )
            return _flow_result(
                result,
                raw_text=raw_text,
                tool_name="start_flow_draft",
                session_state=session_state,
            )
        compound = self.registry.call("plan_compound_command", text=route_text)
        if compound.ok:
            return AgentOrchestratorResult(
                kind="compound_plan_draft",
                message=compound.message,
                payload={
                    "kind": "compound_plan_draft",
                    "text": compound.message,
                    "raw_text": raw_text,
                    "normalized_text": route_text,
                    "applied_memories": [dict(item) for item in applied_memories],
                    "generates_command": False,
                    "tool_name": "plan_compound_command",
                    "tool_result": compound.to_dict(),
                },
            )
        if _looks_like_command_catalog_query(route_text):
            catalog = self.registry.call("query_command_catalog", text=route_text)
            if catalog.ok:
                return _tool_result(
                    catalog,
                    raw_text=raw_text,
                    route_text=route_text,
                    applied_memories=applied_memories,
                    kind="command_catalog",
                    tool_name="query_command_catalog",
                )

        dashboard = self.registry.call("query_dashboard_section", text=route_text)
        if dashboard.ok and _looks_like_non_execution_dashboard_query(route_text):
            return AgentOrchestratorResult(
                kind="dashboard_query_action",
                message=dashboard.message,
                payload={
                    "kind": "dashboard_query_action",
                    "action_type": str(dashboard.data.get("action_type", "query") or "query"),
                    "target": str(dashboard.data.get("target", "") or ""),
                    "text": dashboard.message,
                    "raw_text": raw_text,
                    "normalized_text": route_text,
                    "applied_memories": [dict(item) for item in applied_memories],
                    "generates_command": False,
                    "tool_name": "query_dashboard_section",
                    "tool_result": dashboard.to_dict(),
                },
            )

        chat = self.registry.call("explain_text", text=route_text)
        if chat.ok:
            return _tool_result(
                chat,
                raw_text=raw_text,
                route_text=route_text,
                applied_memories=applied_memories,
                kind="chat_answer",
                tool_name="explain_text",
            )

        if _looks_like_memory_setting(route_text):
            return self._fallback("疑似参数或记忆设置文本，交回兼容 AgentOrchestrator。")
        missing_wake = _missing_wake_word_for_execution_text(route_text)
        if missing_wake is not None:
            return missing_wake
        if _looks_like_control_command(route_text):
            if not self.registry.control_tools_enabled:
                return self._fallback("控制类工具未启用，交回兼容 AgentOrchestrator。")
            atomic = self.registry.call("apply_atomic_template", text=route_text)
            if atomic.ok:
                record = _query_record_from_tool_data(atomic.data.get("query_record"))
                return AgentOrchestratorResult(
                    kind="atomic_template_action",
                    message=atomic.message,
                    payload={
                        "kind": "atomic_template_action",
                        "action_type": str(atomic.data.get("action_type", "") or "atomic_template"),
                        "target": str(atomic.data.get("target", "") or ""),
                        "text": atomic.message,
                        "raw_text": raw_text,
                        "normalized_text": route_text,
                        "applied_memories": [dict(item) for item in applied_memories],
                        "record": record,
                        "requires_confirmation": bool(atomic.data.get("requires_confirmation", True)),
                        "risk_level": str(atomic.data.get("risk_level", "") or ""),
                        "generates_command": True,
                        "tool_name": "apply_atomic_template",
                        "tool_result": atomic.to_dict(),
                    },
                )
            system = self.registry.call("build_system_action_draft", text=route_text)
            if system.ok:
                return AgentOrchestratorResult(
                    kind="restricted_agent",
                    message=system.message,
                    payload=_restricted_result_from_system_action(system.data),
                )
            draft = self.registry.call("build_command_draft", text=route_text)
            if draft.ok:
                if self.registry.safety_review_agent is not None and self.registry.confirmation_agent is not None:
                    precheck = self.registry.call("run_safety_precheck", draft=draft.data.get("draft", {}))
                    if precheck.ok:
                        confirm = self.registry.call("create_pending_confirm", draft=_draft_with_precheck(draft.data, precheck.data))
                        if confirm.ok:
                            return AgentOrchestratorResult(
                                kind="confirm_plan",
                                message=confirm.message,
                                payload={
                                    "kind": "confirm_plan",
                                    "text": confirm.message,
                                    "raw_text": raw_text,
                                    "normalized_text": route_text,
                                    "applied_memories": [dict(item) for item in applied_memories],
                                    "generates_command": False,
                                    "tool_name": "create_pending_confirm",
                                    "tool_result": confirm.to_dict(),
                                    "draft": _draft_with_precheck(draft.data, precheck.data),
                                    "precheck": dict(precheck.data.get("precheck", {}) or {}),
                                },
                            )
                        return AgentOrchestratorResult(
                            kind="confirm_rejected",
                            message=confirm.message,
                            payload={
                                "kind": "confirm_rejected",
                                "raw_text": raw_text,
                                "normalized_text": route_text,
                                "applied_memories": [dict(item) for item in applied_memories],
                                "generates_command": False,
                                "tool_name": "create_pending_confirm",
                                "tool_result": confirm.to_dict(),
                                "draft": _draft_with_precheck(draft.data, precheck.data),
                                "precheck": dict(precheck.data.get("precheck", {}) or {}),
                            },
                        )
                    return AgentOrchestratorResult(
                        kind="precheck_failed",
                        message=precheck.message,
                        payload={
                            "kind": "precheck_failed",
                            "raw_text": raw_text,
                            "normalized_text": route_text,
                            "applied_memories": [dict(item) for item in applied_memories],
                            "generates_command": False,
                            "tool_name": "run_safety_precheck",
                            "tool_result": precheck.to_dict(),
                            "draft": dict(draft.data.get("draft", {}) or {}),
                            "precheck": dict(precheck.data.get("precheck", {}) or {}),
                        },
                    )
                return AgentOrchestratorResult(
                    kind="restricted_agent",
                    message=draft.message,
                    payload=_restricted_result_from_command_draft(draft.data),
                )
            return self._fallback("疑似控制或参数设置文本，交回兼容 AgentOrchestrator。")

        if _looks_like_command_catalog_query(route_text):
            catalog = self.registry.call("query_command_catalog", text=route_text)
            if catalog.ok:
                return _tool_result(
                    catalog,
                    raw_text=raw_text,
                    route_text=route_text,
                    applied_memories=applied_memories,
                    kind="command_catalog",
                    tool_name="query_command_catalog",
                )

        dashboard = self.registry.call("query_dashboard_section", text=route_text)
        if dashboard.ok:
            return AgentOrchestratorResult(
                kind="dashboard_query_action",
                message=dashboard.message,
                payload={
                    "kind": "dashboard_query_action",
                    "action_type": str(dashboard.data.get("action_type", "query") or "query"),
                    "target": str(dashboard.data.get("target", "") or ""),
                    "text": dashboard.message,
                    "raw_text": raw_text,
                    "normalized_text": route_text,
                    "applied_memories": [dict(item) for item in applied_memories],
                    "generates_command": False,
                    "tool_name": "query_dashboard_section",
                    "tool_result": dashboard.to_dict(),
                },
            )

        chat = self.registry.call("explain_text", text=route_text)
        if chat.ok:
            return _tool_result(
                chat,
                raw_text=raw_text,
                route_text=route_text,
                applied_memories=applied_memories,
                kind="chat_answer",
                tool_name="explain_text",
            )

        missing_wake = _missing_wake_word_for_execution_text(route_text)
        if missing_wake is not None:
            return missing_wake
        return _clarification_result(
            raw_text=raw_text,
            route_text=route_text,
            applied_memories=applied_memories,
            message="请补充明确的问题、状态查询或控制指令。没有触发机械手动作。",
        )

    @staticmethod
    def _fallback(reason: str) -> AgentOrchestratorResult:
        return AgentOrchestratorResult(
            kind="tool_calling_unavailable",
            message=reason,
            payload={"fallback_required": True, "reason": reason},
        )


def _flow_result(result: ToolResult, *, raw_text: str, tool_name: str, session_state: SessionState) -> AgentOrchestratorResult:
    tool_result_payload = result.to_dict()
    tool_data = tool_result_payload.get("data")
    plan = None
    if isinstance(tool_data, dict):
        plan = tool_data.pop("plan", None)
    next_state = session_state.with_tool_result(
        tool_name=tool_name,
        tool_result=result,
        user_text=raw_text,
        normalized_text=raw_text,
    )
    draft = dict(result.data.get("draft", {}) or {})
    if draft:
        next_state = next_state.with_flow_draft(draft)
    return AgentOrchestratorResult(
        kind="flow_draft",
        message=result.message,
        payload={
            "kind": "flow_draft",
            "text": result.message,
            "raw_text": raw_text,
            "generates_command": False,
            "tool_name": tool_name,
            "tool_result": tool_result_payload,
            "draft": draft,
            "plan": plan,
            "session_state": next_state.to_dict(),
        },
    )


def _confirm_result(result: ToolResult, *, raw_text: str, tool_name: str, kind: str) -> AgentOrchestratorResult:
    return AgentOrchestratorResult(
        kind=kind,
        message=result.message,
        payload={
            "kind": kind,
            "text": result.message,
            "raw_text": raw_text,
            "generates_command": False,
            "tool_name": tool_name,
            "tool_result": result.to_dict(),
        },
    )


def _tool_result(
    result: ToolResult,
    *,
    raw_text: str,
    route_text: str,
    applied_memories: tuple[dict[str, Any], ...],
    kind: str,
    tool_name: str,
) -> AgentOrchestratorResult:
    return AgentOrchestratorResult(
        kind=kind,
        message=result.message,
        payload={
            "kind": kind,
            "text": result.message,
            "raw_text": raw_text,
            "normalized_text": route_text,
            "applied_memories": [dict(item) for item in applied_memories],
            "generates_command": False,
            "tool_name": tool_name,
            "tool_result": result.to_dict(),
        },
    )


def _clarification_result(
    *,
    raw_text: str,
    route_text: str,
    applied_memories: tuple[dict[str, Any], ...],
    message: str,
) -> AgentOrchestratorResult:
    return AgentOrchestratorResult(
        kind="clarification",
        message=message,
        payload={
            "kind": "clarification",
            "text": message,
            "raw_text": raw_text,
            "normalized_text": route_text,
            "applied_memories": [dict(item) for item in applied_memories],
            "generates_command": False,
        },
    )


def _missing_wake_word_for_execution_text(text: str) -> AgentOrchestratorResult | None:
    understanding_agent = CommandUnderstandingAgent()
    understanding = understanding_agent.understand(text)
    if AgentOrchestrator._has_wake_word(text):
        return None
    if AgentOrchestrator._looks_like_non_execution_question(text):
        return None
    if AgentOrchestrator._execution_intent_requires_wake_word(understanding):
        return _missing_wake_word_result(text, understanding)
    wake_checked = understanding_agent.understand(f"小正，{text}")
    if AgentOrchestrator._execution_intent_requires_wake_word(wake_checked):
        return _missing_wake_word_result(text, wake_checked)
    if AgentOrchestrator._atomic_template_text_requires_wake_word(text):
        return _missing_wake_word_result(text, wake_checked)
    return None


def _missing_wake_word_result(text: str, understanding: CommandUnderstandingResult) -> AgentOrchestratorResult:
    return AgentOrchestratorResult(
        kind="clarification",
        message="生产执行指令缺少“小正或小兵”唤醒词，未执行。请带唤醒词重新下发生产指令。",
        payload={
            "kind": "clarification",
            "reason": "missing_wake_word",
            "needs_model": False,
            "generates_command": False,
            "raw_text": str(text or ""),
            "normalized_text": str(text or ""),
            "understanding": {
                "raw_text": str(getattr(understanding, "raw_text", "") or text or ""),
                "intent": str(getattr(understanding, "intent", "") or ""),
                "func_id": getattr(understanding, "func_id", None),
                "confidence": float(getattr(understanding, "confidence", 0.0) or 0.0),
                "clarification": str(getattr(understanding, "clarification", "") or ""),
                "bypass_completion": bool(getattr(understanding, "bypass_completion", False)),
            },
        },
    )


def _query_record_from_tool_data(value: Any) -> QueryRecord | None:
    if isinstance(value, QueryRecord):
        return value
    if not isinstance(value, dict):
        return None
    query_key = str(value.get("query_key", "") or "")
    if not query_key:
        return None
    try:
        func_num = int(value.get("func_num", 0) or 0)
    except (TypeError, ValueError):
        return None
    params = value.get("params")
    return QueryRecord(
        query_key=query_key,
        func_num=func_num,
        params=dict(params or {}) if isinstance(params, dict) else {},
        keywords=str(value.get("keywords", "") or ""),
        description=str(value.get("description", "") or ""),
        safety_level=int(value.get("safety_level", 5) or 5),
    )


def _feedback_result(result: ToolResult, *, raw_text: str, tool_name: str, kind: str) -> AgentOrchestratorResult:
    return AgentOrchestratorResult(
        kind=kind,
        message=result.message,
        payload={
            "kind": kind,
            "text": result.message,
            "raw_text": raw_text,
            "generates_command": False,
            "tool_name": tool_name,
            "tool_result": result.to_dict(),
        },
    )


def _feedback_vote(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return ""
    if any(word in compact for word in ("没用", "不对", "错了", "不好", "无效", "差评", "踩")):
        return "down"
    if any(word in compact for word in ("有用", "正确", "对了", "很好", "可以", "赞")):
        return "up"
    return ""


def _feedback_target_type(session_state: SessionState) -> str:
    result = dict(session_state.last_agent_result or {})
    kind = str(result.get("kind", "") or "")
    if "memory" in kind:
        return "memory"
    if kind:
        return "answer"
    return "interaction"


def _feedback_target_id(session_state: SessionState) -> str:
    result = dict(session_state.last_agent_result or {})
    return str(result.get("target_id", "") or result.get("draft_id", "") or session_state.last_interaction_id or session_state.thread_id)


def _looks_like_confirm_execution(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    return compact in {"确认", "确认执行", "执行确认", "可以执行", "确认运行", "开始执行"} or (
        "确认" in compact and any(word in compact for word in ("执行", "运行", "计划", "草案"))
    )


def _looks_like_positive_pending_ack(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return compact in {"好的", "好", "可以", "行", "嗯", "嗯嗯", "那就这个", "就这个", "就这样", "可以了"}


def _looks_like_followup_execute(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if not any(word in compact for word in ("执行", "运行", "开始")):
        return False
    return any(word in compact for word in ("刚刚", "刚才", "上一个", "上次", "这个", "它", "刚创建", "刚才创建"))


def _looks_like_cancel_execution(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    explicit = {"取消", "取消执行", "取消确认", "取消计划", "不要执行", "先不执行", "撤销执行"}
    if compact in explicit:
        return True
    return any(word in compact for word in ("取消", "撤销", "不执行", "别执行", "不要执行")) and any(
        word in compact for word in ("执行", "确认", "计划", "草案")
    )


def _pending_confirm_draft_id(session_state: SessionState) -> str:
    pending = dict(session_state.pending_confirm or {})
    return str(pending.get("draft_id", "") or pending.get("confirm_id", "") or pending.get("plan_id", "") or "")


def _is_waiting_flow_name(session_state: SessionState) -> bool:
    if "flow_name" not in tuple(session_state.pending_missing_fields or ()):
        return False
    if str(session_state.current_intent or "") == "create_flow":
        return True
    return bool(session_state.current_flow_draft)


def _is_waiting_flow_step_params(session_state: SessionState) -> bool:
    missing = set(str(field) for field in tuple(session_state.pending_missing_fields or ()))
    if not missing.intersection({"target_pose", "target", "pose", "delay_sec", "io_no", "io_action"}):
        return False
    return bool(session_state.current_flow_draft) and str(session_state.current_intent or "") in {"create_flow", "flow_create", ""}


def _is_editing_flow(session_state: SessionState) -> bool:
    if not session_state.current_flow_draft:
        return False
    return str(session_state.mode or "") in {"editing_flow", "creating_flow", "clarifying"}


def _looks_like_append_flow_step(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    has_step_marker = any(word in compact for word in ("添加", "新增", "加上", "第一步", "第1步", "下一步"))
    has_step_marker = has_step_marker or bool(re.search(r"(?:步骤|第)(?:[一二三四五六七八九十]+|\d+)(?:步)?", compact))
    if not has_step_marker:
        return False
    return any(word in compact for word in ("移动", "走到", "到位置", "等待", "延时", "IO", "输出"))


def _looks_like_save_flow(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(word in compact for word in ("保存并执行", "保存后执行")):
        return False
    return any(word in compact for word in ("保存流程", "保存草案", "保存这个流程", "确认保存", "确认草案"))


def _looks_like_query_flow(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    return any(word in compact for word in ("查看流程", "看一下流程", "当前流程", "看看流程", "流程草案", "刚刚的流程"))


def _looks_like_flow_context_followup(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(word in compact for word in ("为什么", "怎么回事", "哪里", "刚才", "刚刚")):
        return True
    return any(word in compact for word in ("对呀", "肯定", "当然", "是的", "用我的坐标", "就用这个"))


def _looks_like_edit_flow_params(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if not re.search(r"-?\d+(?:\.\d+)?\s*%?", compact):
        return False
    has_edit_word = any(word in compact for word in ("改成", "改为", "修改为", "调成", "设为", "设置为"))
    has_param_word = any(word in compact for word in ("速度", "加速度", "减速度", "加速", "减速"))
    return has_edit_word or has_param_word


def _looks_like_flow_creation(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact or "流程" not in compact:
        return False
    if any(word in compact for word in ("哪些流程", "有什么流程", "查看流程", "查询流程", "执行流程", "运行流程")):
        return False
    return any(word in compact for word in ("创建", "新建", "添加", "建立"))


def _extract_inline_flow_name(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    patterns = (
        r"(?:流程名|流程名称|流程名字|名字|名称)(?:叫|为|是)(?P<name>[^，。,.；;]+)",
        r"(?:创建|新建|添加|建立)(?:一个|新的|新)?(?P<name>[^，。,.；;]*?)流程",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            name = _clean_flow_name(match.group("name"))
            if name and name not in {"新", "新的", "一个", "新的一个"}:
                return name
    return ""


def _extract_flow_name(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    patterns = (
        r"(?:流程名|流程名称|流程名字|名字|名称)(?:叫|为|是)(?P<name>[^，。,.；;]+)",
        r"(?:现在)?(?:流程)?(?:名字|名称)?(?:叫|为|是)(?P<name>[^，。,.；;]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            name = _clean_flow_name(match.group("name"))
            if name:
                return name
    return _clean_flow_name(compact)


def _clean_flow_name(value: str) -> str:
    text = re.sub(r"^[的地得\s]+", "", str(value or "").strip())
    text = re.sub(r"[，。,.；;！!？?]+$", "", text)
    return text.strip()


def _looks_like_control_command(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    command = LocalToolRegistry().call("parse_command_params", text=compact)
    if command.ok or bool(command.data.get("needs_model", False)):
        return True
    return bool(_has_motion_marker(compact))


def _looks_like_memory_setting(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(word in compact for word in ("确认模式", "新手模式", "专家模式", "保存当前位置", "删除位置")):
        return True
    if re.search(r"(速度|加速度|减速度|加速|减速|步长)\-?\d+(?:\.\d+)?%?", compact):
        return not _has_motion_marker(compact)
    return False


def _looks_like_command_catalog_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if not any(word in compact for word in ("命令", "流程", "模板")):
        return False
    return any(
        marker in compact
        for marker in (
            "有哪些",
            "有什么",
            "支持哪些",
            "可用",
            "列表",
            "所有",
            "全部",
            "多少个",
            "几个",
            "命令和流程",
            "流程和命令",
        )
    )


def _looks_like_non_execution_dashboard_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if re.search(r"(速度|加速度|减速度|高度|X|Y|Z|RX|RY|RZ)-?\d+(?:\.\d+)?%?", compact, flags=re.IGNORECASE):
        return False
    if re.search(r"(升高|降低|上升|下降|前进|后退|左移|右移)-?\d+(?:\.\d+)?", compact):
        return False
    return any(
        marker in compact
        for marker in (
            "吗",
            "为什么",
            "状态",
            "就绪",
            "报警",
            "不能",
            "风险",
            "正常",
            "进度",
            "到哪",
            "怎么样",
            "什么原因",
        )
    )


def _has_motion_marker(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if re.search(r"(?:RX|RY|RZ|X|Y|Z)-?\d+(?:\.\d+)?", compact, flags=re.IGNORECASE):
        return True
    if re.search(r"J[1-6](?:转到|到|绝对|正转|反转|负转|逆时针|回退)-?\d+(?:\.\d+)?", compact, flags=re.IGNORECASE):
        return True
    return any(word in compact for word in ("移动", "走到", "到位置", "前进", "后退", "左移", "右移", "上升", "下降"))


def _restricted_result_from_command_draft(data: dict[str, Any]) -> RestrictedAgentResult:
    draft_data = dict(data.get("draft", {}) or {})
    params = dict(draft_data.get("params", {}) or {})
    draft = CommandDraft(
        draft_id=str(draft_data.get("draft_id", "") or ""),
        func_id=int(draft_data.get("func_id", 0) or 0),
        intent=str(draft_data.get("intent", "") or ""),
        params=params,
        param_sources=dict(draft_data.get("param_sources", {}) or {}),
        raw_text=str(draft_data.get("raw_text", "") or ""),
        confidence=float(draft_data.get("confidence", 0.0) or 0.0),
        precheck_result=dict(draft_data.get("precheck_result") or {}),
        confirmed=bool(draft_data.get("confirmed", False)),
    )
    understanding = CommandUnderstandingResult(
        raw_text=draft.raw_text,
        intent=draft.intent,
        func_id=draft.func_id,
        extracted_params=params,
        confidence=draft.confidence,
    )
    return RestrictedAgentResult(
        kind="waiting_confirmation",
        intent=draft.intent,
        func_id=draft.func_id,
        message="已生成命令草案，等待操作者确认。",
        understanding=understanding,
        draft=draft,
        precheck_result={},
        confirmation_text="",
    )


def _restricted_result_from_system_action(data: dict[str, Any]) -> RestrictedAgentResult:
    intent = str(data.get("intent", "") or "")
    func_id = int(data.get("func_id", 104) or 104)
    understanding = CommandUnderstandingResult(
        raw_text=str(data.get("raw_text", "") or ""),
        intent=intent,
        func_id=func_id,
        extracted_params={},
        confidence=float(data.get("confidence", 1.0) or 1.0),
        bypass_completion=True,
    )
    return RestrictedAgentResult(
        kind="bypass",
        intent=intent,
        func_id=func_id,
        message="已生成系统动作草案，等待本地门禁处理。",
        understanding=understanding,
    )


def _draft_with_precheck(draft_data: dict[str, Any], precheck_data: dict[str, Any]) -> dict[str, Any]:
    draft = dict(draft_data.get("draft", {}) or {})
    if not draft:
        return draft
    draft["precheck_result"] = dict(precheck_data.get("precheck", {}) or {})
    return draft
