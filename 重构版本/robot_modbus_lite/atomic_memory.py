"""Runtime memory for the V2.0 secondary atomic command layer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import QueryRecord


@dataclass
class AtomicMemory:
    """Remember operator preferences used by fuzzy atomic commands."""

    current_speed: float = 50.0
    current_step_mm: float = 10.0
    current_step_deg: float = 5.0
    current_acc: float = 100.0
    current_dec: float = 100.0
    confirm_mode: str = "beginner"
    positions: dict[str, tuple[float, float, float, float, float, float]] = field(default_factory=dict)
    last_direction: tuple[float, float, float] | None = None
    last_step: float | None = None
    last_record: QueryRecord | None = None
    last_command_params: dict[str, Any] | None = None
    position_stack: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)

    def set_speed(self, value: float) -> None:
        self.current_speed = self._clamp_pct(value)

    def speed_up(self, delta: float = 10.0) -> None:
        self.set_speed(self.current_speed + delta)

    def speed_down(self, delta: float = 10.0) -> None:
        self.set_speed(self.current_speed - delta)

    def set_acc(self, value: float) -> None:
        self.current_acc = self._clamp_pct(value)

    def set_dec(self, value: float) -> None:
        self.current_dec = self._clamp_pct(value)

    def set_step_mm(self, value: float) -> None:
        self.current_step_mm = max(0.1, float(value))

    def set_step_deg(self, value: float) -> None:
        self.current_step_deg = max(0.1, float(value))

    def set_confirm_mode(self, mode: str) -> None:
        if mode not in {"beginner", "skilled", "expert"}:
            raise ValueError(f"unsupported confirm mode: {mode}")
        self.confirm_mode = mode

    def save_position(self, name: str, pose: tuple[float, float, float, float, float, float]) -> None:
        self.positions[self._normalize_name(name)] = self._pose6(pose)

    def get_position(self, name: str) -> tuple[float, float, float, float, float, float] | None:
        return self.positions.get(self._normalize_name(name))

    def delete_position(self, name: str) -> None:
        self.positions.pop(self._normalize_name(name), None)

    def push_position(self, pose: tuple[float, float, float, float, float, float]) -> None:
        self.position_stack.append(self._pose6(pose))

    def pop_position(self) -> tuple[float, float, float, float, float, float] | None:
        if not self.position_stack:
            return None
        return self.position_stack.pop()

    def remember_record(self, record: QueryRecord) -> None:
        self.last_record = record
        self.last_command_params = dict(record.params)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_speed": self.current_speed,
            "current_step_mm": self.current_step_mm,
            "current_step_deg": self.current_step_deg,
            "current_acc": self.current_acc,
            "current_dec": self.current_dec,
            "confirm_mode": self.confirm_mode,
            "positions": {name: list(pose) for name, pose in self.positions.items()},
            "position_stack": [list(pose) for pose in self.position_stack],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AtomicMemory":
        memory = cls()
        if not isinstance(payload, dict):
            return memory
        memory.set_speed(float(payload.get("current_speed", memory.current_speed)))
        memory.set_step_mm(float(payload.get("current_step_mm", memory.current_step_mm)))
        memory.set_step_deg(float(payload.get("current_step_deg", memory.current_step_deg)))
        memory.set_acc(float(payload.get("current_acc", memory.current_acc)))
        memory.set_dec(float(payload.get("current_dec", memory.current_dec)))
        try:
            memory.set_confirm_mode(str(payload.get("confirm_mode", memory.confirm_mode)))
        except ValueError:
            memory.confirm_mode = "beginner"
        positions = payload.get("positions", {})
        if isinstance(positions, dict):
            for name, pose in positions.items():
                try:
                    memory.save_position(str(name), tuple(float(value) for value in pose[:6]))  # type: ignore[index]
                except Exception:
                    continue
        stack = payload.get("position_stack", [])
        if isinstance(stack, list):
            for pose in stack:
                try:
                    memory.push_position(tuple(float(value) for value in pose[:6]))  # type: ignore[index]
                except Exception:
                    continue
        return memory

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "AtomicMemory":
        source = Path(path)
        if not source.exists():
            return cls()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        return cls.from_dict(payload if isinstance(payload, dict) else {})

    @staticmethod
    def _normalize_name(name: str) -> str:
        return str(name or "").strip().upper()

    @staticmethod
    def _pose6(pose: tuple[float, float, float, float, float, float]) -> tuple[float, float, float, float, float, float]:
        values = tuple(float(value) for value in pose)
        if len(values) != 6:
            raise ValueError("pose must contain exactly 6 values")
        return values  # type: ignore[return-value]

    @staticmethod
    def _clamp_pct(value: float) -> float:
        return min(150.0, max(5.0, float(value)))
