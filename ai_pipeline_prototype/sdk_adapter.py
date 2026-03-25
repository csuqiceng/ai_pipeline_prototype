from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MotionSDKError(RuntimeError):
    pass


@dataclass
class MotionSDKStatus:
    connected: bool
    servo_enabled: bool
    alarm_active: bool
    emergency_stopped: bool
    current_pose: list[float]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MotionSDKConfig:
    host: str = "127.0.0.1"
    port: int = 5000
    controller_id: str = "ZMC406"
    default_speed: int = 80
    connection_type: str = "eth"
    com_port: int = 0
    pci_card: int = 0
    connect_timeout_ms: int = 3000
    command_timeout_ms: int = 2000
    axes: tuple[int, int, int] = (0, 1, 2)
    homing_mode: int = 0
    stop_mode: int = 3
    gripper_output: int = 0
    gripper_close_value: int = 1
    gripper_open_value: int = 0
    prefer_bus_home: bool = False
    force_mock: bool = False


class _MockZMotionBackend:
    def __init__(self, config: MotionSDKConfig) -> None:
        self.config = config
        self.connected = False
        self.positions = [0.0 for _ in config.axes]
        self.axis_enabled = [False for _ in config.axes]
        self.axis_idle = [1 for _ in config.axes]
        self.axis_status = [0 for _ in config.axes]
        self.axis_stop_reason = [0 for _ in config.axes]
        self.gripper_closed = False

    def connect(self) -> None:
        self.connected = True
        self.axis_enabled = [True for _ in self.config.axes]

    def disconnect(self) -> None:
        self.connected = False
        self.axis_enabled = [False for _ in self.config.axes]

    def set_timeout(self, timeout_ms: int) -> None:
        _ = timeout_ms

    def move_axes_abs(self, axes: list[int], positions: list[float], speed: int) -> None:
        _ = speed
        for axis, position in zip(axes, positions):
            idx = self.config.axes.index(axis)
            self.positions[idx] = position
            self.axis_idle[idx] = 1
            self.axis_stop_reason[idx] = 0

    def set_gripper(self, closed: bool) -> None:
        self.gripper_closed = closed

    def home(self) -> None:
        self.positions = [0.0 for _ in self.config.axes]
        self.axis_idle = [1 for _ in self.config.axes]
        self.axis_stop_reason = [0 for _ in self.config.axes]

    def stop(self) -> None:
        self.axis_idle = [1 for _ in self.config.axes]
        self.axis_stop_reason = [self.config.stop_mode for _ in self.config.axes]

    def read_status(self) -> MotionSDKStatus:
        raw = {
            "backend": "mock",
            "axis_enabled": list(self.axis_enabled),
            "axis_idle": list(self.axis_idle),
            "axis_status": list(self.axis_status),
            "axis_stop_reason": list(self.axis_stop_reason),
            "gripper_closed": self.gripper_closed,
        }
        return MotionSDKStatus(
            connected=self.connected,
            servo_enabled=all(self.axis_enabled) if self.axis_enabled else False,
            alarm_active=any(reason not in (0, self.config.stop_mode) for reason in self.axis_stop_reason),
            emergency_stopped=any(reason == self.config.stop_mode for reason in self.axis_stop_reason),
            current_pose=list(self.positions[:3]),
            raw=raw,
        )


