from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class QueryRecord:
    query_key: str
    function_id: int
    registers: tuple[float, float, float, float, float, float, float]
    function_name: str = "movabs"
    data_format: str = "IEE"
    template_type: str = "parametric"
    keywords: str = ""
    description: str = ""
    pos_id: int = 0
    device_id: int = 1
    acc_percent: float = 40.0
    safety_level: int = 5
    io_grip: int = 0
    io_door: int = 0
    ext_p1: float = 0.0
    ext_p2: float = 0.0

    def payload(self) -> list[float]:
        return [float(self.function_id), *self.registers]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_standard_params(self) -> dict[str, float | int]:
        return {
            "x": self.registers[0],
            "y": self.registers[1],
            "z": self.registers[2],
            "rx": self.registers[3],
            "ry": self.registers[4],
            "rz": self.registers[5],
            "speedPercent": self.registers[6],
            "accPercent": self.acc_percent,
            "deviceId": self.device_id,
            "posId": self.pos_id,
            "ioGrip": self.io_grip,
            "ioDoor": self.io_door,
            "extP1": self.ext_p1,
            "extP2": self.ext_p2,
        }


@dataclass(frozen=True)
class ParsedCommand:
    raw_text: str
    query_key: str


@dataclass(frozen=True)
class VrWriteRequest:
    start_vr: int
    values: tuple[float, ...]


@dataclass(frozen=True)
class VrReadRequest:
    start_vr: int
    count: int


@dataclass(frozen=True)
class FixedVrCommand:
    trigger_vr: int
    trigger_value: float
    payload_start_vr: int
    payload_values: tuple[float, ...]

    def preview_dict(self) -> dict:
        return {
            "trigger_vr": self.trigger_vr,
            "trigger_value": self.trigger_value,
            "payload_start_vr": self.payload_start_vr,
            "payload_values": list(self.payload_values),
        }


@dataclass(frozen=True)
class StandardProtocolCommand:
    task_id: int
    cmd: str
    code: int
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    speed_percent: int = 0
    acc_percent: int = 0
    pos_id: int = 0
    device_id: int = 0
    io_grip: int = 0
    io_door: int = 0
    ext_p1: float = 0.0
    ext_p2: float = 0.0
    safety_level: int = 5
    desc: str = ""

    def to_vr_values(self) -> tuple[float, ...]:
        return (
            float(self.code),
            float(self.task_id),
            float(self.x),
            float(self.y),
            float(self.z),
            float(self.rx),
            float(self.ry),
            float(self.rz),
            float(self.speed_percent),
            float(self.acc_percent),
            float(self.device_id),
            float(self.io_grip),
            float(self.io_door),
            float(self.ext_p1),
            float(self.ext_p2),
            float(self.safety_level),
        )

    def to_write_request(self, start_vr: int = 0) -> "VrWriteRequest":
        return VrWriteRequest(start_vr=start_vr, values=self.to_vr_values())

    def to_json_dict(self) -> dict:
        params: dict[str, float | int] = {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "rx": self.rx,
            "ry": self.ry,
            "rz": self.rz,
            "speedPercent": self.speed_percent,
            "accPercent": self.acc_percent,
        }

        if self.pos_id:
            params["posId"] = self.pos_id
        if self.device_id:
            params["deviceId"] = self.device_id
        if self.io_grip:
            params["ioGrip"] = self.io_grip
        if self.io_door:
            params["ioDoor"] = self.io_door
        if self.ext_p1:
            params["extP1"] = self.ext_p1
        if self.ext_p2:
            params["extP2"] = self.ext_p2

        return {
            "taskId": self.task_id,
            "cmd": self.cmd,
            "code": self.code,
            "params": params,
            "safetyLevel": self.safety_level,
            "desc": self.desc,
        }


@dataclass(frozen=True)
class StandardProtocolStatus:
    result: int
    status: int
    cur_x: float
    cur_y: float
    cur_z: float
    cur_rx: float
    cur_ry: float
    cur_rz: float
    alm_code: int
    io_stat: int

    @classmethod
    def from_vr_values(cls, values: list[float]) -> "StandardProtocolStatus":
        padded = list(values[:10]) + [0.0] * max(0, 10 - len(values))
        return cls(
            result=int(padded[0]),
            status=int(padded[1]),
            cur_x=float(padded[2]),
            cur_y=float(padded[3]),
            cur_z=float(padded[4]),
            cur_rx=float(padded[5]),
            cur_ry=float(padded[6]),
            cur_rz=float(padded[7]),
            alm_code=int(padded[8]),
            io_stat=int(padded[9]),
        )


@dataclass(frozen=True)
class StandardMirrorAck:
    mirror_values: tuple[float, ...]
    ack: int

    @classmethod
    def from_vr_values(cls, values: list[float], command_length: int = 16) -> "StandardMirrorAck":
        padded = list(values[: command_length + 1]) + [0.0] * max(0, command_length + 1 - len(values))
        return cls(
            mirror_values=tuple(float(v) for v in padded[:command_length]),
            ack=int(padded[command_length]),
        )


