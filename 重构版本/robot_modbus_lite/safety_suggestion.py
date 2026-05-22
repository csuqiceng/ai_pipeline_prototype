"""Generate conservative L1 safety adjustment suggestions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .system_config import AxisRangeConfig


class SafetySuggestionService:
    """Clamps adjustable plan fields to configured L1 limits."""

    def __init__(self, config: AxisRangeConfig) -> None:
        self.config = config

    def suggest(self, plan: dict[str, Any]) -> dict[str, Any]:
        adjusted = deepcopy(plan)
        messages: list[str] = []

        target = adjusted.get("target")
        if isinstance(target, dict):
            self._clamp_target(target, messages)

        speed = adjusted.get("speed")
        if isinstance(speed, dict):
            self._clamp_speed(speed, messages)

        return {
            "available": bool(messages),
            "adjusted_plan": adjusted,
            "messages": messages,
        }

    def _clamp_target(self, target: dict[str, Any], messages: list[str]) -> None:
        for axis, axis_range in {"x": self.config.x, "y": self.config.y, "z": self.config.z}.items():
            if axis not in target:
                continue
            value = self._float_or_none(target.get(axis))
            if value is None:
                continue
            clamped = min(max(value, axis_range[0]), axis_range[1])
            if clamped != value:
                target[axis] = clamped
                messages.append(f"目标 {axis.upper()} 调整为 {clamped:.1f}")
        if self.config.joint_limits and "joints" in target:
            adjusted_joints = self._clamp_joints(target.get("joints"), messages)
            if adjusted_joints is not None:
                target["joints"] = adjusted_joints

    def _clamp_joints(self, joints: object, messages: list[str]) -> object | None:
        if isinstance(joints, (list, tuple)):
            adjusted = list(joints)
            for index, value in enumerate(adjusted[: len(self.config.joint_limits)]):
                numeric = self._float_or_none(value)
                if numeric is None:
                    continue
                clamped = self._clamp_joint_value(index, numeric, messages)
                adjusted[index] = clamped
            return tuple(adjusted) if isinstance(joints, tuple) else adjusted
        if isinstance(joints, dict):
            adjusted = dict(joints)
            for key, value in list(adjusted.items()):
                index = self._joint_index(key)
                numeric = self._float_or_none(value)
                if index is None or numeric is None or not 0 <= index < len(self.config.joint_limits):
                    continue
                adjusted[key] = self._clamp_joint_value(index, numeric, messages)
            return adjusted
        return None

    def _clamp_joint_value(self, index: int, value: float, messages: list[str]) -> float:
        limit = self.config.joint_limits[index]
        clamped = min(max(value, limit[0]), limit[1])
        if clamped != value:
            messages.append(f"目标 J{index + 1} 调整为 {clamped:.1f}")
        return clamped

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

    def _clamp_speed(self, speed: dict[str, Any], messages: list[str]) -> None:
        checks = [
            ("spd_pct", "速度百分比", self.config.safe_speed_max),
            ("acc_pct", "加速度百分比", self.config.safe_acc_max),
            ("dec_pct", "减速度百分比", self.config.safe_dec_max),
        ]
        for key, label, max_value in checks:
            if key not in speed or max_value <= 0:
                continue
            value = self._float_or_none(speed.get(key))
            if value is None:
                continue
            clamped = min(max(value, 0.0), max_value)
            if clamped != value:
                speed[key] = clamped
                messages.append(f"{label}调整为 {clamped:.1f}")

    @staticmethod
    def _float_or_none(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
