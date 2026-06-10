from __future__ import annotations

from collections.abc import Callable
from typing import Any

from robot_modbus_lite.agent_tools import chat_tools, command_tools, compound_tools, flow_tools, memory_tools, safety_tools, status_tools
from robot_modbus_lite.agent_tools.tool_result import ToolResult
from robot_modbus_lite.agent.atomic_template import AtomicTemplateAgent
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.execution_plan_service import ExecutionPlanService

from .memory_store import AgentMemoryStore
from .session_state import SessionState
from .tool_schemas import validate_tool_args, validate_tool_result


class LocalToolRegistry:
    def __init__(
        self,
        *,
        memory_store: AgentMemoryStore | None = None,
        atomic_memory_provider: Callable[[], Any] | None = None,
        restricted_service: Any = None,
        flow_service: Any = None,
        execution_plan_service: ExecutionPlanService | None = None,
        controller_snapshot_provider: Callable[[], Any] | None = None,
        position_registry_provider: Callable[[], Any] | None = None,
        safety_review_agent: Any = None,
        runtime_snapshot_provider: Callable[[], dict[str, Any]] | None = None,
        start_pose_provider: Callable[[], tuple[float, float, float, float, float, float] | None] | None = None,
        confirmation_agent: Any = None,
        clock: Callable[[], float] | None = None,
        status_signature_provider: Callable[[], str] | None = None,
        safety_signature_provider: Callable[[], str] | None = None,
        flow_draft_parse_func: Callable[[str], Any] | None = None,
        control_tools_enabled: bool = True,
    ) -> None:
        self.memory_store = memory_store
        self.atomic_memory_provider = atomic_memory_provider
        self.restricted_service = restricted_service
        self.flow_service = flow_service
        self.execution_plan_service = execution_plan_service
        self.controller_snapshot_provider = controller_snapshot_provider
        self.position_registry_provider = position_registry_provider
        self.safety_review_agent = safety_review_agent
        self.runtime_snapshot_provider = runtime_snapshot_provider
        self.start_pose_provider = start_pose_provider
        self.confirmation_agent = confirmation_agent
        self.clock = clock
        self.status_signature_provider = status_signature_provider
        self.safety_signature_provider = safety_signature_provider
        self.flow_draft_parse_func = flow_draft_parse_func
        self.control_tools_enabled = bool(control_tools_enabled)
        self._tools: dict[str, Callable[..., ToolResult]] = {
            "lookup_command_schema": self._lookup_command_schema,
            "parse_command_intent": self._parse_command_intent,
            "parse_command_params": self._parse_command_params,
            "validate_required_params": self._validate_required_params,
            "check_param_bounds": self._check_param_bounds,
            "resolve_command_address": self._resolve_command_address,
            "build_system_action_draft": self._build_system_action_draft,
            "build_command_draft": self._build_command_draft,
            "apply_atomic_template": self._apply_atomic_template,
            "draft_to_query_record": self._draft_to_query_record,
            "run_safety_precheck": self._run_safety_precheck,
            "create_pending_confirm": self._create_pending_confirm,
            "query_pending_confirm": self._query_pending_confirm,
            "confirm_pending_plan": self._confirm_pending_plan,
            "cancel_pending_plan": self._cancel_pending_plan,
            "expire_pending_plan": self._expire_pending_plan,
            "split_compound_command": self._split_compound_command,
            "plan_compound_command": self._plan_compound_command,
            "start_flow_draft": self._start_flow_draft,
            "set_flow_name": self._set_flow_name,
            "append_flow_step": self._append_flow_step,
            "answer_flow_clarification": self._answer_flow_clarification,
            "edit_flow_draft_params": self._edit_flow_draft_params,
            "save_flow_draft": self._save_flow_draft,
            "query_registered_flow": self._query_registered_flow,
            "prepare_registered_flow_execution": self._prepare_registered_flow_execution,
            "set_flow_draft": self._set_flow_draft,
            "query_current_flow_draft": self._query_current_flow_draft,
            "cancel_flow_draft": self._cancel_flow_draft,
            "query_command_catalog": self._query_command_catalog,
            "explain_text": self._explain_text,
            "query_dashboard_section": self._query_dashboard_section,
            "get_axis_status": self._get_axis_status,
            "get_alarm": self._get_alarm,
            "get_execution_progress": self._get_execution_progress,
            "query_saved_position": self._query_saved_position,
            "create_memory_candidate": self._create_memory_candidate,
            "query_memory_candidates": self._query_memory_candidates,
            "query_memory_review": self._query_memory_review,
            "approve_memory_candidate": self._approve_memory_candidate,
            "disable_memory": self._disable_memory,
            "rollback_memory": self._rollback_memory,
            "lookup_active_memory": self._lookup_active_memory,
            "record_feedback_vote": self._record_feedback_vote,
            "record_memory_applied": self._record_memory_applied,
            "save_position_alias": self._save_position_alias,
            "delete_position_alias": self._delete_position_alias,
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def call(self, tool_name: str, **kwargs: Any) -> ToolResult:
        name = str(tool_name)
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                state="tool_not_found",
                message=f"未找到工具：{tool_name}",
                code="TOOL_NOT_FOUND",
                data={"tool_name": str(tool_name)},
            )
        validation = validate_tool_args(name, kwargs)
        if not validation.ok:
            return validation
        validated_kwargs = validation.data.get("args", {})
        try:
            result = tool(**validated_kwargs)
        except Exception as exc:
            return ToolResult.failure(
                state="tool_call_failed",
                message=str(exc),
                code="TOOL_CALL_FAILED",
                data={"tool_name": str(tool_name)},
            )
        return validate_tool_result(name, result)

    def call_idempotent(
        self,
        tool_name: str,
        *,
        session_state: SessionState,
        tool_call_id: str,
        **kwargs: Any,
    ) -> tuple[ToolResult, SessionState]:
        existing = session_state.get_idempotent_tool_result(tool_call_id)
        if existing is not None:
            return _tool_result_from_dict(existing), session_state.with_idempotent_tool_replay(tool_call_id)
        result = self.call(tool_name, **kwargs)
        return result, session_state.with_idempotent_tool_call(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_result=result,
        )

    @staticmethod
    def _lookup_command_schema(**kwargs: Any) -> ToolResult:
        return command_tools.lookup_command_schema(kwargs.get("command_name", kwargs.get("func_id", "")))

    @staticmethod
    def _parse_command_intent(**kwargs: Any) -> ToolResult:
        return command_tools.parse_command_intent(str(kwargs.get("text", "") or ""))

    @staticmethod
    def _parse_command_params(**kwargs: Any) -> ToolResult:
        return command_tools.parse_command_params(str(kwargs.get("text", "") or ""))

    @staticmethod
    def _validate_required_params(**kwargs: Any) -> ToolResult:
        return command_tools.validate_required_params(
            int(kwargs.get("func_id", 0) or 0),
            dict(kwargs.get("params", {}) or {}),
        )

    @staticmethod
    def _check_param_bounds(**kwargs: Any) -> ToolResult:
        return command_tools.check_param_bounds(
            dict(kwargs.get("params", {}) or {}),
            bounds=dict(kwargs.get("bounds", {}) or {}),
        )

    @staticmethod
    def _resolve_command_address(**kwargs: Any) -> ToolResult:
        return command_tools.resolve_command_address(str(kwargs.get("name", "") or kwargs.get("address_name", "") or ""))

    @staticmethod
    def _build_system_action_draft(**kwargs: Any) -> ToolResult:
        return command_tools.build_system_action_draft(str(kwargs.get("text", "") or ""))

    def _build_command_draft(self, **kwargs: Any) -> ToolResult:
        return command_tools.build_command_draft(
            str(kwargs.get("text", "") or ""),
            snapshot_provider=self.controller_snapshot_provider,
        )

    def _apply_atomic_template(self, **kwargs: Any) -> ToolResult:
        memory = self._require_atomic_memory()
        template_lookup = None
        table = getattr(self.flow_service, "table", None)
        if table is not None:
            template_lookup = AtomicTemplateAgent.query_table_position_template_lookup(table)
        if not memory.ok and template_lookup is None:
            return memory
        atomic_memory = memory.data["memory"] if memory.ok else AtomicMemory()
        return command_tools.apply_atomic_template(
            str(kwargs.get("text", "") or ""),
            memory=atomic_memory,
            template_lookup=template_lookup,
        )

    @staticmethod
    def _draft_to_query_record(**kwargs: Any) -> ToolResult:
        return command_tools.draft_to_query_record(kwargs.get("draft", {}) or {})

    def _run_safety_precheck(self, **kwargs: Any) -> ToolResult:
        agent = self._require_safety_review_agent()
        if not agent.ok:
            return agent
        snapshot = kwargs.get("snapshot")
        if snapshot is None and self.runtime_snapshot_provider is not None:
            snapshot = self.runtime_snapshot_provider()
        start_pose = kwargs.get("start_pose")
        if start_pose is None and self.start_pose_provider is not None:
            start_pose = self.start_pose_provider()
        return safety_tools.run_safety_precheck(
            agent.data["agent"],
            kwargs.get("draft", {}) or {},
            snapshot=dict(snapshot or {}),
            start_pose=start_pose,
        )

    def _create_pending_confirm(self, **kwargs: Any) -> ToolResult:
        agent = self._require_confirmation_agent()
        if not agent.ok:
            return agent
        return safety_tools.create_pending_confirm(
            agent.data["agent"],
            _command_draft_from_value(kwargs.get("draft", {}) or {}),
            now=self._now(),
            status_signature=self._status_signature(),
            safety_signature=self._safety_signature(),
        )

    def _query_pending_confirm(self, **kwargs: Any) -> ToolResult:
        agent = self._require_confirmation_agent()
        if not agent.ok:
            return agent
        return safety_tools.query_pending_confirm(
            agent.data["agent"],
            str(kwargs.get("draft_id", "") or ""),
        )

    def _confirm_pending_plan(self, **kwargs: Any) -> ToolResult:
        agent = self._require_confirmation_agent()
        if not agent.ok:
            return agent
        return safety_tools.confirm_pending_plan(
            agent.data["agent"],
            str(kwargs.get("draft_id", "") or ""),
            now=self._now(),
            status_signature=self._status_signature(),
            safety_signature=self._safety_signature(),
        )

    def _cancel_pending_plan(self, **kwargs: Any) -> ToolResult:
        agent = self._require_confirmation_agent()
        if not agent.ok:
            return agent
        return safety_tools.cancel_pending_plan(
            agent.data["agent"],
            str(kwargs.get("draft_id", "") or ""),
        )

    def _expire_pending_plan(self, **kwargs: Any) -> ToolResult:
        agent = self._require_confirmation_agent()
        if not agent.ok:
            return agent
        return safety_tools.expire_pending_plan(
            agent.data["agent"],
            str(kwargs.get("draft_id", "") or ""),
        )

    @staticmethod
    def _split_compound_command(**kwargs: Any) -> ToolResult:
        return compound_tools.split_compound_command(str(kwargs.get("text", "") or ""))

    def _plan_compound_command(self, **kwargs: Any) -> ToolResult:
        return compound_tools.plan_compound_command(
            str(kwargs.get("text", "") or ""),
            restricted_service=self.restricted_service,
        )

    def _start_flow_draft(self, **kwargs: Any) -> ToolResult:
        parsed = flow_tools.parse_existing_flow_draft(
            self.flow_draft_parse_func,
            str(kwargs.get("text", "") or ""),
        )
        if parsed is not None:
            return parsed
        return flow_tools.start_flow_draft(kwargs.get("flow_name"))

    @staticmethod
    def _set_flow_name(**kwargs: Any) -> ToolResult:
        return flow_tools.set_flow_name(
            kwargs.get("draft", {}) if isinstance(kwargs.get("draft"), dict) else {},
            str(kwargs.get("flow_name", "") or ""),
        )

    def _append_flow_step(self, **kwargs: Any) -> ToolResult:
        service = self._require_execution_plan_service()
        if not service.ok:
            return service
        return flow_tools.append_flow_step(
            service.data["service"],
            step_text=str(kwargs.get("step_text", "") or ""),
            draft=kwargs.get("draft", {}) if isinstance(kwargs.get("draft"), dict) else None,
        )

    def _answer_flow_clarification(self, **kwargs: Any) -> ToolResult:
        service = self._require_execution_plan_service()
        if not service.ok:
            return service
        return flow_tools.answer_flow_clarification(
            service.data["service"],
            str(kwargs.get("text", "") or ""),
            draft=kwargs.get("draft", {}) if isinstance(kwargs.get("draft"), dict) else None,
            snapshot_provider=self.controller_snapshot_provider,
        )

    def _edit_flow_draft_params(self, **kwargs: Any) -> ToolResult:
        service = self._require_execution_plan_service()
        if not service.ok:
            return service
        return flow_tools.edit_flow_draft_params(
            service.data["service"],
            text=str(kwargs.get("text", "") or ""),
            draft=kwargs.get("draft", {}) if isinstance(kwargs.get("draft"), dict) else None,
        )

    def _save_flow_draft(self, **kwargs: Any) -> ToolResult:
        service = self._require_flow_service()
        if not service.ok:
            return service
        return flow_tools.save_flow_draft(
            service.data["service"],
            dict(kwargs.get("draft", {}) or {}),
        )

    def _query_registered_flow(self, **kwargs: Any) -> ToolResult:
        service = self._require_flow_service()
        if not service.ok:
            return service
        return flow_tools.query_registered_flow(
            service.data["service"],
            str(kwargs.get("flow_name", "") or kwargs.get("name", "") or ""),
        )

    def _prepare_registered_flow_execution(self, **kwargs: Any) -> ToolResult:
        service = self._require_flow_service()
        if not service.ok:
            return service
        return flow_tools.prepare_registered_flow_execution(
            service.data["service"],
            str(kwargs.get("flow_name", "") or kwargs.get("name", "") or ""),
            mode=str(kwargs.get("mode", "") or "start"),
        )

    def _set_flow_draft(self, **kwargs: Any) -> ToolResult:
        service = self._require_execution_plan_service()
        if not service.ok:
            return service
        return flow_tools.set_flow_draft(service.data["service"], dict(kwargs.get("draft", {}) or {}))

    def _query_current_flow_draft(self, **kwargs: Any) -> ToolResult:
        service = self._require_execution_plan_service()
        if service.ok:
            return flow_tools.query_current_draft(service.data["service"])
        draft = kwargs.get("draft")
        if isinstance(draft, dict) and draft:
            return flow_tools.query_flow_draft(draft)
        return service

    def _cancel_flow_draft(self, **kwargs: Any) -> ToolResult:
        service = self._require_execution_plan_service()
        if not service.ok:
            return service
        return flow_tools.cancel_flow_draft(service.data["service"])

    @staticmethod
    def _explain_text(**kwargs: Any) -> ToolResult:
        return chat_tools.explain_text(str(kwargs.get("text", "") or ""))

    def _query_command_catalog(self, **kwargs: Any) -> ToolResult:
        service = self._require_flow_service()
        if not service.ok:
            return service
        return chat_tools.query_command_catalog(
            service.data["service"],
            text=str(kwargs.get("text", "") or ""),
        )

    @staticmethod
    def _query_dashboard_section(**kwargs: Any) -> ToolResult:
        return status_tools.query_dashboard_section(str(kwargs.get("text", "") or ""))

    def _get_axis_status(self, **kwargs: Any) -> ToolResult:
        snapshot = kwargs.get("snapshot")
        if snapshot is None and self.runtime_snapshot_provider is not None:
            snapshot = self.runtime_snapshot_provider()
        return status_tools.get_axis_status(
            dict(snapshot or {}),
            axis=kwargs.get("axis"),
        )

    def _get_alarm(self, **kwargs: Any) -> ToolResult:
        snapshot = kwargs.get("snapshot")
        if snapshot is None and self.runtime_snapshot_provider is not None:
            snapshot = self.runtime_snapshot_provider()
        return status_tools.get_alarm(dict(snapshot or {}))

    def _get_execution_progress(self, **kwargs: Any) -> ToolResult:
        snapshot = kwargs.get("snapshot")
        if snapshot is None and self.runtime_snapshot_provider is not None:
            snapshot = self.runtime_snapshot_provider()
        return status_tools.get_execution_progress(dict(snapshot or {}))

    @staticmethod
    def _query_saved_position(**kwargs: Any) -> ToolResult:
        lookup = kwargs.get("lookup")
        return status_tools.query_saved_position(str(kwargs.get("text", "") or ""), lookup=lookup)

    def _create_memory_candidate(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        return memory_tools.create_memory_candidate(
            store.data["store"],
            kind=str(kwargs.get("kind", "") or ""),
            key=str(kwargs.get("key", "") or ""),
            value=dict(kwargs.get("value", {}) or {}),
            source=str(kwargs.get("source", "") or ""),
            confidence=kwargs.get("confidence"),
        )

    def _query_memory_candidates(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        kind = kwargs.get("kind")
        return memory_tools.query_memory_candidates(
            store.data["store"],
            kind=None if kind is None else str(kind),
        )

    def _query_memory_review(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        status = kwargs.get("status")
        kind = kwargs.get("kind")
        return memory_tools.query_memory_review(
            store.data["store"],
            status=None if status is None else str(status),
            kind=None if kind is None else str(kind),
            include_audit=bool(kwargs.get("include_audit", True)),
        )

    def _approve_memory_candidate(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        return memory_tools.approve_memory_candidate(
            store.data["store"],
            str(kwargs.get("memory_id", "") or ""),
            reviewer=str(kwargs.get("reviewer", "") or ""),
        )

    def _disable_memory(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        return memory_tools.disable_memory(
            store.data["store"],
            str(kwargs.get("memory_id", "") or ""),
            reviewer=str(kwargs.get("reviewer", "") or ""),
            reason=str(kwargs.get("reason", "") or ""),
        )

    def _rollback_memory(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        return memory_tools.rollback_memory(
            store.data["store"],
            str(kwargs.get("memory_id", "") or ""),
            reviewer=str(kwargs.get("reviewer", "") or ""),
            reason=str(kwargs.get("reason", "") or ""),
        )

    def _lookup_active_memory(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        key = kwargs.get("key")
        return memory_tools.lookup_active_memory(
            store.data["store"],
            kind=str(kwargs.get("kind", "") or ""),
            key=None if key is None else str(key),
        )

    def _record_feedback_vote(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        return memory_tools.record_feedback_vote(
            store.data["store"],
            interaction_id=str(kwargs.get("interaction_id", "") or ""),
            target_type=str(kwargs.get("target_type", "") or ""),
            target_id=str(kwargs.get("target_id", "") or ""),
            vote=str(kwargs.get("vote", "") or ""),
            note=str(kwargs.get("note", "") or ""),
        )

    def _record_memory_applied(self, **kwargs: Any) -> ToolResult:
        store = self._require_memory_store()
        if not store.ok:
            return store
        return memory_tools.record_memory_applied(
            store.data["store"],
            str(kwargs.get("memory_id", "") or ""),
            context=dict(kwargs.get("context", {}) or {}),
        )

    def _save_position_alias(self, **kwargs: Any) -> ToolResult:
        registry = self._require_position_registry()
        if not registry.ok:
            return registry
        pose = kwargs.get("pose")
        if pose in (None, (), []):
            pose = self.start_pose_provider() if self.start_pose_provider is not None else ()
        return memory_tools.save_position_alias(
            registry.data["registry"],
            name=str(kwargs.get("name", "") or kwargs.get("position_name", "") or ""),
            pose=pose or (),
            created_by=str(kwargs.get("created_by", "") or "operator"),
            spd=int(kwargs.get("spd", 50) or 50),
            move_type=int(kwargs.get("move_type", 0) or 0),
        )

    def _delete_position_alias(self, **kwargs: Any) -> ToolResult:
        registry = self._require_position_registry()
        if not registry.ok:
            return registry
        return memory_tools.delete_position_alias(
            registry.data["registry"],
            name=str(kwargs.get("name", "") or kwargs.get("position_name", "") or ""),
        )

    def _require_memory_store(self) -> ToolResult:
        if self.memory_store is None:
            return ToolResult.failure(
                state="memory_store_unavailable",
                message="记忆库未配置。",
                code="MEMORY_STORE_UNAVAILABLE",
            )
        return ToolResult.success(
            state="memory_store_available",
            message="记忆库可用。",
            data={"store": self.memory_store},
        )

    def _require_atomic_memory(self) -> ToolResult:
        if self.atomic_memory_provider is None:
            return ToolResult.failure(
                state="atomic_memory_unavailable",
                message="原子记忆未配置。",
                code="ATOMIC_MEMORY_UNAVAILABLE",
            )
        try:
            memory = self.atomic_memory_provider()
        except Exception as exc:
            return ToolResult.failure(
                state="atomic_memory_unavailable",
                message=str(exc),
                code="ATOMIC_MEMORY_UNAVAILABLE",
            )
        if memory is None:
            return ToolResult.failure(
                state="atomic_memory_unavailable",
                message="原子记忆未配置。",
                code="ATOMIC_MEMORY_UNAVAILABLE",
            )
        return ToolResult.success(
            state="atomic_memory_available",
            message="原子记忆可用。",
            data={"memory": memory},
        )

    def _require_execution_plan_service(self) -> ToolResult:
        if self.execution_plan_service is None:
            return ToolResult.failure(
                state="execution_plan_service_unavailable",
                message="流程草案服务未配置。",
                code="EXECUTION_PLAN_SERVICE_UNAVAILABLE",
            )
        return ToolResult.success(
            state="execution_plan_service_available",
            message="流程草案服务可用。",
            data={"service": self.execution_plan_service},
        )

    def _require_position_registry(self) -> ToolResult:
        if self.position_registry_provider is None:
            return ToolResult.failure(
                state="position_registry_unavailable",
                message="位置库未配置。",
                code="POSITION_REGISTRY_UNAVAILABLE",
            )
        try:
            registry = self.position_registry_provider()
        except Exception as exc:
            return ToolResult.failure(
                state="position_registry_unavailable",
                message=str(exc),
                code="POSITION_REGISTRY_UNAVAILABLE",
            )
        if registry is None:
            return ToolResult.failure(
                state="position_registry_unavailable",
                message="位置库未配置。",
                code="POSITION_REGISTRY_UNAVAILABLE",
            )
        return ToolResult.success(
            state="position_registry_available",
            message="位置库可用。",
            data={"registry": registry},
        )

    def _require_flow_service(self) -> ToolResult:
        if self.flow_service is None:
            return ToolResult.failure(
                state="flow_service_unavailable",
                message="流程保存服务未配置。",
                code="FLOW_SERVICE_UNAVAILABLE",
            )
        return ToolResult.success(
            state="flow_service_available",
            message="流程保存服务可用。",
            data={"service": self.flow_service},
        )

    def _require_safety_review_agent(self) -> ToolResult:
        if self.safety_review_agent is None:
            return ToolResult.failure(
                state="safety_review_unavailable",
                message="安全预检服务未配置。",
                code="SAFETY_REVIEW_UNAVAILABLE",
            )
        return ToolResult.success(
            state="safety_review_available",
            message="安全预检服务可用。",
            data={"agent": self.safety_review_agent},
        )

    def _require_confirmation_agent(self) -> ToolResult:
        if self.confirmation_agent is None:
            return ToolResult.failure(
                state="confirmation_agent_unavailable",
                message="确认状态机未配置。",
                code="CONFIRMATION_AGENT_UNAVAILABLE",
            )
        return ToolResult.success(
            state="confirmation_agent_available",
            message="确认状态机可用。",
            data={"agent": self.confirmation_agent},
        )

    def _now(self) -> float:
        if self.clock is None:
            return 0.0
        return float(self.clock())

    def _status_signature(self) -> str:
        if self.status_signature_provider is None:
            return ""
        return str(self.status_signature_provider())

    def _safety_signature(self) -> str:
        if self.safety_signature_provider is None:
            return ""
        return str(self.safety_signature_provider())


def _tool_result_from_dict(value: dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=bool(value.get("ok", False)),
        state=str(value.get("state", "") or ""),
        message=str(value.get("message", "") or ""),
        data=dict(value.get("data", {}) or {}),
        errors=[dict(error) for error in value.get("errors", []) or []],
    )


def _command_draft_from_value(value: Any) -> Any:
    return safety_tools._command_draft_from_value(value)
