from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent


_ACTIONABLE_INTENTS = {
    "move_linear",
    "continuous_path",
    "delay_blocking",
    "delay_parallel",
    "io",
    "sys_pause",
    "sys_resume",
    "sys_cancel",
    "alarm_reset",
}


@dataclass(frozen=True)
class CompoundSplitResult:
    kind: str
    steps: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class CompoundPlanResult:
    kind: str
    plan_id: str
    raw_text: str
    created_at: float
    steps: tuple[str, ...] = ()
    step_results: tuple[Any, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass(frozen=True)
class CompoundStepState:
    index: int
    text: str
    result: Any
    status: str = "pending"
    reason: str = ""


@dataclass(frozen=True)
class CompoundStepMachine:
    plan_id: str
    raw_text: str
    steps: tuple[CompoundStepState, ...]
    current_index: int = 0
    status: str = "waiting_step_confirmation"
    reason: str = ""

    @classmethod
    def from_plan(cls, plan: CompoundPlanResult) -> "CompoundStepMachine":
        steps = tuple(
            CompoundStepState(index=index, text=text, result=result)
            for index, (text, result) in enumerate(zip(plan.steps, plan.step_results))
        )
        if not steps:
            return cls(
                plan_id=plan.plan_id,
                raw_text=plan.raw_text,
                steps=(),
                current_index=0,
                status="blocked",
                reason=plan.reason or "复合计划没有可确认步骤。",
            )
        blocked_index, blocked_reason = cls._first_blocked_step(steps)
        if blocked_index is not None:
            return cls(
                plan_id=plan.plan_id,
                raw_text=plan.raw_text,
                steps=cls._replace_step(steps, blocked_index, status="blocked", reason=blocked_reason),
                current_index=blocked_index,
                status="blocked",
                reason=blocked_reason,
            )
        return cls(
            plan_id=plan.plan_id,
            raw_text=plan.raw_text,
            steps=cls._replace_step(steps, 0, status="waiting_confirmation"),
            current_index=0,
            status="waiting_step_confirmation",
        )

    @property
    def current_step_text(self) -> str:
        if not self.steps:
            return ""
        return self.steps[self.current_index].text

    def confirm_current(self) -> "CompoundStepMachine":
        if self.status != "waiting_step_confirmation":
            return self
        return CompoundStepMachine(
            plan_id=self.plan_id,
            raw_text=self.raw_text,
            steps=self._replace_step(self.steps, self.current_index, status="confirmed"),
            current_index=self.current_index,
            status="step_confirmed",
            reason="",
        )

    def mark_current_completed(self) -> "CompoundStepMachine":
        if self.status != "step_confirmed":
            return self
        completed_steps = self._replace_step(self.steps, self.current_index, status="completed")
        next_index = self.current_index + 1
        if next_index >= len(completed_steps):
            return CompoundStepMachine(
                plan_id=self.plan_id,
                raw_text=self.raw_text,
                steps=completed_steps,
                current_index=self.current_index,
                status="completed",
                reason="",
            )
        return CompoundStepMachine(
            plan_id=self.plan_id,
            raw_text=self.raw_text,
            steps=self._replace_step(completed_steps, next_index, status="waiting_confirmation"),
            current_index=next_index,
            status="waiting_step_confirmation",
            reason="",
        )

    def mark_current_failed(self, reason: str) -> "CompoundStepMachine":
        detail = str(reason or "复合指令步骤执行失败。")
        return CompoundStepMachine(
            plan_id=self.plan_id,
            raw_text=self.raw_text,
            steps=self._replace_step(self.steps, self.current_index, status="failed", reason=detail),
            current_index=self.current_index,
            status="failed",
            reason=detail,
        )

    @staticmethod
    def _first_blocked_step(steps: tuple[CompoundStepState, ...]) -> tuple[int | None, str]:
        blocking_kinds = {"precheck_failed", "blocked", "clarification"}
        for step in steps:
            kind = str(_result_value(step.result, "kind") or "")
            if kind in blocking_kinds:
                reason = str(_result_value(step.result, "message") or _result_value(step.result, "reason") or kind)
                return step.index, reason
        return None, ""

    @staticmethod
    def _replace_step(
        steps: tuple[CompoundStepState, ...],
        index: int,
        *,
        status: str,
        reason: str = "",
    ) -> tuple[CompoundStepState, ...]:
        updated = list(steps)
        step = updated[index]
        updated[index] = CompoundStepState(
            index=step.index,
            text=step.text,
            result=step.result,
            status=status,
            reason=reason,
        )
        return tuple(updated)


class CompoundCommandCoordinator:
    def __init__(
        self,
        *,
        understanding_agent: Any | None = None,
        restricted_service: Any = None,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.understanding_agent = understanding_agent or CommandUnderstandingAgent()
        self.restricted_service = restricted_service
        self.clock = clock or time.time
        self.id_factory = id_factory or (lambda: f"compound:{uuid4().hex[:8]}")

    def split(self, text: str) -> CompoundSplitResult:
        compact = re.sub(r"\s+", "", str(text or ""))
        if any(word in compact for word in ("同时", "并行", "如果", "循环", "重复")):
            return CompoundSplitResult(kind="unsupported_compound", reason="暂不支持并行、条件、循环类复合指令。")
        steps = tuple(
            clean_part for part in re.split(r"然后|再|接着", str(text or "")) if (clean_part := part.strip(" ，,;；"))
        )
        if len(steps) < 2:
            return CompoundSplitResult(kind="not_compound", reason="没有检测到多步顺序指令。")
        for step in steps:
            if not self._is_actionable(step):
                return CompoundSplitResult(kind="not_compound", reason=f"子步骤不可执行：{step}")
        return CompoundSplitResult(kind="compound_sequence", steps=steps)

    def plan(self, text: str) -> CompoundPlanResult:
        split_result = self.split(text)
        if split_result.kind != "compound_sequence":
            return CompoundPlanResult(
                kind=split_result.kind,
                plan_id=self.id_factory(),
                raw_text=text,
                created_at=self.clock(),
                reason=split_result.reason,
            )
        if self.restricted_service is None:
            return CompoundPlanResult(
                kind="compound_plan_draft",
                plan_id=self.id_factory(),
                raw_text=text,
                created_at=self.clock(),
                steps=split_result.steps,
                step_results=(),
            )

        step_results = tuple(self.restricted_service.parse(step) for step in split_result.steps)
        return CompoundPlanResult(
            kind="compound_plan_draft",
            plan_id=self.id_factory(),
            raw_text=text,
            created_at=self.clock(),
            steps=split_result.steps,
            step_results=step_results,
        )

    def _is_actionable(self, text: str) -> bool:
        understanding = self.understanding_agent.understand(text)
        return str(getattr(understanding, "intent", "") or "") in _ACTIONABLE_INTENTS


def _result_value(result: Any, key: str) -> Any:
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)
