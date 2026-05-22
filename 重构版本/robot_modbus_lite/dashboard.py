"""Operator dashboard cache backed by the current runtime fields."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass(frozen=True)
class DashboardSnapshot:
    """Normalized snapshot used by operator UI, precheck, and local archives."""

    ts: str
    position: dict[str, Any] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)
    motion: dict[str, Any] = field(default_factory=dict)
    connection: dict[str, Any] = field(default_factory=dict)
    hardware: dict[str, Any] = field(default_factory=dict)
    refresh_ms: int = 50
    boards: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "refresh_ms": int(self.refresh_ms),
            "position": dict(self.position),
            "safety": dict(self.safety),
            "motion": dict(self.motion),
            "connection": dict(self.connection),
            "hardware": dict(self.hardware),
            "boards": dict(self.boards),
        }


class DashboardCache:
    """Builds and stores the latest operator-facing dashboard snapshot."""

    def __init__(
        self,
        *,
        refresh_ms: int = 50,
        clock: Callable[[], float] | None = None,
        stale_after_ms: int = 1000,
    ) -> None:
        self.refresh_ms = int(refresh_ms)
        self.clock = clock or time.monotonic
        self.stale_after_ms = int(stale_after_ms)
        self.snapshot = DashboardSnapshot(
            ts=datetime.now().isoformat(timespec="milliseconds"),
            refresh_ms=self.refresh_ms,
            boards=self._empty_boards(),
        )

    def update_from_source(self, source: Any) -> DashboardSnapshot:
        alarm_code = str(getattr(source, "alarm_code", "") or "")
        alarm_active = bool(alarm_code and alarm_code not in {"0", "ERR_000", "-"})
        monitor_label = getattr(source, "monitor_label", None)
        monitor_text = monitor_label.text() if monitor_label is not None and hasattr(monitor_label, "text") else ""
        monitor_online = monitor_text == "实时监控运行中"
        feedback_age_ms = self._feedback_age_ms(source, monitor_online=monitor_online)
        feedback_fresh = monitor_online and (
            feedback_age_ms is None or feedback_age_ms <= self.stale_after_ms
        )
        if monitor_online and feedback_fresh:
            realtime_feedback = "online"
            controller_state = "online"
        elif monitor_online:
            realtime_feedback = "stale"
            controller_state = "stale"
        else:
            realtime_feedback = "offline"
            controller_state = "unknown"
        position = {
            "x": getattr(source, "robot_x", "-"),
            "y": getattr(source, "robot_y", "-"),
            "z": getattr(source, "robot_z", "-"),
            "r": getattr(source, "robot_r", "-"),
            "joints": tuple(getattr(source, "robot_joints", ()) or ()),
            "mpos_j": tuple(getattr(source, "robot_mpos_joints", getattr(source, "robot_joints", ())) or ()),
            "mpos_c": tuple(getattr(source, "robot_mpos_pose", ()) or ()),
            "dpos_j": tuple(getattr(source, "robot_dpos_joints", ()) or ()),
            "dpos_c": tuple(getattr(source, "robot_dpos_pose", ()) or ()),
        }
        safety = {
            "estop": bool(getattr(source, "estop_active", False)),
            "paused": bool(getattr(source, "pause_active", False)),
            "alarm_active": alarm_active,
            "alarm_code": alarm_code or "-",
            "alarm_text": getattr(source, "alarm_text", "-"),
        }
        motion = {
            "busy": getattr(source, "busy", "-"),
            "running_state": getattr(source, "run_state", "-"),
            "current_func": getattr(source, "current_func_text", "-"),
            "speed": getattr(source, "robot_speed", "-"),
            "motion_percent": getattr(source, "motion_percent", "-"),
            "result": getattr(source, "result", "-"),
            "io_status": getattr(source, "io_status", "-"),
            "axis_status": tuple(getattr(source, "axis_status", ()) or ()),
            "motion_type": tuple(getattr(source, "motion_type", ()) or ()),
        }
        connection = {
            "realtime_feedback": realtime_feedback,
            "controller": controller_state,
            "feedback_age_ms": feedback_age_ms,
            "feedback_fresh": feedback_fresh,
            "stale_after_ms": self.stale_after_ms,
        }
        hardware = {
            "servo_enable": getattr(source, "servo_enable", "-"),
            "claw_enable": getattr(source, "claw_enable", "-"),
            "claw_brake": getattr(source, "claw_brake", "-"),
        }
        snapshot = DashboardSnapshot(
            ts=datetime.now().isoformat(timespec="milliseconds"),
            position=position,
            safety=safety,
            motion=motion,
            connection=connection,
            hardware=hardware,
            refresh_ms=self.refresh_ms,
            boards=self._build_boards(source, position, safety, motion, connection, hardware),
        )
        self.snapshot = snapshot
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot.to_dict()

    def _build_boards(
        self,
        source: Any,
        position: dict[str, Any],
        safety: dict[str, Any],
        motion: dict[str, Any],
        connection: dict[str, Any],
        hardware: dict[str, Any],
    ) -> dict[str, Any]:
        axis_ranges = getattr(source, "axis_ranges", None)
        l1_result = getattr(source, "_operator_last_precheck_result", None) or {}
        l2_result = getattr(source, "_operator_last_motion_plan_result", None) or {}
        l3_result = getattr(source, "_operator_last_process_precheck_result", None) or {}
        ecat_ok = connection.get("realtime_feedback") == "online"
        return {
            "device_status": {
                "system_state": motion.get("running_state", "-"),
                "estop": bool(safety.get("estop")),
                "pause": bool(safety.get("paused")),
                "alarm": bool(safety.get("alarm_active")),
                "alarm_code": safety.get("alarm_code", "-"),
                "mpos_j": position.get("mpos_j", ()),
                "mpos_c": position.get("mpos_c", ()),
                "dpos_j": position.get("dpos_j") or position.get("joints", ()),
                "dpos_c": position.get("dpos_c")
                or (position.get("x", "-"), position.get("y", "-"), position.get("z", "-")),
                "r_current": position.get("r", "-"),
                "z_current": position.get("z", "-"),
            },
            "action_feasibility": {
                "channel_idle": motion.get("busy") in {"空闲", "0", 0, "-", ""},
                "precheck_status": l1_result.get("status", "unknown"),
                "motion_status": l2_result.get("status", "unknown"),
                "current_func": motion.get("current_func", "-"),
                "result": motion.get("result", "-"),
            },
            "safety_boundary": {
                "x_range": getattr(axis_ranges, "x", None),
                "y_range": getattr(axis_ranges, "y", None),
                "z_range": getattr(axis_ranges, "z", None),
                "safe_r_range": (
                    getattr(axis_ranges, "safe_r_min", None),
                    getattr(axis_ranges, "safe_r_max", None),
                ),
                "safe_z_range": (
                    getattr(axis_ranges, "safe_z_min", None),
                    getattr(axis_ranges, "safe_z_max", None),
                ),
                "joint_limits": tuple(getattr(axis_ranges, "joint_limits", ()) or ()),
                "current_r": position.get("r", "-"),
                "current_z": position.get("z", "-"),
            },
            "motion_limits": {
                "speed": motion.get("speed", "-"),
                "motion_percent": motion.get("motion_percent", "-"),
                "safe_speed_max": getattr(axis_ranges, "safe_speed_max", None),
                "safe_acc_max": getattr(axis_ranges, "safe_acc_max", None),
                "safe_dec_max": getattr(axis_ranges, "safe_dec_max", None),
                "axis_status": motion.get("axis_status", ()),
                "motion_type": motion.get("motion_type", ()),
            },
            "process_preview": {
                "flow_status": getattr(source, "flow_status", "-"),
                "flow_current_step": getattr(source, "flow_current_step", "-"),
                "current_flow_name": getattr(source, "current_flow_name", "") or "-",
                "l3_status": l3_result.get("status", "unknown"),
                "progress_percent": self._progress_percent(l3_result),
                "risk_summary": self._result_messages(l3_result),
            },
            "process_adaptation": {
                "l2_status": l2_result.get("status", "unknown"),
                "fstatus": l2_result.get("selected_fstatus", l2_result.get("fstatus", "-")),
                "singularity": l2_result.get("singularity", "-"),
                "suggestion": l2_result.get("suggestion", "-"),
                "rejected_fstatuses": l2_result.get("rejected_fstatuses", ()),
                "need_midpoint": bool(l2_result.get("need_midpoint", False)),
                "midpoint_pose": l2_result.get("midpoint_pose"),
                "midpoint_fstatus": l2_result.get("midpoint_fstatus"),
            },
            "communication_faults": {
                "ecat_ok": ecat_ok,
                "controller": connection.get("controller", "unknown"),
                "realtime_feedback": connection.get("realtime_feedback", "unknown"),
                "feedback_age_ms": connection.get("feedback_age_ms"),
                "feedback_fresh": connection.get("feedback_fresh"),
                "stale_after_ms": connection.get("stale_after_ms"),
                "io_status": motion.get("io_status", "-"),
                "servo_enable": hardware.get("servo_enable", "-"),
            },
        }

    def _feedback_age_ms(self, source: Any, *, monitor_online: bool) -> int | None:
        last_feedback = getattr(source, "_last_feedback_monotonic_sec", None)
        if last_feedback is None:
            return None if not monitor_online else 0
        try:
            age_ms = int(round((self.clock() - float(last_feedback)) * 1000))
        except (TypeError, ValueError):
            return None if not monitor_online else 0
        return max(0, age_ms)

    @staticmethod
    def _empty_boards() -> dict[str, Any]:
        return {
            "device_status": {},
            "action_feasibility": {},
            "safety_boundary": {},
            "motion_limits": {},
            "process_preview": {},
            "process_adaptation": {},
            "communication_faults": {},
        }

    @staticmethod
    def _result_messages(result: dict[str, Any]) -> list[str]:
        messages: list[str] = []
        for item in result.get("items", []) or []:
            if isinstance(item, dict) and item.get("message"):
                messages.append(str(item["message"]))
        return messages

    @staticmethod
    def _progress_percent(result: dict[str, Any]) -> int | str:
        if "progress_percent" in result:
            return result.get("progress_percent", "-")
        if result.get("status") == "pass":
            return 100
        return "-"
