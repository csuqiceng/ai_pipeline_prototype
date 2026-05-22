"""Data models for the V2.0 secondary atomic command layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AtomicKind = Literal[
    "chat",
    "warning",
    "system",
    "memory",
    "template",
    "query",
    "unsupported",
]
RiskLevel = Literal["low", "medium", "high", "emergency"]


@dataclass(frozen=True)
class AtomicElements:
    """Elements extracted from a single natural-language atomic command."""

    raw_text: str
    command_text: str
    family: str = ""
    axis_no: int | None = None
    direction: int | None = None
    step: float | None = None
    target: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    rx: float | None = None
    ry: float | None = None
    rz: float | None = None
    spd_pct: float | None = None
    acc_pct: float | None = None
    dec_pct: float | None = None
    io_no: int | None = None
    io_action: int | None = None
    delay_sec: float | None = None
    move_type: int = 0
    fuzzy_pos: int = 1
    name: str | None = None


@dataclass(frozen=True)
class AtomicResolved:
    """Resolved atomic result, ready for adapter or UI handling."""

    kind: AtomicKind
    action_type: str
    target: str | None
    reason: str
    params: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = "low"
    requires_confirmation: bool = True
