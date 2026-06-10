from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from typing import Any, TypedDict

from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent_tools.tool_result import ToolResult

from .local_tool_registry import LocalToolRegistry
from .local_tool_runner import LocalToolCallingRunner
from .session_state import SessionState
from .tool_calling_agent import LocalToolSpec, build_local_tool_specs


class LangChainRuntimeUnavailable(RuntimeError):
    pass


class _RuntimeGraphState(TypedDict, total=False):
    text: str
    session_state: dict[str, Any]
    tool_specs: list[dict[str, Any]]
    compound_step_result: dict[str, Any]
    tool_decision: dict[str, Any]
    direct_tool_result_ok: bool
    direct_tool_error: dict[str, Any]
    direct_tool_result: dict[str, Any]
    kind: str
    message: str
    payload: dict[str, Any]
    pending_confirm_expired: bool


_CONFIRM_TOOL_NAMES = {
    "create_pending_confirm",
    "query_pending_confirm",
    "confirm_pending_plan",
    "cancel_pending_plan",
    "expire_pending_plan",
}


_FLOW_TOOL_NAMES = {
    "start_flow_draft",
    "set_flow_name",
    "append_flow_step",
    "answer_flow_clarification",
    "save_flow_draft",
    "set_flow_draft",
    "query_current_flow_draft",
    "cancel_flow_draft",
}


_COMPOUND_TOOL_NAMES = {
    "split_compound_command",
    "plan_compound_command",
}


def langchain_dependencies_available(*, find_spec: Callable[[str], Any] | None = None) -> bool:
    finder = find_spec or importlib.util.find_spec
    return bool(finder("langchain_core")) and bool(finder("langgraph"))


def build_langchain_tools(
    registry: LocalToolRegistry,
    *,
    find_spec: Callable[[str], Any] | None = None,
) -> tuple[Any, ...]:
    if not langchain_dependencies_available(find_spec=find_spec):
        raise LangChainRuntimeUnavailable("LangChain/LangGraph 依赖不可用，无法创建 LangChain tools。")
    from langchain_core.tools import StructuredTool

    tools: list[Any] = []
    for name in registry.tool_names:
        tools.append(
            StructuredTool.from_function(
                name=name,
                description=f"Call local robot agent tool: {name}",
                func=_tool_func(registry, name),
            )
        )
    return tuple(tools)


class LangChainToolRunner:
    def __init__(self, registry: LocalToolRegistry, *, graph_app: Any = None) -> None:
        self.registry = registry
        self.graph_app = graph_app

    def __call__(
        self,
        text: str,
        session_state: SessionState,
        tool_specs: tuple[LocalToolSpec, ...],
    ) -> AgentOrchestratorResult:
        if self.graph_app is None:
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="LangChain/LangGraph runner 尚未配置 graph_app。",
                payload={"fallback_required": True},
            )
        response = self.graph_app.invoke(
            {
                "text": str(text or ""),
                "session_state": session_state.to_dict(),
                "tool_specs": [spec.__dict__ for spec in tool_specs],
            }
        )
        return _result_from_graph_response(response)


def _tool_func(registry: LocalToolRegistry, name: str):
    def call_tool(payload_json: str = "{}") -> dict[str, Any]:
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "state": "tool_payload_invalid",
                "message": str(exc),
                "data": {"tool_name": name},
                "errors": [{"code": "TOOL_PAYLOAD_INVALID", "message": str(exc)}],
            }
        if not isinstance(payload, dict):
            payload = {}
        return registry.call(name, **payload).to_dict()

    return call_tool


def _result_from_graph_response(response: Any) -> AgentOrchestratorResult:
    if isinstance(response, AgentOrchestratorResult):
        return response
    if isinstance(response, dict):
        return AgentOrchestratorResult(
            kind=str(response.get("kind", "") or "tool_calling_unavailable"),
            message=str(response.get("message", "") or ""),
            payload=response.get("payload"),
        )
    return AgentOrchestratorResult(
        kind="tool_calling_unavailable",
        message="LangChain/LangGraph graph_app 返回了不可识别结果。",
        payload={"fallback_required": True, "raw_response": repr(response)},
    )


