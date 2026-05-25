from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .permission_service import PermissionService


Pose6 = tuple[float, float, float, float, float, float]


def _pose6(values: Any) -> Pose6:
    seq = list(values)
    if len(seq) < 6:
        raise ValueError("position pose requires 6 numeric values")
    return tuple(float(v) for v in seq[:6])  # type: ignore[return-value]


@dataclass
class PositionEntry:
    name: str
    pose: Pose6
    spd: int = 50
    move_type: int = 0
    locked: bool = False
    is_system: bool = False
    created_by: str = "operator"
    created_at: str = ""
    updated_at: str = ""

    def normalized_name(self) -> str:
        return self.name.strip().lower()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pose"] = list(self.pose)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionEntry":
        return cls(
            name=str(payload.get("name", "")).strip(),
            pose=_pose6(payload.get("pose", (0, 0, 0, 0, 0, 0))),
            spd=int(payload.get("spd", 50)),
            move_type=int(payload.get("move_type", 0)),
            locked=bool(payload.get("locked", False)),
            is_system=bool(payload.get("is_system", False)),
            created_by=str(payload.get("created_by", "operator")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    def to_func108_params(self, spd_override: int | None = None) -> dict[str, float | int | str]:
        x, y, z, rx, ry, rz = self.pose
        return {
            "target_x": x,
            "target_y": y,
            "target_z": z,
            "target_rx": rx,
            "target_ry": ry,
            "target_rz": rz,
            "spd_pct": int(spd_override if spd_override is not None else self.spd),
            "acc_pct": int(spd_override if spd_override is not None else self.spd),
            "dec_pct": int(spd_override if spd_override is not None else self.spd),
            "move_type": int(self.move_type),
            "fuzzy_pos": 0,
            "fuzzy_spd": 0,
            "fuzzy_acc": 1,
            "fuzzy_dec": 1,
            "position_name": self.name,
        }


class PositionRegistry:
    def __init__(self, path: str | Path, *, permission: PermissionService | None = None):
        self.path = Path(path)
        self.permission = permission or PermissionService("operator")
        self._positions: dict[str, PositionEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        items = payload.get("positions", [])
        if isinstance(items, dict):
            items = items.values()
        for item in items:
            entry = PositionEntry.from_dict(dict(item))
            if entry.name:
                self._positions[entry.normalized_name()] = entry

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.1",
            "updated_at": datetime.now().isoformat(),
            "positions": [entry.to_dict() for entry in sorted(self._positions.values(), key=lambda e: e.name)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, entry: PositionEntry) -> tuple[bool, str]:
        self.permission.require("position.create")
        key = entry.normalized_name()
        if not key:
            return False, "位置名称不能为空"
        if key in self._positions:
            return False, f"位置'{entry.name}'已存在"
        now = datetime.now().isoformat()
        entry.created_at = entry.created_at or now
        entry.updated_at = now
        entry.created_by = entry.created_by or self.permission.normalized_actor()
        self._positions[key] = entry
        self._save()
        return True, f"位置'{entry.name}'已保存"

    def update(self, name: str, **kwargs: Any) -> tuple[bool, str]:
        self.permission.require("position.update")
        entry = self.get(name)
        if entry is None:
            return False, f"位置'{name}'不存在"
        if entry.locked and not kwargs.pop("unlock", False):
            return False, f"位置'{entry.name}'已锁定，不可修改"
        for key in ("pose", "spd", "move_type", "locked", "is_system"):
            if key in kwargs:
                value = _pose6(kwargs[key]) if key == "pose" else kwargs[key]
                setattr(entry, key, value)
        entry.updated_at = datetime.now().isoformat()
        self._save()
        return True, f"位置'{entry.name}'已更新"

    def set_position(self, name: str, pose: Pose6, **kwargs: Any) -> tuple[bool, str]:
        existing = self.get(name)
        if existing is None:
            return self.add(PositionEntry(name=name, pose=_pose6(pose), **kwargs))
        return self.update(name, pose=pose, **kwargs)

    def remove(self, name: str) -> tuple[bool, str]:
        self.permission.require("position.delete")
        entry = self.get(name)
        if entry is None:
            return False, f"位置'{name}'不存在"
        if entry.locked or entry.is_system:
            return False, f"位置'{entry.name}'已锁定，不可删除"
        del self._positions[entry.normalized_name()]
        self._save()
        return True, f"位置'{entry.name}'已删除"

    def get(self, name: str) -> PositionEntry | None:
        return self._positions.get(str(name or "").strip().lower())

    def list_all(self) -> list[PositionEntry]:
        return list(sorted(self._positions.values(), key=lambda entry: entry.name))


def migrate_atomic_positions(
    atomic_state_path: str | Path,
    registry_path: str | Path,
    *,
    permission: PermissionService | None = None,
    created_by: str = "migration",
) -> dict[str, int]:
    source = Path(atomic_state_path)
    result = {"created": 0, "skipped": 0, "failed": 0}
    if not source.exists():
        return result
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        result["failed"] += 1
        return result
    positions = payload.get("positions") if isinstance(payload, dict) else None
    if not isinstance(positions, dict):
        return result
    registry = PositionRegistry(
        registry_path,
        permission=permission or PermissionService("engineer"),
    )
    for name, pose in positions.items():
        position_name = str(name or "").strip()
        if not position_name:
            result["skipped"] += 1
            continue
        existing = registry.get(position_name)
        if existing is not None:
            result["skipped"] += 1
            continue
        try:
            ok, _message = registry.add(
                PositionEntry(
                    name=position_name,
                    pose=_pose6(pose),
                    spd=50,
                    move_type=0,
                    created_by=created_by,
                )
            )
        except Exception:
            result["failed"] += 1
            continue
        if ok:
            result["created"] += 1
        else:
            result["skipped"] += 1
    return result
