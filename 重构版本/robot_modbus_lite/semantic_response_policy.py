"""V2.1 semantic-level response policy table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SemanticResponsePolicy:
    semantic_level: int
    semantic_label: str
    ack_limit_ms: int
    result_deadline_ms: int
    progress_interval_ms: int
    requires_precheck: bool
    requires_confirmation: bool
    emergency_fast_path: bool
    priority: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_level": self.semantic_level,
            "semantic_label": self.semantic_label,
            "ack_limit_ms": self.ack_limit_ms,
            "result_deadline_ms": self.result_deadline_ms,
            "progress_interval_ms": self.progress_interval_ms,
            "requires_precheck": self.requires_precheck,
            "requires_confirmation": self.requires_confirmation,
            "emergency_fast_path": self.emergency_fast_path,
            "priority": self.priority,
        }


_POLICIES: dict[int, SemanticResponsePolicy] = {
    1: SemanticResponsePolicy(
        semantic_level=1,
        semantic_label="闲聊咨询层",
        ack_limit_ms=50,
        result_deadline_ms=1000,
        progress_interval_ms=0,
        requires_precheck=False,
        requires_confirmation=False,
        emergency_fast_path=False,
    ),
    2: SemanticResponsePolicy(
        semantic_level=2,
        semantic_label="工艺查询层",
        ack_limit_ms=50,
        result_deadline_ms=5000,
        progress_interval_ms=2000,
        requires_precheck=False,
        requires_confirmation=False,
        emergency_fast_path=False,
    ),
    3: SemanticResponsePolicy(
        semantic_level=3,
        semantic_label="常规生产执行层",
        ack_limit_ms=50,
        result_deadline_ms=2000,
        progress_interval_ms=1000,
        requires_precheck=True,
        requires_confirmation=True,
        emergency_fast_path=False,
    ),
    4: SemanticResponsePolicy(
        semantic_level=4,
        semantic_label="系统管理层",
        ack_limit_ms=50,
        result_deadline_ms=2000,
        progress_interval_ms=0,
        requires_precheck=False,
        requires_confirmation=False,
        emergency_fast_path=False,
    ),
    5: SemanticResponsePolicy(
        semantic_level=5,
        semantic_label="应急安全层",
        ack_limit_ms=30,
        result_deadline_ms=100,
        progress_interval_ms=0,
        requires_precheck=False,
        requires_confirmation=False,
        emergency_fast_path=True,
        priority="high",
    ),
}


def policy_for_level(level: int | str | None) -> SemanticResponsePolicy:
    try:
        normalized = int(level or 0)
    except (TypeError, ValueError):
        normalized = 0
    return _POLICIES.get(
        normalized,
        SemanticResponsePolicy(
            semantic_level=0,
            semantic_label="未识别层",
            ack_limit_ms=50,
            result_deadline_ms=500,
            progress_interval_ms=0,
            requires_precheck=False,
            requires_confirmation=False,
            emergency_fast_path=False,
        ),
    )


def policy_for_plan(plan: Any) -> SemanticResponsePolicy:
    policy = policy_for_level(getattr(plan, "semantic_level", 0))
    plan_deadline = int(getattr(plan, "response_deadline_ms", 0) or 0)
    if policy.semantic_level != 0 and plan_deadline == 500:
        plan_deadline = 0
    return SemanticResponsePolicy(
        semantic_level=policy.semantic_level,
        semantic_label=str(getattr(plan, "semantic_label", "") or policy.semantic_label),
        ack_limit_ms=policy.ack_limit_ms,
        result_deadline_ms=plan_deadline or policy.result_deadline_ms,
        progress_interval_ms=policy.progress_interval_ms,
        requires_precheck=bool(getattr(plan, "requires_precheck", policy.requires_precheck)),
        requires_confirmation=bool(getattr(plan, "requires_confirmation", policy.requires_confirmation)),
        emergency_fast_path=policy.emergency_fast_path,
        priority=str(getattr(plan, "priority", "") or policy.priority),
    )
