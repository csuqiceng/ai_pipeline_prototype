from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MotionSDKConfig:
    host: str = "127.0.0.1"
    port: int = 5000
    controller_id: str = "ZMC406"
    default_speed: int = 80


class MotionSDKClient:
    """Motion SDK 客户端占位封装。

    当前只返回命令字符串，用于验证接口边界。
    后续可在这里替换成真实 SDK 调用，比如连接控制器、下发命令、读取状态。
    """

    def __init__(self, config: MotionSDKConfig) -> None:
        self.config = config
        self.connected = False

    def connect(self) -> str:
        self.connected = True
        return (
            f"connect(controller={self.config.controller_id}, "
            f"host={self.config.host}, port={self.config.port})"
        )

    def disconnect(self) -> str:
        self.connected = False
        return f"disconnect(controller={self.config.controller_id})"

    def move_to_pose(self, x: float, y: float, z: float, speed: int) -> str:
        return f"move_to_pose(x={x}, y={y}, z={z}, speed={speed})"

    def set_gripper(self, closed: bool) -> str:
        state = "close" if closed else "open"
        return f"set_gripper(state={state})"

    def home(self) -> str:
        return "home_axes()"

    def stop(self) -> str:
        return "emergency_stop()"
