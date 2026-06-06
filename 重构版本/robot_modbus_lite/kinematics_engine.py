"""Kinematics engine interfaces for L2 motion planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class InverseKinematicsResult:
    """Inverse kinematics result for a pose and FSTATUS candidate."""

    success: bool
    joints: tuple[float, ...]
    fstatus: int
    message: str = ""


@runtime_checkable
class KinematicsEngine(Protocol):
    """Protocol implemented by controller-backed kinematics adapters."""

    def inverse(self, pose: tuple[float, float, float, float, float, float], fstatus: int) -> InverseKinematicsResult:
        """Return inverse kinematics for a six-dimensional pose."""
        ...


@runtime_checkable
class FrameTrans2Transport(Protocol):
    """Minimal transport needed to invoke ZMotion FRAME_TRANS2."""

    def set_table(self, index: int, value: float) -> None:
        """Set one TABLE value."""
        ...

    def get_table(self, index: int) -> float:
        """Read one TABLE value."""
        ...

    def execute(self, command: str) -> None:
        """Execute one controller command string."""
        ...

    def frame_trans2(self, axis_list: tuple[int, ...], table_in: int, table_out: int, mode: int) -> None:
        """Invoke controller SDK ZAux_Direct_FrameTrans2 directly when available."""
        ...


class UnavailableKinematicsEngine:
    """Explicit placeholder until controller FRAME_TRANS2 is wired."""

    def inverse(self, pose: tuple[float, float, float, float, float, float], fstatus: int) -> InverseKinematicsResult:
        return InverseKinematicsResult(False, (), fstatus, "未配置控制器 FRAME_TRANS2 逆解接口")


class FrameTrans2KinematicsEngine:
    """Kinematics engine backed by controller FRAME_TRANS2."""

    def __init__(
        self,
        transport: FrameTrans2Transport,
        *,
        input_base: int = 550,
        output_base: int = 560,
        axis_list: tuple[int, ...] = (6, 7, 8, 9, 10, 11),
    ) -> None:
        self.transport = transport
        self.input_base = int(input_base)
        self.output_base = int(output_base)
        self.axis_list = tuple(int(axis) for axis in axis_list)

    def inverse(self, pose: tuple[float, float, float, float, float, float], fstatus: int) -> InverseKinematicsResult:
        try:
            values = self._six_values(pose)
            for offset, value in enumerate(values):
                self.transport.set_table(self.input_base + offset, value)
            self.transport.set_table(self.input_base + 6, float(fstatus))
            frame_trans2 = getattr(self.transport, "frame_trans2", None)
            if callable(frame_trans2):
                frame_trans2(self.axis_list, self.input_base, self.output_base, 2)
            else:
                self.transport.execute(f"FRAME_TRANS2({self.input_base},{self.output_base},2)")
            joints = tuple(float(self.transport.get_table(self.output_base + offset)) for offset in range(6))
            return InverseKinematicsResult(True, joints, int(fstatus))
        except Exception as exc:
            return InverseKinematicsResult(False, (), int(fstatus), str(exc))

    @staticmethod
    def _six_values(values: tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
        padded = [float(value) for value in list(values)[:6]]
        padded += [0.0] * max(0, 6 - len(padded))
        return tuple(padded[:6])  # type: ignore[return-value]
