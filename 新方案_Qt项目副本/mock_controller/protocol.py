from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VrField:
    index: int
    name: str
    dtype: str
    access: str
    desc: str


VR_OFFSET = {
    "CMD_CODE": VrField(0, "CMD_CODE", "UINT16", "R/W", "命令码，写入即触发"),
    "TASK_ID": VrField(1, "TASK_ID", "UINT16", "R/W", "任务序列号"),
    "POS_X": VrField(2, "POS_X", "FLOAT", "R/W", "X轴坐标(mm)"),
    "POS_Y": VrField(3, "POS_Y", "FLOAT", "R/W", "Y轴坐标(mm)"),
    "POS_Z": VrField(4, "POS_Z", "FLOAT", "R/W", "Z轴坐标(mm)"),
    "ROT_RX": VrField(5, "ROT_RX", "FLOAT", "R/W", "姿态RX(角度)"),
    "ROT_RY": VrField(6, "ROT_RY", "FLOAT", "R/W", "姿态RY(角度)"),
    "ROT_RZ": VrField(7, "ROT_RZ", "FLOAT", "R/W", "姿态RZ(角度)"),
    "SPD_PCT": VrField(8, "SPD_PCT", "UINT16", "R/W", "速度百分比(0-100)"),
    "ACC_PCT": VrField(9, "ACC_PCT", "UINT16", "R/W", "加速度百分比(0-100)"),
    "DEV_ID": VrField(10, "DEV_ID", "UINT16", "R/W", "设备/工位ID"),
    "IO_GRIP": VrField(11, "IO_GRIP", "UINT16", "R/W", "夹爪动作 0=松开 1=夹紧"),
    "IO_DOOR": VrField(12, "IO_DOOR", "UINT16", "R/W", "机床门动作 0=关 1=开"),
    "EXT_P1": VrField(13, "EXT_P1", "FLOAT", "R/W", "扩展参数1(如延时ms)"),
    "EXT_P2": VrField(14, "EXT_P2", "FLOAT", "R/W", "扩展参数2(如力度)"),
    "SAFETY_LV": VrField(15, "SAFETY_LV", "UINT16", "R/W", "安全等级(1-5)"),
    "RESULT": VrField(16, "RESULT", "INT16", "R", "执行结果 0=成功 -1=失败"),
    "STATUS": VrField(17, "STATUS", "UINT16", "R", "状态 0=空闲 1=运行 2=暂停 3=故障"),
    "CUR_X": VrField(18, "CUR_X", "FLOAT", "R", "当前实际X"),
    "CUR_Y": VrField(19, "CUR_Y", "FLOAT", "R", "当前实际Y"),
    "CUR_Z": VrField(20, "CUR_Z", "FLOAT", "R", "当前实际Z"),
    "CUR_RX": VrField(21, "CUR_RX", "FLOAT", "R", "当前实际RX"),
    "CUR_RY": VrField(22, "CUR_RY", "FLOAT", "R", "当前实际RY"),
    "CUR_RZ": VrField(23, "CUR_RZ", "FLOAT", "R", "当前实际RZ"),
    "ALM_CODE": VrField(24, "ALM_CODE", "UINT16", "R", "报警代码"),
    "IO_STAT": VrField(25, "IO_STAT", "UINT16", "R", "IO状态位"),
}

VR_SIZE = 26
VR_TOTAL = 1024
MIRROR_VR_START = 500
MIRROR_VR_COUNT = 16
ACK_VR = 516
EXEC_TRIGGER_VR = 517
MONITOR_VR_START = 700
MONITOR_VR_COUNT = 20

WRITEABLE_FIELDS = {name for name, f in VR_OFFSET.items() if "W" in f.access}

READABLE_FIELDS = {name for name, f in VR_OFFSET.items() if "R" in f.access}


class CMD:
    MOVE_ABS = 1001
    MOVE_REL = 1002
    HOME = 1003
    GRIP_SET = 1004
    DOOR_CTRL = 1005
    WAIT_MS = 1006
    CHECK_IN = 1007
    EMG_RESET = 1008
    SYS_RESET = 4001
    SYS_ESTOP = 4002
    SYS_PAUSE = 4003
    SYS_RESUME = 4004
    AUTO_START = 6001
    AUTO_STOP = 6002

    _NAMES = {
        1001: "MOVE_ABS",
        1002: "MOVE_REL",
        1003: "HOME",
        1004: "GRIP_SET",
        1005: "DOOR_CTRL",
        1006: "WAIT_MS",
        1007: "CHECK_IN",
        1008: "EMG_RESET",
        4001: "SYS_RESET",
        4002: "SYS_ESTOP",
        4003: "SYS_PAUSE",
        4004: "SYS_RESUME",
        6001: "AUTO_START",
        6002: "AUTO_STOP",
    }

    @classmethod
    def name(cls, code: int) -> str:
        return cls._NAMES.get(code, f"UNKNOWN({code})")


STATUS_IDLE = 0
STATUS_RUNNING = 1
STATUS_PAUSED = 2
STATUS_FAULT = 3

RESULT_OK = 0
RESULT_FAIL = -1

ALM_NORMAL = 0
ALM_DOOR_OPEN = 101
ALM_OUT_OF_RANGE = 102
ALM_SPEED_LIMIT = 103
ALM_GRIPPER_FAULT = 104
ALM_EMERGENCY_STOP = 105
ALM_COLLISION = 106

SAFETY_MAX = 5
SAFETY_MIN_AUTO = 3

X_RANGE = (-300.0, 300.0)
Y_RANGE = (-300.0, 300.0)
Z_RANGE = (0.0, 300.0)

SPEED_MAX_AUTO = 100
SPEED_MAX_DEBUG = 30
SPEED_MAX_DANGER = 20
