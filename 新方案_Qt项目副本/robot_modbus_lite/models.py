from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable


FUNC_NAME_MAP = {
    104: "FUNC104_STOP",
    106: "FUNC106_JOINT_JOG",
    107: "FUNC107_VIRTUAL_JOG",
    108: "FUNC108_LINEAR_MOVE",
}


@dataclass(frozen=True)
class QueryRecord:
    query_key: str
    func_num: int
    params: dict[str, float | int]
    keywords: str = ""
    description: str = ""
    safety_level: int = 5

    def payload(self) -> list[float]:
        return [float(self.func_num)]

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def function_id(self) -> int:
        return self.func_num

    @property
    def function_name(self) -> str:
        return FUNC_NAME_MAP.get(self.func_num, f"FUNC{self.func_num}")

    @property
    def template_type(self) -> str:
        return "parametric"

    @property
    def registers(self) -> tuple[float, float, float, float, float, float, float]:
        if self.func_num == 108:
            return (
                self.float_param("target_x"),
                self.float_param("target_y"),
                self.float_param("target_z"),
                self.float_param("target_rx"),
                self.float_param("target_ry"),
                self.float_param("target_rz"),
                self.float_param("spd"),
            )
        if self.func_num in (106, 107):
            return (
                self.float_param("axis_no"),
                self.float_param("pos_val"),
                self.float_param("spd"),
                self.float_param("acc_v"),
                self.float_param("dec_v"),
                self.float_param("fuzzy_pos"),
                self.float_param("stop_cmd"),
            )
        return (
            self.float_param("stop_mode"),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )

    def float_param(self, key: str, default: float = 0.0) -> float:
        return float(self.params.get(key, default))

    def int_param(self, key: str, default: int = 0) -> int:
        return int(float(self.params.get(key, default)))

    def pose_tuple(self) -> tuple[float, float, float, float, float, float] | None:
        if self.func_num != 108:
            return None
        return (
            self.float_param("target_x"),
            self.float_param("target_y"),
            self.float_param("target_z"),
            self.float_param("target_rx"),
            self.float_param("target_ry"),
            self.float_param("target_rz"),
        )

    def speed_value(self) -> float:
        return self.float_param("spd")

    def acc_value(self) -> float:
        return self.float_param("acc_v")

    def summary_text(self) -> str:
        if self.func_num == 104:
            return f"stop_mode={self.int_param('stop_mode')}"
        if self.func_num in (106, 107):
            axis_label = "关节轴" if self.func_num == 106 else "虚拟轴"
            return (
                f"{axis_label} {self.int_param('axis_no')}  "
                f"目标 {self.float_param('pos_val')}  "
                f"速度 {self.float_param('spd')}"
            )
        pose = self.pose_tuple()
        if pose is None:
            return "-"
        x, y, z, _, _, _ = pose
        return f"X {x}  Y {y}  Z {z}"


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
    step_delay_ms: int = 1000

    def to_dict(self) -> dict:
        return asdict(self)



# ── 六轴机械手数据模型 (VPLC516E) ──────────────────────────────────


@dataclass(frozen=True)
class SixAxisCommand:
    """六轴机械手协议命令 (Func 104/106/107/108)"""
    func_num: int
    desc: str = ""
    # Func 104: 停止
    stop_mode: int = 0           # 0=急停 1=慢停
    # Func 106/107: 点动参数
    axis_no: int = 0
    pos_val: float = 0.0
    spd: float = 0.0
    acc_v: float = 0.0
    dec_v: float = 0.0
    fuzzy_pos: float = 0.0
    fuzzy_spd: float = 0.0
    fuzzy_acc: float = 0.0
    fuzzy_dec: float = 0.0
    stop_cmd: int = 0
    # Func 108: 直线插补/PTP
    target_x: float = 0.0
    target_y: float = 0.0
    target_z: float = 0.0
    target_rx: float = 0.0
    target_ry: float = 0.0
    target_rz: float = 0.0
    move_type: int = 0           # 0=直线插补 1=PTP
    # 特殊命令参数
    io_grip: int = 0
    io_door: int = 0
    ext_p1: float = 0.0

    def to_func_writes(self) -> list[VrWriteRequest]:
        if self.func_num == 104:
            return [
                VrWriteRequest(start_vr=0, values=(104.0,)),
                VrWriteRequest(start_vr=2, values=(float(self.stop_mode),)),
            ]
        if self.func_num in (106, 107):
            return [
                VrWriteRequest(start_vr=0, values=(float(self.func_num),)),
                VrWriteRequest(start_vr=2, values=(float(self.axis_no),)),
                VrWriteRequest(start_vr=4, values=(self.pos_val,)),
                VrWriteRequest(start_vr=6, values=(self.spd,)),
                VrWriteRequest(start_vr=8, values=(self.acc_v,)),
                VrWriteRequest(start_vr=10, values=(self.dec_v,)),
                VrWriteRequest(start_vr=12, values=(self.fuzzy_pos,)),
                VrWriteRequest(start_vr=14, values=(self.fuzzy_spd,)),
                VrWriteRequest(start_vr=16, values=(self.fuzzy_acc,)),
                VrWriteRequest(start_vr=18, values=(self.fuzzy_dec,)),
                VrWriteRequest(start_vr=20, values=(float(self.stop_cmd),)),
            ]
        if self.func_num == 108:
            return [
                VrWriteRequest(start_vr=0, values=(108.0,)),
                VrWriteRequest(start_vr=2, values=(self.target_x,)),
                VrWriteRequest(start_vr=4, values=(self.target_y,)),
                VrWriteRequest(start_vr=6, values=(self.target_z,)),
                VrWriteRequest(start_vr=8, values=(self.target_rx,)),
                VrWriteRequest(start_vr=10, values=(self.target_ry,)),
                VrWriteRequest(start_vr=12, values=(self.target_rz,)),
                VrWriteRequest(start_vr=14, values=(self.spd,)),
                VrWriteRequest(start_vr=16, values=(self.acc_v,)),
                VrWriteRequest(start_vr=18, values=(self.dec_v,)),
                VrWriteRequest(start_vr=20, values=(float(self.stop_cmd),)),
                VrWriteRequest(start_vr=22, values=(self.fuzzy_pos,)),
                VrWriteRequest(start_vr=24, values=(self.fuzzy_spd,)),
                VrWriteRequest(start_vr=26, values=(self.fuzzy_acc,)),
                VrWriteRequest(start_vr=28, values=(self.fuzzy_dec,)),
                VrWriteRequest(start_vr=30, values=(float(self.move_type),)),
            ]
        return []

    def to_trigger_write(self) -> VrWriteRequest:
        return VrWriteRequest(start_vr=32, values=(1.0,))

    def expected_echo_points(self) -> list[tuple[int, float]]:
        points: list[tuple[int, float]] = []
        for request in self.to_func_writes():
            for index, value in enumerate(request.values):
                points.append((request.start_vr + index * 2, float(value)))
        return points


