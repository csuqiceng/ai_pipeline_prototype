from __future__ import annotations

from dataclasses import dataclass, field, replace
import uuid
from typing import Any

from robot_modbus_lite.agent_tools.tool_result import ToolResult


@dataclass(frozen=True)
class SessionState:
    thread_id: str
    mode: str = "idle"
    current_intent: str = ""
    current_flow_draft: dict[str, Any] = field(default_factory=dict)
    current_compound_plan: dict[str, Any] = field(default_factory=dict)
    pending_missing_fields: tuple[str, ...] = ()
    pending_confirm: dict[str, Any] = field(default_factory=dict)
    pending_execution: dict[str, Any] = field(default_factory=dict)
    last_confirmed_execution: dict[str, Any] = field(default_factory=dict)
    last_failed_execution: dict[str, Any] = field(default_factory=dict)
    last_tool_call: dict[str, Any] = field(default_factory=dict)
    tool_call_history: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_interaction_id: str = ""
    last_agent_result: dict[str, Any] = field(default_factory=dict)
    last_user_text: str = ""
    last_normalized_text: str = ""
    applied_memories: tuple[dict[str, Any], ...] = ()
    safety_context: dict[str, Any] = field(default_factory=dict)

    def with_tool_result(
        self,
        *,
        tool_name: str,
        tool_result: ToolResult,
        user_text: str = "",
        normalized_text: str = "",
    ) -> "SessionState":
        missing = self._missing_fields(tool_result)
        next_mode = "clarifying" if missing else self._mode_from_tool_state(tool_result.state)
        next_intent = str(tool_result.data.get("intent", "") or self.current_intent)
        return replace(
            self,
            mode=next_mode,
            current_intent=next_intent,
            pending_missing_fields=missing,
            last_tool_call={
                "tool_name": str(tool_name),
                "result": tool_result.to_dict(),
            },
            last_user_text=str(user_text or self.last_user_text),
            last_normalized_text=str(normalized_text or self.last_normalized_text),
        )

    def with_idempotent_tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        tool_result: ToolResult,
    ) -> "SessionState":
        clean_tool_call_id = str(tool_call_id or "")
        if not clean_tool_call_id:
            return self
        history = dict(self.tool_call_history)
        history[clean_tool_call_id] = {
            "tool_name": str(tool_name or ""),
            "result": tool_result.to_dict(),
        }
        return replace(self, tool_call_history=history)

    def with_idempotent_tool_replay(self, tool_call_id: str) -> "SessionState":
        clean_tool_call_id = str(tool_call_id or "")
        entry = self.tool_call_history.get(clean_tool_call_id)
        if not clean_tool_call_id or not isinstance(entry, dict):
            return self
        history = dict(self.tool_call_history)
        updated = dict(entry)
        updated["replay_count"] = int(updated.get("replay_count", 0) or 0) + 1
        history[clean_tool_call_id] = updated
        return replace(self, tool_call_history=history)

    def get_idempotent_tool_result(self, tool_call_id: str) -> dict[str, Any] | None:
        entry = self.tool_call_history.get(str(tool_call_id or ""))
        if not isinstance(entry, dict):
            return None
        result = entry.get("result")
        if not isinstance(result, dict):
            return None
        return dict(result)

    def cancel_pending_plan(self) -> "SessionState":
        next_mode = "editing_flow" if self.current_flow_draft else "idle"
        return replace(
            self,
            mode=next_mode,
            current_compound_plan={},
            pending_confirm={},
            pending_execution={},
        )

    def with_flow_draft(self, draft: dict[str, Any] | None) -> "SessionState":
        if draft:
            return replace(
                self,
                mode="clarifying" if self.mode == "clarifying" and self.pending_missing_fields else "editing_flow",
                current_flow_draft=dict(draft),
            )
        next_mode = "waiting_confirm" if self.pending_confirm else "idle"
        return replace(
            self,
            mode=next_mode,
            current_flow_draft={},
            pending_missing_fields=(),
        )

    def with_compound_plan(self, plan: dict[str, Any] | None) -> "SessionState":
        if plan:
            return replace(
                self,
                mode="editing_flow",
                current_compound_plan=dict(plan),
            )
        next_mode = "editing_flow" if self.current_flow_draft else ("waiting_confirm" if self.pending_confirm else "idle")
        return replace(
            self,
            mode=next_mode,
            current_compound_plan={},
        )

    def advance_compound_step(self, *, ok: bool, reason: str = "") -> "SessionState":
        plan = dict(self.current_compound_plan or {})
        if not plan:
            return self.with_pending_confirm(None)
        steps = list(plan.get("steps", []) or [])
        step_results = list(plan.get("step_results", []) or [])
        try:
            index = int(plan.get("active_step_index", 0) or 0)
        except (TypeError, ValueError):
            index = 0
        step = steps[index] if 0 <= index < len(steps) else str(plan.get("active_step", "") or "")
        result = {
            "index": index,
            "step": step,
            "ok": bool(ok),
            "reason": str(reason or ""),
        }
        completed_steps = [dict(item) for item in list(plan.get("completed_steps", []) or []) if isinstance(item, dict)]
        if not ok:
            plan["status"] = "failed"
            plan["failed_step"] = result
            return replace(
                self,
                mode="blocked",
                current_compound_plan=plan,
                pending_confirm={},
                pending_execution={},
            )
        completed_steps.append(result)
        next_index = index + 1
        plan["completed_steps"] = completed_steps
        if next_index >= len(steps):
            plan["status"] = "completed"
            plan["active_step_index"] = None
            plan.pop("active_step", None)
            plan.pop("active_step_result", None)
            return replace(
                self,
                mode="editing_flow" if self.current_flow_draft else "idle",
                current_compound_plan=plan,
                pending_confirm={},
                pending_execution={},
            )
        plan["status"] = "waiting_step_confirm"
        plan["active_step_index"] = next_index
        plan["active_step"] = steps[next_index]
        if next_index < len(step_results):
            active_result = step_results[next_index]
            plan["active_step_result"] = dict(active_result) if isinstance(active_result, dict) else active_result
        else:
            plan.pop("active_step_result", None)
        return replace(
            self,
            mode="editing_flow",
            current_compound_plan=plan,
            pending_confirm={},
            pending_execution={},
        )

    def with_pending_confirm(self, confirm: dict[str, Any] | None) -> "SessionState":
        if confirm:
            return replace(
                self,
                mode="waiting_confirm",
                pending_confirm=dict(confirm),
            )
        next_mode = "editing_flow" if self.current_flow_draft else "idle"
        return replace(
            self,
            mode=next_mode,
            pending_confirm={},
            pending_execution={},
        )

    def with_pending_execution(self, execution: dict[str, Any] | None) -> "SessionState":
        if execution:
            payload = dict(execution)
            return replace(
                self,
                mode="waiting_confirm",
                current_intent=str(payload.get("intent", "") or self.current_intent),
                pending_execution=payload,
            )
        next_mode = "waiting_confirm" if self.pending_confirm else ("editing_flow" if self.current_flow_draft else "idle")
        return replace(
            self,
            mode=next_mode,
            pending_execution={},
        )

    def mark_pending_execution_confirmed(self) -> "SessionState":
        context = dict(self.pending_execution or {})
        next_mode = "editing_flow" if self.current_flow_draft else "idle"
        return replace(
            self,
            mode=next_mode,
            pending_confirm={},
            pending_execution={},
            last_confirmed_execution=context,
        )

    def with_memory_application(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        applied_memories: tuple[dict[str, Any], ...],
    ) -> "SessionState":
        return replace(
            self,
            last_user_text=str(raw_text or self.last_user_text),
            last_normalized_text=str(normalized_text or raw_text or self.last_normalized_text),
            applied_memories=tuple(dict(item) for item in applied_memories),
        )

    def with_agent_result(
        self,
        *,
        result_kind: str,
        target_id: str = "",
        interaction_id: str = "",
    ) -> "SessionState":
        clean_interaction_id = str(interaction_id or "").strip() or f"interaction-{uuid.uuid4().hex[:12]}"
        clean_target_id = str(target_id or "").strip() or clean_interaction_id
        return replace(
            self,
            last_interaction_id=clean_interaction_id,
            last_agent_result={
                "kind": str(result_kind or ""),
                "target_id": clean_target_id,
            },
        )

    def expire_pending_confirm(self) -> "SessionState":
        return replace(
            self,
            mode="confirm_expired",
            pending_confirm={},
            pending_execution={},
        )

    def record_execution_failure(self, *, query_record: dict[str, Any] | None, error: str) -> "SessionState":
        execution_context = dict(self.pending_execution or self.last_confirmed_execution or {})
        failed_execution = {
            "query_record": dict(query_record or {}),
            "error": str(error or ""),
            "execution_context": execution_context,
        }
        result = ToolResult.failure(
            state="execution_failed",
            message=f"执行失败：{str(error or '')}。已清理待执行状态。",
            code="EXECUTION_FAILED",
            data=failed_execution,
        )
        failed = replace(
            self,
            mode="execution_failed",
            pending_confirm={},
            pending_execution={},
            last_failed_execution=failed_execution,
            last_tool_call={
                "tool_name": "controller_execution",
                "result": result.to_dict(),
            },
        )
        if failed.current_compound_plan:
            failed = failed.advance_compound_step(ok=False, reason=str(error or "控制器写入失败。"))
            failed = replace(
                failed,
                last_tool_call={
                    "tool_name": "controller_execution",
                    "result": result.to_dict(),
                },
            )
        return failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "mode": self.mode,
            "current_intent": self.current_intent,
            "current_flow_draft": dict(self.current_flow_draft),
            "current_compound_plan": dict(self.current_compound_plan),
            "pending_missing_fields": list(self.pending_missing_fields),
            "pending_confirm": dict(self.pending_confirm),
            "pending_execution": dict(self.pending_execution),
            "last_confirmed_execution": dict(self.last_confirmed_execution),
            "last_failed_execution": dict(self.last_failed_execution),
            "last_tool_call": dict(self.last_tool_call),
            "tool_call_history": {key: dict(value) for key, value in self.tool_call_history.items()},
            "last_interaction_id": self.last_interaction_id,
            "last_agent_result": dict(self.last_agent_result),
            "last_user_text": self.last_user_text,
            "last_normalized_text": self.last_normalized_text,
            "applied_memories": [dict(item) for item in self.applied_memories],
            "safety_context": dict(self.safety_context),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionState":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            thread_id=str(data.get("thread_id", "") or "operator-ui"),
            mode=str(data.get("mode", "") or "idle"),
            current_intent=str(data.get("current_intent", "") or ""),
            current_flow_draft=_dict_value(data.get("current_flow_draft")),
            current_compound_plan=_dict_value(data.get("current_compound_plan")),
            pending_missing_fields=tuple(str(field) for field in _list_value(data.get("pending_missing_fields"))),
            pending_confirm=_dict_value(data.get("pending_confirm")),
            pending_execution=_dict_value(data.get("pending_execution")),
            last_confirmed_execution=_dict_value(data.get("last_confirmed_execution")),
            last_failed_execution=_dict_value(data.get("last_failed_execution")),
            last_tool_call=_dict_value(data.get("last_tool_call")),
            tool_call_history={
                str(key): _dict_value(value)
                for key, value in _dict_value(data.get("tool_call_history")).items()
            },
            last_interaction_id=str(data.get("last_interaction_id", "") or ""),
            last_agent_result=_dict_value(data.get("last_agent_result")),
            last_user_text=str(data.get("last_user_text", "") or ""),
            last_normalized_text=str(data.get("last_normalized_text", "") or ""),
            applied_memories=tuple(_dict_value(item) for item in _list_value(data.get("applied_memories"))),
            safety_context=_dict_value(data.get("safety_context")),
        )

    @staticmethod
    def _missing_fields(tool_result: ToolResult) -> tuple[str, ...]:
        for error in tool_result.errors:
            fields = error.get("fields")
            if isinstance(fields, (list, tuple)):
                return tuple(str(field) for field in fields)
        data_fields = tool_result.data.get("missing_fields")
        if isinstance(data_fields, (list, tuple)):
            return tuple(str(field) for field in data_fields)
        return ()

    @staticmethod
    def _mode_from_tool_state(state: str) -> str:
        if state in {"waiting_confirmation", "pending_confirm_created"}:
            return "waiting_confirm"
        if state in {"flow_draft_created", "flow_draft_updated"}:
            return "editing_flow"
        if state in {"precheck_failed", "blocked"}:
            return "blocked"
        return "idle"


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []
