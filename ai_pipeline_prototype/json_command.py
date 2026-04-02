from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, Optional

from .controller_service import ControllerService
from .executor import RobotExecutor
from .models import DispatchState, DispatchResult


class CommandType(str, Enum):
    MOVE = "MOVE"
    GRASP = "GRASP"
    RELEASE = "RELEASE"
    HOME = "HOME"
    STOP = "STOP"
    OFFSET_MOVE = "OFFSET_MOVE"
    INCREMENTAL_MOVE = "INCREMENTAL_MOVE"
    ROTATE = "ROTATE"
    PICK_PLACE = "PICK_PLACE"
    ASSEMBLE = "ASSEMBLE"
    INSPECT = "INSPECT"


@dataclass
class Offset:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rotation: float = 0.0


@dataclass
class CommandParameters:
    target: Optional[str] = None
    offset: Optional[Offset] = None
    speed: int = 50
    relative: bool = False
    force: Optional[float] = None
    position: Optional[float] = None
    tasks: Optional[list[Dict[str, Any]]] = None


@dataclass
class JSONCommand:
    command: CommandType
    parameters: CommandParameters
    timestamp: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JSONCommand:
        command_type = CommandType(data.get("command"))
        params_data = data.get("parameters", {})
        
        offset_data = params_data.get("offset")
        offset = Offset(**offset_data) if offset_data else None
        
        parameters = CommandParameters(
            target=params_data.get("target"),
            offset=offset,
            speed=params_data.get("speed", 50),
            relative=params_data.get("relative", False),
            force=params_data.get("force"),
            position=params_data.get("position"),
            tasks=params_data.get("tasks")
        )
        
        return cls(
            command=command_type,
            parameters=parameters,
            timestamp=data.get("timestamp")
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "command": self.command.value,
            "parameters": asdict(self.parameters),
            "timestamp": self.timestamp
        }
        if self.parameters.offset:
            result["parameters"]["offset"] = asdict(self.parameters.offset)
        return result


class JSONCommandExecutor:
    """JSON指令执行器，负责解析和执行JSON格式的指令"""

    def __init__(self, executor: RobotExecutor, controller_service: ControllerService) -> None:
        self.executor = executor
        self.controller_service = controller_service

    def execute(self, command: JSONCommand) -> DispatchResult:
        history: list[str] = []
        task_id = f"json_task_{command.timestamp.replace(':', '_').replace('.', '_')}"

        try:
            if command.command == CommandType.MOVE:
                return self._execute_move(command, task_id, history)
            elif command.command == CommandType.GRASP:
                return self._execute_grasp(command, task_id, history)
            elif command.command == CommandType.RELEASE:
                return self._execute_release(command, task_id, history)
            elif command.command == CommandType.HOME:
                return self._execute_home(command, task_id, history)
            elif command.command == CommandType.STOP:
                return self._execute_stop(command, task_id, history)
            elif command.command == CommandType.OFFSET_MOVE:
                return self._execute_offset_move(command, task_id, history)
            elif command.command == CommandType.PICK_PLACE:
                return self._execute_pick_place(command, task_id, history)
            else:
                return DispatchResult(
                    task_id=task_id,
                    final_state=DispatchState.ERROR,
                    success=False,
                    message=f"不支持的命令类型: {command.command}",
                    history=history
                )
        except Exception as exc:
            history.append(f"{DispatchState.ERROR.value}: {exc}")
            return DispatchResult(
                task_id=task_id,
                final_state=DispatchState.ERROR,
                success=False,
                message=str(exc),
                history=history
            )

    def _execute_move(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        params = command.parameters
        if params.target == "HOME":
            return self._execute_home(command, task_id, history)
        
        # 这里需要根据target解析出具体的位置坐标
        # 暂时使用默认位置
        position = [100.0, 100.0, 50.0]  # 默认位置
        
        if params.relative and params.offset:
            current_pose = self.controller_service.get_status().current_pose
            position = [
                current_pose[0] + params.offset.x,
                current_pose[1] + params.offset.y,
                current_pose[2] + params.offset.z
            ]
        
        command_str = self.executor.move_to(position, params.speed)
        history.append(f"{DispatchState.MOVE_TO_PICK.value}: {command_str}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="移动任务执行完成",
            history=history
        )

    def _execute_grasp(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        params = command.parameters
        target_id = params.target
        command_str = self.executor.grip(target_id)
        history.append(f"{DispatchState.GRIP.value}: {command_str}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="抓取任务执行完成",
            history=history
        )

    def _execute_release(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        command_str = self.executor.release()
        history.append(f"{DispatchState.RELEASE.value}: {command_str}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="释放任务执行完成",
            history=history
        )

    def _execute_home(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        command_str = self.executor.home()
        history.append(f"{DispatchState.HOMING.value}: {command_str}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="回零任务执行完成",
            history=history
        )

    def _execute_stop(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        command_str = self.executor.stop()
        history.append(f"{DispatchState.STOPPING.value}: {command_str}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="停止任务执行完成",
            history=history
        )

    def _execute_offset_move(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        params = command.parameters
        if not params.offset:
            raise ValueError("OFFSET_MOVE命令需要提供offset参数")
        
        current_pose = self.controller_service.get_status().current_pose
        position = [
            current_pose[0] + params.offset.x,
            current_pose[1] + params.offset.y,
            current_pose[2] + params.offset.z
        ]
        
        command_str = self.executor.move_to(position, params.speed)
        history.append(f"{DispatchState.MOVE_TO_PICK.value}: {command_str}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="相对移动任务执行完成",
            history=history
        )

    def _execute_pick_place(self, command: JSONCommand, task_id: str, history: list[str]) -> DispatchResult:
        params = command.parameters
        
        # 解析拾取位置
        pick_position = [100.0, 100.0, 50.0]  # 默认拾取位置
        if params.target:
            # 根据target解析拾取位置
            pass
        
        # 解析放置位置
        place_position = [200.0, 200.0, 50.0]  # 默认放置位置
        # 这里可以根据具体的参数结构解析放置位置
        # 例如，如果有destination参数，可以从params中获取
        pass
        
        # 执行拾取操作
        move_pick = self.executor.move_to(pick_position, params.speed)
        history.append(f"{DispatchState.MOVE_TO_PICK.value}: {move_pick}")
        
        grip = self.executor.grip(params.target)
        history.append(f"{DispatchState.GRIP.value}: {grip}")
        
        # 执行放置操作
        move_place = self.executor.move_to(place_position, params.speed)
        history.append(f"{DispatchState.MOVE_TO_PLACE.value}: {move_place}")
        
        release = self.executor.release()
        history.append(f"{DispatchState.RELEASE.value}: {release}")
        
        return DispatchResult(
            task_id=task_id,
            final_state=DispatchState.DONE,
            success=True,
            message="拾取放置任务执行完成",
            history=history
        )
