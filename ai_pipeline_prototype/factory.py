from __future__ import annotations

from ai_pipeline_prototype.controller_service import ControllerService
from ai_pipeline_prototype.executor import MotionSDKRobotExecutor, RobotExecutor, SimulatedRobotExecutor
from ai_pipeline_prototype.sdk_adapter import MotionSDKClient, MotionSDKConfig


def build_executor(mode: str, fail_on: str | None = None) -> RobotExecutor:
    if mode == "sim":
        return SimulatedRobotExecutor(fail_on=fail_on)

    if mode == "sdk":
        client = MotionSDKClient(MotionSDKConfig())
        service = ControllerService(client)
        return MotionSDKRobotExecutor(client, service)

    raise ValueError(f"不支持的执行模式: {mode}")
