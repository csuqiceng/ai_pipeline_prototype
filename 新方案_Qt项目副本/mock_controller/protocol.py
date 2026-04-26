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


# ── V3.0 Modbus TCP 协议常量 ──────────────────────────────────────

# IEEE 寄存器地址 (4x float)
MODBUS_FUNC_ADDR = 0           # IEEE(0)  函数号
MODBUS_PARAM_ADDR = 2          # IEEE(2)  参数区起始
MODBUS_PARAM_MAX = 30          # IEEE(30) 参数区结束
MODBUS_TRIGGER_ADDR = 32       # IEEE(32) 执行触发 (写1触发)
MODBUS_STATUS_ADDR = 34        # IEEE(34) 函数状态

# BIT 寄存器地址 (0x)
MODBUS_ALARM_BIT = 243         # BIT(243) 总报警 0=正常 1=有报警
MODBUS_READY_BIT = 253         # BIT(253) 启动就绪
MODBUS_ESTOP_BIT = 150         # BIT(150) 急停触发

# 实时数据 IEEE 地址
MODBUS_RT_J1_START = 1500      # IEEE(1500~1510) J1~J6角度
MODBUS_RT_XYZ_START = 1512     # IEEE(1512~1522) X/Y/Z/RX/RY/RZ
MODBUS_RT_XYZ_COUNT = 6
MODBUS_RT_SAFE_START = 1700    # IEEE(1700~1706) 安全参数
MODBUS_RT_R3D = 1740           # IEEE(1740) R3d距离
MODBUS_RT_ZHEIGHT = 1742       # IEEE(1742) Z高度

# Func 102 参数偏移 (IEEE地址)
V30_P_X = 2
V30_P_Y = 4
V30_P_Z = 6
V30_P_RX = 8
V30_P_RY = 10
V30_P_RZ = 12
V30_P_SPEED = 14               # mm/s
V30_P_ACCEL = 16               # mm/s²
V30_P_DECEL = 18               # mm/s²
V30_P_FUZZY = 20               # 0=精确 1=模糊
V30_P_FUZZY_STEP = 22          # 模糊最大步长 mm

# Func 101 参数偏移
V30_P_AXIS = 2                 # 轴号 0~5
V30_P_DIR = 4                  # 方向 1=正 -1=反
V30_P_AXIS_SPEED = 6           # 速度 度/s
V30_P_ANGLE = 8                # 角度 度
V30_P_AXIS_FUZZY = 10          # 模糊标志

# Func 104 参数偏移
V30_P_STOP_MODE = 2            # 0=急停 1=慢停

# IEEE(34) 函数状态值
V30_STATUS_IDLE = 0
V30_STATUS_READY = 1
V30_STATUS_EXECUTING = 2
V30_STATUS_COMPLETE = 4
# 组合码
V30_STATUS_COMPLETE_ALARM = 12     # 4+8 完成+报警
V30_STATUS_COMPLETE_RADIUS = 28    # 4+8+16 完成+半径超限
V30_STATUS_COMPLETE_HEIGHT = 44    # 4+8+32 完成+高度超限
V30_STATUS_ALARM_ILLEGAL = 72      # 8+64 报警+指令非法

# 默认速度 mm/s
V30_DEFAULT_SPEED = 3000.0
V30_DEFAULT_ACCEL = 1000.0
V30_DEFAULT_DECEL = 1000.0


class FuncV3:
    """V3.0 函数号定义"""
    JOINT_MOVE = 101              # 关节移动
    LINE_MOVE = 102               # 直线插补运动
    ALARM_CLEAR = 103             # 报警清除
    STOP = 104                    # 停止
    STATUS_QUERY = 105            # 状态查询
    WELD_TRACK = 106              # 焊缝巡迹(预留)

    _NAMES = {
        101: "JOINT_MOVE",
        102: "LINE_MOVE",
        103: "ALARM_CLEAR",
        104: "STOP",
        105: "STATUS_QUERY",
        106: "WELD_TRACK",
    }

    @classmethod
    def name(cls, code: int) -> str:
        return cls._NAMES.get(code, f"UNKNOWN_V30({code})")


def v30_status_is_complete(status: int) -> bool:
    """IEEE(34)值是否表示执行完成（含组合码）"""
    return (status & 4) != 0

def v30_status_has_alarm(status: int) -> bool:
    """IEEE(34)值是否包含报警"""
    return (status & 8) != 0
