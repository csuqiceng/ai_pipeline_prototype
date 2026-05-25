"""Shared L1 safety precheck service for Qt and Web entry points."""

from __future__ import annotations

import math
from typing import Any

from .system_config import AxisRangeConfig


DEFAULT_MAX_SPHERE_RADIUS = 1200.0
DEFAULT_SPEED_CLAMPS = {
    "joint": 50.0,
    "home": 50.0,
    "calibration": 30.0,
}


def infer_l1_action_type(params: dict[str, Any]) -> str:
    explicit = params.get("action_type") or params.get("motion_type")
    if explicit:
        return str(explicit).strip().lower()
    func_id = int(float(params.get("func_id") or params.get("func") or 0))
    action = str(params.get("action") or params.get("action_name") or "")
    if func_id == 106 or "关节" in action:
        return "joint"
    if func_id == 107:
        return "virtual"
    if func_id == 108 or "移动" in action:
        return "move"
    if "回零" in action:
        return "home"
    if "标定" in action:
        return "calibration"
    return ""


class SafetyPrecheckService:
    """Runs controller-independent L1 checks against a snapshot and plan."""

    def __init__(
        self,
        config: AxisRangeConfig,
        *,
        max_sphere_radius: float = DEFAULT_MAX_SPHERE_RADIUS,
        speed_clamps: dict[str, float] | None = None,
    ) -> None:
        self.config = config
        self.max_sphere_radius = float(max_sphere_radius)
        self.speed_clamps = dict(DEFAULT_SPEED_CLAMPS if speed_clamps is None else speed_clamps)

    def run_l1(self, snapshot: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = plan or {}
        plan_id = str(plan.get("plan_id") or "adhoc")
        safety = snapshot.get("safety", {})
        connection = snapshot.get("connection", {})
        motion = snapshot.get("motion", {})
        position = snapshot.get("position", {})
        cartesian = position.get("cartesian", position)

        active_plan_id = motion.get("active_plan_id")
        running_state = motion.get("running_state")
        channel_ok = running_state in {"idle", "waiting_confirm", "空闲", None} and (
            not active_plan_id or active_plan_id == plan_id
        )

        items = [
            self._item("estop", "L1", "无紧急停止", not bool(safety.get("estop")), "急停回路正常。", "急停已触发。"),
            self._item("alarm", "L1", "无活动报警", not bool(safety.get("alarm_active")), "当前没有活动报警。", "当前存在活动报警。"),
            self._item("paused", "L1", "未处于暂停状态", not bool(safety.get("paused")), "系统未暂停。", "系统处于暂停状态。"),
            self._item("controller", "L1", "控制器在线", connection.get("controller") == "online", "控制器连接正常。", "控制器未在线。"),
            self._item(
                "realtime_feedback",
                "L1",
                "实时反馈在线",
                connection.get("realtime_feedback") == "online",
                "实时反馈正常。",
                "实时反馈未在线。",
            ),
            self._item("channel_idle", "L1", "执行通道可用", channel_ok, "当前通道可接收计划。", "当前已有其他任务占用执行通道。"),
        ]
        items.extend(self._current_space_items(cartesian))
        items.extend(self._target_limit_items(plan.get("target", {})))
        items.extend(self._speed_limit_items(plan.get("speed", {}), plan))

        status = "pass" if all(item["status"] == "pass" for item in items) else "fail"
        return {
            "plan_id": plan_id,
            "status": status,
            "items": items,
            "suggestion": None if status == "pass" else "请处理失败项后再执行计划。",
        }

    def _current_space_items(self, cartesian: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        r_value = self._float_or_none(cartesian.get("r"))
        z_value = self._float_or_none(cartesian.get("z"))
        if self.config.safe_r_max > 0 and r_value is not None:
            items.append(
                self._item(
                    "current_r_range",
                    "L1",
                    "当前 R 在安全范围内",
                    self.config.safe_r_min <= r_value <= self.config.safe_r_max,
                    "当前 R 未越界。",
                    f"当前 R={r_value:.1f}mm 超出安全范围。",
                )
            )
        if self.config.safe_z_max > 0 and z_value is not None:
            items.append(
                self._item(
                    "current_z_range",
                    "L1",
                    "当前 Z 在安全范围内",
                    self.config.safe_z_min <= z_value <= self.config.safe_z_max,
                    "当前 Z 未越界。",
                    f"当前 Z={z_value:.1f}mm 超出安全范围。",
                )
            )
        return items

    def _target_limit_items(self, target: object) -> list[dict[str, str]]:
        if not isinstance(target, dict):
            return []
        ranges = {"x": self.config.x, "y": self.config.y, "z": self.config.z}
        items = []
        for axis, axis_range in ranges.items():
            if axis not in target:
                continue
            value = self._float_or_none(target.get(axis))
            if value is None:
                continue
            label = axis.upper()
            items.append(
                self._item(
                    f"target_{axis}_range",
                    "L1",
                    f"目标 {label} 在软限位内",
                    axis_range[0] <= value <= axis_range[1],
                    f"目标 {label} 未越界。",
                    f"目标 {label}={value:.1f} 超出软限位 {axis_range[0]:.1f}~{axis_range[1]:.1f}。",
                )
            )
        sphere_item = self._target_sphere_item(target)
        if sphere_item is not None:
            items.append(sphere_item)
        items.extend(self._joint_limit_items(target.get("joints")))
        return items

    def _target_sphere_item(self, target: dict[str, Any]) -> dict[str, str] | None:
        if self.max_sphere_radius <= 0:
            return None
        values = [self._float_or_none(target.get(axis)) for axis in ("x", "y", "z")]
        if any(value is None for value in values):
            return None
        x, y, z = (float(value) for value in values if value is not None)
        radius = math.sqrt(x * x + y * y + z * z)
        return self._item(
            "target_sphere_radius",
            "L1",
            "目标球面半径在上限内",
            radius <= self.max_sphere_radius,
            f"目标球面半径 {radius:.1f}mm 未超限。",
            f"目标球面半径 {radius:.1f}mm 超过上限 {self.max_sphere_radius:.1f}mm。",
        )

    def _joint_limit_items(self, joints: object) -> list[dict[str, str]]:
        if not self.config.joint_limits:
            return []
        values = self._joint_values(joints)
        items: list[dict[str, str]] = []
        for index, value in values.items():
            if not 0 <= index < len(self.config.joint_limits) or value is None:
                continue
            limit = self.config.joint_limits[index]
            label = f"J{index + 1}"
            items.append(
                self._item(
                    f"target_j{index + 1}_range",
                    "L1",
                    f"目标 {label} 在软限位内",
                    limit[0] <= value <= limit[1],
                    f"目标 {label} 未越界。",
                    f"目标 {label}={value:.1f} 超出软限位 {limit[0]:.1f}~{limit[1]:.1f}。",
                )
            )
        return items

    def _joint_values(self, joints: object) -> dict[int, float | None]:
        if isinstance(joints, (list, tuple)):
            return {index: self._float_or_none(value) for index, value in enumerate(joints)}
        if not isinstance(joints, dict):
            return {}
        values: dict[int, float | None] = {}
        for key, value in joints.items():
            index = self._joint_index(key)
            if index is not None:
                values[index] = self._float_or_none(value)
        return values

    @staticmethod
    def _joint_index(key: object) -> int | None:
        if isinstance(key, int):
            return key
        text = str(key).strip().lower()
        if text.startswith("j"):
            text = text[1:]
            if text.isdigit():
                return int(text) - 1
        if text.isdigit():
            return int(text)
        return None

    def _speed_limit_items(self, speed: object, plan: dict[str, Any] | None = None) -> list[dict[str, str]]:
        if not isinstance(speed, dict):
            return []
        checks = [
            ("spd_pct", "speed_pct", "速度百分比", self.config.safe_speed_max),
            ("acc_pct", "acc_pct", "加速度百分比", self.config.safe_acc_max),
            ("dec_pct", "dec_pct", "减速度百分比", self.config.safe_dec_max),
        ]
        items = []
        for key, item_id, label, max_value in checks:
            if key not in speed or max_value <= 0:
                continue
            value = self._float_or_none(speed.get(key))
            if value is None:
                continue
            items.append(
                self._item(
                    item_id,
                    "L1",
                    f"{label}未超限",
                    0 <= value <= max_value,
                    f"{label}未超限。",
                    f"{label}={value:.1f} 超过上限 {max_value:.1f}。",
                )
            )
        action_type = infer_l1_action_type(plan or {})
        clamp = self.speed_clamps.get(action_type)
        speed_value = self._float_or_none(speed.get("spd_pct"))
        if action_type and clamp is not None and speed_value is not None:
            items.append(
                self._item(
                    "action_speed_clamp",
                    "L1",
                    "动作类型速度钳位",
                    0 <= speed_value <= float(clamp),
                    f"{action_type} 速度钳位 {float(clamp):.1f}% 内。",
                    f"{action_type} 速度钳位 {float(clamp):.1f}%，当前 {speed_value:.1f}%。",
                )
            )
        return items

    def _item(
        self,
        item_id: str,
        level: str,
        label: str,
        passed: bool,
        pass_message: str,
        fail_message: str,
    ) -> dict[str, str]:
        return {
            "id": item_id,
            "level": level,
            "label": label,
            "status": "pass" if passed else "fail",
            "message": pass_message if passed else fail_message,
        }

    @staticmethod
    def _float_or_none(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
