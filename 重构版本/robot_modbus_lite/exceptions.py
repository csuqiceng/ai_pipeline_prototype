"""重构后各模块共享的领域异常定义。"""

from __future__ import annotations

import traceback


class SixAxisCommandRuntimeError(RuntimeError):
    """六轴命令失败异常，携带状态字和报警诊断信息。"""
    def __init__(
        self,
        message: str,
        *,
        status_raw: int,
        system_state: int,
        alarm_raw: int,
        func_num: int,
        curr_func: int,
        motion_state: int,
    ) -> None:
        """初始化对象。"""
        super().__init__(message)
        self.status_raw = int(status_raw)
        self.system_state = int(system_state)
        self.alarm_raw = int(alarm_raw)
        self.func_num = int(func_num)
        self.curr_func = int(curr_func)
        self.motion_state = int(motion_state)

    @property
    def is_cmd_busy(self) -> bool:
        """处理忙。"""
        return (self.alarm_raw & 0x04) != 0


class BackgroundTaskError(RuntimeError):
    """后台任务失败异常，用于主线程统一展示错误。"""
    def __init__(self, exc: Exception) -> None:
        """初始化对象。"""
        super().__init__(str(exc))
        self.error_type = type(exc).__name__
        self.error_message = str(exc)
        self.traceback_text = traceback.format_exc()
