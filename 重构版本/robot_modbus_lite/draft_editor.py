"""Pure editor for execution plan drafts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .execution_plan import ExecutionPlan, ExecutionStep, ExecutionPlanStatus


@dataclass(frozen=True)
class EditResult:
    ok: bool
    plan: ExecutionPlan
    message: str
    changed: bool = False


class DraftEditor:
    """Edits execution plans without mutating the input plan."""

    def __init__(self) -> None:
        self._undo_snapshot: ExecutionPlan | None = None

    def update_step_params(self, plan: ExecutionPlan, step_id: int, params: dict[str, Any]) -> EditResult:
        found = False
        steps: list[ExecutionStep] = []
        for step in plan.steps:
            if step.step_id == step_id:
                found = True
                merged = dict(step.params)
                merged.update(params)
                steps.append(replace(step, params=merged))
            else:
                steps.append(self._copy_step(step))
        if not found:
            return EditResult(False, plan, f"当前方案没有第 {step_id} 步")
        return self._changed(plan, plan.with_steps(steps), "已更新步骤参数")

    def update_all_speed(self, plan: ExecutionPlan, speed_pct: float) -> EditResult:
        steps: list[ExecutionStep] = []
        changed = False
        for step in plan.steps:
            params = dict(step.params)
            for key in ("spd_pct", "acc_pct", "dec_pct"):
                if key in params:
                    params[key] = float(speed_pct)
                    changed = True
            steps.append(replace(step, params=params))
        if not changed:
            return EditResult(True, plan, "当前方案没有可修改的速度参数", changed=False)
        return self._changed(plan, plan.with_steps(steps), "已更新整体速度")

    def delete_step(self, plan: ExecutionPlan, step_id: int) -> EditResult:
        if not any(step.step_id == step_id for step in plan.steps):
            return EditResult(False, plan, f"当前方案没有第 {step_id} 步")
        remaining = [self._copy_step(step) for step in plan.steps if step.step_id != step_id]
        renumbered = [replace(step, step_id=index) for index, step in enumerate(remaining, start=1)]
        return self._changed(plan, plan.with_steps(renumbered), "已删除步骤")

    def append_step(self, plan: ExecutionPlan, step: ExecutionStep) -> EditResult:
        next_id = len(plan.steps) + 1
        appended = replace(self._copy_step(step), step_id=next_id)
        return self._changed(plan, plan.with_steps([*plan.steps, appended]), "已追加步骤")

    def undo(self, plan: ExecutionPlan) -> EditResult:
        if self._undo_snapshot is None:
            return EditResult(False, plan, "当前没有可撤销的修改")
        snapshot = self._undo_snapshot
        self._undo_snapshot = None
        return EditResult(True, snapshot, "已撤销上一次修改", changed=True)

    def clear_undo(self) -> None:
        self._undo_snapshot = None

    def _changed(self, before: ExecutionPlan, after: ExecutionPlan, message: str) -> EditResult:
        self._undo_snapshot = before
        if after.status == ExecutionPlanStatus.DRAFT:
            after = after.transition_to(ExecutionPlanStatus.MODIFIED)
        return EditResult(True, after, message, changed=True)

    @staticmethod
    def _copy_step(step: ExecutionStep) -> ExecutionStep:
        return replace(step, params=dict(step.params))
