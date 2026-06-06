from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from robot_modbus_lite.agent.drafts import CommandDraft


DEFAULT_POSE_ANGLE_LIMITS = {
    "pose_upper_angle": 90.0,
    "pose_lower_angle": 90.0,
    "pose_cw_angle": 90.0,
    "pose_ccw_angle": 90.0,
}


@dataclass(frozen=True)
class PoseAngleSafetyChecker:
    """Pose angle check for the restricted Agent safety pipeline."""

    limits: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_POSE_ANGLE_LIMITS))

    def __call__(self, *, draft: CommandDraft, l2_result: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        resolved_limits = self._limits_from_snapshot(snapshot)
        pitch, yaw = self._compute_pose_angles(draft=draft, l2_result=l2_result)
        items = [
            self._item(
                "pose_upper_angle",
                "姿态上夹角",
                pitch <= resolved_limits["pose_upper_angle"],
                f"上夹角 {pitch:.1f}° 未超过 {resolved_limits['pose_upper_angle']:.1f}°。",
                f"上夹角超限：当前 {pitch:.1f}°，上限 {resolved_limits['pose_upper_angle']:.1f}°。",
            ),
            self._item(
                "pose_lower_angle",
                "姿态下夹角",
                -pitch <= resolved_limits["pose_lower_angle"],
                f"下夹角 {max(-pitch, 0.0):.1f}° 未超过 {resolved_limits['pose_lower_angle']:.1f}°。",
                f"下夹角超限：当前 {max(-pitch, 0.0):.1f}°，上限 {resolved_limits['pose_lower_angle']:.1f}°。",
            ),
            self._item(
                "pose_cw_angle",
                "姿态顺时针夹角",
                yaw <= resolved_limits["pose_cw_angle"],
                f"顺时针夹角 {max(yaw, 0.0):.1f}° 未超过 {resolved_limits['pose_cw_angle']:.1f}°。",
                f"顺时针夹角超限：当前 {max(yaw, 0.0):.1f}°，上限 {resolved_limits['pose_cw_angle']:.1f}°。",
            ),
            self._item(
                "pose_ccw_angle",
                "姿态逆时针夹角",
                -yaw <= resolved_limits["pose_ccw_angle"],
                f"逆时针夹角 {max(-yaw, 0.0):.1f}° 未超过 {resolved_limits['pose_ccw_angle']:.1f}°。",
                f"逆时针夹角超限：当前 {max(-yaw, 0.0):.1f}°，上限 {resolved_limits['pose_ccw_angle']:.1f}°。",
            ),
        ]
        failed = [item for item in items if item["status"] != "pass"]
        return {
            "status": "fail" if failed else "pass",
            "items": items,
            "pitch_angle": round(pitch, 6),
            "yaw_angle": round(yaw, 6),
            "selected_fstatus": l2_result.get("selected_fstatus"),
            "suggestion": "；".join(item["message"] for item in failed) if failed else None,
        }

    def _compute_pose_angles(self, *, draft: CommandDraft, l2_result: dict[str, Any]) -> tuple[float, float]:
        joints = l2_result.get("joints")
        if isinstance(joints, (list, tuple)) and len(joints) >= 3:
            try:
                tool_dir = compute_tool_direction(
                    self._float(draft.params.get("target_rx")),
                    self._float(draft.params.get("target_ry")),
                    self._float(draft.params.get("target_rz")),
                )
                arm_dir = compute_arm_direction(self._float(joints[0]), self._float(joints[2]))
                return decompose_tool_arm_angles(tool_dir, arm_dir)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        return self._float(draft.params.get("target_ry")), self._float(draft.params.get("target_rz"))

    def _limits_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, float]:
        limits = dict(DEFAULT_POSE_ANGLE_LIMITS)
        limits.update({key: self._float(value, default=limits[key]) for key, value in self.limits.items() if key in limits})
        candidates = []
        if isinstance(snapshot, dict):
            candidates.append(snapshot.get("pose_angles"))
            safety = snapshot.get("safety")
            if isinstance(safety, dict):
                candidates.append(safety.get("pose_angles"))
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in tuple(limits):
                if key in candidate:
                    limits[key] = self._float(candidate.get(key), default=limits[key])
        return limits

    @staticmethod
    def _float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _item(item_id: str, label: str, passed: bool, pass_message: str, fail_message: str) -> dict[str, str]:
        return {
            "id": item_id,
            "level": "L2",
            "label": label,
            "status": "pass" if passed else "fail",
            "message": pass_message if passed else fail_message,
        }


def compute_tool_direction(rx_deg: float, ry_deg: float, rz_deg: float) -> tuple[float, float, float]:
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    matrix = (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy, cy * sx, cy * cx),
    )
    return _normalize((matrix[0][0], matrix[1][0], matrix[2][0]))


def compute_arm_direction(j1_deg: float, j3_motor_deg: float) -> tuple[float, float, float]:
    j1 = math.radians(j1_deg)
    j3 = math.radians(j3_motor_deg)
    return _normalize((math.cos(j3) * math.cos(j1), math.cos(j3) * math.sin(j1), math.sin(j3)))


def decompose_tool_arm_angles(
    tool_dir: tuple[float, float, float],
    arm_dir: tuple[float, float, float],
) -> tuple[float, float]:
    arm_z = _normalize(arm_dir)
    horizontal_norm = math.hypot(arm_z[0], arm_z[1])
    if horizontal_norm < 1e-6:
        arm_y = (0.0, 1.0, 0.0)
    else:
        arm_y = _normalize((arm_z[1], -arm_z[0], 0.0))
    arm_x = _normalize(_cross(arm_y, arm_z))

    tool = _normalize(tool_dir)
    local_x = _dot(tool, arm_x)
    local_y = _dot(tool, arm_y)
    local_z = _dot(tool, arm_z)
    return math.degrees(math.atan2(local_x, local_z)), math.degrees(math.atan2(local_y, local_z))


def _normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm < 1e-9:
        raise ValueError("zero-length vector")
    return tuple(value / norm for value in vector)


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
