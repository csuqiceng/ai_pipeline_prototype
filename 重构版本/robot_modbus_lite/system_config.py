"""轴范围、回显参数和安全阈值配置的持久化与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SYSTEM_CONFIG = {
    "x": [-3000.0, 3000.0],
    "y": [-3000.0, 3000.0],
    "z": [0.0, 3000.0],
    "safe_r_min": 0.0,
    "safe_r_max": 0.0,
    "safe_z_min": 0.0,
    "safe_z_max": 0.0,
    "safe_speed_max": 0.0,
    "safe_acc_max": 0.0,
    "safe_dec_max": 0.0,
    "motion_timeout_sec": 180.0,
    "echo_retry_interval_sec": 0.005,
    "echo_retry_count": 3,
    "echo_write_rounds": 2,
    "echo_compare_epsilon": 0.001,
}


@dataclass(frozen=True)
class AxisRangeConfig:
    """运行时可调的轴范围、回显和安全参数。"""
    x: tuple[float, float]
    y: tuple[float, float]
    z: tuple[float, float]
    safe_r_min: float = 0.0
    safe_r_max: float = 0.0
    safe_z_min: float = 0.0
    safe_z_max: float = 0.0
    safe_speed_max: float = 0.0
    safe_acc_max: float = 0.0
    safe_dec_max: float = 0.0
    motion_timeout_sec: float = 180.0
    echo_retry_interval_sec: float = 0.005
    echo_retry_count: int = 3
    echo_write_rounds: int = 2
    echo_compare_epsilon: float = 0.001

    @classmethod
    def from_dict(cls, data: dict) -> "AxisRangeConfig":
        """处理相关数据。"""
        return cls(
            x=_pair(data.get("x"), DEFAULT_SYSTEM_CONFIG["x"]),
            y=_pair(data.get("y"), DEFAULT_SYSTEM_CONFIG["y"]),
            z=_pair(data.get("z"), DEFAULT_SYSTEM_CONFIG["z"]),
            safe_r_min=float(data.get("safe_r_min", DEFAULT_SYSTEM_CONFIG["safe_r_min"])),
            safe_r_max=float(data.get("safe_r_max", DEFAULT_SYSTEM_CONFIG["safe_r_max"])),
            safe_z_min=float(data.get("safe_z_min", DEFAULT_SYSTEM_CONFIG["safe_z_min"])),
            safe_z_max=float(data.get("safe_z_max", DEFAULT_SYSTEM_CONFIG["safe_z_max"])),
            safe_speed_max=float(data.get("safe_speed_max", DEFAULT_SYSTEM_CONFIG["safe_speed_max"])),
            safe_acc_max=float(data.get("safe_acc_max", DEFAULT_SYSTEM_CONFIG["safe_acc_max"])),
            safe_dec_max=float(data.get("safe_dec_max", DEFAULT_SYSTEM_CONFIG["safe_dec_max"])),
            motion_timeout_sec=float(data.get("motion_timeout_sec", DEFAULT_SYSTEM_CONFIG["motion_timeout_sec"])),
            echo_retry_interval_sec=float(data.get("echo_retry_interval_sec", DEFAULT_SYSTEM_CONFIG["echo_retry_interval_sec"])),
            echo_retry_count=int(float(data.get("echo_retry_count", DEFAULT_SYSTEM_CONFIG["echo_retry_count"]))),
            echo_write_rounds=int(float(data.get("echo_write_rounds", DEFAULT_SYSTEM_CONFIG["echo_write_rounds"]))),
            echo_compare_epsilon=float(data.get("echo_compare_epsilon", DEFAULT_SYSTEM_CONFIG["echo_compare_epsilon"])),
        )

    def to_dict(self) -> dict:
        """处理相关数据。"""
        return {
            "x": [float(self.x[0]), float(self.x[1])],
            "y": [float(self.y[0]), float(self.y[1])],
            "z": [float(self.z[0]), float(self.z[1])],
            "safe_r_min": float(self.safe_r_min),
            "safe_r_max": float(self.safe_r_max),
            "safe_z_min": float(self.safe_z_min),
            "safe_z_max": float(self.safe_z_max),
            "safe_speed_max": float(self.safe_speed_max),
            "safe_acc_max": float(self.safe_acc_max),
            "safe_dec_max": float(self.safe_dec_max),
            "motion_timeout_sec": float(self.motion_timeout_sec),
            "echo_retry_interval_sec": float(self.echo_retry_interval_sec),
            "echo_retry_count": int(self.echo_retry_count),
            "echo_write_rounds": int(self.echo_write_rounds),
            "echo_compare_epsilon": float(self.echo_compare_epsilon),
        }


def ensure_system_config_json(path: Path) -> Path:
    """确保系统配置配置文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_SYSTEM_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_system_config(path: Path) -> AxisRangeConfig:
    """加载系统配置。"""
    ensure_system_config_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return AxisRangeConfig.from_dict(data)


def save_system_config(path: Path, config: AxisRangeConfig) -> None:
    """保存系统配置。"""
    ensure_system_config_json(path)
    path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def validate_system_config(config: AxisRangeConfig) -> str | None:
    """校验系统配置。"""
    for axis_name, axis_range in [("X", config.x), ("Y", config.y), ("Z", config.z)]:
        if axis_range[0] > axis_range[1]:
            return f"{axis_name} 范围最小值不能大于最大值。"
    if config.safe_r_min < 0 or config.safe_r_max < 0:
        return "半径限位不能小于 0。"
    if config.safe_r_max > 0 and config.safe_r_min > config.safe_r_max:
        return "最小半径不能大于最大半径。"
    if config.safe_z_max > 0 and config.safe_z_min > config.safe_z_max:
        return "最低高度不能大于最高高度。"
    for label, value in [
        ("最大速度", config.safe_speed_max),
        ("最大加速度", config.safe_acc_max),
        ("最大减速度", config.safe_dec_max),
    ]:
        if value < 0:
            return f"{label} 不能小于 0。"
    if config.motion_timeout_sec <= 0:
        return "运动超时时间必须大于 0 秒。"
    if config.echo_retry_interval_sec < 0:
        return "回显重试间隔不能小于 0 秒。"
    if config.echo_retry_count <= 0:
        return "回显重试次数必须大于 0。"
    if config.echo_write_rounds <= 0:
        return "回显写入轮次必须大于 0。"
    if config.echo_compare_epsilon <= 0:
        return "回显浮点容差必须大于 0。"
    return None


def _pair(value: object, fallback: list[float]) -> tuple[float, float]:
    """处理相关数据。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return float(fallback[0]), float(fallback[1])
    return float(value[0]), float(value[1])
