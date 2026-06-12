"""模拟控制器客户端，提供与真实控制器客户端一致的读写接口。"""

from __future__ import annotations

import time
from typing import Any

from .controller import MockController


class MockZMotionVrClient:
    """模拟控制器客户端，复用真实客户端的读写接口形状。"""
    def __init__(self, host: str, **kw: Any) -> None:
        """初始化对象。"""
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
        self._tables: dict[int, float] = {}

    def connect(self) -> None:
        """连接相关数据。"""
        time.sleep(self._connect_delay)
        self.connected = True

    def disconnect(self) -> None:
        """断开连接相关数据。"""
        self.connected = False

    def write_vr(self, request: Any) -> None:
        """写入寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_vr(request.start_vr, list(request.values))

    def read_vr(self, request: Any) -> list[float]:
        """读取寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_vr(request.start_vr, request.count)

    # ── 通信寄存器方法 ─────────────────────────────────────────────

    def write_modbus_float(self, request: Any) -> None:
        """写入通信寄存器浮点寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_modbus_float(request.start_vr, list(request.values))

    def read_modbus_float(self, request: Any) -> list[float]:
        """读取通信寄存器浮点寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_modbus_float(request.start_vr, request.count)

    def write_modbus_long(self, request: Any) -> None:
        """写入通信寄存器长整型寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_modbus_long(request.start_vr, list(request.values))

    def read_modbus_long(self, request: Any) -> list[int]:
        """读取通信寄存器长整型寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_modbus_long(request.start_vr, request.count)

    def write_modbus_bit(self, start: int, values: list[int]) -> None:
        """写入通信寄存器位寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._ctrl.write_modbus_bit(start, values)

    def read_modbus_bit(self, start: int, count: int) -> list[int]:
        """读取通信寄存器位寄存器。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return self._ctrl.read_modbus_bit(start, count)

    def snapshot(self) -> dict[str, float]:
        """生成快照相关数据。"""
        return self._ctrl.snapshot()

    def set_on_command(self, callback: Any) -> None:
        """设置命令。"""
        self._ctrl.set_on_command(callback)

    def set_table(self, index: int, value: float) -> None:
        """写入模拟 TABLE 值，供 FRAME_TRANS2 预演使用。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        self._tables[int(index)] = float(value)

    def get_table(self, index: int) -> float:
        """读取模拟 TABLE 值，供 FRAME_TRANS2 预演使用。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        return float(self._tables.get(int(index), 0.0))

    def frame_trans2(self, axis_list: tuple[int, ...], table_in: int, table_out: int, mode: int) -> None:
        """模拟 ZAux_Direct_FrameTrans2 逆解接口。"""
        if not self.connected:
            raise RuntimeError("控制器未连接。")
        if int(mode) != 2:
            raise RuntimeError(f"模拟控制器仅支持 FrameTrans2 mode=2，当前 mode={mode}")
        x = float(self._tables.get(int(table_in), 0.0))
        y = float(self._tables.get(int(table_in) + 1, 0.0))
        z = float(self._tables.get(int(table_in) + 2, 0.0))
        rx = float(self._tables.get(int(table_in) + 3, 0.0))
        ry = float(self._tables.get(int(table_in) + 4, 0.0))
        rz = float(self._tables.get(int(table_in) + 5, 0.0))
        fstatus = float(self._tables.get(int(table_in) + 6, 0.0))
        joints = (
            max(-180.0, min(180.0, x / 10.0)),
            max(-90.0, min(90.0, z / 20.0 - 45.0)),
            max(-120.0, min(120.0, y / 10.0)),
            max(-180.0, min(180.0, 30.0 + fstatus)),
            max(-120.0, min(120.0, ry + rx * 0.1)),
            max(-360.0, min(360.0, rz)),
        )
        for offset, value in enumerate(joints):
            self._tables[int(table_out) + offset] = float(value)
