from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable


FUNC_NAME_MAP = {
    104: "FUNC104_STOP",
    106: "FUNC106_JOINT_JOG",
    107: "FUNC107_VIRTUAL_JOG",
    108: "FUNC108_LINEAR_MOVE",
    11: "FUNC11_MULTI_POINT_INTERP",
    109: "FUNC109_TIMER_CHECK",
    110: "FUNC110_DELAY",
    120: "FUNC120_IO",
}

SIX_MOTION_FUNCS: frozenset[int] = frozenset({11, 106, 107, 108, 109})
SIX_PROGRAM_FUNCS: frozenset[int] = frozenset({110, 120})
SIX_SYSTEM_FUNCS: frozenset[int] = frozenset({104})


def six_func_slot(func_num: int) -> str:
    if func_num in SIX_MOTION_FUNCS:
        return "motion"
    if func_num in SIX_PROGRAM_FUNCS:
        return "program"
    if func_num in SIX_SYSTEM_FUNCS:
        return "system"
    return "unknown"


@dataclass(frozen=True)
class QueryRecord:
    query_key: str
    func_num: int
    params: dict[str, Any]
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
                self.float_param("spd_pct"),
            )
        if self.func_num in (106, 107):
            return (
                self.float_param("axis_no"),
                self.float_param("pos_val"),
                self.float_param("spd_pct"),
                self.float_param("acc_pct"),
                self.float_param("dec_pct"),
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

    def spd_pct_value(self) -> float:
        return self.float_param("spd_pct")

    def acc_pct_value(self) -> float:
        return self.float_param("acc_pct")

    def dec_pct_value(self) -> float:
        return self.float_param("dec_pct")

    def summary_text(self) -> str:
        if self.func_num == 104:
            return (
                f"estop={self.int_param('estop_ctrl')} "
                f"pause={self.int_param('pause_ctrl')} "
                f"cancel={self.int_param('cancel_ctrl')} "
                f"reset={self.int_param('reset_ctrl')}"
            )
        if self.func_num in (106, 107):
            axis_label = "关节轴" if self.func_num == 106 else "虚拟轴"
            return (
                f"{axis_label} {self.int_param('axis_no')}  "
                f"目标 {self.float_param('pos_val')}  "
                f"速度百分比 {self.float_param('spd_pct')}"
            )
        pose = self.pose_tuple()
        if pose is None:
            if self.func_num == 11:
                return f"points={self.int_param('point_count')} spd_pct={self.float_param('spd_pct')}"
            if self.func_num == 109:
                return f"check={self.int_param('check_value')} delay={self.float_param('delay_sec')}s"
            if self.func_num == 110:
                return f"delay={self.float_param('delay_sec')}s"
            if self.func_num == 120:
                return f"io={self.int_param('io_no')} action={self.int_param('io_action')}"
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
    # Func 104: 系统控制字
    stop_mode: int = 0           # deprecated: 仅兼容旧 Func104 stop_mode 模板
    estop_ctrl: int = 0          # 0=无操作 1=急停 2=解除急停
    pause_ctrl: int = 0          # 0=无操作 1=暂停 2=继续
    cancel_ctrl: int = 0         # 0=无操作 1=取消/结束 2=保留
    reset_ctrl: int = 0          # 0=无操作 1=报警复位
    # Func 106/107: 点动参数
    axis_no: int = 0
    pos_val: float = 0.0
    spd_pct: float = 0.0
    acc_pct: float = 0.0
    dec_pct: float = 0.0
    fuzzy_pos: int = 0
    fuzzy_spd: int = 0
    fuzzy_acc: int = 0
    fuzzy_dec: int = 0
    stop_cmd: int = 0
    # Func 108: 直线插补/PTP
    target_x: float = 0.0
    target_y: float = 0.0
    target_z: float = 0.0
    target_rx: float = 0.0
    target_ry: float = 0.0
    target_rz: float = 0.0
    move_type: int = 0           # 0=直线插补 1=PTP
    point_count: int = 0
    interp_points: tuple[tuple[float, float, float, float, float, float], ...] = ()
    # deprecated: 旧本地负函数号命令参数，V4.3 由 Func120/Func110 替代
    io_grip: int = 0
    io_door: int = 0
    ext_p1: float = 0.0
    # Func 109/110/120
    check_value: int = 0
    delay_sec: float = 0.0
    io_no: int = 0
    io_action: int = 0

    def to_func_writes(self) -> list[VrWriteRequest]:
        if self.func_num == 104:
            return [
                VrWriteRequest(start_vr=0, values=(104.0,)),
                VrWriteRequest(start_vr=2, values=(float(self.estop_ctrl),)),
                VrWriteRequest(start_vr=4, values=(float(self.pause_ctrl),)),
                VrWriteRequest(start_vr=6, values=(float(self.cancel_ctrl),)),
                VrWriteRequest(start_vr=8, values=(float(self.reset_ctrl),)),
            ]
        if self.func_num in (106, 107):
            return [
                VrWriteRequest(start_vr=0, values=(float(self.func_num),)),
                VrWriteRequest(start_vr=2, values=(float(self.axis_no),)),
                VrWriteRequest(start_vr=4, values=(self.pos_val,)),
                VrWriteRequest(start_vr=6, values=(self.spd_pct,)),
                VrWriteRequest(start_vr=8, values=(self.acc_pct,)),
                VrWriteRequest(start_vr=10, values=(self.dec_pct,)),
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
                VrWriteRequest(start_vr=14, values=(self.spd_pct,)),
                VrWriteRequest(start_vr=16, values=(self.acc_pct,)),
                VrWriteRequest(start_vr=18, values=(self.dec_pct,)),
                VrWriteRequest(start_vr=20, values=(float(self.stop_cmd),)),
                VrWriteRequest(start_vr=22, values=(self.fuzzy_pos,)),
                VrWriteRequest(start_vr=24, values=(self.fuzzy_spd,)),
                VrWriteRequest(start_vr=26, values=(self.fuzzy_acc,)),
                VrWriteRequest(start_vr=28, values=(self.fuzzy_dec,)),
                VrWriteRequest(start_vr=30, values=(float(self.move_type),)),
            ]
        if self.func_num == 11:
            writes = [
                VrWriteRequest(start_vr=0, values=(11.0,)),
                VrWriteRequest(start_vr=2, values=(float(self.point_count),)),
                VrWriteRequest(start_vr=14, values=(self.spd_pct,)),
                VrWriteRequest(start_vr=16, values=(self.acc_pct,)),
                VrWriteRequest(start_vr=18, values=(self.dec_pct,)),
            ]
            for idx, point in enumerate(self.interp_points[: self.point_count]):
                base = 400 + idx * 12
                writes.extend(
                    VrWriteRequest(start_vr=base + offset * 2, values=(float(value),))
                    for offset, value in enumerate(point)
                )
            return writes
        if self.func_num == 109:
            return [
                VrWriteRequest(start_vr=0, values=(109.0,)),
                VrWriteRequest(start_vr=2, values=(float(self.check_value),)),
                VrWriteRequest(start_vr=4, values=(self.delay_sec,)),
            ]
        if self.func_num == 110:
            return [
                VrWriteRequest(start_vr=0, values=(110.0,)),
                VrWriteRequest(start_vr=6, values=(self.delay_sec,)),
            ]
        if self.func_num == 120:
            return [
                VrWriteRequest(start_vr=0, values=(120.0,)),
                VrWriteRequest(start_vr=2, values=(float(self.io_no),)),
                VrWriteRequest(start_vr=4, values=(float(self.io_action),)),
            ]
        return []

    def to_trigger_write(self) -> VrWriteRequest:
        return VrWriteRequest(start_vr=32, values=(1.0,))

    def expected_echo_points(self) -> list[tuple[int, float]]:
        values_by_addr: dict[int, float] = {}
        for request in self.to_func_writes():
            for index, value in enumerate(request.values):
                values_by_addr[request.start_vr + index * 2] = float(value)
        points: list[tuple[int, float]] = []
        for src_addr in range(0, 34, 2):
            if src_addr in values_by_addr:
                points.append((280 + src_addr, values_by_addr[src_addr]))
        return points


@dataclass(frozen=True)
class SixAxisStatus:
    """六轴 V4.3 LONG(34) 状态解析。"""
    raw: int
    func_num: int | None = None

    FUNC_STATE_FIELDS: ClassVar[dict[int, tuple[int, int]]] = {
        104: (0, 0x00000003),
        106: (2, 0x0000000C),
        107: (4, 0x00000030),
        108: (6, 0x000000C0),
        109: (8, 0x00000300),
        110: (10, 0x00000C00),
        11: (14, 0x0000C000),
        120: (18, 0x000C0000),
    }
    STATE_IDLE = 0
    STATE_EXEC = 1
    STATE_DONE = 2
    STATE_ERR = 3
    MOTION_FUNCS: ClassVar[frozenset[int]] = SIX_MOTION_FUNCS
    PROGRAM_FUNCS: ClassVar[frozenset[int]] = SIX_PROGRAM_FUNCS
    SYSTEM_FUNCS: ClassVar[frozenset[int]] = SIX_SYSTEM_FUNCS

    def function_state(self, func_num: int | None = None) -> int:
        target = self.func_num if func_num is None else func_num
        if target not in self.FUNC_STATE_FIELDS:
            return self.STATE_IDLE
        shift, mask = self.FUNC_STATE_FIELDS[target]
        return (self.raw & mask) >> shift

    def _active_states(self) -> list[int]:
        if self.func_num in self.FUNC_STATE_FIELDS:
            return [self.function_state(self.func_num)]
        return [
            (self.raw & mask) >> shift
            for shift, mask in self.FUNC_STATE_FIELDS.values()
        ]

    @property
    def is_complete(self) -> bool:
        return self.STATE_DONE in self._active_states()

    @property
    def is_received(self) -> bool:
        return self.is_executing or self.is_complete or self.has_error

    @property
    def is_executing(self) -> bool:
        return self.STATE_EXEC in self._active_states()

    @property
    def has_error(self) -> bool:
        return self.STATE_ERR in self._active_states()

    @property
    def has_alarm(self) -> bool:
        return (self.raw & (1 << 24)) != 0

    @property
    def is_estop(self) -> bool:
        return (self.raw & (1 << 25)) != 0

    @property
    def is_paused(self) -> bool:
        return (self.raw & (1 << 26)) != 0

    @property
    def is_cancelled(self) -> bool:
        return (self.raw & (1 << 27)) != 0

    @property
    def is_ready(self) -> bool:
        return (self.raw & (1 << 28)) != 0

    @property
    def can_send(self) -> bool:
        if self.has_alarm or self.is_estop or not self.is_ready:
            return False
        return all(state != self.STATE_EXEC for state in self._active_states())

    def slot_busy(self, slot: str) -> bool:
        if slot == "motion":
            funcs = self.MOTION_FUNCS
        elif slot == "program":
            funcs = self.PROGRAM_FUNCS
        elif slot == "system":
            return False
        else:
            funcs = self.FUNC_STATE_FIELDS.keys()
        return any(self.function_state(func_num) == self.STATE_EXEC for func_num in funcs)

    def can_send_for(self, func_num: int) -> bool:
        if func_num in self.SYSTEM_FUNCS:
            return True
        if self.has_alarm or self.is_estop or not self.is_ready:
            return False
        if self.function_state(func_num) == self.STATE_ERR:
            return False
        return not self.slot_busy(six_func_slot(func_num))

    @classmethod
    def from_value(cls, val: float | int, func_num: int | None = None) -> "SixAxisStatus":
        return cls(raw=int(val), func_num=func_num)


@dataclass(frozen=True)
class SixAxisAlarmDetail:
    """六轴 V4.3 LONG(38) 报警详情。"""
    radius: bool = False      # Bit0: 半径超限
    height: bool = False      # Bit1: 高度超限
    cmd_busy: bool = False    # Bit2: 指令忙（重复触发）
    speed: bool = False       # Bit3: 速度超限
    accel: bool = False       # Bit4: 加速度超限
    decel: bool = False       # Bit5: 减速度超限
    ecat_exceeded: bool = False  # Bit6: ECAT通讯异常
    drive_alarm: bool = False       # Bit7: 驱动器报警
    func_id_invalid: bool = False   # Bit8: 函数号非法
    param_invalid: bool = False     # Bit9: 参数非法

    @classmethod
    def from_value(cls, val: float | int) -> "SixAxisAlarmDetail":
        raw = int(val)
        return cls(
            radius=(raw & 1) != 0,
            height=(raw & 2) != 0,
            cmd_busy=(raw & 4) != 0,
            speed=(raw & 8) != 0,
            accel=(raw & 16) != 0,
            decel=(raw & 32) != 0,
            ecat_exceeded=(raw & 64) != 0,
            drive_alarm=(raw & 128) != 0,
            func_id_invalid=(raw & 256) != 0,
            param_invalid=(raw & 512) != 0,
        )

    def __str__(self) -> str:
        parts = []
        if self.radius: parts.append("半径超限")
        if self.height: parts.append("高度超限")
        if self.cmd_busy: parts.append("指令忙")
        if self.speed: parts.append("速度超限")
        if self.accel: parts.append("加速度超限")
        if self.decel: parts.append("减速度超限")
        if self.ecat_exceeded: parts.append("ECAT异常")
        if self.drive_alarm: parts.append("驱动器报警")
        if self.func_id_invalid: parts.append("函数号非法")
        if self.param_invalid: parts.append("参数非法")
        return "、".join(parts) if parts else "无报警详情"


@dataclass(frozen=True)
class SixAxisRealtimeData:
    """六轴实时坐标 — V4.3 使用 MPOS: IEEE(1600~1610) 关节 + IEEE(1612~1622) 位姿。"""
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
    def write_modbus_long(self, request: VrWriteRequest) -> None: ...
    def read_modbus_long(self, request: VrReadRequest) -> list[int]: ...
    # deprecated: V4.3 新协议路径不再使用 BIT/REG，仅保留旧兼容。
    def write_modbus_bit(self, start: int, values: list[int]) -> None: ...
    def read_modbus_bit(self, start: int, count: int) -> list[int]: ...