@dataclass(frozen=True)
class SixAxisStatus:
    """六轴 IEEE(34) 状态解析 — 错误和报警是两个独立概念

    Bit2=完成(4), Bit3=错误(8), Bit6=报警(64)
    68=完成+报警(运动已结束,记录警告,不中断), 72=错误+报警(严重,raise)
    """
    raw: int

    @property
    def is_complete(self) -> bool:
        return (self.raw & 4) != 0

    @property
    def is_received(self) -> bool:
        return (self.raw & 1) != 0

    @property
    def is_executing(self) -> bool:
        return (self.raw & 2) != 0

    @property
    def has_error(self) -> bool:
        return (self.raw & 8) != 0

    @property
    def has_alarm(self) -> bool:
        return (self.raw & 64) != 0

    @property
    def can_send(self) -> bool:
        return self.raw in (0, 4)

    @classmethod
    def from_value(cls, val: float) -> "SixAxisStatus":
        return cls(raw=int(val))


@dataclass(frozen=True)
class SixAxisAlarmDetail:
    """六轴 IEEE(38) 报警详情 — 位组合"""
    radius: bool = False      # Bit0: 半径超限
    height: bool = False      # Bit1: 高度超限
    speed: bool = False       # Bit3: 速度超限
    accel: bool = False       # Bit4: 加速度超限
    decel: bool = False       # Bit5: 减速度超限
    ecat_exceeded: bool = False  # Bit6: ECAT通讯异常

    @classmethod
    def from_value(cls, val: float) -> "SixAxisAlarmDetail":
        raw = int(val)
        return cls(
            radius=(raw & 1) != 0,
            height=(raw & 2) != 0,
            speed=(raw & 8) != 0,
            accel=(raw & 16) != 0,
            decel=(raw & 32) != 0,
            ecat_exceeded=(raw & 64) != 0,
        )

    def __str__(self) -> str:
        parts = []
        if self.radius: parts.append("半径超限")
        if self.height: parts.append("高度超限")
        if self.speed: parts.append("速度超限")
        if self.accel: parts.append("加速度超限")
        if self.decel: parts.append("减速度超限")
        if self.ecat_exceeded: parts.append("ECAT异常")
        return "、".join(parts) if parts else "无报警详情"


@dataclass(frozen=True)
class SixAxisRealtimeData:
    """六轴实时坐标 — 分两组读取: IEEE(58,6) J1~J6 + IEEE(40,6) X/Y/Z/Rx/Ry/Rz"""
    j1: float = 0.0
    j2: float = 0.0
    j3: float = 0.0
    j4: float = 0.0
    j5: float = 0.0
    j6: float = 0.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0

    @classmethod
    def from_joints_and_xyz(cls, joint_values: list[float], xyz_values: list[float]) -> "SixAxisRealtimeData":
        j = list(joint_values[:6]) + [0.0] * max(0, 6 - len(joint_values))
        v = list(xyz_values[:6]) + [0.0] * max(0, 6 - len(xyz_values))
        return cls(
            j1=j[0], j2=j[1], j3=j[2], j4=j[3], j5=j[4], j6=j[5],
            x=v[0], y=v[1], z=v[2], rx=v[3], ry=v[4], rz=v[5],
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
