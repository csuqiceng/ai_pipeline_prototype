"""主窗口拆分后共享的小型运行状态容器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RobotRealtimeState:
    """界面展示用的机器人实时位姿和连接状态。"""
    robot_x: str = "1250.0"
    robot_y: str = "0.0"
    robot_z: str = "860.0"
    robot_r: str = "0.0 / 0.0 / 0.0"
    robot_joints: tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    robot_speed: str = "30% / 40%"
    claw_enable: str = "0"
    claw_brake: str = "0"
    servo_enable: str = "0"
    run_state: str = "空闲"
    monitor_task: str = "-"
    motion_percent: str = "0%"
    echo_cmd: str = "-"
    exec_state: str = "0"
    mode: str = "自动"
    busy: str = "空闲"
    result: str = "0"
    alarm_code: str = "ERR_000"
    alarm_text: str = "系统正常"
    io_status: str = "0"
    task_id: int = 1001


@dataclass
class FlowExecutionState:
    """流程执行游标、当前步骤和回调状态。"""
    current_flow_name: str | None = None
    step_index: int = 0
    status: str = "空闲"
    running: bool = False
    current_step: str = "-"
    run_id: int = 0


@dataclass
class LogSessionState:
    """日志会话标识和持久化路径状态。"""
    entries: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = 0
    session_path: Path | None = None
    persist_error_reported: bool = False


@dataclass
class VoiceRuntimeState:
    """语音录音、子进程和识别结果的运行状态。"""
    process: Any | None = None
    stop_flag_path: Path | None = None
    result_path: Path | None = None
    recorder_thread: Any | None = None
    proxy_capturing: bool = False


@dataclass
class NlpRuntimeState:
    """自然语言解析计划和当前动作状态。"""
    last_plan: Any | None = None
    sequence_running: bool = False
    parse_running: bool = False
    pending_actions: list[Any] = field(default_factory=list)
    pending_index: int = 0