class _VendorZMotionBackend:
    def __init__(self, config: MotionSDKConfig) -> None:
        self.config = config
        self._sdk = self._load_sdk_wrapper()
        self._device = self._sdk.ZAUXDLL()
        self.connected = False
        self.gripper_closed = False

    def connect(self) -> None:
        if self.config.connection_type == "eth":
            ret = self._device.ZAux_OpenEth(self.config.host)
        elif self.config.connection_type == "com":
            ret = self._device.ZAux_OpenCom(self.config.com_port)
        elif self.config.connection_type == "pci":
            ret = self._device.ZAux_OpenPci(self.config.pci_card)
        elif self.config.connection_type == "fast":
            ret = self._device.ZAux_FastOpen(2, self.config.host, self.config.connect_timeout_ms)
        else:
            raise MotionSDKError(f"不支持的连接方式: {self.config.connection_type}")
        self._ensure_ok(ret, "connect")
        self.connected = True
        self.set_timeout(self.config.command_timeout_ms)

    def disconnect(self) -> None:
        ret = self._device.ZAux_Close()
        self._ensure_ok(ret, "disconnect")
        self.connected = False

    def set_timeout(self, timeout_ms: int) -> None:
        ret = self._device.ZAux_SetTimeOut(timeout_ms)
        self._ensure_ok(ret, "set_timeout")

    def move_axes_abs(self, axes: list[int], positions: list[float], speed: int) -> None:
        if not axes:
            raise MotionSDKError("未配置运动轴，不能执行 move_axes_abs")
        self._set_axis_speed(speed)
        if len(axes) == 1:
            ret = self._device.ZAux_Direct_Single_MoveAbs(axes[0], positions[0])
        else:
            ret = self._device.ZAux_Direct_MultiMoveAbs(len(axes), len(axes), axes, positions)
        self._ensure_ok(ret, "move_axes_abs")

    def set_gripper(self, closed: bool) -> None:
        io_value = self.config.gripper_close_value if closed else self.config.gripper_open_value
        ret = self._device.ZAux_Direct_SetOp(self.config.gripper_output, io_value)
        self._ensure_ok(ret, "set_gripper")
        self.gripper_closed = closed

    def home(self) -> None:
        for axis in self.config.axes:
            if self.config.prefer_bus_home:
                ret = self._device.ZAux_BusCmd_Datum(axis, self.config.homing_mode)
            else:
                ret = self._device.ZAux_Direct_Single_Datum(axis, self.config.homing_mode)
            self._ensure_ok(ret, f"home(axis={axis})")

    def stop(self) -> None:
        for axis in self.config.axes:
            ret = self._device.ZAux_Direct_Single_Cancel(axis, self.config.stop_mode)
            self._ensure_ok(ret, f"stop(axis={axis})")

    def read_status(self) -> MotionSDKStatus:
        pose = self._get_positions()
        axis_enabled = [self._read_int("ZAux_Direct_GetAxisEnable", axis) for axis in self.config.axes]
        axis_idle = [self._read_int("ZAux_Direct_GetIfIdle", axis) for axis in self.config.axes]
        axis_status = [self._read_int("ZAux_Direct_GetAxisStatus", axis) for axis in self.config.axes]
        axis_stop_reason = [self._read_int("ZAux_Direct_GetAxisStopReason", axis) for axis in self.config.axes]
        raw = {
            "backend": "vendor",
            "axis_enabled": axis_enabled,
            "axis_idle": axis_idle,
            "axis_status": axis_status,
            "axis_stop_reason": axis_stop_reason,
            "gripper_closed": self.gripper_closed,
        }
        return MotionSDKStatus(
            connected=self.connected,
            servo_enabled=all(value != 0 for value in axis_enabled) if axis_enabled else False,
            alarm_active=any(value != 0 for value in axis_status) or any(
                reason not in (0, self.config.stop_mode) for reason in axis_stop_reason
            ),
            emergency_stopped=any(reason == self.config.stop_mode for reason in axis_stop_reason),
            current_pose=pose,
            raw=raw,
        )

    def _get_positions(self) -> list[float]:
        try:
            ret, values = self._device.ZAux_GetModbusDpos(len(self.config.axes))
            self._ensure_ok(ret, "get_positions")
            pose = [float(values[index]) for index in range(min(3, len(self.config.axes)))]
            if len(pose) < 3:
                pose.extend([0.0] * (3 - len(pose)))
            return pose
        except Exception:
            pose = []
            for axis in self.config.axes[:3]:
                pose.append(self._read_float("ZAux_Direct_GetDpos", axis))
            while len(pose) < 3:
                pose.append(0.0)
            return pose

    def _set_axis_speed(self, speed: int) -> None:
        for axis in self.config.axes:
            try:
                ret = self._device.ZAux_Direct_SetSpeed(axis, float(speed))
            except AttributeError:
                ret = self._device.ZAux_Direct_SetLspeed(axis, float(speed))
            self._ensure_ok(ret, f"set_speed(axis={axis})")

    def _read_int(self, method_name: str, axis: int) -> int:
        method = getattr(self._device, method_name)
        ret, value = method(axis)
        self._ensure_ok(ret, f"{method_name}(axis={axis})")
        return int(getattr(value, "value", value))

    def _read_float(self, method_name: str, axis: int) -> float:
        method = getattr(self._device, method_name)
        ret, value = method(axis)
        self._ensure_ok(ret, f"{method_name}(axis={axis})")
        return float(getattr(value, "value", value))

    def _ensure_ok(self, ret: int, action: str) -> None:
        if ret != 0:
            raise MotionSDKError(f"{action} failed with code {ret}")

    def _load_sdk_wrapper(self) -> Any:
        repo_root = Path(__file__).resolve().parents[1]
        wrapper_path = repo_root / "Windows Python（64位）" / "Windows Python（64位）" / "zmcdll" / "zauxdllPython.py"
        dll_dir = repo_root / "Windows Python（64位）" / "Windows Python（64位）" / "dll库文件"
        if not wrapper_path.exists():
            raise MotionSDKError(f"未找到 SDK Python 示例: {wrapper_path}")
        if not dll_dir.exists():
            raise MotionSDKError(f"未找到 SDK DLL 目录: {dll_dir}")

        spec = importlib.util.spec_from_file_location("vendor_zauxdllPython", wrapper_path)
        if spec is None or spec.loader is None:
            raise MotionSDKError("无法加载 SDK Python 示例模块")
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


