"""Safety review adapter for restricted Agent command drafts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterable

from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.motion_plan import MotionPlanService
from robot_modbus_lite.safety_precheck import SafetyPrecheckService


class SafetyReviewAgent:
    """Run existing L1/L2 prechecks and normalize results for Agent confirmation."""

    def __init__(
        self,
        *,
        l1_service: SafetyPrecheckService,
        motion_plan_service: MotionPlanService | None = None,
        pose_angle_checker: Callable[..., dict[str, Any]] | None = None,
        robot_safety_checker: Any = None,
        strict_l2: bool = False,
    ) -> None:
        self.l1_service = l1_service
        self.motion_plan_service = motion_plan_service
        self.pose_angle_checker = pose_angle_checker
        self.robot_safety_checker = robot_safety_checker
        self.strict_l2 = bool(strict_l2)

    def review(
        self,
        draft: CommandDraft,
        *,
        snapshot: dict[str, Any],
        start_pose: Iterable[float] | None = None,
    ) -> dict[str, Any]:
        is_linear_motion = draft.intent in {"move_linear", "continuous_path"} or draft.func_id in {8, 102, 108, 112}
        if self.robot_safety_checker is not None and is_linear_motion:
            return self._review_with_robot_safety_checker(draft, snapshot=snapshot, start_pose=start_pose)

        l1_plan = self._draft_to_l1_plan(draft, start_pose=start_pose)
        l1 = self.l1_service.run_l1(snapshot, l1_plan)
        items = [dict(item) for item in l1.get("items", [])]
        if l1.get("status") != "pass":
            summary = self._with_suggestion(
                self._failure_summary("L1预检未通过。", items),
                l1.get("suggestion"),
            )
            return {
                "valid": False,
                "status": "fail",
                "blocking_level": "L1",
                "summary": summary,
                "items": items,
                "l1": l1,
                "l2": None,
                "selected_fstatus": None,
                "suggestion": l1.get("suggestion"),
            }

        if self.motion_plan_service is None or not is_linear_motion:
            return {
                "valid": True,
                "status": "pass",
                "blocking_level": None,
                "summary": "L1通过。",
                "items": items,
                "l1": l1,
                "l2": None,
                "selected_fstatus": None,
                "suggestion": None,
            }

        l2 = self.motion_plan_service.plan(
            target_pose=self._target_pose(draft, start_pose=start_pose),
            start_pose=start_pose,
        )
        items.extend(dict(item) for item in l2.get("items", []))
        l2_status = str(l2.get("status") or "")
        if l2_status == "pass":
            pose_result = self._run_pose_angle_check(draft=draft, l2_result=l2, snapshot=snapshot)
            if pose_result is not None:
                items.extend(dict(item) for item in pose_result.get("items", []))
                if str(pose_result.get("status") or "") != "pass":
                    return {
                        "valid": False,
                        "status": "fail",
                        "blocking_level": "POSE",
                        "summary": "L1通过，L2通过，姿态夹角未通过。",
                        "items": items,
                        "l1": l1,
                        "l2": l2,
                        "pose_angles": pose_result,
                        "selected_fstatus": l2.get("selected_fstatus"),
                        "suggestion": pose_result.get("suggestion"),
                    }
            return {
                "valid": True,
                "status": "pass",
                "blocking_level": None,
                "summary": f"L1通过，L2通过，FSTATUS={l2.get('selected_fstatus')}。"
                if pose_result is None
                else f"L1通过，L2通过，姿态夹角通过，FSTATUS={l2.get('selected_fstatus')}。",
                "items": items,
                "l1": l1,
                "l2": l2,
                "pose_angles": pose_result,
                "selected_fstatus": l2.get("selected_fstatus"),
                "suggestion": l2.get("suggestion"),
            }
        if l2_status == "unavailable" and not self.strict_l2:
            return {
                "valid": True,
                "status": "warning",
                "blocking_level": None,
                "summary": "L1安全检查通过；L2运动规划预演暂不可用，需现场确认。",
                "items": items,
                "l1": l1,
                "l2": l2,
                "selected_fstatus": None,
                "suggestion": l2.get("suggestion"),
            }
        return {
            "valid": False,
            "status": "fail",
            "blocking_level": "L2",
            "summary": self._with_suggestion(self._l2_failure_summary(l2), self._l2_operator_suggestion(l2)),
            "items": items,
            "l1": l1,
            "l2": l2,
            "selected_fstatus": l2.get("selected_fstatus"),
            "suggestion": l2.get("suggestion"),
        }

    def _review_with_robot_safety_checker(
        self,
        draft: CommandDraft,
        *,
        snapshot: dict[str, Any],
        start_pose: Iterable[float] | None,
    ) -> dict[str, Any]:
        result = self.robot_safety_checker.check_target(
            target_pose=self._target_pose(draft, start_pose=start_pose),
            snapshot=snapshot,
            speed={
                "spd_pct": draft.params.get("spd_pct"),
                "acc_pct": draft.params.get("acc_pct"),
                "dec_pct": draft.params.get("dec_pct"),
            },
            start_pose=start_pose,
            plan_id=draft.draft_id,
            func_id=draft.func_id,
        )
        items = [dict(item) for item in result.get("items", [])]
        l2 = result.get("l2") if isinstance(result.get("l2"), dict) else None
        pose_angles = result.get("pose_angles") if isinstance(result.get("pose_angles"), dict) else None
        selected_fstatus = None
        ik_result = result.get("ik_result")
        if isinstance(ik_result, dict):
            selected_fstatus = ik_result.get("selected_fstatus")
        if selected_fstatus is None and l2 is not None:
            selected_fstatus = l2.get("selected_fstatus")
        if bool(result.get("safe")):
            return {
                "valid": True,
                "status": "pass",
                "blocking_level": None,
                "summary": str(result.get("detail_zh") or "安全判定通过。"),
                "items": items,
                "l1": result.get("l1"),
                "l2": l2,
                "pose_angles": pose_angles,
                "robot_safety": result,
                "selected_fstatus": selected_fstatus,
                "suggestion": result.get("suggestion_zh"),
            }
        blocking_level = result.get("blocking_level") or "SAFETY"
        return {
            "valid": False,
            "status": "fail",
            "blocking_level": blocking_level,
            "summary": str(result.get("detail_zh") or "安全判定未通过。"),
            "items": items,
            "l1": result.get("l1"),
            "l2": l2,
            "pose_angles": pose_angles,
            "robot_safety": result,
            "selected_fstatus": selected_fstatus,
            "suggestion": result.get("suggestion_zh"),
        }

    def _run_pose_angle_check(
        self,
        *,
        draft: CommandDraft,
        l2_result: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.pose_angle_checker is None or draft.func_id != 108:
            return None
        result = self.pose_angle_checker(draft=draft, l2_result=l2_result, snapshot=snapshot)
        return dict(result or {})

    @staticmethod
    def _draft_to_l1_plan(draft: CommandDraft, *, start_pose: Iterable[float] | None = None) -> dict[str, Any]:
        params = draft.params
        is_motion = draft.intent in {"move_linear", "continuous_path"} or draft.func_id in {8, 102, 108, 112}
        plan: dict[str, Any] = {
            "plan_id": draft.draft_id,
            "func_id": int(draft.func_id),
            "action_type": "move" if is_motion else "",
            "target": {},
            "speed": {},
        }
        if is_motion:
            pose = SafetyReviewAgent._target_pose(draft, start_pose=start_pose)
            plan["target"] = {
                "x": pose[0],
                "y": pose[1],
                "z": pose[2],
            }
            plan["speed"] = {
                "spd_pct": params.get("spd_pct"),
                "acc_pct": params.get("acc_pct"),
                "dec_pct": params.get("dec_pct"),
            }
        return plan

    @staticmethod
    def _failure_summary(prefix: str, items: list[dict[str, Any]]) -> str:
        failed_messages = [
            str(item.get("message") or "").strip()
            for item in items
            if str(item.get("status") or "") == "fail" and str(item.get("message") or "").strip()
        ]
        if not failed_messages:
            return prefix
        return f"{prefix}失败项：" + "；".join(failed_messages[:3])

    @staticmethod
    def _with_suggestion(summary: str, suggestion: Any) -> str:
        text = str(summary or "").strip()
        suggestion_text = str(suggestion or "").strip()
        if not suggestion_text or suggestion_text in text:
            return text
        return f"{text} 建议：{suggestion_text}"

    @staticmethod
    def _l2_failure_summary(l2: dict[str, Any]) -> str:
        failed_messages = [
            str(item.get("message") or "").strip()
            for item in list(l2.get("items") or [])
            if str(item.get("status") or "") == "fail" and str(item.get("message") or "").strip()
        ]
        if failed_messages:
            return "L1通过，但 L2逆解失败：" + "；".join(failed_messages[:3])
        suggestion = str(l2.get("suggestion") or "").strip()
        if suggestion:
            return f"L1通过，但 L2逆解失败：{suggestion}"
        return "L1通过，但 L2逆解失败：未找到可执行的关节构型。"

    @staticmethod
    def _l2_operator_suggestion(l2: dict[str, Any]) -> str:
        failed_ids = {
            str(item.get("id") or "")
            for item in list(l2.get("items") or [])
            if str(item.get("status") or "") == "fail"
        }
        if "kinematics_engine" in failed_ids or str(l2.get("status") or "") == "unavailable":
            return "接入或检查 FrameTrans2(mode=2) 逆解服务后重试；无法接入时需人工现场确认。"
        suggestions = [
            "调整 RX/RY/RZ 姿态后重试",
            "将目标点适当移到更外侧或更高的位置",
            "增加中间点，避开关节限位或奇异区",
            "检查关节软限位和 Qt 离线逆解模型是否与控制器一致",
        ]
        if "path_singularity" in failed_ids:
            suggestions.insert(0, "优先采用系统建议的中间点绕行")
        return "；".join(dict.fromkeys(suggestions)) + "。"

    @staticmethod
    def _target_pose(
        draft: CommandDraft,
        *,
        start_pose: Iterable[float] | None = None,
    ) -> tuple[float, float, float, float, float, float]:
        params = draft.params
        raw_pose = (
            float(params["target_x"]),
            float(params["target_y"]),
            float(params["target_z"]),
            float(params["target_rx"]),
            float(params["target_ry"]),
            float(params["target_rz"]),
        )
        if not SafetyReviewAgent._is_incremental_motion(draft):
            return raw_pose
        if start_pose is None:
            return raw_pose
        base = SafetyReviewAgent._six_tuple(start_pose)
        return tuple(base[index] + raw_pose[index] for index in range(6))  # type: ignore[return-value]

    @staticmethod
    def _is_incremental_motion(draft: CommandDraft) -> bool:
        params = draft.params
        try:
            if int(float(params.get("position_increment", 0) or 0)) == 1:
                return True
        except (TypeError, ValueError):
            pass
        try:
            return int(float(params.get("fuzzy_pos", 0) or 0)) == 1
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _six_tuple(values: Iterable[float]) -> tuple[float, float, float, float, float, float]:
        padded = [float(value) for value in list(values)[:6]]
        padded += [0.0] * max(0, 6 - len(padded))
        return tuple(padded[:6])  # type: ignore[return-value]
