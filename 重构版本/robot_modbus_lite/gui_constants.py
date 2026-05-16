"""图形界面共享的标签、按钮映射和协议时序常量。"""

from __future__ import annotations

FUNC_OPTIONS = {
    "Func11 多点插补": 11,
    "Func104 系统控制": 104,
    "Func106 关节轴点动": 106,
    "Func107 虚拟轴点动": 107,
    "Func108 直线插补/PTP": 108,
    "Func109 定时检测": 109,
    "Func110 延时": 110,
    "Func120 IO控制": 120,
}

FUNC_LABELS = {value: key for key, value in FUNC_OPTIONS.items()}
FUNC_LABELS.update({
    11: "Func11 多点插补",
    109: "Func109 定时检测",
    110: "Func110 延时",
    120: "Func120 IO控制",
})

STOP_CMD_LABELS = {
    0: "正常",
    1: "急停",
    2: "快停",
    3: "慢停",
    4: "暂停",
    5: "恢复",
}

MOVE_TYPE_LABELS = {
    0: "直线插补",
    1: "PTP关节",
}

SIX_ECHO_RETRY_INTERVAL_SEC = 0.005
SIX_ECHO_MAX_RETRY_COUNT = 3
SIX_ECHO_CONSECUTIVE_FAIL_THRESHOLD = 3
SIX_ECHO_COMPARE_EPSILON = 0.001
SIX_ECHO_WRITE_ROUNDS = 2
SIX_POST_TRIGGER_SETTLE_SEC = 0.08
SIX_CMD_BUSY_RECOVERY_MAX_RETRIES = 2
SIX_READY_RECOVERY_TIMEOUT_SEC = 5.0
SIX_CMD_BUSY_SLOT_WAIT_TIMEOUT_SEC = 5.0

SYSTEM_COMMANDS = {
    "报警复位": ("alarm_reset", "报警已复位"),
    "暂停": ("sys_pause", "当前任务已暂停"),
    "继续": ("sys_resume", "当前任务继续运行"),
    "急停": ("sys_estop", "急停触发，系统锁定"),
}

SYSTEM_COMMAND_CODES = {
    "alarm_reset": 4001,
    "sys_pause": 4003,
    "sys_resume": 4004,
    "sys_estop": 4002,
}
