from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class MemoryParams:
    last_motion_speed_pct: int = 50
    last_jog_speed_pct: int = 30
    last_home_speed_pct: int = 30
    last_calib_speed_pct: int = 20
    preferred_rehearsal_spd: int = 20
    total_commands: int = 0
    last_command_time: str = ""


class MemoryManager:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.memory = MemoryParams()
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        memory_payload = payload.get("memory", payload)
        self.memory = MemoryParams(**{**asdict(MemoryParams()), **memory_payload})

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.1",
            "updated_at": datetime.now().isoformat(),
            "memory": asdict(self.memory),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_after_command(self, action: str, params: dict[str, Any]) -> None:
        speed = params.get("spd_pct", params.get("speed_pct"))
        if isinstance(speed, (int, float)) and 0 <= int(speed) <= 100:
            if action == "移动":
                self.memory.last_motion_speed_pct = int(speed)
            elif action in {"点动", "关节运动", "虚拟轴运动"}:
                self.memory.last_jog_speed_pct = int(speed)
            elif action == "回零":
                self.memory.last_home_speed_pct = int(speed)
            elif action == "标定":
                self.memory.last_calib_speed_pct = int(speed)
        self.memory.total_commands += 1
        self.memory.last_command_time = datetime.now().isoformat()
        self._save()
