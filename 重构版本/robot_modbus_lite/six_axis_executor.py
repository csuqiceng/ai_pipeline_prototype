"""Controller-independent six-axis execution service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .gui_constants import MOVE_TYPE_LABELS, STOP_CMD_LABELS
from .models import ControllerClient, QueryRecord, SixAxisCommand
from .service import RobotModbusService
from .six_axis_command_mixin import SixAxisCommandMixin
from .system_config import AxisRangeConfig


@dataclass
class SixAxisExecutionResult:
    ok: bool
    query_key: str
    func_num: int
    feedback: list[float] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


class SixAxisExecutionService(SixAxisCommandMixin):
    """Runs the existing six-axis protocol chain outside Qt widgets."""

    def __init__(self, service: RobotModbusService, axis_ranges: AxisRangeConfig) -> None:
        self.service = service
        self.axis_ranges = axis_ranges
        self.motion_percent = "空闲"
        self.logs: list[dict[str, Any]] = []

    def execute_record(self, client: ControllerClient, record: QueryRecord) -> SixAxisExecutionResult:
        self.logs = []
        try:
            feedback = self._execute_send_six(client, record)
            return SixAxisExecutionResult(
                ok=True,
                query_key=record.query_key,
                func_num=record.func_num,
                feedback=[float(value) for value in feedback],
                logs=list(self.logs),
            )
        except Exception as exc:
            return SixAxisExecutionResult(
                ok=False,
                query_key=record.query_key,
                func_num=record.func_num,
                logs=list(self.logs),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _append_log(self, category: str, action: str, result: str, detail: str, extra: dict[str, Any] | None = None) -> None:
        self.logs.append(
            {
                "category": category,
                "action": action,
                "result": result,
                "detail": detail,
                "extra": extra or {},
            }
        )

    @staticmethod
    def _fmt(value: object) -> str:
        try:
            return f"{float(value):.3f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)

    def _describe_six_motion_options(self, six_cmd: SixAxisCommand) -> str:
        stop_cmd = int(six_cmd.stop_cmd)
        stop_desc = STOP_CMD_LABELS.get(stop_cmd, f"stop_cmd={stop_cmd}")
        detail = (
            f"stop_cmd={stop_cmd}({stop_desc}) | "
            f"fuzzy_pos={int(six_cmd.fuzzy_pos)} "
            f"fuzzy_spd={int(six_cmd.fuzzy_spd)} "
            f"fuzzy_acc={int(six_cmd.fuzzy_acc)} "
            f"fuzzy_dec={int(six_cmd.fuzzy_dec)}"
        )
        if six_cmd.func_num == 108:
            move_type = int(six_cmd.move_type)
            move_desc = MOVE_TYPE_LABELS.get(move_type, f"move_type={move_type}")
            detail += f" | move_type={move_type}({move_desc})"
        return detail
