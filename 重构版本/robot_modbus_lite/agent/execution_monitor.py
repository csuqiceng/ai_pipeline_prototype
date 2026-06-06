from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from robot_modbus_lite.models import QueryRecord


@dataclass(frozen=True)
class ExecutionMonitorSnapshot:
    status: str
    query_key: str
    func_id: int
    started_at: float = 0.0
    updated_at: float = 0.0
    detail: str = ""
    result_code: str = ""
    progress_pct: int | None = None
    feedback: tuple[float, ...] = field(default_factory=tuple)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["feedback"] = list(self.feedback)
        payload["context"] = dict(self.context)
        return payload

    @classmethod
    def from_mapping(cls, value: Any) -> "ExecutionMonitorSnapshot | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                status=str(value.get("status", "") or ""),
                query_key=str(value.get("query_key", "") or ""),
                func_id=int(value.get("func_id", 0) or 0),
                started_at=float(value.get("started_at", 0.0) or 0.0),
                updated_at=float(value.get("updated_at", 0.0) or 0.0),
                detail=str(value.get("detail", "") or ""),
                result_code=str(value.get("result_code", "") or ""),
                progress_pct=_int_or_none(value.get("progress_pct")),
                feedback=tuple(float(item) for item in (value.get("feedback", ()) or ())),
                context=dict(value.get("context", {}) or {}),
            )
        except Exception:
            return None


