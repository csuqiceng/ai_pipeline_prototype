"""Execution plan model for dialog-driven robot actions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from time import time
from typing import Any
from uuid import uuid4


class ExecutionPlanStatus(str, Enum):
    DRAFT = "draft"
    NEED_CLARIFICATION = "need_clarification"
    MODIFIED = "modified"
    PRECHECKING = "prechecking"
    PRECHECK_FAILED = "precheck_failed"
    READY = "ready"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[ExecutionPlanStatus, set[ExecutionPlanStatus]] = {
    ExecutionPlanStatus.DRAFT: {
        ExecutionPlanStatus.NEED_CLARIFICATION,
        ExecutionPlanStatus.MODIFIED,
        ExecutionPlanStatus.PRECHECKING,
        ExecutionPlanStatus.READY,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.NEED_CLARIFICATION: {
        ExecutionPlanStatus.NEED_CLARIFICATION,
        ExecutionPlanStatus.MODIFIED,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.MODIFIED: {
        ExecutionPlanStatus.PRECHECKING,
        ExecutionPlanStatus.NEED_CLARIFICATION,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.PRECHECKING: {
        ExecutionPlanStatus.PRECHECK_FAILED,
        ExecutionPlanStatus.READY,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.PRECHECK_FAILED: {
        ExecutionPlanStatus.MODIFIED,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.READY: {
        ExecutionPlanStatus.CONFIRMED,
        ExecutionPlanStatus.MODIFIED,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.CONFIRMED: {
        ExecutionPlanStatus.EXECUTING,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.EXECUTING: {
        ExecutionPlanStatus.COMPLETED,
        ExecutionPlanStatus.FAILED,
    },
    ExecutionPlanStatus.FAILED: {
        ExecutionPlanStatus.MODIFIED,
        ExecutionPlanStatus.CANCELLED,
    },
    ExecutionPlanStatus.COMPLETED: set(),
    ExecutionPlanStatus.CANCELLED: set(),
}


class InvalidPlanTransition(ValueError):
    """Raised when an execution plan status transition is not allowed."""


@dataclass(frozen=True)
class StepPrecheckResult:
    l1_status: str = "skipped"
    l2_status: str = "skipped"
    l3_status: str = "skipped"
    blocking: bool = False
    items: tuple[dict[str, str], ...] = ()
    suggestions: tuple[dict[str, Any], ...] = ()
    midpoint: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionStep:
    step_id: int
    action: str
    func_id: int
    params: dict[str, Any] = field(default_factory=dict)
    target_label: str = ""
    description: str = ""
    precheck: StepPrecheckResult | None = None


@dataclass(frozen=True)
class RiskItem:
    level: str
    source: str
    step_id: int | None
    message: str


@dataclass(frozen=True)
class SuggestionItem:
    suggestion_id: str
    source: str
    step_id: int | None
    message: str
    patch: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionPlan:
    plan_id: str
    name: str
    status: ExecutionPlanStatus
    steps: tuple[ExecutionStep, ...] = ()
    risks: tuple[RiskItem, ...] = ()
    suggestions: tuple[SuggestionItem, ...] = ()
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: int = 1

    @classmethod
    def new(cls, *, name: str, source: str) -> "ExecutionPlan":
        now = f"{time():.6f}"
        return cls(
            plan_id=uuid4().hex,
            name=name,
            status=ExecutionPlanStatus.DRAFT,
            source=source,
            created_at=now,
            updated_at=now,
        )

    def transition_to(self, status: ExecutionPlanStatus | str) -> "ExecutionPlan":
        next_status = ExecutionPlanStatus(status)
        if next_status not in VALID_TRANSITIONS[self.status]:
            raise InvalidPlanTransition(f"{self.status.value} -> {next_status.value} is not allowed")
        return replace(self, status=next_status, updated_at=f"{time():.6f}")

    def with_steps(self, steps: list[ExecutionStep] | tuple[ExecutionStep, ...]) -> "ExecutionPlan":
        return replace(self, steps=tuple(steps), updated_at=f"{time():.6f}")


def flow_draft_to_execution_plan(draft: dict[str, Any], *, source: str = "flow_draft") -> ExecutionPlan:
    name = str(draft.get("flow_name") or draft.get("name") or "未命名流程")
    steps: list[ExecutionStep] = []
    for index, item in enumerate(draft.get("expanded_steps") or draft.get("steps") or [], start=1):
        if not isinstance(item, dict):
            continue
        params = dict(item.get("params") or {})
        step = ExecutionStep(
            step_id=index,
            action=str(item.get("action") or item.get("type") or ""),
            func_id=int(item.get("func_id") or item.get("func_num") or 0),
            params=params,
            target_label=str(item.get("target_label") or item.get("position_name") or item.get("position") or ""),
            description=str(item.get("description") or ""),
        )
        steps.append(step)
    return ExecutionPlan.new(name=name, source=source).with_steps(steps)


def execution_plan_to_flow_draft(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        "flow_name": plan.name,
        "expanded_steps": [
            {
                "step_id": step.step_id,
                "description": step.description,
                "action": step.action,
                "func_id": step.func_id,
                "target_label": step.target_label,
                "position_name": step.target_label,
                "params": dict(step.params),
            }
            for step in plan.steps
        ],
    }