class MotionSDKClient:
    """Motion SDK 客户端封装。

    优先接入仓库内置的 `zmcdll` Python 示例与 DLL；若当前环境无法加载 DLL，
    会自动回退到 mock 后端，保证上层服务和 demo 仍可运行。

    基于 `zauxdllPython.py`，当前已确认并接入的关键 SDK 函数包括：
    - 连接类: `ZAux_OpenEth`, `ZAux_OpenCom`, `ZAux_OpenPci`, `ZAux_FastOpen`, `ZAux_Close`
    - 通用控制: `ZAux_SetTimeOut`, `ZAux_Direct_SetOp`
    - 单轴运动: `ZAux_Direct_Single_MoveAbs`, `ZAux_Direct_Single_Cancel`, `ZAux_Direct_Single_Datum`
    - 多轴运动: `ZAux_Direct_MultiMoveAbs`
    - 状态读取: `ZAux_GetModbusDpos`, `ZAux_Direct_GetDpos`, `ZAux_Direct_GetAxisEnable`,
      `ZAux_Direct_GetIfIdle`, `ZAux_Direct_GetAxisStatus`, `ZAux_Direct_GetAxisStopReason`
    - 总线回零: `ZAux_BusCmd_Datum`
    """

    def __init__(self, config: MotionSDKConfig) -> None:
        self.config = config
        self.connected = False
        self.backend_name = "mock"
        self.last_error: str | None = None
        self._backend = self._build_backend()

    def connect(self) -> str:
        try:
            self._backend.connect()
            self.last_error = None
        except MotionSDKError as exc:
            if self.backend_name == "vendor" and not self.config.force_mock:
                self.last_error = str(exc)
                self._backend = _MockZMotionBackend(self.config)
                self.backend_name = "mock"
                self._backend.connect()
            else:
                raise
        self.connected = True
        return self._format_command(
            "connect",
            controller=self.config.controller_id,
            backend=self.backend_name,
            connection=self.config.connection_type,
            host=self.config.host,
            port=self.config.port,
            fallback_error=self.last_error,
        )

    def disconnect(self) -> str:
        self._backend.disconnect()
        self.connected = False
        return self._format_command("disconnect", controller=self.config.controller_id, backend=self.backend_name)

    def move_to_pose(self, x: float, y: float, z: float, speed: int) -> str:
        axes = list(self.config.axes[:3])
        positions = [x, y, z][: len(axes)]
        self._backend.move_axes_abs(axes, positions, speed)
        return self._format_command(
            "move_to_pose",
            backend=self.backend_name,
            axes=axes,
            x=x,
            y=y,
            z=z,
            speed=speed,
        )

    def set_gripper(self, closed: bool) -> str:
        self._backend.set_gripper(closed)
        state = "close" if closed else "open"
        return self._format_command(
            "set_gripper",
            backend=self.backend_name,
            state=state,
            output=self.config.gripper_output,
        )

    def home(self) -> str:
        self._backend.home()
        return self._format_command(
            "home_axes",
            backend=self.backend_name,
            axes=list(self.config.axes),
            homing_mode=self.config.homing_mode,
            prefer_bus_home=self.config.prefer_bus_home,
        )

    def stop(self) -> str:
        self._backend.stop()
        return self._format_command(
            "emergency_stop",
            backend=self.backend_name,
            axes=list(self.config.axes),
            stop_mode=self.config.stop_mode,
        )

    def get_status(self) -> MotionSDKStatus:
        status = self._backend.read_status()
        self.connected = status.connected
        if self.last_error:
            status.raw["last_error"] = self.last_error
        return status

    def list_supported_functions(self) -> list[str]:
        return [
            "ZAux_OpenEth",
            "ZAux_OpenCom",
            "ZAux_OpenPci",
            "ZAux_FastOpen",
            "ZAux_Close",
            "ZAux_SetTimeOut",
            "ZAux_Direct_SetOp",
            "ZAux_Direct_Single_MoveAbs",
            "ZAux_Direct_MultiMoveAbs",
            "ZAux_Direct_Single_Datum",
            "ZAux_BusCmd_Datum",
            "ZAux_Direct_Single_Cancel",
            "ZAux_GetModbusDpos",
            "ZAux_Direct_GetDpos",
            "ZAux_Direct_GetAxisEnable",
            "ZAux_Direct_GetIfIdle",
            "ZAux_Direct_GetAxisStatus",
            "ZAux_Direct_GetAxisStopReason",
            "ZAux_Direct_GetHomeStatus",
        ]

    def _build_backend(self) -> _MockZMotionBackend | _VendorZMotionBackend:
        if self.config.force_mock:
            return _MockZMotionBackend(self.config)
        try:
            backend = _VendorZMotionBackend(self.config)
            self.backend_name = "vendor"
            return backend
        except Exception:
            self.backend_name = "mock"
            return _MockZMotionBackend(self.config)

    def _format_command(self, action: str, **fields: Any) -> str:
        joined = ", ".join(f"{key}={value}" for key, value in fields.items())
        return f"{action}({joined})"
