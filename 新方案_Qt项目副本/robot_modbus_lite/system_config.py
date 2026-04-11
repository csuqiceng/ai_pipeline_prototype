from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SYSTEM_CONFIG = {
    "x": [-300.0, 300.0],
    "y": [-300.0, 300.0],
    "z": [0.0, 300.0],
}


@dataclass(frozen=True)
class AxisRangeConfig:
    x: tuple[float, float]
    y: tuple[float, float]
    z: tuple[float, float]

    @classmethod
    def from_dict(cls, data: dict) -> "AxisRangeConfig":
        return cls(
            x=_pair(data.get("x"), DEFAULT_SYSTEM_CONFIG["x"]),
            y=_pair(data.get("y"), DEFAULT_SYSTEM_CONFIG["y"]),
            z=_pair(data.get("z"), DEFAULT_SYSTEM_CONFIG["z"]),
        )

    def to_dict(self) -> dict[str, list[float]]:
        return {
            "x": [float(self.x[0]), float(self.x[1])],
            "y": [float(self.y[0]), float(self.y[1])],
            "z": [float(self.z[0]), float(self.z[1])],
        }


def ensure_system_config_json(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_SYSTEM_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_system_config(path: Path) -> AxisRangeConfig:
    ensure_system_config_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return AxisRangeConfig.from_dict(data)


def save_system_config(path: Path, config: AxisRangeConfig) -> None:
    ensure_system_config_json(path)
    path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def validate_system_config(config: AxisRangeConfig) -> str | None:
    for axis_name, axis_range in [("X", config.x), ("Y", config.y), ("Z", config.z)]:
        if axis_range[0] > axis_range[1]:
            return f"{axis_name} 范围最小值不能大于最大值。"
    return None


def _pair(value: object, fallback: list[float]) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return float(fallback[0]), float(fallback[1])
    return float(value[0]), float(value[1])
