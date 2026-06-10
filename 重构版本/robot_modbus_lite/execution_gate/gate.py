from __future__ import annotations

from dataclasses import dataclass

from robot_modbus_lite.agent_tools.tool_result import ToolResult


EXECUTION_GATE_CHECK_ORDER = (
    "wake_word",
    "permission",
    "params_complete",
    "bounds",
    "safety_precheck",
    "pending_confirm",
    "confirmed",
)


@dataclass(frozen=True)
class ExecutionGateInput:
    action_type: str
    is_execution: bool
    has_wake_word: bool = False
    permission_ok: bool = True
    missing_fields: tuple[str, ...] = ()
    bounds_ok: bool = True
    safety_ok: bool = True
    requires_confirmation: bool = False
    has_pending_confirm: bool = False
    confirmed: bool = False


def evaluate_execution_gate(payload: ExecutionGateInput) -> ToolResult:
    base_data = {"action_type": payload.action_type, "check_order": list(EXECUTION_GATE_CHECK_ORDER)}
    if not payload.is_execution:
        return ToolResult.success(
            state="gate_skipped_non_execution",
            message="非执行类动作跳过执行门禁。",
            data=base_data,
        )
    if not payload.has_wake_word:
        return ToolResult.failure(
            state="wake_word_required",
            message="控制类动作需要唤醒词。",
            code="WAKE_WORD_REQUIRED",
            data=base_data,
        )
    if not payload.permission_ok:
        return ToolResult.failure(
            state="permission_denied",
            message="当前权限不允许执行该动作。",
            code="PERMISSION_DENIED",
            data=base_data,
        )
    if payload.missing_fields:
        fields = [str(field) for field in payload.missing_fields]
        data = dict(base_data)
        data["missing_fields"] = fields
        return ToolResult.failure(
            state="missing_params",
            message="参数不完整，不能执行。",
            code="MISSING_REQUIRED_PARAMS",
            data=data,
            fields=fields,
        )
    if not payload.bounds_ok:
        return ToolResult.failure(
            state="bounds_failed",
            message="参数边界检查未通过。",
            code="PARAM_BOUNDS_FAILED",
            data=base_data,
        )
    if not payload.safety_ok:
        return ToolResult.failure(
            state="safety_precheck_failed",
            message="安全预检未通过。",
            code="SAFETY_PRECHECK_FAILED",
            data=base_data,
        )
    if payload.requires_confirmation and not payload.has_pending_confirm:
        return ToolResult.failure(
            state="confirmation_required",
            message="需要先创建待确认计划。",
            code="CONFIRMATION_REQUIRED",
            data=base_data,
        )
    if payload.requires_confirmation and not payload.confirmed:
        return ToolResult.failure(
            state="waiting_confirmation",
            message="等待用户确认。",
            code="WAITING_CONFIRMATION",
            data=base_data,
        )
    return ToolResult.success(
        state="execution_allowed",
        message="执行门禁通过。",
        data=base_data,
    )
