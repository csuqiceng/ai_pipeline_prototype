from __future__ import annotations

from typing import Any

from robot_modbus_lite.agent.confirmation import ConfirmationAgent, ConfirmationError, DraftStatus
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent_tools.tool_result import ToolResult


def run_safety_precheck(
    agent: Any,
    draft: CommandDraft | dict[str, Any],
    *,
    snapshot: dict[str, Any],
    start_pose: tuple[float, float, float, float, float, float] | None = None,
) -> ToolResult:
    command_draft = _command_draft_from_value(draft)
    precheck = dict(agent.review(command_draft, snapshot=dict(snapshot or {}), start_pose=start_pose) or {})
    data = {
        "draft_id": command_draft.draft_id,
        "intent": command_draft.intent,
        "func_id": command_draft.func_id,
        "precheck": precheck,
    }
    if bool(precheck.get("valid")):
        return ToolResult.success(
            state="safety_precheck_passed",
            message=str(precheck.get("summary") or "安全预检通过。"),
            data=data,
        )
    return ToolResult.failure(
        state="safety_precheck_failed",
        message=str(precheck.get("summary") or "安全预检未通过。"),
        code="SAFETY_PRECHECK_FAILED",
        data=data,
    )


def create_pending_confirm(
    agent: ConfirmationAgent,
    draft: CommandDraft,
    *,
    now: float,
    status_signature: str,
    safety_signature: str,
) -> ToolResult:
    existing = agent.get_session(draft.draft_id)
    if existing is not None and existing.status == DraftStatus.WAITING_CONFIRMATION:
        confirmation_text = agent.render_confirmation_text(existing.draft)
        return ToolResult.success(
            state="waiting_confirmation",
            message="已存在待确认计划，复用当前确认。用于同一 draft_id 的重复创建是幂等的。",
            data={
                "draft_id": existing.draft_id,
                "status": existing.status.value,
                "expires_at": existing.expires_at,
                "confirmation_text": confirmation_text,
            },
        )
    if existing is not None:
        return ToolResult.failure(
            state="confirm_lifecycle_closed",
            message=f"草案确认生命周期已结束：{existing.status.value}，不能重新创建待确认计划。",
            code="CONFIRM_LIFECYCLE_CLOSED",
            data={"draft_id": existing.draft_id, "status": existing.status.value},
        )
    session = agent.begin(
        draft,
        now=now,
        status_signature=status_signature,
        safety_signature=safety_signature,
    )
    confirmation_text = agent.render_confirmation_text(draft)
    return ToolResult.success(
        state="waiting_confirmation",
        message="已创建待确认计划。",
        data={
            "draft_id": session.draft_id,
            "status": session.status.value,
            "expires_at": session.expires_at,
            "confirmation_text": confirmation_text,
        },
    )


def query_pending_confirm(agent: ConfirmationAgent, draft_id: str) -> ToolResult:
    status = agent.get_status(str(draft_id))
    if status is None:
        return ToolResult.failure(
            state="confirm_not_found",
            message="当前没有对应的待确认计划。",
            code="CONFIRM_NOT_FOUND",
            data={"draft_id": str(draft_id)},
        )
    return ToolResult.success(
        state="confirm_status",
        message=f"确认计划状态：{status.value}",
        data={"draft_id": str(draft_id), "status": status.value},
    )


def confirm_pending_plan(
    agent: ConfirmationAgent,
    draft_id: str,
    *,
    now: float,
    status_signature: str,
    safety_signature: str,
) -> ToolResult:
    try:
        record = agent.confirm(
            str(draft_id),
            now=now,
            status_signature=status_signature,
            safety_signature=safety_signature,
        )
    except ConfirmationError as exc:
        return ToolResult.failure(
            state="confirm_rejected",
            message=str(exc),
            code="CONFIRM_REJECTED",
            data={"draft_id": str(draft_id)},
        )
    return ToolResult.success(
        state="confirmed",
        message="确认已通过，已生成执行记录。",
        data={
            "draft_id": str(draft_id),
            "query_record": _query_record_to_dict(record),
        },
    )


def cancel_pending_plan(agent: ConfirmationAgent, draft_id: str) -> ToolResult:
    try:
        agent.reject(str(draft_id))
    except ConfirmationError as exc:
        return ToolResult.failure(
            state="cancel_rejected",
            message=str(exc),
            code="CANCEL_REJECTED",
            data={"draft_id": str(draft_id)},
        )
    return ToolResult.success(
        state="cancelled",
        message="已取消待确认计划。",
        data={"draft_id": str(draft_id)},
    )


def expire_pending_plan(agent: ConfirmationAgent, draft_id: str) -> ToolResult:
    try:
        agent.expire(str(draft_id))
    except ConfirmationError as exc:
        return ToolResult.failure(
            state="expire_rejected",
            message=str(exc),
            code="EXPIRE_REJECTED",
            data={"draft_id": str(draft_id)},
        )
    return ToolResult.success(
        state="expired",
        message="待确认计划已过期。",
        data={"draft_id": str(draft_id)},
    )


def _query_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "query_key": str(getattr(record, "query_key", "") or ""),
        "func_num": int(getattr(record, "func_num", 0) or 0),
        "params": dict(getattr(record, "params", {}) or {}),
        "description": str(getattr(record, "description", "") or ""),
    }


def _command_draft_from_value(value: CommandDraft | dict[str, Any]) -> CommandDraft:
    if isinstance(value, CommandDraft):
        return value
    payload = dict(value or {})
    return CommandDraft(
        draft_id=str(payload.get("draft_id", "") or ""),
        func_id=int(payload.get("func_id", 0) or 0),
        intent=str(payload.get("intent", "") or ""),
        params=dict(payload.get("params", {}) or {}),
        param_sources=dict(payload.get("param_sources", {}) or {}),
        raw_text=str(payload.get("raw_text", "") or ""),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        precheck_result=payload.get("precheck_result"),
        confirmed=bool(payload.get("confirmed", False)),
    )