def build_default_langgraph_app(
    registry: LocalToolRegistry,
    *,
    tool_decider: Callable[[dict[str, Any]], Any] | None = None,
) -> Any:
    if not langchain_dependencies_available():
        raise LangChainRuntimeUnavailable("LangChain/LangGraph 依赖不可用，无法创建默认 graph_app。")
    from langgraph.graph import END, StateGraph

    local_runner = LocalToolCallingRunner(registry)

    def check_pending_timeout(state: dict[str, Any]) -> dict[str, Any]:
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        next_state = dict(state)
        next_state["pending_confirm_expired"] = _pending_confirm_expired(session_state, now=_registry_now(registry))
        return next_state

    def expire_pending_state(state: dict[str, Any]) -> dict[str, Any]:
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        pending_confirm = dict(session_state.pending_confirm or {})
        expired_state = session_state.expire_pending_confirm()
        tool_result = ToolResult.failure(
            state="confirm_expired",
            message="待确认计划已过期，请重新生成执行草案。",
            code="CONFIRM_EXPIRED",
            data={"pending_confirm": pending_confirm},
        )
        payload = {
            "kind": "confirm_result",
            "text": tool_result.message,
            "raw_text": str(state.get("text", "") or ""),
            "generates_command": False,
            "tool_name": "expire_pending_plan",
            "tool_result": tool_result.to_dict(),
            "session_state": expired_state.to_dict(),
        }
        return {
            **dict(state),
            "kind": "confirm_result",
            "message": tool_result.message,
            "payload": payload,
            "session_state": expired_state.to_dict(),
            "direct_tool_result_ok": True,
            "direct_tool_result": tool_result.to_dict(),
        }

    def sync_compound_step_result(state: dict[str, Any]) -> dict[str, Any]:
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        result_payload = state.get("compound_step_result") if isinstance(state.get("compound_step_result"), dict) else {}
        reason = str(result_payload.get("reason", "") or "")
        advanced_state = session_state.advance_compound_step(ok=bool(result_payload.get("ok", False)), reason=reason)
        ok = advanced_state.current_compound_plan.get("status") != "failed"
        result_data = {
            "compound_step_result": dict(result_payload),
            "compound_plan": dict(advanced_state.current_compound_plan),
            "generates_command": False,
        }
        if ok:
            tool_result = ToolResult.success(
                state="compound_step_advanced",
                message=reason or "复合指令步骤已完成。",
                data=result_data,
            )
        else:
            tool_result = ToolResult.failure(
                state="compound_step_failed",
                message=reason or "复合指令步骤失败。",
                code="COMPOUND_STEP_FAILED",
                data=result_data,
            )
        payload = {
            "kind": "compound_step_result",
            "text": tool_result.message,
            "raw_text": str(state.get("text", "") or ""),
            "generates_command": False,
            "tool_name": "compound_step_result",
            "tool_result": tool_result.to_dict(),
            "session_state": advanced_state.to_dict(),
        }
        return {
            **dict(state),
            "kind": "compound_step_result",
            "message": tool_result.message,
            "payload": payload,
            "session_state": advanced_state.to_dict(),
            "direct_tool_result_ok": True,
            "direct_tool_result": tool_result.to_dict(),
        }

    def decide_tool(state: dict[str, Any]) -> dict[str, Any]:
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        tool_specs = build_local_tool_specs()
        if tool_decider is not None:
            decision = _decide_tool_call(tool_decider, state, session_state, tool_specs)
            if decision is not None:
                tool_name, args, tool_call_id = decision
                next_state = dict(state)
                next_state["tool_decision"] = {
                    "tool_name": tool_name,
                    "args": args,
                    "tool_call_id": tool_call_id,
                }
                return next_state
        next_state = dict(state)
        next_state["tool_decision"] = {}
        return next_state

    def call_tool(state: dict[str, Any]) -> dict[str, Any]:
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        decision = state.get("tool_decision") if isinstance(state.get("tool_decision"), dict) else {}
        tool_name = str(decision.get("tool_name", "") or "")
        args = decision.get("args", {}) if isinstance(decision.get("args"), dict) else {}
        tool_call_id = str(decision.get("tool_call_id", "") or "")
        if not tool_name:
            next_state = dict(state)
            next_state["direct_tool_result_ok"] = False
            return next_state
        if tool_call_id:
            tool_result, session_state = registry.call_idempotent(
                tool_name,
                session_state=session_state,
                tool_call_id=tool_call_id,
                **args,
            )
        else:
            tool_result = registry.call(tool_name, **args)
        if tool_result.ok:
            session_state = _session_state_after_tool_result(
                session_state,
                tool_name=tool_name,
                tool_result=tool_result,
                raw_text=str(state.get("text", "") or ""),
            )
            result = _result_from_direct_tool_call(
                tool_name,
                tool_result,
                raw_text=str(state.get("text", "") or ""),
            )
            payload = dict(result.payload or {})
            payload["session_state"] = session_state.to_dict()
            return {
                **dict(state),
                "kind": result.kind,
                "message": result.message,
                "payload": payload,
                "session_state": session_state.to_dict(),
                "direct_tool_result_ok": True,
                "direct_tool_result": tool_result.to_dict(),
            }
        if (
            str(tool_result.state) in {"tool_args_invalid", "tool_schema_not_found"}
            or tool_name in _CONFIRM_TOOL_NAMES
            or tool_name in _FLOW_TOOL_NAMES
            or tool_name in _COMPOUND_TOOL_NAMES
        ):
            result = _result_from_direct_tool_call(
                tool_name,
                tool_result,
                raw_text=str(state.get("text", "") or ""),
            )
            payload = dict(result.payload or {})
            payload["session_state"] = session_state.to_dict()
            return {
                **dict(state),
                "kind": result.kind,
                "message": result.message,
                "payload": payload,
                "session_state": session_state.to_dict(),
                "direct_tool_result_ok": True,
                "direct_tool_result": tool_result.to_dict(),
            }
        return {
            **dict(state),
            "session_state": session_state.to_dict(),
            "direct_tool_result_ok": False,
            "direct_tool_error": tool_result.to_dict(),
            }

    def sync_compound_state(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("tool_decision") if isinstance(state.get("tool_decision"), dict) else {}
        tool_name = str(decision.get("tool_name", "") or "")
        if tool_name not in _COMPOUND_TOOL_NAMES:
            return dict(state)
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        tool_result_payload = state.get("direct_tool_result") if isinstance(state.get("direct_tool_result"), dict) else {}
        tool_result = _tool_result_from_payload(tool_result_payload)
        if tool_result is not None:
            session_state = _session_state_after_tool_result(
                session_state,
                tool_name=tool_name,
                tool_result=tool_result,
                raw_text=str(state.get("text", "") or ""),
            )
        payload = dict(state.get("payload") or {}) if isinstance(state.get("payload"), dict) else {}
        payload["session_state"] = session_state.to_dict()
        return {
            **dict(state),
            "payload": payload,
            "session_state": session_state.to_dict(),
        }

    def sync_flow_state(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("tool_decision") if isinstance(state.get("tool_decision"), dict) else {}
        tool_name = str(decision.get("tool_name", "") or "")
        if tool_name not in _FLOW_TOOL_NAMES:
            return dict(state)
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        tool_result_payload = state.get("direct_tool_result") if isinstance(state.get("direct_tool_result"), dict) else {}
        tool_result = _tool_result_from_payload(tool_result_payload)
        if tool_result is not None:
            session_state = _session_state_after_tool_result(
                session_state,
                tool_name=tool_name,
                tool_result=tool_result,
                raw_text=str(state.get("text", "") or ""),
            )
        payload = dict(state.get("payload") or {}) if isinstance(state.get("payload"), dict) else {}
        payload["session_state"] = session_state.to_dict()
        return {
            **dict(state),
            "payload": payload,
            "session_state": session_state.to_dict(),
        }

    def sync_confirm_state(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("tool_decision") if isinstance(state.get("tool_decision"), dict) else {}
        tool_name = str(decision.get("tool_name", "") or "")
        if tool_name not in _CONFIRM_TOOL_NAMES:
            return dict(state)
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        tool_result = state.get("direct_tool_result") if isinstance(state.get("direct_tool_result"), dict) else {}
        data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
        if tool_name in {"create_pending_confirm", "query_pending_confirm"} and tool_result.get("ok"):
            confirm = _pending_confirm_from_tool_data(data)
            if confirm:
                session_state = session_state.with_pending_confirm(confirm)
        elif tool_name in {"cancel_pending_plan", "expire_pending_plan", "confirm_pending_plan"} and tool_result.get("ok"):
            session_state = session_state.with_pending_confirm(None)
        payload = dict(state.get("payload") or {}) if isinstance(state.get("payload"), dict) else {}
        payload["session_state"] = session_state.to_dict()
        return {
            **dict(state),
            "payload": payload,
            "session_state": session_state.to_dict(),
        }

    def run_local_rules(state: dict[str, Any]) -> dict[str, Any]:
        session_payload = state.get("session_state") if isinstance(state.get("session_state"), dict) else {}
        session_state = SessionState.from_dict(session_payload)
        tool_specs = build_local_tool_specs()
        result = local_runner(str(state.get("text", "") or ""), session_state, tool_specs)
        payload = dict(result.payload or {}) if isinstance(result.payload, dict) else result.payload
        if isinstance(payload, dict):
            tool_name = str(payload.get("tool_name", "") or "")
            tool_result_payload = payload.get("tool_result") if isinstance(payload.get("tool_result"), dict) else {}
            tool_result = _tool_result_from_payload(tool_result_payload)
            if tool_name and tool_result is not None:
                session_state = _session_state_after_tool_result(
                    session_state,
                    tool_name=tool_name,
                    tool_result=tool_result,
                    raw_text=str(state.get("text", "") or ""),
                )
                payload["session_state"] = session_state.to_dict()
        return {
            "kind": result.kind,
            "message": result.message,
            "payload": payload,
            "session_state": session_state.to_dict(),
        }

    def route_after_decision(state: dict[str, Any]) -> str:
        decision = state.get("tool_decision") if isinstance(state.get("tool_decision"), dict) else {}
        return "call_tool" if decision.get("tool_name") else "local_rules"

    def route_after_timeout_check(state: dict[str, Any]) -> str:
        if state.get("pending_confirm_expired"):
            return "expire_pending_state"
        if isinstance(state.get("compound_step_result"), dict):
            return "sync_compound_step_result"
        return "decide_tool"

    def route_after_tool_call(state: dict[str, Any]) -> str:
        if not state.get("direct_tool_result_ok"):
            return "local_rules"
        decision = state.get("tool_decision") if isinstance(state.get("tool_decision"), dict) else {}
        tool_name = str(decision.get("tool_name", "") or "")
        if tool_name in _FLOW_TOOL_NAMES:
            return "sync_flow_state"
        if tool_name in _CONFIRM_TOOL_NAMES:
            return "sync_confirm_state"
        if tool_name in _COMPOUND_TOOL_NAMES:
            return "sync_compound_state"
        return "done"

    graph = StateGraph(_RuntimeGraphState)
    graph.add_node("check_pending_timeout", check_pending_timeout)
    graph.add_node("expire_pending_state", expire_pending_state)
    graph.add_node("sync_compound_step_result", sync_compound_step_result)
    graph.add_node("decide_tool", decide_tool)
    graph.add_node("call_tool", call_tool)
    graph.add_node("sync_flow_state", sync_flow_state)
    graph.add_node("sync_confirm_state", sync_confirm_state)
    graph.add_node("sync_compound_state", sync_compound_state)
    graph.add_node("local_rules", run_local_rules)
    graph.set_entry_point("check_pending_timeout")
    graph.add_conditional_edges(
        "check_pending_timeout",
        route_after_timeout_check,
        {
            "expire_pending_state": "expire_pending_state",
            "sync_compound_step_result": "sync_compound_step_result",
            "decide_tool": "decide_tool",
        },
    )
    graph.add_conditional_edges(
        "decide_tool",
        route_after_decision,
        {"call_tool": "call_tool", "local_rules": "local_rules"},
    )
    graph.add_conditional_edges(
        "call_tool",
        route_after_tool_call,
        {
            "done": END,
            "sync_flow_state": "sync_flow_state",
            "sync_confirm_state": "sync_confirm_state",
            "sync_compound_state": "sync_compound_state",
            "local_rules": "local_rules",
        },
    )
    graph.add_edge("sync_flow_state", END)
    graph.add_edge("sync_confirm_state", END)
    graph.add_edge("sync_compound_state", END)
    graph.add_edge("sync_compound_step_result", END)
    graph.add_edge("expire_pending_state", END)
    graph.add_edge("local_rules", END)
    return graph.compile()


def _decide_tool_call(
    tool_decider: Callable[[dict[str, Any]], Any],
    state: dict[str, Any],
    session_state: SessionState,
    tool_specs: tuple[LocalToolSpec, ...],
) -> tuple[str, dict[str, Any], str] | None:
    try:
        decision = tool_decider(
            {
                "text": str(state.get("text", "") or ""),
                "session_state": session_state.to_dict(),
                "tool_specs": [spec.__dict__ for spec in tool_specs],
            }
        )
    except Exception:
        return None
    if not isinstance(decision, dict):
        return None
    tool_name = str(decision.get("tool_name", "") or decision.get("name", "") or "")
    if not tool_name:
        return None
    args = decision.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    tool_call_id = str(decision.get("tool_call_id", "") or decision.get("idempotency_key", "") or "")
    return tool_name, dict(args), tool_call_id


def _registry_now(registry: LocalToolRegistry) -> float:
    clock = getattr(registry, "clock", None)
    if callable(clock):
        try:
            return float(clock())
        except Exception:
            return 0.0
    return 0.0


def _pending_confirm_expired(session_state: SessionState, *, now: float) -> bool:
    pending = dict(session_state.pending_confirm or {})
    if not pending:
        return False
    try:
        expires_at = float(pending.get("expires_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if expires_at <= 0:
        return False
    return float(now) > expires_at


def _session_state_after_tool_result(
    session_state: SessionState,
    *,
    tool_name: str,
    tool_result: Any,
    raw_text: str,
) -> SessionState:
    updated = session_state.with_tool_result(
        tool_name=tool_name,
        tool_result=tool_result,
        user_text=raw_text,
        normalized_text=raw_text,
    )
    data = tool_result.data if isinstance(getattr(tool_result, "data", None), dict) else {}
    if tool_name in {
        "start_flow_draft",
        "set_flow_name",
        "append_flow_step",
        "answer_flow_clarification",
        "set_flow_draft",
        "query_current_flow_draft",
    }:
        draft = data.get("draft")
        if isinstance(draft, dict):
            updated = updated.with_flow_draft(draft)
    if tool_name == "cancel_flow_draft" and tool_result.ok:
        updated = updated.with_flow_draft(None)
    if tool_name in _COMPOUND_TOOL_NAMES:
        if tool_result.ok:
            updated = updated.with_compound_plan(_compound_plan_from_tool_data(data))
        elif tool_name == "plan_compound_command":
            updated = updated.with_compound_plan(None)
    if tool_name in {"create_pending_confirm", "query_pending_confirm"}:
        confirm = _pending_confirm_from_tool_data(data)
        if confirm:
            updated = updated.with_pending_confirm(confirm)
    if tool_name in {"cancel_pending_plan", "expire_pending_plan", "confirm_pending_plan"}:
        updated = updated.with_pending_confirm(None)
    return updated


def _compound_plan_from_tool_data(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if str(data.get("kind", "") or "") == "compound_plan_draft":
        steps = list(data.get("steps", []) or [])
        step_results = [dict(item) if isinstance(item, dict) else item for item in list(data.get("step_results", []) or [])]
        plan = {
            "kind": "compound_plan_draft",
            "plan_id": str(data.get("plan_id", "") or ""),
            "raw_text": str(data.get("raw_text", "") or ""),
            "created_at": float(data.get("created_at", 0.0) or 0.0),
            "steps": steps,
            "step_results": step_results,
            "generates_command": False,
        }
        if steps:
            plan["active_step_index"] = 0
            plan["active_step"] = steps[0]
            if step_results:
                plan["active_step_result"] = step_results[0]
            plan["status"] = "waiting_step_confirm"
        return plan
    if str(data.get("kind", "") or "") == "compound_sequence":
        return {
            "kind": "compound_sequence",
            "steps": list(data.get("steps", []) or []),
            "generates_command": False,
        }
    return {}


def _tool_result_from_payload(payload: dict[str, Any]) -> ToolResult | None:
    if not isinstance(payload, dict):
        return None
    return ToolResult(
        ok=bool(payload.get("ok", False)),
        state=str(payload.get("state", "") or ""),
        message=str(payload.get("message", "") or ""),
        data=dict(payload.get("data", {}) or {}) if isinstance(payload.get("data"), dict) else {},
        errors=[dict(error) for error in payload.get("errors", []) if isinstance(error, dict)]
        if isinstance(payload.get("errors"), list)
        else [],
    )


def _pending_confirm_from_tool_data(data: dict[str, Any]) -> dict[str, Any]:
    existing = data.get("pending_confirm") or data.get("plan")
    if isinstance(existing, dict):
        return dict(existing)
    draft_id = str(data.get("draft_id", "") or "")
    status = str(data.get("status", "") or "")
    if not draft_id or not status:
        return {}
    confirm = {
        "draft_id": draft_id,
        "status": status,
    }
    if "expires_at" in data:
        confirm["expires_at"] = data.get("expires_at")
    if "confirmation_text" in data:
        confirm["confirmation_text"] = str(data.get("confirmation_text", "") or "")
    return confirm


def _result_from_direct_tool_call(tool_name: str, tool_result: Any, *, raw_text: str) -> AgentOrchestratorResult:
    payload = {
        "kind": _kind_for_tool_name(tool_name),
        "text": tool_result.message,
        "raw_text": raw_text,
        "generates_command": False,
        "tool_name": tool_name,
        "tool_result": tool_result.to_dict(),
    }
    return AgentOrchestratorResult(
        kind=str(payload["kind"]),
        message=str(tool_result.message or ""),
        payload=payload,
    )


def _kind_for_tool_name(tool_name: str) -> str:
    if tool_name == "explain_text":
        return "chat_answer"
    if tool_name in {"query_dashboard_section", "get_axis_status", "get_alarm", "get_execution_progress"}:
        return "dashboard_query_action"
    if tool_name in {
        "start_flow_draft",
        "set_flow_name",
        "append_flow_step",
        "answer_flow_clarification",
        "save_flow_draft",
        "set_flow_draft",
        "query_current_flow_draft",
        "query_registered_flow",
        "cancel_flow_draft",
    }:
        return "flow_draft"
    if tool_name == "prepare_registered_flow_execution":
        return "registered_flow_plan"
    if tool_name in {"split_compound_command", "plan_compound_command"}:
        return "compound_plan_draft"
    if tool_name in {
        "create_memory_candidate",
        "query_memory_candidates",
        "query_memory_review",
        "approve_memory_candidate",
        "disable_memory",
        "rollback_memory",
        "lookup_active_memory",
        "record_memory_applied",
        "save_position_alias",
        "delete_position_alias",
    }:
        return "memory_tool_result"
    if tool_name in {"confirm_pending_plan", "cancel_pending_plan", "expire_pending_plan", "query_pending_confirm"}:
        return "confirm_result"
    if tool_name in {
        "parse_command_intent",
        "parse_command_params",
        "lookup_command_schema",
        "validate_required_params",
        "check_param_bounds",
        "resolve_command_address",
        "build_system_action_draft",
        "build_command_draft",
        "apply_atomic_template",
        "draft_to_query_record",
        "run_safety_precheck",
        "create_pending_confirm",
    }:
        return "command_tool_result"
    return "tool_result"
