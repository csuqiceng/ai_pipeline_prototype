from __future__ import annotations

from ai_pipeline_prototype.controller_service import ControllerService
from ai_pipeline_prototype.sdk_adapter import MotionSDKClient


class ExecutionError(Exception):
    pass


class RobotExecutor:
    """控制执行接口。后续可替换为 Motion SDK 封装实现。"""

    def move_to(self, point: list[float], speed: int) -> str:
        raise NotImplementedError

    def grip(self, target_id: str | None) -> str:
        raise NotImplementedError

    def release(self) -> str:
        raise NotImplementedError

    def home(self) -> str:
        raise NotImplementedError

    def stop(self) -> str:
        raise NotImplementedError


class SimulatedRobotExecutor(RobotExecutor):
    """模拟控制器行为，便于先验证调度逻辑。"""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on

    def move_to(self, point: list[float], speed: int) -> str:
        self._maybe_fail("move_to")
        return f"move_to(point={point}, speed={speed})"

    def grip(self, target_id: str | None) -> str:
        self._maybe_fail("grip")
        return f"grip(target_id={target_id})"

    def release(self) -> str:
        self._maybe_fail("release")
        return "release()"

    def home(self) -> str:
        self._maybe_fail("home")
        return "home()"

    def stop(self) -> str:
        self._maybe_fail("stop")
        return "stop()"

    def _maybe_fail(self, action: str) -> None:
        if self.fail_on == action:
            raise ExecutionError(f"模拟执行失败: {action}")


class MotionSDKRobotExecutor(RobotExecutor):
    """SDK 风格执行器。通过客户端封装与控制器通信。"""

    def __init__(self, client: MotionSDKClient, service: ControllerService | None = None) -> None:
        self.client = client
        self.service = service or ControllerService(client)
        self.service.connect()

    def move_to(self, point: list[float], speed: int) -> str:
        x, y, z = point
        return self.service.move_to_pose(x, y, z, speed)

    def grip(self, target_id: str | None) -> str:
        command = self.service.set_gripper(True)
        return f"{command}, target_id={target_id}"

    def release(self) -> str:
        return self.service.set_gripper(False)

    def home(self) -> str:
        return self.service.home()

    def stop(self) -> str:
        return self.service.stop()
