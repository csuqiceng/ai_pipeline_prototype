from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent_tools.memory_tools import record_feedback_vote
from robot_modbus_lite.agent_tools.tool_result import ToolResult

from .feedback_learner import learn_memory_candidates_from_feedback
from .local_tool_registry import LocalToolRegistry
from .memory_normalizer import apply_active_memory_to_text
from .memory_seed import import_json_seed_memories
from .memory_store import AgentMemoryStore, default_agent_memory_path
from .runtime_factory import LangChainRunnerFactory, create_tool_calling_runtime
from .session_state import SessionState
from .tool_calling_agent import ToolCallingAgentRuntime


LogFunc = Callable[[str, str, str, str], None]
LegacyFallback = Callable[[str], AgentOrchestratorResult]


class OperatorAgentRuntimeBridge:
    def __init__(
        self,
        *,
        runtime_root: str | Path | None = None,
        log_func: LogFunc | None = None,
        memory_store: AgentMemoryStore | None = None,
        atomic_memory_provider: Callable[[], Any] | None = None,
        restricted_service_provider: Callable[[], Any] | None = None,
        flow_service_provider: Callable[[], Any] | None = None,
        execution_plan_service_provider: Callable[[], Any] | None = None,
        controller_snapshot_provider: Callable[[], Any] | None = None,
        position_registry_provider: Callable[[], Any] | None = None,
        safety_review_agent_provider: Callable[[], Any] | None = None,
        runtime_snapshot_provider: Callable[[], dict[str, Any]] | None = None,
        start_pose_provider: Callable[[], tuple[float, float, float, float, float, float] | None] | None = None,
        confirmation_agent_provider: Callable[[], Any] | None = None,
        clock: Callable[[], float] | None = None,
        status_signature_provider: Callable[[], str] | None = None,
        safety_signature_provider: Callable[[], str] | None = None,
        flow_draft_parse_func: Callable[[str], Any] | None = None,
        control_tools_enabled_provider: Callable[[], bool] | None = None,
        langchain_runner_factory: LangChainRunnerFactory | None = None,
        langchain_graph_app: object | None = None,
        langchain_available: bool | None = None,
        tool_decider: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root) if runtime_root is not None else Path.cwd()
        self.log_func = log_func
        self.atomic_memory_provider = atomic_memory_provider
        self.restricted_service_provider = restricted_service_provider
        self.flow_service_provider = flow_service_provider
        self.execution_plan_service_provider = execution_plan_service_provider
        self.controller_snapshot_provider = controller_snapshot_provider
        self.position_registry_provider = position_registry_provider
        self.safety_review_agent_provider = safety_review_agent_provider
        self.runtime_snapshot_provider = runtime_snapshot_provider
        self.start_pose_provider = start_pose_provider
        self.confirmation_agent_provider = confirmation_agent_provider
        self.clock = clock
        self.status_signature_provider = status_signature_provider
        self.safety_signature_provider = safety_signature_provider
        self.flow_draft_parse_func = flow_draft_parse_func
        self.control_tools_enabled_provider = control_tools_enabled_provider
        self.langchain_runner_factory = langchain_runner_factory
        self.langchain_graph_app = langchain_graph_app
        self.langchain_available = langchain_available
        self.tool_decider = tool_decider
        self._session_states: dict[str, SessionState] = {}
        self._memory_store: AgentMemoryStore | None = memory_store
        self._tool_calling_runtime: ToolCallingAgentRuntime | None = None

    def session_state(self, thread_id: str) -> SessionState:
        clean_thread_id = str(thread_id or "operator-ui")
        state = self._session_states.get(clean_thread_id)
        if state is None:
            state = SessionState(thread_id=clean_thread_id)
            self._session_states[clean_thread_id] = state
        return state

    def set_session_state(self, state: SessionState) -> SessionState:
        self._session_states[state.thread_id] = state
        return state

    def memory_store(self) -> AgentMemoryStore:
        if self._memory_store is None:
            self._memory_store = AgentMemoryStore(default_agent_memory_path(self.runtime_root))
            import_json_seed_memories(self._memory_store, self.runtime_root / "data")
        return self._memory_store

    def tool_calling_runtime(self) -> ToolCallingAgentRuntime:
        if self._tool_calling_runtime is None:
            registry = LocalToolRegistry(
                memory_store=self.memory_store(),
                atomic_memory_provider=self.atomic_memory_provider,
                restricted_service=self._restricted_service(),
                flow_service=self._flow_service(),
                execution_plan_service=self._execution_plan_service(),
                controller_snapshot_provider=self.controller_snapshot_provider,
                position_registry_provider=self.position_registry_provider,
                safety_review_agent=self._safety_review_agent(),
                runtime_snapshot_provider=self.runtime_snapshot_provider,
                start_pose_provider=self.start_pose_provider,
                confirmation_agent=self._confirmation_agent(),
                clock=self.clock,
                status_signature_provider=self.status_signature_provider,
                safety_signature_provider=self.safety_signature_provider,
                flow_draft_parse_func=self.flow_draft_parse_func,
                control_tools_enabled=self._control_tools_enabled(),
            )
            self._tool_calling_runtime = create_tool_calling_runtime(
                registry,
                langchain_available=self.langchain_available,
                langchain_runner_factory=self.langchain_runner_factory,
                langchain_graph_app=self.langchain_graph_app,
                tool_decider=self.tool_decider,
            )
        return self._tool_calling_runtime

    def _restricted_service(self) -> Any:
        if self.restricted_service_provider is None:
            return None
        try:
            return self.restricted_service_provider()
        except Exception as exc:
            self._log("Agent", "获取受限服务", "失败", str(exc))
            return None

    def _control_tools_enabled(self) -> bool:
        if self.control_tools_enabled_provider is None:
            return True
        try:
            return bool(self.control_tools_enabled_provider())
        except Exception:
            return False

    def _flow_service(self) -> Any:
        if self.flow_service_provider is None:
            return None
        try:
            return self.flow_service_provider()
        except Exception as exc:
            self._log("Agent", "获取流程保存服务", "失败", str(exc))
            return None

    def _execution_plan_service(self) -> Any:
        if self.execution_plan_service_provider is None:
            return None
        try:
            return self.execution_plan_service_provider()
        except Exception as exc:
            self._log("Agent", "获取流程草案服务", "失败", str(exc))
            return None

    def _safety_review_agent(self) -> Any:
        if self.safety_review_agent_provider is None:
            return None
        try:
            return self.safety_review_agent_provider()
        except Exception as exc:
            self._log("Agent", "获取安全预检服务", "失败", str(exc))
            return None

    def _confirmation_agent(self) -> Any:
        if self.confirmation_agent_provider is None:
            return None
        try:
            return self.confirmation_agent_provider()
        except Exception as exc:
            self._log("Agent", "获取确认状态机", "失败", str(exc))
            return None

    def apply_active_memory_to_text(self, text: str, *, thread_id: str) -> str:
        try:
            result = apply_active_memory_to_text(self.memory_store(), text)
        except Exception as exc:
            self._log("Agent", "经验记忆归一化", "失败", str(exc))
            return str(text or "")
        state = self.session_state(thread_id).with_memory_application(
            raw_text=str(text or ""),
            normalized_text=result.text,
            applied_memories=result.applied,
        )
        self.set_session_state(state)
        return result.text

    def handle_text(
        self,
        text: str,
        *,
        thread_id: str,
        legacy_fallback: LegacyFallback,
    ) -> AgentOrchestratorResult:
        normalized_text = self.apply_active_memory_to_text(text, thread_id=thread_id)
        result = self.tool_calling_runtime().handle(
            normalized_text,
            session_state=self.session_state(thread_id),
        )
        if self.result_requires_legacy_fallback(result):
            fallback_result = legacy_fallback(normalized_text)
            self._record_last_agent_result(
                fallback_result,
                thread_id=thread_id,
            )
            return fallback_result
        result = self._result_with_text_context(
            result,
            thread_id=thread_id,
            raw_text=text,
            normalized_text=normalized_text,
        )
        self._record_langgraph_session_state(result, thread_id=thread_id)
        self._record_runtime_tool_result(
            result,
            thread_id=thread_id,
            raw_text=text,
            normalized_text=normalized_text,
        )
        self._record_restricted_agent_result(result, thread_id=thread_id)
        self._record_last_agent_result(result, thread_id=thread_id)
        return result

    @staticmethod
    def result_requires_legacy_fallback(result: Any) -> bool:
        if getattr(result, "kind", "") != "tool_calling_unavailable":
            return False
        payload = getattr(result, "payload", {}) or {}
        return not isinstance(payload, dict) or bool(payload.get("fallback_required", True))

    def set_pending_confirm(self, plan: Any, *, thread_id: str, expires_at: float) -> dict[str, Any]:
        payload = self.pending_confirm_payload(plan, expires_at=expires_at)
        self.set_session_state(self.session_state(thread_id).with_pending_confirm(payload))
        return payload

    def clear_pending_confirm(self, *, thread_id: str) -> SessionState:
        state = self.session_state(thread_id).with_pending_confirm(None)
        return self.set_session_state(state)

    def expire_pending_confirm(self, *, thread_id: str) -> SessionState:
        current = self.session_state(thread_id)
        draft_id = str(current.pending_confirm.get("draft_id", "") or current.pending_confirm.get("plan_id", "") or "")
        if draft_id:
            self.tool_calling_runtime().tool_registry.call(
                "expire_pending_plan",
                draft_id=draft_id,
            )
        state = current.expire_pending_confirm()
        return self.set_session_state(state)

    def confirm_pending_plan(self, draft_id: str, *, thread_id: str) -> ToolResult:
        result = self.tool_calling_runtime().tool_registry.call(
            "confirm_pending_plan",
            draft_id=str(draft_id or ""),
        )
        if result.ok:
            state = self.session_state(thread_id).mark_pending_execution_confirmed()
            self.set_session_state(state)
        return result

    def cancel_pending_plan(self, draft_id: str, *, thread_id: str) -> ToolResult:
        result = self.tool_calling_runtime().tool_registry.call(
            "cancel_pending_plan",
            draft_id=str(draft_id or ""),
        )
        if result.ok:
            state = self.session_state(thread_id).cancel_pending_plan()
            self.set_session_state(state)
        return result

    def record_execution_failure(
        self,
        *,
        thread_id: str,
        query_record: dict[str, Any] | None,
        error: str,
    ) -> ToolResult:
        state = self.session_state(thread_id).record_execution_failure(
            query_record=query_record,
            error=str(error or ""),
        )
        self.set_session_state(state)
        return _tool_result_from_dict(state.last_tool_call["result"])

    def record_compound_step_result(self, *, thread_id: str, ok: bool, reason: str = "") -> AgentOrchestratorResult:
        runtime = self.tool_calling_runtime()
        state = self.session_state(thread_id)
        runner = getattr(runtime, "runner", None)
        graph_app = getattr(runner, "graph_app", None)
        event = {"ok": bool(ok), "reason": str(reason or "")}
        graph_error = ""
        if graph_app is not None:
            try:
                response = graph_app.invoke(
                    {
                        "text": "compound_step_result",
                        "compound_step_result": event,
                        "session_state": state.to_dict(),
                        "tool_specs": [spec.__dict__ for spec in runtime.tool_specs],
                    }
                )
                result = _agent_result_from_graph_response(response)
                self._record_langgraph_session_state(result, thread_id=thread_id)
                return result
            except Exception as exc:
                graph_error = str(exc)
                self._log("Agent", "复合指令步骤同步", "失败", graph_error)
        updated = state.advance_compound_step(ok=bool(ok), reason=str(reason or ""))
        self.set_session_state(updated)
        payload = {
            "kind": "compound_step_result",
            "text": str(reason or ""),
            "compound_step_result": event,
            "session_state": updated.to_dict(),
            "generates_command": False,
        }
        if graph_error:
            payload["graph_error"] = graph_error
        return AgentOrchestratorResult(
            kind="compound_step_result",
            message=str(reason or ("复合指令步骤已完成。" if ok else "复合指令步骤失败。")),
            payload=payload,
        )

    def set_flow_draft(self, draft: dict[str, Any], *, thread_id: str) -> dict[str, Any]:
        stored = dict(draft)
        self.set_session_state(self.session_state(thread_id).with_flow_draft(stored))
        return stored

    def clear_flow_draft(self, *, thread_id: str) -> SessionState:
        state = self.session_state(thread_id).with_flow_draft(None)
        return self.set_session_state(state)

    @staticmethod
    def pending_confirm_payload(plan: Any, *, expires_at: float) -> dict[str, Any]:
        plan_id = (
            getattr(plan, "plan_id", None)
            or getattr(plan, "draft_id", None)
            or getattr(plan, "raw_text", None)
            or id(plan)
        )
        return {
            "plan_id": str(plan_id),
            "source": str(getattr(plan, "source", "") or ""),
            "reason": str(getattr(plan, "reason", "") or ""),
            "expires_at": float(expires_at or 0.0),
        }

    def record_feedback_vote(
        self,
        *,
        interaction_id: str,
        target_type: str,
        target_id: str,
        vote: str,
        note: str = "",
    ) -> ToolResult:
        if not str(interaction_id or "") or not str(target_id or ""):
            return ToolResult.failure(
                state="feedback_vote_missing_target",
                message="当前没有可关联的交互记录，未记录反馈。",
                code="FEEDBACK_TARGET_MISSING",
            )
        return record_feedback_vote(
            self.memory_store(),
            interaction_id=interaction_id,
            target_type=target_type,
            target_id=target_id,
            vote=vote,
            note=note,
        )

    def learn_memory_candidates_from_feedback(self) -> ToolResult:
        result = learn_memory_candidates_from_feedback(self.memory_store())
        return ToolResult.success(
            state="memory_candidates_learned",
            message=f"已从反馈中生成 {result.created_count} 条候选经验。",
            data={
                "created_count": result.created_count,
                "skipped_count": result.skipped_count,
                "created": [dict(item) for item in result.created],
            },
        )

    def _record_runtime_tool_result(
        self,
        result: AgentOrchestratorResult,
        *,
        thread_id: str,
        raw_text: str,
        normalized_text: str,
    ) -> None:
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            return
        tool_result_payload = payload.get("tool_result")
        if not isinstance(tool_result_payload, dict):
            return
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
        if not tool_name:
            return
        tool_result = _tool_result_from_dict(tool_result_payload)
        state = self.session_state(thread_id).with_tool_result(
            tool_name=tool_name,
            tool_result=tool_result,
            user_text=str(raw_text or ""),
            normalized_text=str(normalized_text or ""),
        )
        if tool_result.state == "compound_plan_draft":
            state = state.with_compound_plan(tool_result.data)
        if tool_result.state in {
            "flow_draft_updated",
            "flow_draft_loaded",
            "flow_draft_needs_clarification",
            "flow_draft_needs_name",
            "flow_draft_plan",
        }:
            state = state.with_flow_draft(tool_result.data.get("draft") if isinstance(tool_result.data, dict) else {})
            if tool_result.state in {"flow_draft_needs_clarification", "flow_draft_needs_name"}:
                state = state.with_tool_result(
                    tool_name=tool_name,
                    tool_result=tool_result,
                    user_text=str(raw_text or ""),
                    normalized_text=str(normalized_text or ""),
                )
        if tool_result.state == "flow_draft_cancelled":
            state = state.with_flow_draft(None)
        if tool_result.state == "flow_draft_saved":
            state = state.with_flow_draft(None)
        if tool_result.state == "waiting_confirmation":
            state = state.with_pending_confirm(_pending_confirm_from_tool_result(tool_result))
            draft_payload = payload.get("draft")
            if isinstance(draft_payload, dict):
                state = state.with_pending_execution(_pending_execution_from_draft_payload(draft_payload))
        if tool_result.state in {"confirmed", "cancelled"}:
            state = state.with_pending_confirm(None).with_pending_execution(None)
        self.set_session_state(state)

    def _record_restricted_agent_result(self, result: AgentOrchestratorResult, *, thread_id: str) -> None:
        if getattr(result, "kind", "") != "restricted_agent":
            return
        payload = getattr(result, "payload", None)
        if str(getattr(payload, "kind", "") or "") != "waiting_confirmation":
            return
        draft = getattr(payload, "draft", None)
        if draft is None:
            return
        pending_execution = {
            "agent_kind": str(getattr(payload, "kind", "") or ""),
            "draft_id": str(getattr(draft, "draft_id", "") or ""),
            "intent": str(getattr(draft, "intent", "") or ""),
            "func_id": getattr(draft, "func_id", None),
            "params": dict(getattr(draft, "params", {}) or {}),
            "param_sources": dict(getattr(draft, "param_sources", {}) or {}),
            "raw_text": str(getattr(draft, "raw_text", "") or ""),
            "confidence": float(getattr(draft, "confidence", 0.0) or 0.0),
            "precheck_result": dict(getattr(payload, "precheck_result", {}) or {}),
        }
        state = self.session_state(thread_id).with_pending_execution(pending_execution)
        self.set_session_state(state)

    def _result_with_text_context(
        self,
        result: AgentOrchestratorResult,
        *,
        thread_id: str,
        raw_text: str,
        normalized_text: str,
    ) -> AgentOrchestratorResult:
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            return result
        enriched = dict(payload)
        enriched["raw_text"] = str(raw_text or "")
        enriched["normalized_text"] = str(normalized_text or raw_text or "")
        enriched["applied_memories"] = [dict(item) for item in self.session_state(thread_id).applied_memories]
        return AgentOrchestratorResult(
            kind=str(getattr(result, "kind", "") or ""),
            message=str(getattr(result, "message", "") or ""),
            payload=enriched,
        )

    def _record_langgraph_session_state(self, result: AgentOrchestratorResult, *, thread_id: str) -> None:
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            return
        state_payload = payload.get("session_state")
        if not isinstance(state_payload, dict):
            return
        state = SessionState.from_dict(state_payload)
        if state.thread_id != str(thread_id or "operator-ui"):
            state = SessionState.from_dict({**state.to_dict(), "thread_id": str(thread_id or "operator-ui")})
        self.set_session_state(state)

    def _record_last_agent_result(self, result: Any, *, thread_id: str) -> None:
        if result is None:
            return
        payload = getattr(result, "payload", {}) or {}
        payload = dict(payload or {}) if isinstance(payload, dict) else {}
        target_id = _agent_result_target_id(result, payload)
        state = self.session_state(thread_id).with_agent_result(
            result_kind=str(getattr(result, "kind", "") or ""),
            target_id=target_id,
        )
        self.set_session_state(state)

    def _log(self, category: str, action: str, status: str, detail: str) -> None:
        if self.log_func is not None:
            self.log_func(category, action, status, detail)