class ExecutionMonitorAgent:
    """Summarize the latest deterministic execution state without executing commands."""

    def __init__(self, *, default_timeout_sec: float = 30.0, position_tolerance_mm: float = 2.0) -> None:
        self.default_timeout_sec = float(default_timeout_sec)
        self.position_tolerance_mm = float(position_tolerance_mm)

    def record_dispatch_started(
        self,
        record: QueryRecord,
        *,
        context: dict[str, Any] | None = None,
        now: float = 0.0,
        progress_pct: int | None = None,
    ) -> ExecutionMonitorSnapshot:
        return ExecutionMonitorSnapshot(
            status="running",
            query_key=record.query_key,
            func_id=int(record.func_num),
            started_at=float(now or 0.0),
            updated_at=float(now or 0.0),
            detail="控制器已收到执行请求，正在等待执行结果。",
            progress_pct=progress_pct,
            context=self._context_with_target(record, context),
        )

    def record_dispatch_result(
        self,
        record: QueryRecord,
        *,
        ok: bool,
        error: str,
        feedback: list[float] | tuple[float, ...] | None = None,
        context: dict[str, Any] | None = None,
        now: float = 0.0,
    ) -> ExecutionMonitorSnapshot:
        values = tuple(float(item) for item in (feedback or ()))
        context_payload = self._context_with_target(record, context)
        deviation = self._position_deviation_mm(record, values)
        if deviation is not None:
            context_payload["position_deviation_mm"] = deviation
        completed_status = "completed_with_warning" if ok and deviation is not None and deviation > self.position_tolerance_mm else "completed"
        detail = "动作执行完成" if ok else str(error or "执行失败")
        if ok and deviation is not None and deviation > self.position_tolerance_mm:
            detail = f"动作执行完成，但位置偏差 {deviation:.1f}mm 超过阈值 {self.position_tolerance_mm:.1f}mm"
        return ExecutionMonitorSnapshot(
            status=completed_status if ok else "failed",
            query_key=record.query_key,
            func_id=int(record.func_num),
            updated_at=float(now or 0.0),
            detail=detail,
            result_code=str(values[0]) if values else ("0" if ok else "9"),
            progress_pct=100 if ok else None,
            feedback=values,
            context=context_payload,
        )

    def answer_completion_query(
        self,
        snapshot: ExecutionMonitorSnapshot | dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> str:
        current = ExecutionMonitorSnapshot.from_mapping(snapshot)
        if current is None or not current.query_key:
            return "当前没有可追踪的执行记录。"
        if current.status == "running":
            elapsed = None
            if now is not None and current.started_at > 0:
                elapsed = max(0.0, float(now) - float(current.started_at))
            if elapsed is not None and elapsed > self.default_timeout_sec:
                return (
                    f"动作可能超时：{current.query_key} 已执行 {elapsed:.1f}秒，"
                    f"超过 {self.default_timeout_sec:.1f}秒仍未收到完成状态。请查看控制器状态和报警信息。"
                )
            progress = f"，当前进度约{current.progress_pct}%" if current.progress_pct is not None else ""
            return f"还在执行：{current.query_key}{progress}。{current.detail or '请等待控制器返回完成状态。'}"
        if current.status == "failed":
            return f"动作执行失败：{current.query_key}。原因：{current.detail or '控制器返回失败状态'}。"
        if current.status in {"completed", "completed_with_warning"}:
            position = self._position_text(current.feedback)
            position_text = f"，当前位置 {position}" if position else ""
            result = f"，结果 {current.result_code}" if current.result_code else ""
            warning = f" {current.detail}。" if current.status == "completed_with_warning" and current.detail else ""
            return f"动作执行完成：{current.query_key}{result}{position_text}。{warning}".strip()
        return f"最近执行状态：{current.status}，指令 {current.query_key}。"

    def update_from_runtime_state(
        self,
        snapshot: ExecutionMonitorSnapshot | dict[str, Any] | None,
        *,
        alarm_active: bool,
        alarm_text: str = "",
        channel_idle: bool,
        current_func: str = "",
        result_code: str = "",
        feedback: list[float] | tuple[float, ...] | None = None,
        now: float = 0.0,
    ) -> ExecutionMonitorSnapshot:
        current = ExecutionMonitorSnapshot.from_mapping(snapshot)
        if current is None or current.status != "running":
            return current or ExecutionMonitorSnapshot(status="", query_key="", func_id=0)
        if alarm_active:
            return ExecutionMonitorSnapshot(
                status="failed",
                query_key=current.query_key,
                func_id=current.func_id,
                started_at=current.started_at,
                updated_at=float(now or current.updated_at),
                detail=str(alarm_text or "执行过程中出现报警"),
                result_code=str(result_code or current.result_code or "9"),
                feedback=tuple(float(item) for item in (feedback or current.feedback or ())),
                context=dict(current.context),
            )
        if channel_idle:
            values = tuple(float(item) for item in (feedback or current.feedback or ()))
            return ExecutionMonitorSnapshot(
                status="completed",
                query_key=current.query_key,
                func_id=current.func_id,
                started_at=current.started_at,
                updated_at=float(now or current.updated_at),
                detail=f"控制器通道已空闲，函数 {current_func or current.func_id}",
                result_code=str(result_code or current.result_code or "0"),
                progress_pct=100,
                feedback=values,
                context=dict(current.context),
            )
        return ExecutionMonitorSnapshot(
            status=current.status,
            query_key=current.query_key,
            func_id=current.func_id,
            started_at=current.started_at,
            updated_at=float(now or current.updated_at),
            detail=current.detail,
            result_code=current.result_code,
            progress_pct=current.progress_pct,
            feedback=current.feedback,
            context=dict(current.context),
        )

    @staticmethod
    def _position_text(feedback: tuple[float, ...]) -> str:
        if len(feedback) >= 4:
            return " / ".join(str(value) for value in feedback[1:4])
        return ""

    @staticmethod
    def _context_with_target(record: QueryRecord, context: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(context or {})
        target = _record_target_xyz(record)
        if target is not None:
            payload["target_xyz"] = target
        return payload

    @staticmethod
    def _position_deviation_mm(record: QueryRecord, feedback: tuple[float, ...]) -> float | None:
        target = _record_target_xyz(record)
        if target is None or len(feedback) < 4:
            return None
        actual = tuple(float(item) for item in feedback[1:4])
        return max(abs(actual_item - target_item) for actual_item, target_item in zip(actual, target))


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _record_target_xyz(record: QueryRecord) -> tuple[float, float, float] | None:
    params = getattr(record, "params", {}) or {}
    try:
        return (
            float(params["target_x"]),
            float(params["target_y"]),
            float(params["target_z"]),
        )
    except Exception:
        return None
