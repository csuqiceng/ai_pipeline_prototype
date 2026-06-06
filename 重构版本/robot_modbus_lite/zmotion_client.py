"""运动控制器厂商库加载和线程安全通信封装。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import threading
from pathlib import Path
from typing import Any

from .models import VrReadRequest, VrWriteRequest


class ZMotionClientError(RuntimeError):
    """控制器厂商库加载或通信失败异常。"""
    pass


_sdk_module_cache: dict[tuple[Path, Path], Any] = {}


def _get_or_load_sdk_module(wrapper_path: Path, dll_dir: Path) -> Any:
    """获取相关数据。"""
    cache_key = (wrapper_path, dll_dir)
    if cache_key in _sdk_module_cache:
        return _sdk_module_cache[cache_key]

    spec = importlib.util.spec_from_file_location("robot_modbus_vendor_zaux", wrapper_path)
    if spec is None or spec.loader is None:
        raise ZMotionClientError("无法创建 SDK 模块加载器。")

    module = importlib.util.module_from_spec(spec)
    old_cwd = Path.cwd()
    old_path = os.environ.get("PATH", "")
    try:
        os.chdir(dll_dir)
        os.environ["PATH"] = f"{dll_dir}{os.pathsep}{old_path}"
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
    finally:
        os.chdir(old_cwd)
        os.environ["PATH"] = old_path
    _sdk_module_cache[cache_key] = module
    return module


class ZMotionVrClient:
    """真实运动控制器客户端封装。"""
    def __init__(self, host: str, *, repo_root: str | Path) -> None:
        """初始化对象。"""
        self.host = host
        self.repo_root = Path(repo_root)
        self._sdk = self._load_sdk_wrapper()
        self._device = self._sdk.ZAUXDLL()
        self._lock = threading.Lock()
        self.connected = False

    def connect(self) -> None:
        """连接相关数据。"""
        with self._lock:
            ret = self._device.ZAux_OpenEth(self.host)
            self._ensure_ok(ret, f"connect({self.host})")
            self.connected = True

    def disconnect(self) -> None:
        """断开连接相关数据。"""
        with self._lock:
            if not self.connected:
                return
            ret = self._device.ZAux_Close()
            self._ensure_ok(ret, "disconnect")
            self.connected = False

    def write_vr(self, request: VrWriteRequest) -> None:
        """写入寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Direct_SetVrf(
                request.start_vr,
                len(request.values),
                list(request.values),
            )
            self._ensure_ok(ret, "ZAux_Direct_SetVrf")

    def read_vr(self, request: VrReadRequest) -> list[float]:
        """读取寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Direct_GetVrf(request.start_vr, request.count)
            self._ensure_ok(ret, "ZAux_Direct_GetVrf")
            return [float(item) for item in values]

    def set_table(self, index: int, value: float) -> None:
        """写入 ZMotion TABLE 值，供 FRAME_TRANS2 预演使用。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Direct_SetTable(int(index), float(value))
            self._ensure_ok(ret, "ZAux_Direct_SetTable")

    def get_table(self, index: int) -> float:
        """读取 ZMotion TABLE 值，供 FRAME_TRANS2 预演使用。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, value = self._device.ZAux_Direct_GetTable(int(index))
            self._ensure_ok(ret, "ZAux_Direct_GetTable")
            return float(value)

    def execute(self, command: str) -> None:
        """执行 ZMotion 控制器指令字符串。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Execute(str(command))
            self._ensure_ok(ret, "ZAux_Execute")

    def frame_trans2(self, axis_list: tuple[int, ...], table_in: int, table_out: int, mode: int) -> None:
        """调用 ZMotion SDK ZAux_Direct_FrameTrans2，用于控制器在线逆解。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            axes = [int(axis) for axis in axis_list]
            try:
                ret = self._device.ZAux_Direct_FrameTrans2(
                    axes,
                    int(table_in),
                    int(table_out),
                    int(mode),
                )
            except TypeError:
                ret = self._device.ZAux_Direct_FrameTrans2(
                    axes,
                    len(axes),
                    int(table_in),
                    int(table_out),
                    int(mode),
                )
            self._ensure_ok(ret, "ZAux_Direct_FrameTrans2")

    # ── 旧版通信寄存器方法 ────────────────────────────────────────

    def write_modbus_float(self, request: VrWriteRequest) -> None:
        """写入通信寄存器浮点寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Modbus_Set4x_Float(
                request.start_vr,
                len(request.values),
                list(request.values),
            )
            self._ensure_ok(ret, "ZAux_Modbus_Set4x_Float")

    def read_modbus_float(self, request: VrReadRequest) -> list[float]:
        """读取通信寄存器浮点寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Modbus_Get4x_Float(
                request.start_vr, request.count,
            )
            self._ensure_ok(ret, "ZAux_Modbus_Get4x_Float")
            return [float(item) for item in values]

    def write_modbus_long(self, request: VrWriteRequest) -> None:
        """写入通信寄存器长整型寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Modbus_Set4x_Long(
                request.start_vr,
                len(request.values),
                [int(item) for item in request.values],
            )
            self._ensure_ok(ret, "ZAux_Modbus_Set4x_Long")

    def read_modbus_long(self, request: VrReadRequest) -> list[int]:
        """读取通信寄存器长整型寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Modbus_Get4x_Long(
                request.start_vr, request.count,
            )
            self._ensure_ok(ret, "ZAux_Modbus_Get4x_Long")
            return [int(item) for item in values]

    def write_modbus_bit(self, start: int, values: list[int]) -> None:
        """写入通信寄存器位寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Modbus_Set0x(start, len(values), values)
            self._ensure_ok(ret, "ZAux_Modbus_Set0x")

    def read_modbus_bit(self, start: int, count: int) -> list[int]:
        """读取通信寄存器位寄存器。"""
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Modbus_Get0x(start, count)
            self._ensure_ok(ret, "ZAux_Modbus_Get0x")
            return [int(item) for item in values]

    def _ensure_ok(self, ret: int, action: str) -> None:
        """确保相关数据。"""
        if ret != 0:
            raise ZMotionClientError(f"{action} failed with code {ret}")

    def _load_sdk_wrapper(self) -> Any:
        """加载相关数据。"""
        wrapper_path = (
            self.repo_root
            / "Windows Python（64位）"
            / "Windows Python（64位）"
            / "zmcdll"
            / "zauxdllPython.py"
        )
        dll_dir = (
            self.repo_root
            / "Windows Python（64位）"
            / "Windows Python（64位）"
            / "dll库文件"
        )
        if not wrapper_path.exists():
            raise ZMotionClientError(f"未找到 SDK 包装文件: {wrapper_path}")
        if not dll_dir.exists():
            raise ZMotionClientError(f"未找到 DLL 目录: {dll_dir}")

        return _get_or_load_sdk_module(wrapper_path.resolve(), dll_dir.resolve())
