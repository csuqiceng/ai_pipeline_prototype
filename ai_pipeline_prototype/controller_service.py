from __future__ import annotations

from ai_pipeline_prototype.models import AlarmEvent, ControllerStatus
from ai_pipeline_prototype.sdk_adapter import MotionSDKClient


class ControllerService:
    """控制器服务层，负责连接、状态、报警和命令执行记录。"""

    def __init__(self, client: MotionSDKClient) -> None:
        self.client = client
        self.status = ControllerStatus(
            connected=False,
            servo_enabled=False,
            alarm_active=False,
            emergency_stopped=False,
            current_pose=[0.0, 0.0, 0.0],
        )
        self.command_history: list[str] = []
        self.alarms: list[AlarmEvent] = []

    def connect(self) -> str:
        command = self.client.connect()
        self.status.connected = True
        self.status.servo_enabled = True
        self._record(command)
        return command

    def disconnect(self) -> str:
        command = self.client.disconnect()
        self.status.connected = False
        self.status.servo_enabled = False
        self._record(command)
        return command

    def move_to_pose(self, x: float, y: float, z: float, speed: int) -> str:
        command = self.client.move_to_pose(x, y, z, speed)
        self.status.current_pose = [x, y, z]
        self._record(command)
        return command

    def set_gripper(self, closed: bool) -> str:
        command = self.client.set_gripper(closed)
        self._record(command)
        return command

    def home(self) -> str:
        command = self.client.home()
        self.status.current_pose = [0.0, 0.0, 0.0]
        self.status.emergency_stopped = False
        self._record(command)
        return command

    def stop(self) -> str:
        command = self.client.stop()
        self.status.emergency_stopped = True
        self._record(command)
        return command

    def get_status(self) -> ControllerStatus:
        return self.status

    def get_alarm_history(self) -> list[AlarmEvent]:
        return list(self.alarms)

    def report_alarm(self, code: str, message: str, level: str = "error") -> AlarmEvent:
        alarm = AlarmEvent(code=code, message=message, level=level)
        self.alarms.append(alarm)
        self.status.alarm_active = True
        return alarm

    def clear_alarms(self) -> None:
        self.alarms.clear()
        self.status.alarm_active = False

    def _record(self, command: str) -> None:
        self.status.last_command = command
        self.command_history.append(command)
