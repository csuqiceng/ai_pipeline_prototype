from __future__ import annotations

import time
from typing import Any

from .controller import MockController


_CONTROLLER_INSTANCE: MockController | None = None


def _get_controller() -> MockController:
    global _CONTROLLER_INSTANCE
    if _CONTROLLER_INSTANCE is None:
        _CONTROLLER_INSTANCE = MockController()
    return _CONTROLLER_INSTANCE


class MockZMotionVrClient:
    def __init__(self, host: str, **kw: Any) -> None:
        self.host = host
        self._ctrl = _get_controller()
        self.connected = False
        self._connect_delay = float(kw.get("connect_delay", 0.05))
        axis_ranges = kw.get("axis_ranges")
        if axis_ranges:
            self._ctrl.set_axis_ranges(
                x_range=axis_ranges.get("x"),
                y_range=axis_ranges.get("y"),
                z_range=axis_ranges.get("z"),
            )

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

    def snapshot(self) -> dict[str, float]:
        return self._ctrl.snapshot()

    def set_on_command(self, callback: Any) -> None:
        self._ctrl.set_on_command(callback)
