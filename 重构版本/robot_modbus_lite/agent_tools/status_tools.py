from __future__ import annotations

from collections.abc import Callable
from typing import Any

from robot_modbus_lite.agent.alarm_explanation import AlarmExplanationAgent
from robot_modbus_lite.agent.axis_status import AxisStatusBitDecomposer
from robot_modbus_lite.agent.dashboard_query import DashboardQueryAgent
from robot_modbus_lite.agent.position_query import PositionQueryAgent
from robot_modbus_lite.agent_tools.tool_result import ToolResult


def query_saved_position(text: str, *, lookup: Callable[[str], Any]) -> ToolResult:
    answer = PositionQueryAgent(lookup=lambda name: lookup(_clean_position_name(name))).answer(text)
    if answer is None:
        return ToolResult.failure(
            state="position_query_not_matched",
            message="未识别为位置查询。",
            code="POSITION_QUERY_NOT_MATCHED",
            data={"raw_text": str(text or "")},
        )
    position_name = _clean_position_name(answer.get("position_name", ""))
    data = {
        "raw_text": str(text or ""),
        "position_name": position_name,
    }
    if "pose" not in answer:
        return ToolResult.failure(
            state="position_not_found",
            message=str(answer.get("text", "") or "位置不存在。"),
            code="POSITION_NOT_FOUND",
            data=data,
        )
    data["pose"] = tuple(float(item) for item in tuple(answer.get("pose") or ()))
    pose = data["pose"]
    return ToolResult.success(
        state="position_found",
        message=(
            f"位置{position_name}坐标：X={pose[0]} Y={pose[1]} Z={pose[2]} "
            f"RX={pose[3]} RY={pose[4]} RZ={pose[5]}。没有触发机械手动作。"
        ),
        data=data,
    )


def _clean_position_name(name: Any) -> str:
    return str(name or "").strip().removesuffix("的")


def query_dashboard_section(text: str) -> ToolResult:
    answer = DashboardQueryAgent().answer(text)
    if answer is None:
        return ToolResult.failure(
            state="dashboard_query_not_matched",
            message="未识别为状态面板查询。",
            code="DASHBOARD_QUERY_NOT_MATCHED",
            data={"raw_text": str(text or "")},
        )
    return ToolResult.success(
        state="dashboard_section_matched",
        message=str(answer.get("text", "") or ""),
        data={
            "raw_text": str(answer.get("raw_text", "") or text or ""),
            "target": str(answer.get("target", "") or ""),
            "action_type": str(answer.get("action_type", "") or "query"),
        },
    )


def get_axis_status(snapshot: dict[str, Any], *, axis: int | None = None) -> ToolResult:
    raw_values = _axis_status_values(snapshot)
    detail = AxisStatusBitDecomposer().decompose(raw_values)
    axes = list(detail.get("axes", []) or [])
    selected_axis = int(axis or 0)
    if selected_axis:
        axes = [item for item in axes if int(item.get("axis", 0) or 0) == selected_axis]
        if not axes:
            return ToolResult.failure(
                state="axis_status_not_found",
                message=f"未找到J{selected_axis}轴状态。",
                code="AXIS_STATUS_NOT_FOUND",
                data={"axis": selected_axis, "generates_command": False},
            )
    has_error = any(bool(item.get("messages")) for item in axes)
    return ToolResult.success(
        state="axis_status_loaded",
        message="已读取轴状态。" if not has_error else "已读取轴状态，存在异常。",
        data={
            "axis": selected_axis or None,
            "axes": axes,
            "has_error": has_error,
            "generates_command": False,
        },
    )


def get_alarm(snapshot: dict[str, Any]) -> ToolResult:
    safety = dict(snapshot.get("safety", {}) or {})
    motion = dict(snapshot.get("motion", {}) or {})
    hardware = dict(snapshot.get("hardware", {}) or {})
    alarm = AlarmExplanationAgent().explain(
        long34=_int_value(safety.get("long34", safety.get("LONG34", snapshot.get("long34", 0)))),
        long36=_int_value(safety.get("long36", safety.get("LONG36", snapshot.get("long36", 0)))),
        long38=_int_value(safety.get("long38", safety.get("LONG38", snapshot.get("long38", 0)))),
        axis_status=_axis_status_values(snapshot),
        current_func=_optional_int(motion.get("current_func", motion.get("func_num", snapshot.get("current_func")))),
        safety_values=safety,
        hardware_values=hardware,
    )
    return ToolResult.success(
        state="alarm_loaded",
        message=str(alarm.get("summary", "") or "已读取报警状态。"),
        data={
            "alarm": alarm,
            "generates_command": False,
        },
    )


def get_execution_progress(snapshot: dict[str, Any]) -> ToolResult:
    source = dict(snapshot or {})
    execution = dict(source.get("execution", {}) or {})
    motion = dict(source.get("motion", {}) or {})
    progress = _first_number(
        execution.get("progress"),
        execution.get("current_progress"),
        execution.get("progress_pct"),
        source.get("progress"),
        source.get("progress_pct"),
        motion.get("progress"),
        motion.get("progress_pct"),
    )
    if progress is None:
        return ToolResult.failure(
            state="execution_progress_unavailable",
            message="当前没有可用执行进度。",
            code="EXECUTION_PROGRESS_UNAVAILABLE",
            data={"generates_command": False},
        )
    value = max(0, min(100, int(round(progress))))
    status = str(execution.get("status", "") or motion.get("running_state", "") or source.get("status", "") or "")
    return ToolResult.success(
        state="execution_progress_loaded",
        message=f"当前执行进度约 {value}%。",
        data={
            "progress": value,
            "status": status,
            "generates_command": False,
        },
    )


def _axis_status_values(snapshot: dict[str, Any]) -> list[int]:
    hardware = dict(snapshot.get("hardware", {}) or {})
    raw = (
        hardware.get("axis_status")
        or hardware.get("axis_status_values")
        or snapshot.get("axis_status")
        or snapshot.get("axisStatus")
        or []
    )
    if not isinstance(raw, (list, tuple)):
        return []
    return [_int_value(item) for item in tuple(raw)[:6]]


def _int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _int_value(value)


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