def _tool_result_from_dict(value: dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=bool(value.get("ok", False)),
        state=str(value.get("state", "") or ""),
        message=str(value.get("message", "") or ""),
        data=dict(value.get("data", {}) or {}),
        errors=[dict(error) for error in value.get("errors", []) or []],
    )


def _agent_result_from_graph_response(response: Any) -> AgentOrchestratorResult:
    if isinstance(response, AgentOrchestratorResult):
        return response
    if isinstance(response, dict):
        return AgentOrchestratorResult(
            kind=str(response.get("kind", "") or "compound_step_result"),
            message=str(response.get("message", "") or ""),
            payload=response.get("payload") if isinstance(response.get("payload"), dict) else {},
        )
    return AgentOrchestratorResult(
        kind="compound_step_result",
        message="复合指令步骤状态已同步。",
        payload={},
    )


def _pending_confirm_from_tool_result(tool_result: ToolResult) -> dict[str, Any]:
    data = dict(tool_result.data or {})
    return {
        "draft_id": str(data.get("draft_id", "") or ""),
        "status": str(data.get("status", "") or tool_result.state),
        "expires_at": float(data.get("expires_at", 0.0) or 0.0),
        "confirmation_text": str(data.get("confirmation_text", "") or ""),
    }


def _pending_execution_from_draft_payload(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_kind": "waiting_confirmation",
        "draft_id": str(draft.get("draft_id", "") or ""),
        "intent": str(draft.get("intent", "") or ""),
        "func_id": draft.get("func_id"),
        "params": dict(draft.get("params", {}) or {}),
        "param_sources": dict(draft.get("param_sources", {}) or {}),
        "raw_text": str(draft.get("raw_text", "") or ""),
        "confidence": float(draft.get("confidence", 0.0) or 0.0),
        "precheck_result": dict(draft.get("precheck_result", {}) or {}),
    }


def _agent_result_target_id(result: Any, payload: dict[str, Any]) -> str:
    for key in ("target_id", "draft_id", "plan_id"):
        value = payload.get(key)
        if value:
            return str(value)
    tool_result = payload.get("tool_result")
    if isinstance(tool_result, dict):
        data = tool_result.get("data")
        if isinstance(data, dict):
            for key in ("draft_id", "memory_id", "flow_name"):
                value = data.get(key)
                if value:
                    return str(value)
    return ""
