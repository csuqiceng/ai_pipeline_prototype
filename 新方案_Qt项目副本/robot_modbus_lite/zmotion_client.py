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
    pass


_sdk_module_cache: dict[tuple[Path, Path], Any] = {}


def _get_or_load_sdk_module(wrapper_path: Path, dll_dir: Path) -> Any:
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
    def __init__(self, host: str, *, repo_root: str | Path) -> None:
        self.host = host
        self.repo_root = Path(repo_root)
        self._sdk = self._load_sdk_wrapper()
        self._device = self._sdk.ZAUXDLL()
        self._lock = threading.Lock()
        self.connected = False

    def connect(self) -> None:
        with self._lock:
            ret = self._device.ZAux_OpenEth(self.host)
            self._ensure_ok(ret, f"connect({self.host})")
            self.connected = True

    def disconnect(self) -> None:
        with self._lock:
            if not self.connected:
                return
            ret = self._device.ZAux_Close()
            self._ensure_ok(ret, "disconnect")
            self.connected = False

    def write_vr(self, request: VrWriteRequest) -> None:
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
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Direct_GetVrf(request.start_vr, request.count)
            self._ensure_ok(ret, "ZAux_Direct_GetVrf")
            return [float(item) for item in values]

    # ── V3.0 Modbus TCP 方法 ──────────────────────────────────────

    def write_modbus_float(self, request: VrWriteRequest) -> None:
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
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Modbus_Get4x_Float(
                request.start_vr, request.count,
            )
            self._ensure_ok(ret, "ZAux_Modbus_Get4x_Float")
            return [float(item) for item in values]

    def write_modbus_long(self, request: VrWriteRequest) -> None:
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
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Modbus_Get4x_Long(
                request.start_vr, request.count,
            )
            self._ensure_ok(ret, "ZAux_Modbus_Get4x_Long")
            return [int(item) for item in values]

    def write_modbus_bit(self, start: int, values: list[int]) -> None:
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret = self._device.ZAux_Modbus_Set0x(start, len(values), values)
            self._ensure_ok(ret, "ZAux_Modbus_Set0x")

    def read_modbus_bit(self, start: int, count: int) -> list[int]:
        with self._lock:
            if not self.connected:
                raise ZMotionClientError("控制器未连接。")
            ret, values = self._device.ZAux_Modbus_Get0x(start, count)
            self._ensure_ok(ret, "ZAux_Modbus_Get0x")
            return [int(item) for item in values]

    def _ensure_ok(self, ret: int, action: str) -> None:
        if ret != 0:
            raise ZMotionClientError(f"{action} failed with code {ret}")

    def _load_sdk_wrapper(self) -> Any:
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
