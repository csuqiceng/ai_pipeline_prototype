"""Safety precheck service used by the Web API."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any

from .motion_plan import MotionPlanService
from .robot_safety_checker import RobotSafetyChecker
from .runtime_paths import resolve_runtime_data_file
from .safety_precheck import SafetyPrecheckService
from .system_config import load_system_config


class WebPrecheckService:
    """Runs controller-independent safety checks before a plan can execute."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        kinematics_engine: Any = None,
        kinematics_engine_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path is not None else None
        self.kinematics_engine = kinematics_engine
        self.kinematics_engine_provider = kinematics_engine_provider

    def run_l1(self, snapshot: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
        config = self._config()
        return SafetyPrecheckService(config).run_l1(snapshot, plan)

    def run_plan(self, snapshot: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = dict(plan or {})
        pose = self._target_pose(plan.get("target"))
        if pose is None:
            l1 = self.run_l1(snapshot, plan)
            return {
                **l1,
                "valid": l1.get("status") == "pass",
                "robot_safety": None,
                "selected_fstatus": None,
            }

        config = self._config()
        l1_service = SafetyPrecheckService(config)
        motion_plan_service = MotionPlanService(
            engine=self._kinematics_engine(),
            joint_limits=tuple(config.joint_limits or ()),
        )
        result = RobotSafetyChecker(
            l1_service=l1_service,
            motion_plan_service=motion_plan_service,
            strict_l2=False,
        ).check_target(
            target_pose=pose,
            snapshot=snapshot,
            speed=dict(plan.get("speed", {}) or {}),
            start_pose=self._start_pose(snapshot),
            plan_id=str(plan.get("plan_id") or "web-plan"),
            func_id=int(plan.get("func_id") or plan.get("func") or 108),
        )
        return {
            "plan_id": str(plan.get("plan_id") or "web-plan"),
            "status": "pass" if result.get("safe") else "fail",
            "valid": bool(result.get("safe")),
            "items": list(result.get("items", []) or []),
            "suggestion": result.get("suggestion_zh"),
            "robot_safety": result,
            "selected_fstatus": (result.get("ik_result") or {}).get("selected_fstatus")
            if isinstance(result.get("ik_result"), dict)
            else None,
        }

    def _config(self):
        return load_system_config(self.config_path or resolve_runtime_data_file("system_config.json"))

    def _kinematics_engine(self) -> Any:
        if self.kinematics_engine_provider is not None:
            try:
                engine = self.kinematics_engine_provider()
                if engine is not None:
                    return engine
            except Exception:
                return self.kinematics_engine
        return self.kinematics_engine

    @staticmethod
    def _target_pose(target: Any) -> tuple[float, float, float, float, float, float] | None:
        if not isinstance(target, dict):
            return None
        values = []
        for key in ("x", "y", "z", "rx", "ry", "rz"):
            if key not in target and f"target_{key}" not in target:
                return None
            value = target.get(key, target.get(f"target_{key}"))
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                return None
        return tuple(values[:6])  # type: ignore[return-value]

    @staticmethod
    def _start_pose(snapshot: dict[str, Any]) -> tuple[float, float, float, float, float, float] | None:
        candidates = []
        position = snapshot.get("position") if isinstance(snapshot, dict) else None
        if isinstance(position, dict):
            candidates.extend(
                [
                    position.get("dpos_c"),
                    position.get("mpos_c"),
                    position.get("cartesian_pose"),
                    position.get("pose"),
                ]
            )
            cartesian = position.get("cartesian")
            if isinstance(cartesian, dict):
                candidates.append(cartesian)
        for candidate in candidates:
            pose = WebPrecheckService._target_pose(candidate)
            if pose is not None:
                return pose
            if isinstance(candidate, (list, tuple)) and len(candidate) >= 6:
                try:
                    return tuple(float(value) for value in candidate[:6])  # type: ignore[return-value]
                except (TypeError, ValueError):
                    continue
        return None