@dataclass(frozen=True)
class StandardRealtimeStatus:
    cur_x: float
    cur_y: float
    cur_z: float
    cur_rx: float
    cur_ry: float
    cur_rz: float
    claw_enable: int
    claw_brake: int
    servo_enable: int
    run_state: int
    alm_code: int
    io_stat: int
    echo_task_id: int
    echo_cmd_code: int
    motion_percent: float
    ack: int
    exec_trigger: int

    @classmethod
    def from_vr_values(cls, values: list[float]) -> "StandardRealtimeStatus":
        padded = list(values[:20]) + [0.0] * max(0, 20 - len(values))
        return cls(
            cur_x=float(padded[0]),
            cur_y=float(padded[1]),
            cur_z=float(padded[2]),
            cur_rx=float(padded[3]),
            cur_ry=float(padded[4]),
            cur_rz=float(padded[5]),
            claw_enable=int(padded[6]),
            claw_brake=int(padded[7]),
            servo_enable=int(padded[8]),
            run_state=int(padded[9]),
            alm_code=int(padded[10]),
            io_stat=int(padded[11]),
            echo_task_id=int(padded[12]),
            echo_cmd_code=int(padded[13]),
            motion_percent=float(padded[14]),
            ack=int(padded[15]),
            exec_trigger=int(padded[16]),
        )


@dataclass(frozen=True)
class FlowDefinition:
    name: str
    steps: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


# ── V3.0 Modbus TCP 数据模型 ──────────────────────────────────────

@dataclass(frozen=True)
class V30Command:
    """V3.0 IEEE/Modbus TCP 协议命令"""
    func_num: int
    desc: str = ""
    # Func 102 直线插补参数
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    speed: float = 0.0      # mm/s
    accel: float = 0.0      # mm/s²
    decel: float = 0.0      # mm/s²
    fuzzy: int = 0           # 0=精确 1=模糊
    fuzzy_step: float = 0.0  # 模糊最大步长 mm
    # Func -1/-2/-3 特殊命令参数
    io_grip: int = 0         # GRIP: 1=合 0=开
    io_door: int = 0         # DOOR: 1=开 0=关
    ext_p1: float = 0.0      # WAIT: 延时ms

    def to_func_writes(self) -> list[VrWriteRequest]:
        """返回V3.0协议需要的写入操作列表（函数号+参数）"""
        _IEEE_FUNC = 0
        _IEEE_P_X, _IEEE_P_Y, _IEEE_P_Z = 2, 4, 6
        _IEEE_P_RX, _IEEE_P_RY, _IEEE_P_RZ = 8, 10, 12
        _IEEE_P_SPEED, _IEEE_P_ACCEL, _IEEE_P_DECEL = 14, 16, 18
        _IEEE_P_FUZZY, _IEEE_P_FUZZY_STEP = 20, 22
        param_map = {
            _IEEE_P_X: self.x, _IEEE_P_Y: self.y, _IEEE_P_Z: self.z,
            _IEEE_P_RX: self.rx, _IEEE_P_RY: self.ry, _IEEE_P_RZ: self.rz,
            _IEEE_P_SPEED: self.speed, _IEEE_P_ACCEL: self.accel, _IEEE_P_DECEL: self.decel,
            _IEEE_P_FUZZY: float(self.fuzzy), _IEEE_P_FUZZY_STEP: self.fuzzy_step,
        }
        writes = [
            VrWriteRequest(start_vr=_IEEE_FUNC, values=(float(self.func_num),)),
        ]
        for addr, val in sorted(param_map.items()):
            writes.append(VrWriteRequest(start_vr=addr, values=(val,)))
        return writes

    def to_trigger_write(self) -> VrWriteRequest:
        return VrWriteRequest(start_vr=32, values=(1.0,))

    def to_json_dict(self) -> dict:
        return {
            "funcNum": self.func_num,
            "x": self.x, "y": self.y, "z": self.z,
            "rx": self.rx, "ry": self.ry, "rz": self.rz,
            "speed": self.speed, "accel": self.accel, "decel": self.decel,
            "fuzzy": self.fuzzy, "fuzzyStep": self.fuzzy_step,
            "desc": self.desc,
        }


@dataclass(frozen=True)
class V30Status:
    """V3.0 IEEE(34) 函数状态解析"""
    raw: int

    @property
    def is_idle(self) -> bool:
        return self.raw == 0

    @property
    def is_complete(self) -> bool:
        return (self.raw & 4) != 0

    @property
    def has_alarm(self) -> bool:
        return (self.raw & 8) != 0

    @property
    def is_executing(self) -> bool:
        return self.raw == 2

    @property
    def can_send(self) -> bool:
        return self.raw in (0, 1, 4)

    @classmethod
    def from_value(cls, val: float) -> "V30Status":
        return cls(raw=int(val))


@dataclass(frozen=True)
class V30RealtimeData:
    """V3.0 IEEE(1512~1522) 实时坐标"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0

    @classmethod
    def from_values(cls, values: list[float]) -> "V30RealtimeData":
        padded = list(values[:6]) + [0.0] * max(0, 6 - len(values))
        return cls(
            x=float(padded[0]), y=float(padded[1]), z=float(padded[2]),
            rx=float(padded[3]), ry=float(padded[4]), rz=float(padded[5]),
        )


@runtime_checkable
class ControllerClient(Protocol):
    connected: bool

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def write_vr(self, request: VrWriteRequest) -> None: ...
    def read_vr(self, request: VrReadRequest) -> list[float]: ...
    def write_modbus_float(self, request: VrWriteRequest) -> None: ...
    def read_modbus_float(self, request: VrReadRequest) -> list[float]: ...
    def write_modbus_bit(self, start: int, values: list[int]) -> None: ...
    def read_modbus_bit(self, start: int, count: int) -> list[int]: ...
