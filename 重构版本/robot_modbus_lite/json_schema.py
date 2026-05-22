"""Standard V2.1 JSON structures for AI-facing data exchange."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dashboard import DashboardSnapshot


COMMAND_INTENT_REQUIRED = (
    "msg_type",
    "msg_id",
    "timestamp",
    "source",
    "raw_text",
    "semantic_level",
    "intent",
    "func_id",
    "confidence",
    "params",
    "fuzzy",
    "emergency_code",
    "is_emergency",
    "priority",
)

INTERACTION_RECORD_REQUIRED = (
    "msg_type",
    "msg_id",
    "session_id",
    "timestamp_start",
    "timestamp_end",
    "duration_ms",
    "input",
    "nlp_result",
    "safety_check",
    "execution",
    "response",
    "device_snapshot",
)

DEVICE_SNAPSHOT_REQUIRED = ("msg_type", "timestamp", "dashboard_type", "refresh_ms", "data")


@dataclass(frozen=True)
class CommandIntent:
    """Type A command intent structure from V2.1 section 6.2."""

    msg_id: str
    timestamp: str
    source: str
    raw_text: str
    semantic_level: int
    intent: str
    func_id: int | None
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    fuzzy: dict[str, Any] = field(default_factory=dict)
    emergency_code: str | None = None
    is_emergency: bool = False
    priority: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_type": "command_intent",
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "raw_text": self.raw_text,
            "semantic_level": int(self.semantic_level),
            "intent": self.intent,
            "func_id": self.func_id,
            "confidence": float(self.confidence),
            "params": dict(self.params),
            "fuzzy": dict(self.fuzzy),
            "emergency_code": self.emergency_code,
            "is_emergency": bool(self.is_emergency),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class InteractionRecord:
    """Type B interaction record structure for archive and model training."""

    msg_id: str
    session_id: str
    timestamp_start: str
    timestamp_end: str
    duration_ms: int
    input: dict[str, Any]
    nlp_result: dict[str, Any]
    safety_check: dict[str, Any]
    execution: dict[str, Any]
    response: dict[str, Any]
    device_snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_type": "interaction_record",
            "msg_id": self.msg_id,
            "session_id": self.session_id,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "duration_ms": int(self.duration_ms),
            "input": dict(self.input),
            "nlp_result": dict(self.nlp_result),
            "safety_check": dict(self.safety_check),
            "execution": dict(self.execution),
            "response": dict(self.response),
            "device_snapshot": dict(self.device_snapshot),
        }


@dataclass(frozen=True)
class DeviceSnapshot:
    """Type C device snapshot structure consumed by UI and AI interfaces."""

    timestamp: str
    dashboard_type: str
    refresh_ms: int
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dashboard_snapshot(
        cls,
        snapshot: DashboardSnapshot,
        *,
        dashboard_type: str = "status",
        refresh_ms: int | None = None,
    ) -> "DeviceSnapshot":
        position = snapshot.position
        safety = snapshot.safety
        motion = snapshot.motion
        connection = snapshot.connection
        joints = list(position.get("joints", ()) or ())
        data = {
            "system_state": str(motion.get("running_state") or "unknown"),
            "func_id_current": motion.get("current_func", "-"),
            "estop": bool(safety.get("estop")),
            "pause": bool(safety.get("paused")),
            "alarm": bool(safety.get("alarm_active")),
            "ready": connection.get("realtime_feedback") == "online",
            "dpos_j": joints,
            "dpos_c": [
                position.get("x", 0.0),
                position.get("y", 0.0),
                position.get("z", 0.0),
            ],
            "mpos_j": list(position.get("mpos_j", ()) or ()),
            "spd_pct_j": list(position.get("spd_pct_j", ()) or ()),
            "r_current": position.get("r", 0.0),
            "z_current": position.get("z", 0.0),
            "ecat_ok": connection.get("realtime_feedback") == "online",
            "alarm_code": safety.get("alarm_code", 0),
        }
        effective_refresh_ms = refresh_ms if refresh_ms is not None else getattr(snapshot, "refresh_ms", 50)
        return cls(timestamp=snapshot.ts, dashboard_type=dashboard_type, refresh_ms=effective_refresh_ms, data=data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_type": "device_snapshot",
            "timestamp": self.timestamp,
            "dashboard_type": self.dashboard_type,
            "refresh_ms": int(self.refresh_ms),
            "data": dict(self.data),
        }


def validate_command_intent(data: dict[str, Any]) -> str | None:
    if data.get("msg_type") != "command_intent":
        return "command_intent.msg_type 必须为 command_intent"
    error = _validate_required("command_intent", data, COMMAND_INTENT_REQUIRED)
    if error:
        return error
    if not isinstance(data.get("params"), dict):
        return "command_intent.params 必须为对象"
    if not isinstance(data.get("fuzzy"), dict):
        return "command_intent.fuzzy 必须为对象"
    return None


def validate_interaction_record(data: dict[str, Any]) -> str | None:
    if data.get("msg_type") != "interaction_record":
        return "interaction_record.msg_type 必须为 interaction_record"
    error = _validate_required("interaction_record", data, INTERACTION_RECORD_REQUIRED)
    if error:
        return error
    for key in ("input", "nlp_result", "safety_check", "execution", "response", "device_snapshot"):
        if not isinstance(data.get(key), dict):
            return f"interaction_record.{key} 必须为对象"
    return None


def validate_device_snapshot(data: dict[str, Any]) -> str | None:
    if data.get("msg_type") != "device_snapshot":
        return "device_snapshot.msg_type 必须为 device_snapshot"
    error = _validate_required("device_snapshot", data, DEVICE_SNAPSHOT_REQUIRED)
    if error:
        return error
    if not isinstance(data.get("data"), dict):
        return "device_snapshot.data 必须为对象"
    return None


def _validate_required(name: str, data: dict[str, Any], required: tuple[str, ...]) -> str | None:
    for key in required:
        if key not in data:
            return f"{name} 缺少字段: {key}"
    return None
