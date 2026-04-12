from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SafePoint:
    name: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    speed_percent: float = 20.0
    acc_percent: float = 20.0
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AvoidanceConfig:
    mode: str = "off"
    rx_threshold: float = 30.0
    ry_threshold: float = 30.0
    rz_threshold: float = 45.0
    low_z_threshold: float = 150.0
    xy_move_threshold: float = 100.0
    safe_points: dict[str, SafePoint] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "rx_threshold": self.rx_threshold,
            "ry_threshold": self.ry_threshold,
            "rz_threshold": self.rz_threshold,
            "low_z_threshold": self.low_z_threshold,
            "xy_move_threshold": self.xy_move_threshold,
            "safe_points": {name: point.to_dict() for name, point in sorted(self.safe_points.items())},
            "rules": self.rules,
        }


def default_avoidance_config() -> AvoidanceConfig:
    return AvoidanceConfig(
        mode="off",
        rx_threshold=30.0,
        ry_threshold=30.0,
        rz_threshold=45.0,
        low_z_threshold=150.0,
        xy_move_threshold=100.0,
        safe_points={
            "SAFE_POINT": SafePoint(
                name="SAFE_POINT",
                x=0.0,
                y=0.0,
                z=200.0,
                rx=0.0,
                ry=0.0,
                rz=0.0,
                speed_percent=20.0,
                acc_percent=20.0,
                description="通用安全中间点",
            )
        },
        rules=[],
    )


def ensure_avoidance_config_json(path: str | Path) -> Path:
    json_path = Path(path)
    if json_path.exists():
        return json_path
    save_avoidance_config(json_path, default_avoidance_config())
    return json_path


def load_avoidance_config(path: str | Path) -> AvoidanceConfig:
    json_path = Path(path)
    if not json_path.exists():
        return default_avoidance_config()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    safe_points_raw = payload.get("safe_points", {})
    safe_points: dict[str, SafePoint] = {}
    if isinstance(safe_points_raw, dict):
        for name, item in safe_points_raw.items():
            if not isinstance(item, dict):
                continue
            point_name = str(item.get("name", name)).strip() or str(name)
            safe_points[point_name] = SafePoint(
                name=point_name,
                x=float(item.get("x", 0.0)),
                y=float(item.get("y", 0.0)),
                z=float(item.get("z", 0.0)),
                rx=float(item.get("rx", 0.0)),
                ry=float(item.get("ry", 0.0)),
                rz=float(item.get("rz", 0.0)),
                speed_percent=float(item.get("speed_percent", item.get("speedPercent", 20.0))),
                acc_percent=float(item.get("acc_percent", item.get("accPercent", 20.0))),
                description=str(item.get("description", "")),
            )
    return AvoidanceConfig(
        mode=str(payload.get("mode", "off")),
        rx_threshold=float(payload.get("rx_threshold", 30.0)),
        ry_threshold=float(payload.get("ry_threshold", 30.0)),
        rz_threshold=float(payload.get("rz_threshold", 45.0)),
        low_z_threshold=float(payload.get("low_z_threshold", 150.0)),
        xy_move_threshold=float(payload.get("xy_move_threshold", 100.0)),
        safe_points=safe_points or default_avoidance_config().safe_points,
        rules=list(payload.get("rules", [])) if isinstance(payload.get("rules", []), list) else [],
    )


def save_avoidance_config(path: str | Path, config: AvoidanceConfig) -> None:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def validate_safe_point(point: SafePoint) -> str | None:
    if not point.name.strip():
        return "中间点名称不能为空。"
    return None
