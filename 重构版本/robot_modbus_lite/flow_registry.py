from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .permission_service import PermissionService


class FlowState(Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    REHEARSAL = "rehearsal"


VALID_TRANSITIONS: dict[FlowState, frozenset[FlowState]] = {
    FlowState.IDLE: frozenset({FlowState.READY, FlowState.REHEARSAL}),
    FlowState.READY: frozenset({FlowState.RUNNING, FlowState.IDLE, FlowState.REHEARSAL}),
    FlowState.RUNNING: frozenset({FlowState.PAUSED, FlowState.COMPLETED, FlowState.ERROR}),
    FlowState.PAUSED: frozenset({FlowState.RUNNING, FlowState.IDLE}),
    FlowState.COMPLETED: frozenset({FlowState.IDLE}),
    FlowState.ERROR: frozenset({FlowState.IDLE}),
    FlowState.REHEARSAL: frozenset({FlowState.IDLE, FlowState.READY}),
}


@dataclass
class FlowStep:
    step_id: int
    action: str
    func_id: int
    params: dict[str, Any] = field(default_factory=dict)
    position_name: str | None = None
    spd_pct: int = 50
    description: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FlowStep":
        return cls(
            step_id=int(payload.get("step_id", 0)),
            action=str(payload.get("action", "")),
            func_id=int(payload.get("func_id", 0)),
            params=dict(payload.get("params", {})),
            position_name=payload.get("position_name"),
            spd_pct=int(payload.get("spd_pct", payload.get("speed_pct", 50))),
            description=str(payload.get("description", "")),
        )


@dataclass
class FlowEntry:
    name: str
    description: str = ""
    steps: list[FlowStep] = field(default_factory=list)
    step_delay_ms: int = 1000
    rehearsal_spd: int = 20
    confirmed: bool = False
    created_by: str = "operator"
    version: int = 1
    state: str = FlowState.IDLE.value
    current_step: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FlowEntry":
        steps = [FlowStep.from_dict(dict(item)) for item in payload.get("steps", [])]
        return cls(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            steps=steps,
            step_delay_ms=int(payload.get("step_delay_ms", 1000)),
            rehearsal_spd=int(payload.get("rehearsal_spd", 20)),
            confirmed=bool(payload.get("confirmed", False)),
            created_by=str(payload.get("created_by", "operator")),
            version=int(payload.get("version", 1)),
            state=str(payload.get("state", FlowState.IDLE.value)),
            current_step=int(payload.get("current_step", 0)),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


class FlowRegistry:
    def __init__(self, path: str | Path, *, permission: PermissionService | None = None):
        self.path = Path(path)
        self.permission = permission or PermissionService("operator")
        self._flows: dict[str, FlowEntry] = {}
        self._load()

    @staticmethod
    def _key(name: str) -> str:
        return str(name or "").strip().lower()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for item in payload.get("flows", []):
            flow = FlowEntry.from_dict(dict(item))
            if flow.name:
                self._flows[self._key(flow.name)] = flow

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.1",
            "updated_at": datetime.now().isoformat(),
            "flows": [asdict(flow) for flow in sorted(self._flows.values(), key=lambda item: item.name)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, entry: FlowEntry) -> tuple[bool, str]:
        self.permission.require("flow.create")
        key = self._key(entry.name)
        if not key:
            return False, "流程名称不能为空"
        if key in self._flows:
            return False, f"流程'{entry.name}'已存在"
        now = datetime.now().isoformat()
        entry.created_at = entry.created_at or now
        entry.updated_at = now
        entry.created_by = entry.created_by or self.permission.normalized_actor()
        self._flows[key] = entry
        self._save()
        return True, f"流程'{entry.name}'已保存"

    def update(self, name: str, *, create_draft: bool = False, **kwargs: Any) -> tuple[bool, str]:
        self.permission.require("flow.update")
        flow = self.get(name)
        if flow is None:
            return False, f"流程'{name}'不存在"
        if flow.confirmed and not create_draft:
            return False, f"流程'{flow.name}'已确认，修改需生成草稿版本"
        if flow.confirmed and create_draft:
            flow.confirmed = False
            flow.version += 1
            flow.state = FlowState.IDLE.value
        for key in ("description", "steps", "step_delay_ms", "rehearsal_spd"):
            if key in kwargs:
                setattr(flow, key, kwargs[key])
        flow.updated_at = datetime.now().isoformat()
        self._save()
        return True, f"流程'{flow.name}'已更新"

    def remove(self, name: str) -> tuple[bool, str]:
        self.permission.require("flow.delete")
        key = self._key(name)
        if key not in self._flows:
            return False, f"流程'{name}'不存在"
        del self._flows[key]
        self._save()
        return True, f"流程'{name}'已删除"

    def confirm(self, name: str) -> tuple[bool, str]:
        self.permission.require("flow.confirm")
        flow = self.get(name)
        if flow is None:
            return False, f"流程'{name}'不存在"
        flow.confirmed = True
        flow.state = FlowState.READY.value
        flow.updated_at = datetime.now().isoformat()
        self._save()
        return True, f"流程'{flow.name}'已确认"

    def transition(self, name: str, target: FlowState) -> bool:
        flow = self.get(name)
        if flow is None:
            return False
        current = FlowState(flow.state)
        if target not in VALID_TRANSITIONS[current]:
            return False
        flow.state = target.value
        flow.updated_at = datetime.now().isoformat()
        self._save()
        return True

    def start_rehearsal(self, name: str) -> tuple[bool, str]:
        self.permission.require("flow.rehearsal")
        flow = self.get(name)
        if flow is None:
            return False, f"流程'{name}'不存在"
        if not self.transition(name, FlowState.REHEARSAL):
            return False, f"流程'{name}'当前状态不可演练"
        return True, f"流程'{name}'演练模式启动，速度{flow.rehearsal_spd}%"

    def get(self, name: str) -> FlowEntry | None:
        return self._flows.get(self._key(name))

    def list_all(self) -> list[FlowEntry]:
        return list(sorted(self._flows.values(), key=lambda item: item.name))
