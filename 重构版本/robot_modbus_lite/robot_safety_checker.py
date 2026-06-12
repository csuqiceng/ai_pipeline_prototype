"""Unified robot safety checker for natural-language motion confirmation."""

from __future__ import annotations

from typing import Any, Iterable

from .agent.drafts import CommandDraft
from .agent.pose_angle import PoseAngleSafetyChecker
from .motion_plan import MotionPlanService
from .safety_precheck import SafetyPrecheckService


class RobotSafetyChecker:
    """Combine L1 position checks, L2 inverse-kinematics preflight, and pose-angle checks."""

    def __init__(
        self,
        *,
        l1_service: SafetyPrecheckService,
        motion_plan_service: MotionPlanService | None = None,
        pose_angle_limits: dict[str, float] | None = None,
        strict_l2: bool = True,
    ) -> None:
        self.l1_service = l1_service
        self.motion_plan_service = motion_plan_service
        self.pose_angle_limits = dict(pose_angle_limits or {})
        self.strict_l2 = bool(strict_l2)

    def check_target(
        self,
        *,
        target_pose: Iterable[float],
        snapshot: dict[str, Any],
        speed: dict[str, Any] | None = None,
        start_pose: Iterable[float] | None = None,
        plan_id: str = "adhoc",
        func_id: int = 108,
    ) -> dict[str, Any]:
        pose = self._six_tuple(target_pose)
        l1_plan = {
            "plan_id": plan_id,
            "func_id": int(func_id),
            "action_type": "move",
            "target": {"x": pose[0], "y": pose[1], "z": pose[2]},
            "speed": dict(speed or {}),
        }
        l1 = self.l1_service.run_l1(snapshot, l1_plan)
        items = [dict(item) for item in l1.get("items", [])]
        if l1.get("status") != "pass":
            return self._result(
                safe=False,
                position_ok=False,
                ik_ok=None,
                pose_ok=None,
                blocking_level="L1",
                detail_zh=self._failure_detail("L1安全预判未通过", items),
                suggestion_zh=self._l1_operator_suggestion(items),
                items=items,
                l1=l1,
                l2=None,
                pose_angles=None,
            )

        if self.motion_plan_service is None:
            detail = "L2逆解预判暂不可用：未配置运动规划服务。"
            return self._result(
                safe=not self.strict_l2,
                position_ok=True,
                ik_ok=None,
                pose_ok=None,
                blocking_level="L2" if self.strict_l2 else None,
                detail_zh=detail,
                suggestion_zh="请接入 FrameTrans2(mode=2) 逆解服务后再执行。" if self.strict_l2 else "需现场确认后再执行。",
                items=items,
                l1=l1,
                l2=None,
                pose_angles=None,
            )

        l2 = self.motion_plan_service.plan(target_pose=pose, start_pose=start_pose)
        items.extend(dict(item) for item in l2.get("items", []))
        l2_status = str(l2.get("status") or "")
        if l2_status != "pass":
            unavailable = l2_status == "unavailable"
            safe = unavailable and not self.strict_l2
            return self._result(
                safe=safe,
                position_ok=True,
                ik_ok=None if unavailable else False,
                pose_ok=None,
                blocking_level=None if safe else "L2",
                detail_zh=self._l2_failure_detail(l2),
                suggestion_zh=self._l2_operator_suggestion(l2),
                items=items,
                l1=l1,
                l2=l2,
                pose_angles=None,
            )

        pose_angles = self._check_pose_angles(pose, l2_result=l2, snapshot=snapshot, plan_id=plan_id, func_id=func_id)
        items.extend(dict(item) for item in pose_angles.get("items", []))
        if pose_angles["status"] != "pass":
            return self._result(
                safe=False,
                position_ok=True,
                ik_ok=True,
                pose_ok=False,
                blocking_level="POSE",
                detail_zh=self._failure_detail("姿态夹角安全预判未通过", pose_angles["items"]),
                suggestion_zh=str(pose_angles.get("suggestion") or "请调整 RX/RY 姿态后重试。"),
                items=items,
                l1=l1,
                l2=l2,
                pose_angles=pose_angles,
            )

        return self._result(
            safe=True,
            position_ok=True,
            ik_ok=True,
            pose_ok=True,
            blocking_level=None,
            detail_zh=f"安全判定通过：L1通过，L2逆解通过，姿态夹角通过，FSTATUS={l2.get('selected_fstatus')}。",
            suggestion_zh=None,
            items=items,
            l1=l1,
            l2=l2,
            pose_angles=pose_angles,
        )

    def _check_pose_angles(
        self,
        pose: tuple[float, float, float, float, float, float],
        *,
        l2_result: dict[str, Any],
        snapshot: dict[str, Any],
        plan_id: str,
        func_id: int,
    ) -> dict[str, Any]:
        config = self.l1_service.config
        limits = {
            "pose_upper_angle": float(getattr(config, "pose_upper_angle", 90.0) or 90.0),
            "pose_lower_angle": float(getattr(config, "pose_lower_angle", 90.0) or 90.0),
            "pose_cw_angle": float(getattr(config, "pose_cw_angle", 90.0) or 90.0),
            "pose_ccw_angle": float(getattr(config, "pose_ccw_angle", 90.0) or 90.0),
        }
        limits.update({key: float(value) for key, value in self.pose_angle_limits.items() if key in limits})
        draft = CommandDraft(
            draft_id=plan_id,
            func_id=int(func_id),
            intent="move_linear",
            params={
                "target_x": pose[0],
                "target_y": pose[1],
                "target_z": pose[2],
                "target_rx": pose[3],
                "target_ry": pose[4],
                "target_rz": pose[5],
            },
            param_sources={},
            raw_text="robot_safety_checker",
            confidence=1.0,
        )
        return PoseAngleSafetyChecker(limits)(draft=draft, l2_result=l2_result, snapshot=snapshot)

    @staticmethod
    def _result(
        *,
        safe: bool,
        position_ok: bool,
        ik_ok: bool | None,
        pose_ok: bool | None,
        blocking_level: str | None,
        detail_zh: str,
        suggestion_zh: str | None,
        items: list[dict[str, Any]],
        l1: dict[str, Any],
        l2: dict[str, Any] | None,
        pose_angles: dict[str, Any] | None,
    ) -> dict[str, Any]:
        ik_result = None
        if l2 is not None and l2.get("status") == "pass":
            ik_result = {
                "selected_fstatus": l2.get("selected_fstatus"),
                "joints": tuple(l2.get("joints", ())),
            }
        return {
            "safe": bool(safe),
            "position_ok": bool(position_ok),
            "ik_ok": ik_ok,
            "pose_ok": pose_ok,
            "blocking_level": blocking_level,
            "detail_zh": detail_zh,
            "suggestion_zh": suggestion_zh,
            "items": items,
            "l1": l1,
            "l2": l2,
            "pose_angles": pose_angles,
            "ik_result": ik_result,
        }

    @staticmethod
    def _failure_detail(prefix: str, items: list[dict[str, Any]]) -> str:
        failed = [
            str(item.get("message") or "").strip()
            for item in items
            if str(item.get("status") or "") == "fail" and str(item.get("message") or "").strip()
        ]
        if not failed:
            return f"{prefix}。"
        return f"{prefix}：{'；'.join(failed[:3])}"

    def _l1_operator_suggestion(self, items: list[dict[str, Any]]) -> str:
        failed_ids = {
            str(item.get("id") or "")
            for item in items
            if str(item.get("status") or "") == "fail"
        }
        config = self.l1_service.config
        suggestions: list[str] = []
        if "target_x_range" in failed_ids:
            suggestions.append(f"将目标 X 调整到软限位 {config.x[0]:.1f}~{config.x[1]:.1f}mm 内")
        if "target_y_range" in failed_ids:
            suggestions.append(f"将目标 Y 调整到软限位 {config.y[0]:.1f}~{config.y[1]:.1f}mm 内")
        if "target_z_range" in failed_ids:
            suggestions.append(f"将目标 Z 调整到软限位 {config.z[0]:.1f}~{config.z[1]:.1f}mm 内")
        if "target_safe_z_range" in failed_ids:
            suggestions.append(f"将目标 Z 调整到安全高度 {config.safe_z_min:.1f}~{config.safe_z_max:.1f}mm 内")
        if "target_r_range" in failed_ids:
            if float(getattr(config, "safe_r_min", 0.0) or 0.0) > 0:
                suggestions.append(
                    f"将目标 X/Y 调整到安全半径 R>={config.safe_r_min:.1f}mm，避免靠近中心盲区"
                )
            if float(getattr(config, "safe_r_max", 0.0) or 0.0) > 0:
                suggestions.append(f"确保目标外径不超过 {config.safe_r_max:.1f}mm")
        if "target_base_angle_range" in failed_ids:
            suggestions.append("调整 X/Y 方向，使底座角度落在 ±160° 内")
        if {"speed_pct", "acc_pct", "dec_pct"} & failed_ids:
            suggestions.append("降低速度/加速度/减速度百分比后重试")
        if {"current_r_range", "current_z_range", "controller", "realtime_feedback"} & failed_ids:
            suggestions.append("先确认控制器在线、实时反馈和当前位姿正确")
        if {"estop", "alarm", "paused", "channel_idle"} & failed_ids:
            suggestions.append("先解除急停/报警/暂停或等待当前任务结束")
        if not suggestions:
            return "请处理失败项后再执行。"
        return "建议：" + "；".join(dict.fromkeys(suggestions)) + "。"

    @staticmethod
    def _l2_failure_detail(l2: dict[str, Any]) -> str:
        failed_messages = [
            str(item.get("message") or "").strip()
            for item in list(l2.get("items") or [])
            if str(item.get("status") or "") == "fail" and str(item.get("message") or "").strip()
        ]
        if failed_messages:
            return "L1安全检查通过，但 L2逆解失败：" + "；".join(failed_messages[:3])
        suggestion = str(l2.get("suggestion") or "").strip()
        if suggestion:
            return f"L1安全检查通过，但 L2逆解失败：{suggestion}"
        return "L1安全检查通过，但 L2逆解失败：未找到可执行的关节构型。"

    @staticmethod
    def _l2_operator_suggestion(l2: dict[str, Any]) -> str:
        failed_ids = {
            str(item.get("id") or "")
            for item in list(l2.get("items") or [])
            if str(item.get("status") or "") == "fail"
        }
        if "kinematics_engine" in failed_ids or str(l2.get("status") or "") == "unavailable":
            return "建议：接入或检查 FrameTrans2(mode=2) 逆解服务后重试；无法接入时需人工现场确认。"
        suggestions = [
            "调整 RX/RY/RZ 姿态后重试",
            "将目标点适当移到更外侧或更高的位置",
            "增加中间点，避开关节限位或奇异区",
            "检查关节软限位和 Qt 离线逆解模型是否与控制器一致",
        ]
        if "path_singularity" in failed_ids:
            suggestions.insert(0, "优先采用系统建议的中间点绕行")
        return "建议：" + "；".join(dict.fromkeys(suggestions)) + "。"

    @staticmethod
    def _six_tuple(values: Iterable[float]) -> tuple[float, float, float, float, float, float]:
        padded = [float(value) for value in list(values)[:6]]
        padded += [0.0] * max(0, 6 - len(padded))
        return tuple(padded[:6])  # type: ignore[return-value]
