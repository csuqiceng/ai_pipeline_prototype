from __future__ import annotations

from collections.abc import Callable
from typing import Any

from robot_modbus_lite.agent.compound import CompoundCommandCoordinator, CompoundPlanResult, CompoundSplitResult
from robot_modbus_lite.agent_tools.tool_result import ToolResult


def split_compound_command(
    text: str,
    *,
    coordinator: CompoundCommandCoordinator | None = None,
) -> ToolResult:
    result = (coordinator or CompoundCommandCoordinator()).split(text)
    if result.kind == "compound_sequence":
        return ToolResult.success(
            state="compound_sequence",
            message="已拆分为顺序复合指令。",
            data=_split_result_to_dict(result),
        )
    code = "UNSUPPORTED_COMPOUND" if result.kind == "unsupported_compound" else "NOT_COMPOUND"
    return ToolResult.failure(
        state=result.kind,
        message=result.reason or "未识别到可执行的顺序复合指令。",
        code=code,
        data=_split_result_to_dict(result),
    )


def plan_compound_command(
    text: str,
    *,
    coordinator: CompoundCommandCoordinator | None = None,
    restricted_service: Any = None,
    clock: Callable[[], float] | None = None,
    id_factory: Callable[[], str] | None = None,
) -> ToolResult:
    active = coordinator or CompoundCommandCoordinator(
        restricted_service=restricted_service,
        clock=clock,
        id_factory=id_factory,
    )
    result = active.plan(text)
    if result.kind == "compound_plan_draft":
        return ToolResult.success(
            state="compound_plan_draft",
            message="已生成复合指令草案，未执行任何控制器写入。",
            data=_plan_result_to_dict(result),
        )
    code = "UNSUPPORTED_COMPOUND" if result.kind == "unsupported_compound" else "NOT_COMPOUND"
    return ToolResult.failure(
        state=result.kind,
        message=result.reason or "未生成复合指令草案。",
        code=code,
        data=_plan_result_to_dict(result),
    )


def _split_result_to_dict(result: CompoundSplitResult) -> dict[str, Any]:
    return {
        "kind": result.kind,
        "steps": list(result.steps),
        "reason": result.reason,
        "generates_command": False,
    }


def _plan_result_to_dict(result: CompoundPlanResult) -> dict[str, Any]:
    return {
        "kind": result.kind,
        "plan_id": result.plan_id,
        "raw_text": result.raw_text,
        "created_at": result.created_at,
        "steps": list(result.steps),
        "step_results": [_serialize_step_result(item) for item in result.step_results],
        "reason": result.reason,
        "generates_command": False,
    }


def _serialize_step_result(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value
