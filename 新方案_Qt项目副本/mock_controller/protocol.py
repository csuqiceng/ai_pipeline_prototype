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


# ── Modbus TCP 寄存器地址映射 ──────────────────────────────────────

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

# ── 六轴机械手协议常量 (VPLC516E) ──────────────────────────────────

# BIT 寄存器地址
SIX_ALARM_BIT = 151              # deprecated: V4.3 使用 Func104 reset_ctrl 报警复位

# IEEE 寄存器地址
SIX_ALARM_DETAIL_ADDR = 38       # IEEE(38) 报警详情(位组合)
SIX_CURR_FUNC_ADDR = 322         # IEEE(322) 当前函数号

# mock 内部运行态缓存；对外同步到 V4.3 DPOS/MPOS(1500+/1600+)。
SIX_RT_J_START = 58
SIX_RT_XYZ_START = 40

# 安全限位地址
SIX_SAFE_R_MIN = 1700            # IEEE(1700) 最小半径
SIX_SAFE_R_MAX = 1702            # IEEE(1702) 最大半径
SIX_SAFE_Z_MIN = 1704            # IEEE(1704) 最小高度
SIX_SAFE_Z_MAX = 1706            # IEEE(1706) 最大高度
SIX_SAFE_SPD_MAX = 1708          # IEEE(1708) 最大速度
SIX_SAFE_ACC_MAX = 1710          # IEEE(1710) 最大加速度
SIX_SAFE_DEC_MAX = 1712          # IEEE(1712) 最大减速度

# IEEE(34) 状态位掩码
# Bit0=已收到(1), Bit1=执行中(2), Bit2=完成(4), Bit3=错误(8), Bit6=报警(64)
SIX_STATUS_RECEIVED = 1          # 下位机收到命令后设置
SIX_STATUS_EXECUTING = 2
SIX_STATUS_COMPLETE = 4
SIX_STATUS_ERROR = 8             # 参数错误/速度=0
SIX_STATUS_ALARM = 64            # 限位截断/ECAT断线
# 组合值
SIX_STATUS_COMPLETE_ALARM = 68   # 4+64 完成+报警(运动已结束,记录警告)
SIX_STATUS_ERROR_ALARM = 72      # 8+64 错误+报警(严重问题)

# Func 104 参数偏移
SIX_P_STOP_MODE = 2              # 0=急停 1=慢停

# Func 106/107 参数偏移 (IEEE地址)
SIX_P_AXIS_NO = 2                # 轴号 (106: 0~5关节, 107: 6~11虚拟轴)
SIX_P_POS_VAL = 4                # 位置值(度或mm)
SIX_P_SPD = 6                    # 速度
SIX_P_ACC_V = 8                  # 加速度
SIX_P_DEC_V = 10                 # 减速度
SIX_P_FUZZY_POS = 12             # 模糊位置
SIX_P_FUZZY_SPD = 14             # 模糊速度
SIX_P_FUZZY_ACC = 16             # 模糊加速度
SIX_P_FUZZY_DEC = 18             # 模糊减速度
SIX_P_STOP_CMD = 20              # 停止命令

# Func 108 参数偏移 (IEEE地址)
SIX_P_TARGET_X = 2
SIX_P_TARGET_Y = 4
SIX_P_TARGET_Z = 6
SIX_P_TARGET_RX = 8
SIX_P_TARGET_RY = 10
SIX_P_TARGET_RZ = 12
SIX_108_SPD = 14
SIX_108_ACC = 16
SIX_108_DEC = 18
SIX_108_STOP_CMD = 20
SIX_108_FUZZY_POS = 22           # Func108模糊参数在IEEE(22~28)
SIX_108_FUZZY_SPD = 24
SIX_108_FUZZY_ACC = 26
SIX_108_FUZZY_DEC = 28
SIX_108_MOVE_TYPE = 30           # 0=直线插补 1=PTP


class FuncSixAxis:
    """六轴机械手函数号定义"""
    MULTI_POINT_INTERP = 11       # 多点插补
    STOP = 104                    # 停止(急停/慢停)
    JOINT_JOG = 106               # 关节点动(J1~J6)
    VIRTUAL_JOG = 107             # 虚拟轴点动(X/Y/Z/Rx/Ry/Rz)
    LINE_MOVE = 108               # 直线插补/PTP运动
    TIMER_CHECK = 109             # 定时检测
    DELAY = 110                   # 延时
    IO_CTRL = 120                 # IO控制

    _NAMES = {
        11: "MULTI_POINT_INTERP",
        104: "STOP",
        106: "JOINT_JOG",
        107: "VIRTUAL_JOG",
        108: "LINE_MOVE",
        109: "TIMER_CHECK",
        110: "DELAY",
        120: "IO_CTRL",
    }

    @classmethod
    def name(cls, code: int) -> str:
        return cls._NAMES.get(code, f"UNKNOWN_SIX({code})")


# 六轴默认速度
SIX_DEFAULT_SPEED = 3000.0
SIX_DEFAULT_ACCEL = 1000.0
SIX_DEFAULT_DECEL = 1000.0
