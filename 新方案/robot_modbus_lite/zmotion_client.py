from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
from typing import Any

from .models import ModbusWriteRequest


class ZMotionClientError(RuntimeError):
    pass


class ZMotionModbusClient:
    def __init__(self, host: str, *, repo_root: str | Path, start_register: int = 0) -> None:
        self.host = host
        self.repo_root = Path(repo_root)
        self.start_register = start_register
        self._sdk = self._load_sdk_wrapper()
        self._device = self._sdk.ZAUXDLL()
        self.connected = False

    def connect(self) -> None:
        ret = self._device.ZAux_OpenEth(self.host)
        self._ensure_ok(ret, f"connect({self.host})")
        self.connected = True

    def disconnect(self) -> None:
        if not self.connected:
            return
        ret = self._device.ZAux_Close()
        self._ensure_ok(ret, "disconnect")
        self.connected = False

    def write_floats(self, request: ModbusWriteRequest) -> None:
        if not self.connected:
            raise ZMotionClientError("控制器未连接。")
        ret = self._device.ZAux_Modbus_Set4x_Float(
            request.start_register,
            len(request.values),
            list(request.values),
        )
        self._ensure_ok(ret, "ZAux_Modbus_Set4x_Float")

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
        return module
