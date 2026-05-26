"""Service facade for dialog-driven execution plans."""

from __future__ import annotations

from time import monotonic

from .clarification_state import ClarificationManager, ClarificationResult, PendingClarification
from .draft_editor import DraftEditor, EditResult
from .execution_plan import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionStep,
    execution_plan_to_flow_draft,
    flow_draft_to_execution_plan,
)


class ExecutionPlanService:
    """Coordinates plan editing and clarification state without GUI dependencies."""

    def __init__(
        self,
        *,
        editor: DraftEditor | None = None,
        clarification_manager: ClarificationManager | None = None,
    ) -> None:
        self.editor = editor or DraftEditor()
        self.clarification_manager = clarification_manager or ClarificationManager()
        self.pending_plan: ExecutionPlan | None = None
        self.default_notices: list[str] = []

    def set_pending_plan(self, plan: ExecutionPlan) -> None:
        self.pending_plan = plan

    def set_pending_flow_draft(self, draft: dict) -> None:
        self.pending_plan = flow_draft_to_execution_plan(draft, source="flow_draft")
        self.default_notices = []
        self._apply_safe_defaults()
        self._create_missing_parameter_clarifications()

    def pending_flow_draft(self) -> dict | None:
        if self.pending_plan is None:
            return None
        return execution_plan_to_flow_draft(self.pending_plan)

    def cancel_pending_plan(self) -> None:
        if self.pending_plan is not None:
            self.clarification_manager.clear(self.pending_plan.plan_id)
        self.pending_plan = None
        self.editor.clear_undo()

    def edit_all_speed(self, speed_pct: float) -> EditResult:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        result = self.editor.update_all_speed(self.pending_plan, speed_pct)
        self.pending_plan = result.plan
        return result

    def edit_step_params(self, step_id: int, params: dict) -> EditResult:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        result = self.editor.update_step_params(self.pending_plan, step_id, params)
        self.pending_plan = result.plan
        return result

    def delete_step(self, step_id: int) -> EditResult:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        result = self.editor.delete_step(self.pending_plan, step_id)
        self.pending_plan = result.plan
        return result

    def append_step(self, step: ExecutionStep) -> EditResult:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        result = self.editor.append_step(self.pending_plan, step)
        self.pending_plan = result.plan
        return result

    def undo(self) -> EditResult:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        result = self.editor.undo(self.pending_plan)
        self.pending_plan = result.plan
        return result

    def add_clarifications(self, clarifications: list[PendingClarification]) -> None:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        self.clarification_manager.add_missing_fields(self.pending_plan, clarifications)

    def current_clarification(self) -> PendingClarification | None:
        if self.pending_plan is None:
            return None
        return self.clarification_manager.current_question(self.pending_plan.plan_id)

    def apply_clarification_answer(self, text: str) -> ClarificationResult:
        if self.pending_plan is None:
            raise RuntimeError("no pending execution plan")
        result = self.clarification_manager.apply_answer(self.pending_plan, text)
        self.pending_plan = result.plan
        return result

    def _create_missing_parameter_clarifications(self) -> None:
        if self.pending_plan is None:
            return
        clarifications: list[PendingClarification] = []
        now = monotonic()
        for step in self.pending_plan.steps:
            if self._step_missing_target_pose(step):
                clarifications.append(
                    PendingClarification.new(
                        self.pending_plan.plan_id,
                        step.step_id,
                        "target_pose",
                        f"第{step.step_id}步缺少目标坐标，请输入 X,Y,Z,RX,RY,RZ。",
                        ("pose",),
                        now=now,
                    )
                )
            if self._step_missing_delay(step):
                clarifications.append(
                    PendingClarification.new(
                        self.pending_plan.plan_id,
                        step.step_id,
                        "delay_sec",
                        f"第{step.step_id}步缺少延时时间，请输入几秒或几毫秒。",
                        ("duration",),
                        now=now,
                    )
                )
            if self._step_missing_io_no(step):
                clarifications.append(
                    PendingClarification.new(
                        self.pending_plan.plan_id,
                        step.step_id,
                        "io_no",
                        f"第{step.step_id}步缺少IO编号，请输入 IO0 到 IO11。",
                        ("io_no",),
                        now=now,
                    )
                )
            if self._step_missing_io_action(step):
                clarifications.append(
                    PendingClarification.new(
                        self.pending_plan.plan_id,
                        step.step_id,
                        "io_action",
                        f"第{step.step_id}步缺少 IO 动作，请回答打开或关闭。",
                        ("io_action",),
                        now=now,
                    )
                )
        if not clarifications:
            return
        self.clarification_manager.clear(self.pending_plan.plan_id)
        self.add_clarifications(clarifications)
        if self.pending_plan.status != ExecutionPlanStatus.NEED_CLARIFICATION:
            self.pending_plan = self.pending_plan.transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)

    @staticmethod
    def _step_missing_target_pose(step: ExecutionStep) -> bool:
        if int(step.func_id) != 108:
            return False
        required = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
        return any(key not in step.params for key in required)

    @staticmethod
    def _step_missing_delay(step: ExecutionStep) -> bool:
        if int(step.func_id) not in {109, 110}:
            return False
        try:
            return float(step.params.get("delay_sec", 0.0)) <= 0.0
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _step_missing_io_no(step: ExecutionStep) -> bool:
        if int(step.func_id) != 120:
            return False
        try:
            value = int(float(step.params.get("io_no")))
        except (TypeError, ValueError):
            return True
        return not (0 <= value <= 11)

    @staticmethod
    def _step_missing_io_action(step: ExecutionStep) -> bool:
        if int(step.func_id) != 120:
            return False
        try:
            value = int(float(step.params.get("io_action")))
        except (TypeError, ValueError):
            return True
        return value not in {0, 1}

    def _apply_safe_defaults(self) -> None:
        if self.pending_plan is None:
            return
        updated_steps: list[ExecutionStep] = []
        changed = False
        for step in self.pending_plan.steps:
            params = dict(step.params)
            if int(step.func_id) == 108 and "move_type" not in params:
                params["move_type"] = 0
                self.default_notices.append(f"第{step.step_id}步未指定移动方式，已默认使用直线插补(move_type=0)。")
                updated_steps.append(ExecutionStep(
                    step_id=step.step_id,
                    action=step.action,
                    func_id=step.func_id,
                    params=params,
                    target_label=step.target_label,
                    description=step.description,
                    precheck=step.precheck,
                ))
                changed = True
            else:
                updated_steps.append(step)
        if changed:
            self.pending_plan = self.pending_plan.with_steps(updated_steps)
