"""L2 motion planning preflight built on a kinematics engine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterable

from .kinematics_engine import InverseKinematicsResult, KinematicsEngine


class MotionPlanService:
    """Finds a feasible FSTATUS and performs a minimal singularity check."""

    def __init__(
        self,
        *,
        engine: KinematicsEngine | None,
        joint_limits: tuple[tuple[float, float], ...] | None = None,
        singular_j4_abs_min: float = 5.0,
        midpoint_ry_offset_deg: float = 5.0,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.engine = engine
        self.joint_limits = joint_limits or tuple((-360.0, 360.0) for _ in range(6))
        self.singular_j4_abs_min = float(singular_j4_abs_min)
        self.midpoint_ry_offset_deg = float(midpoint_ry_offset_deg)
        self.progress_callback = progress_callback

    def plan(
        self,
        *,
        target_pose: Iterable[float],
        start_pose: Iterable[float] | None = None,
        fstatus_candidates: Iterable[int] = range(8),
    ) -> dict:
        self._publish_progress("start", 0, "准备 L2 运动规划预演。")
        if self.engine is None:
            self._publish_progress("complete", 100, "L2 运动规划预演不可用，未配置运动学逆解引擎。")
            return {
                "status": "unavailable",
                "selected_fstatus": None,
                "joints": (),
                "items": [
                    self._item(
                        "kinematics_engine",
                        "L2",
                        "运动学逆解引擎可用",
                        False,
                        "运动学逆解引擎可用。",
                        "未配置运动学逆解引擎。",
                    )
                ],
                "suggestion": "未配置运动学逆解引擎，无法执行 L2 运动规划预演。",
            }

        pose = self._six_tuple(target_pose)
        candidates: list[InverseKinematicsResult] = []
        items = []
        self._publish_progress("fstatus_scan", 25, "正在遍历 FSTATUS 候选。")
        for fstatus in fstatus_candidates:
            result = self.engine.inverse(pose, int(fstatus))
            if not result.success:
                continue
            if not self._joints_within_limits(result.joints):
                continue
            candidates.append(result)
        if not candidates:
            self._publish_progress("complete", 100, "L2 运动规划预演未通过，未找到满足关节限位的 FSTATUS。")
            return {
                "status": "fail",
                "selected_fstatus": None,
                "joints": (),
                "items": [
                    self._item(
                        "find_best_fstatus",
                        "L2",
                        "FSTATUS 可达解",
                        False,
                        "已找到可达 FSTATUS。",
                        "未找到满足关节限位的 FSTATUS。",
                    )
                ],
                "suggestion": "请调整目标位姿或补充中间点后重试。",
            }

        self._publish_progress("candidate_scored", 55, f"已找到 {len(candidates)} 个可达 FSTATUS，正在评分。")
        sorted_candidates = sorted(candidates, key=lambda item: self._joint_score(item.joints))
        rejected_by_singularity: list[tuple[int, str]] = []
        selected = None
        selected_singular_message = ""
        start_pose_tuple = self._six_tuple(start_pose) if start_pose is not None else None
        self._publish_progress("singularity_check", 75, "正在检查 5 个插值点奇异风险。")
        for candidate in sorted_candidates:
            singular_ok, singular_message = self._path_singularity_ok(
                candidate.joints,
                selected_fstatus=candidate.fstatus,
                start_pose=start_pose_tuple,
                target_pose=pose,
            )
            if singular_ok:
                selected = candidate
                selected_singular_message = singular_message
                break
            rejected_by_singularity.append((candidate.fstatus, singular_message))
        if selected is None:
            midpoint = self._suggest_midpoint(
                start_pose=start_pose_tuple,
                target_pose=pose,
                candidates=sorted_candidates,
            )
            items.append(
                self._item(
                    "find_best_fstatus",
                    "L2",
                    "FSTATUS 可达解",
                    True,
                    f"已找到 {len(sorted_candidates)} 个可达 FSTATUS。",
                    "未找到满足关节限位的 FSTATUS。",
                )
            )
            rejected_text = "；".join(f"FSTATUS={fstatus}: {message}" for fstatus, message in rejected_by_singularity[:4])
            items.append(
                self._item(
                    "path_singularity",
                    "L2",
                    "路径奇异点检查",
                    False,
                    "J4 未进入奇异阈值。",
                    rejected_text or "所有 FSTATUS 候选均接近奇异阈值。",
                )
            )
            if midpoint is not None:
                items.append(
                    self._item(
                        "midpoint_plan",
                        "L2",
                        "中点绕行建议",
                        True,
                        f"建议经 RY 偏移中点绕行：{midpoint['midpoint_pose']}。",
                        "未找到可用中点。",
                    )
                )
            self._publish_progress("complete", 100, "L2 运动规划预演未通过，所有 FSTATUS 候选均接近奇异区。")
            suggestion = "检测到直线路径接近奇异区，建议经中点绕行后再执行。" if midpoint else "检测到所有 FSTATUS 候选均接近奇异区，建议规划中间点。"
            return {
                "status": "fail",
                "selected_fstatus": None,
                "joints": (),
                "items": items,
                "rejected_fstatuses": tuple(fstatus for fstatus, _message in rejected_by_singularity),
                "suggestion": suggestion,
                "need_midpoint": bool(midpoint),
                "midpoint_pose": tuple(midpoint["midpoint_pose"]) if midpoint else None,
                "midpoint_fstatus": midpoint["midpoint_fstatus"] if midpoint else None,
            }
        items.append(
            self._item(
                "find_best_fstatus",
                "L2",
                "FSTATUS 可达解",
                True,
                self._fstatus_selection_message(selected.fstatus, rejected_by_singularity),
                "未找到满足关节限位的 FSTATUS。",
            )
        )
        items.append(
            self._item(
                "path_singularity",
                "L2",
                "路径奇异点检查",
                True,
                "J4 未进入奇异阈值。",
                selected_singular_message,
            )
        )
        self._publish_progress("complete", 100, f"L2 运动规划预演通过，FSTATUS={selected.fstatus}。")
        return {
            "status": "pass",
            "selected_fstatus": selected.fstatus,
            "joints": tuple(float(value) for value in selected.joints[:6]),
            "items": items,
            "rejected_fstatuses": tuple(fstatus for fstatus, _message in rejected_by_singularity),
            "suggestion": None,
            "need_midpoint": False,
            "midpoint_pose": None,
            "midpoint_fstatus": None,
        }

    def _publish_progress(self, stage: str, percent: int, message: str, **extra: Any) -> None:
        if self.progress_callback is None:
            return
        event = {"stage": stage, "percent": int(percent), "message": message}
        event.update(extra)
        try:
            self.progress_callback(event)
        except Exception:
            return

    def _joints_within_limits(self, joints: tuple[float, ...]) -> bool:
        if len(joints) < 6:
            return False
        return all(limit[0] <= float(value) <= limit[1] for value, limit in zip(joints[:6], self.joint_limits))

    @staticmethod
    def _joint_score(joints: tuple[float, ...]) -> float:
        return sum(abs(float(value)) for value in joints[:6])

    @staticmethod
    def _fstatus_selection_message(selected_fstatus: int, rejected_by_singularity: list[tuple[int, str]]) -> str:
        if not rejected_by_singularity:
            return f"选中 FSTATUS={selected_fstatus}。"
        rejected = "、".join(f"FSTATUS={fstatus}" for fstatus, _message in rejected_by_singularity[:4])
        return f"{rejected} 接近奇异区，改选 FSTATUS={selected_fstatus}。"

    def _path_singularity_ok(
        self,
        joints: tuple[float, ...],
        *,
        selected_fstatus: int,
        start_pose: tuple[float, float, float, float, float, float] | None,
        target_pose: tuple[float, float, float, float, float, float],
    ) -> tuple[bool, str]:
        if len(joints) < 4:
            return False, "逆解关节数量不足。"
        if abs(float(joints[3])) <= self.singular_j4_abs_min:
            return False, f"J4={joints[3]:.1f} 接近奇异阈值。"
        if start_pose is None or self.engine is None:
            return True, "J4 未进入奇异阈值。"
        for pose in self._interpolate_poses(start_pose, target_pose, samples=5):
            result = self.engine.inverse(pose, selected_fstatus)
            if not result.success or len(result.joints) < 4:
                return False, f"插值点逆解失败: {result.message or pose}"
            j4 = float(result.joints[3])
            if abs(j4) <= self.singular_j4_abs_min:
                return False, f"插值点 J4={j4:.1f} 接近奇异阈值。"
        return True, "J4 未进入奇异阈值。"

    def _suggest_midpoint(
        self,
        *,
        start_pose: tuple[float, float, float, float, float, float] | None,
        target_pose: tuple[float, float, float, float, float, float],
        candidates: list[InverseKinematicsResult],
    ) -> dict[str, object] | None:
        if start_pose is None or self.engine is None or not candidates:
            return None
        base_midpoint = tuple((float(start_pose[index]) + float(target_pose[index])) / 2.0 for index in range(6))
        for offset in (abs(self.midpoint_ry_offset_deg), -abs(self.midpoint_ry_offset_deg)):
            midpoint = list(base_midpoint)
            midpoint[4] = float(midpoint[4]) + offset
            midpoint_pose = tuple(float(value) for value in midpoint)
            for candidate in candidates:
                result = self.engine.inverse(midpoint_pose, candidate.fstatus)
                if not result.success or not self._joints_within_limits(result.joints):
                    continue
                if len(result.joints) >= 4 and abs(float(result.joints[3])) > self.singular_j4_abs_min:
                    return {
                        "midpoint_pose": midpoint_pose,
                        "midpoint_fstatus": int(candidate.fstatus),
                        "midpoint_joints": tuple(float(value) for value in result.joints[:6]),
                    }
        return None

    @staticmethod
    def _interpolate_poses(
        start_pose: tuple[float, float, float, float, float, float],
        target_pose: tuple[float, float, float, float, float, float],
        *,
        samples: int,
    ) -> list[tuple[float, float, float, float, float, float]]:
        count = max(1, int(samples))
        poses = []
        for index in range(1, count + 1):
            ratio = index / count
            poses.append(
                tuple(
                    float(start_pose[offset]) + (float(target_pose[offset]) - float(start_pose[offset])) * ratio
                    for offset in range(6)
                )
            )
        return poses  # type: ignore[return-value]

    @staticmethod
    def _six_tuple(values: Iterable[float]) -> tuple[float, float, float, float, float, float]:
        padded = [float(value) for value in list(values)[:6]]
        padded += [0.0] * max(0, 6 - len(padded))
        return tuple(padded[:6])  # type: ignore[return-value]

    @staticmethod
    def _item(
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
