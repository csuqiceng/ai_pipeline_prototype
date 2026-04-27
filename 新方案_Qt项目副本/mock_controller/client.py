from __future__ import annotations

import time
from typing import Any

from .controller import MockController


class MockZMotionVrClient:
    def __init__(self, host: str, **kw: Any) -> None:
        self.host = host
        self.connected = False
        self._connect_delay = float(kw.get("connect_delay", 0.05))
        axis_ranges = kw.get("axis_ranges")
        ctrl_kwargs: dict[str, Any] = {}
        if axis_ranges:
            ctrl_kwargs["x_range"] = axis_ranges.get("x")
            ctrl_kwargs["y_range"] = axis_ranges.get("y")
            ctrl_kwargs["z_range"] = axis_ranges.get("z")
        self._ctrl = MockController(**ctrl_kwargs)

    def connect(self) -> None:
        time.sleep(self._connect_delay)
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def write_vr(self, request: Any) -> None:
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_vr(request.start_vr, list(request.values))

    def read_vr(self, request: Any) -> list[float]:
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_vr(request.start_vr, request.count)

    # ── Modbus TCP 方法 ─────────────────────────────────────────────

    def write_modbus_float(self, request: Any) -> None:
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_modbus_float(request.start_vr, list(request.values))

    def read_modbus_float(self, request: Any) -> list[float]:
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_modbus_float(request.start_vr, request.count)

    def write_modbus_bit(self, start: int, values: list[int]) -> None:
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_modbus_bit(start, values)

    def read_modbus_bit(self, start: int, count: int) -> list[int]:
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_modbus_bit(start, count)

    def snapshot(self) -> dict[str, float]:
        return self._ctrl.snapshot()

    def set_on_command(self, callback: Any) -> None:
        self._ctrl.set_on_command(callback)
