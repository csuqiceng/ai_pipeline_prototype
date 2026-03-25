from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class VoiceInput:
    text: str
    intent: str
    target_area: str | None = None
    destination_area: str | None = None
    confidence: float = 0.0
    timestamp: str = field(default_factory=now_iso)


@dataclass
class VisionInput:
    camera_id: str
    target_found: bool
    target_id: str | None = None
    target_type: str | None = None
    position: list[float] | None = None
    angle: float | None = None
    confidence: float = 0.0
    safe_region_ok: bool = False
    timestamp: str = field(default_factory=now_iso)


@dataclass
class Task:
    task_id: str
    task_type: str
    target_id: str | None = None
    target_area: str | None = None
    destination_area: str | None = None
    pick_point: list[float] | None = None
    place_point: list[float] | None = None
    pose: list[float] | None = None
    speed: int = 80
    priority: str = "normal"
    safety_check: bool = True
    source: str = "voice+vision+planner"
    confidence: float = 0.0
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DispatchState(str, Enum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    STOPPING = "STOPPING"
    HOMING = "HOMING"
    MOVE_TO_PICK = "MOVE_TO_PICK"
    GRIP = "GRIP"
    MOVE_TO_PLACE = "MOVE_TO_PLACE"
    RELEASE = "RELEASE"
    DONE = "DONE"
    ERROR = "ERROR"


@dataclass
class DispatchResult:
    task_id: str
    final_state: DispatchState
    success: bool
    message: str
    history: list[str]


@dataclass
class ControllerStatus:
    connected: bool
    servo_enabled: bool
    alarm_active: bool
    emergency_stopped: bool
    current_pose: list[float]
    last_command: str | None = None


@dataclass
class AlarmEvent:
    code: str
    message: str
    level: str
    timestamp: str = field(default_factory=now_iso)
