"""轴范围、回显参数和安全阈值配置的持久化与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SYSTEM_CONFIG = {
    "x": [-3000.0, 3000.0],
    "y": [-3000.0, 3000.0],
    "z": [0.0, 3000.0],
    "safe_r_min": 200.0,
    "safe_r_max": 1800.0,
    "safe_z_min": 0.0,
    "safe_z_max": 2500.0,
    "safe_speed_max": 150.0,
    "safe_acc_max": 150.0,
    "safe_dec_max": 150.0,
    "motion_timeout_sec": 180.0,
    "echo_retry_interval_sec": 0.005,
    "echo_retry_count": 3,
    "echo_write_rounds": 2,
    "echo_compare_epsilon": 0.001,
    "emergency_codes": ["A1B2"],
    "operator_tts_enabled": False,
    "broadcast_dedupe_window_sec": 5.0,
    "tts_retry_delay_sec": 5.0,
    "tts_max_failures": 3,
    "operator_confirm_timeout_sec": 60.0,
    "operator_dashboard_refresh_ms": 50,
    "operator_view_refresh_ms": 500,
    "controller_realtime_poll_ms": 500,
    "dashboard_stale_after_ms": 1000,
    "l3_min_step_delay_ms": 0,
    "l3_cumulative_error_limit_mm": 0.0,
    "l3_forbidden_boxes": [],
    "joint_limits": [],
}


@dataclass(frozen=True)
class AxisRangeConfig:
    """运行时可调的轴范围、回显和安全参数。"""
    x: tuple[float, float]
    y: tuple[float, float]
    z: tuple[float, float]
    safe_r_min: float = 200.0
    safe_r_max: float = 1800.0
    safe_z_min: float = 0.0
    safe_z_max: float = 2500.0
    safe_speed_max: float = 150.0
    safe_acc_max: float = 150.0
    safe_dec_max: float = 150.0
    motion_timeout_sec: float = 180.0
    echo_retry_interval_sec: float = 0.005
    echo_retry_count: int = 3
    echo_write_rounds: int = 2
    echo_compare_epsilon: float = 0.001
    emergency_codes: tuple[str, ...] = ("A1B2",)
    operator_tts_enabled: bool = False
    broadcast_dedupe_window_sec: float = 5.0
    tts_retry_delay_sec: float = 5.0
    tts_max_failures: int = 3
    operator_confirm_timeout_sec: float = 60.0
    operator_dashboard_refresh_ms: int = 50
    operator_view_refresh_ms: int = 500
    controller_realtime_poll_ms: int = 500
    dashboard_stale_after_ms: int = 1000
    l3_min_step_delay_ms: int = 0
    l3_cumulative_error_limit_mm: float = 0.0
    l3_forbidden_boxes: tuple[dict[str, Any], ...] = ()
    joint_limits: tuple[tuple[float, float], ...] = ()

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
            emergency_codes=_string_tuple(data.get("emergency_codes"), DEFAULT_SYSTEM_CONFIG["emergency_codes"]),
            operator_tts_enabled=bool(data.get("operator_tts_enabled", DEFAULT_SYSTEM_CONFIG["operator_tts_enabled"])),
            broadcast_dedupe_window_sec=float(
                data.get("broadcast_dedupe_window_sec", DEFAULT_SYSTEM_CONFIG["broadcast_dedupe_window_sec"])
            ),
            tts_retry_delay_sec=float(data.get("tts_retry_delay_sec", DEFAULT_SYSTEM_CONFIG["tts_retry_delay_sec"])),
            tts_max_failures=int(float(data.get("tts_max_failures", DEFAULT_SYSTEM_CONFIG["tts_max_failures"]))),
            operator_confirm_timeout_sec=float(
                data.get("operator_confirm_timeout_sec", DEFAULT_SYSTEM_CONFIG["operator_confirm_timeout_sec"])
            ),
            operator_dashboard_refresh_ms=int(
                float(data.get("operator_dashboard_refresh_ms", DEFAULT_SYSTEM_CONFIG["operator_dashboard_refresh_ms"]))
            ),
            operator_view_refresh_ms=int(
                float(data.get("operator_view_refresh_ms", DEFAULT_SYSTEM_CONFIG["operator_view_refresh_ms"]))
            ),
            controller_realtime_poll_ms=int(
                float(data.get("controller_realtime_poll_ms", DEFAULT_SYSTEM_CONFIG["controller_realtime_poll_ms"]))
            ),
            dashboard_stale_after_ms=int(
                float(data.get("dashboard_stale_after_ms", DEFAULT_SYSTEM_CONFIG["dashboard_stale_after_ms"]))
            ),
            l3_min_step_delay_ms=int(float(data.get("l3_min_step_delay_ms", DEFAULT_SYSTEM_CONFIG["l3_min_step_delay_ms"]))),
            l3_cumulative_error_limit_mm=float(
                data.get("l3_cumulative_error_limit_mm", DEFAULT_SYSTEM_CONFIG["l3_cumulative_error_limit_mm"])
            ),
            l3_forbidden_boxes=_box_tuple(data.get("l3_forbidden_boxes", DEFAULT_SYSTEM_CONFIG["l3_forbidden_boxes"])),
            joint_limits=_pair_tuple(data.get("joint_limits", DEFAULT_SYSTEM_CONFIG["joint_limits"])),
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
            "emergency_codes": list(self.emergency_codes),
            "operator_tts_enabled": bool(self.operator_tts_enabled),
            "broadcast_dedupe_window_sec": float(self.broadcast_dedupe_window_sec),
            "tts_retry_delay_sec": float(self.tts_retry_delay_sec),
            "tts_max_failures": int(self.tts_max_failures),
            "operator_confirm_timeout_sec": float(self.operator_confirm_timeout_sec),
            "operator_dashboard_refresh_ms": int(self.operator_dashboard_refresh_ms),
            "operator_view_refresh_ms": int(self.operator_view_refresh_ms),
            "controller_realtime_poll_ms": int(self.controller_realtime_poll_ms),
            "dashboard_stale_after_ms": int(self.dashboard_stale_after_ms),
            "l3_min_step_delay_ms": int(self.l3_min_step_delay_ms),
            "l3_cumulative_error_limit_mm": float(self.l3_cumulative_error_limit_mm),
            "l3_forbidden_boxes": [dict(box) for box in self.l3_forbidden_boxes],
            "joint_limits": [[float(item[0]), float(item[1])] for item in self.joint_limits],
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
    if config.broadcast_dedupe_window_sec < 0:
        return "主动播报去重窗口不能小于 0 秒。"
    if config.tts_retry_delay_sec < 0:
        return "TTS 重试间隔不能小于 0 秒。"
    if config.tts_max_failures <= 0:
        return "TTS 最大连续失败次数必须大于 0。"
    if config.operator_confirm_timeout_sec <= 0:
        return "安全确认超时时间必须大于 0 秒。"
    if config.operator_dashboard_refresh_ms <= 0:
        return "用户页看板刷新周期必须大于 0 毫秒。"
    if config.operator_view_refresh_ms <= 0:
        return "用户页界面刷新周期必须大于 0 毫秒。"
    if config.controller_realtime_poll_ms <= 0:
        return "控制器实时轮询周期必须大于 0 毫秒。"
    if config.dashboard_stale_after_ms <= 0:
        return "看板过期阈值必须大于 0 毫秒。"
    if config.l3_min_step_delay_ms < 0:
        return "L3 最小步间隔不能小于 0 毫秒。"
    if config.l3_cumulative_error_limit_mm < 0:
        return "L3 累计误差上限不能小于 0。"
    if config.joint_limits and len(config.joint_limits) != 6:
        return "关节软限位必须为空或包含 6 组范围。"
    for index, joint_range in enumerate(config.joint_limits, start=1):
        if joint_range[0] > joint_range[1]:
            return f"J{index} 软限位最小值不能大于最大值。"
    return None


def _pair(value: object, fallback: list[float]) -> tuple[float, float]:
    """处理相关数据。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return float(fallback[0]), float(fallback[1])
    return float(value[0]), float(value[1])


def _string_tuple(value: object, fallback: list[str]) -> tuple[str, ...]:
    """Return non-empty string tuple from config data."""
    source = value if isinstance(value, (list, tuple)) else fallback
    result = tuple(str(item).strip() for item in source if str(item).strip())
    return result or tuple(fallback)


def _box_tuple(value: object) -> tuple[dict[str, Any], ...]:
    """Return configured forbidden boxes as shallow-copied dictionaries."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, dict))


def _pair_tuple(value: object) -> tuple[tuple[float, float], ...]:
    """Return tuple of numeric min/max pairs."""
    if not isinstance(value, (list, tuple)):
        return ()
    pairs: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        pairs.append((float(item[0]), float(item[1])))
    return tuple(pairs)
