# 对话式执行方案助手 Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 `ExecutionPlan`、追问管理、草案编辑器和服务骨架，为后续步骤级编辑、参数追问、重预检和结构化 UI 提供稳定模型层。

**Architecture:** Phase 0 不接入 GUI 行为，不改变执行链路。新增独立模型和纯逻辑服务：`execution_plan.py` 负责数据模型/状态机/Path A+B 转换，`clarification_state.py` 负责追问队列，`draft_editor.py` 负责不可变草案编辑，`execution_plan_service.py` 负责统一持有 editor/clarification manager 的服务骨架。

**Tech Stack:** Python dataclasses, pytest, existing `VoiceNlpPlan` / `FlowDefinition` / flow draft dict.

---

## Files

- Create: `robot_modbus_lite/execution_plan.py`
- Create: `robot_modbus_lite/clarification_state.py`
- Create: `robot_modbus_lite/draft_editor.py`
- Create: `robot_modbus_lite/execution_plan_service.py`
- Create: `tests/test_execution_plan_model.py`
- Create: `tests/test_clarification_state.py`
- Create: `tests/test_draft_editor.py`
- Create: `tests/test_execution_plan_service.py`
- Do not modify GUI files in Phase 0.

## Task 1: ExecutionPlan Model And State Machine

**Files:**
- Create: `tests/test_execution_plan_model.py`
- Create: `robot_modbus_lite/execution_plan.py`

- [ ] **Step 1: Write failing model tests**

Add tests covering status validation, status transition rejection, and flow draft conversion:

```python
import pytest

from robot_modbus_lite.execution_plan import (
    ExecutionPlan,
    ExecutionPlanStatus,
    InvalidPlanTransition,
    flow_draft_to_execution_plan,
)


def _flow_draft():
    return {
        "flow_name": "打招呼",
        "expanded_steps": [
            {
                "description": "移动到 home",
                "action": "move_position",
                "func_id": 108,
                "params": {"target_x": 1475.0, "target_y": 0.0, "target_z": 1545.0, "spd_pct": 50.0},
            },
            {
                "description": "上移",
                "action": "virtual_nudge",
                "func_id": 107,
                "params": {"axis_no": 8, "pos_val": 50.0, "spd_pct": 50.0},
            },
        ],
    }


def test_flow_draft_to_execution_plan_maps_expanded_steps():
    plan = flow_draft_to_execution_plan(_flow_draft(), source="flow_draft")

    assert plan.name == "打招呼"
    assert plan.status == ExecutionPlanStatus.DRAFT
    assert len(plan.steps) == 2
    assert plan.steps[0].step_id == 1
    assert plan.steps[0].func_id == 108
    assert plan.steps[1].params["axis_no"] == 8


def test_execution_plan_transition_rejects_invalid_jump_to_executing():
    plan = ExecutionPlan.new(name="P", source="test")

    with pytest.raises(InvalidPlanTransition):
        plan.transition_to(ExecutionPlanStatus.EXECUTING)


def test_execution_plan_transition_allows_draft_to_modified():
    plan = ExecutionPlan.new(name="P", source="test")
    updated = plan.transition_to(ExecutionPlanStatus.MODIFIED)

    assert updated.status == ExecutionPlanStatus.MODIFIED
    assert plan.status == ExecutionPlanStatus.DRAFT
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
pytest tests/test_execution_plan_model.py -q
```

Expected: fails because `robot_modbus_lite.execution_plan` does not exist.

- [ ] **Step 3: Implement model and state transition**

Implement:

- `ExecutionPlanStatus` enum/string enum with `draft`, `need_clarification`, `modified`, `prechecking`, `precheck_failed`, `ready`, `confirmed`, `executing`, `completed`, `failed`, `cancelled`.
- Frozen dataclasses: `ExecutionStep`, `StepPrecheckResult`, `RiskItem`, `SuggestionItem`, `ExecutionPlan`.
- `ExecutionPlan.new(name, source)`.
- `ExecutionPlan.transition_to(next_status) -> ExecutionPlan`, returning a new object.
- `flow_draft_to_execution_plan(draft, source="flow_draft")`.
- `execution_plan_to_flow_draft(plan)`.

Minimum transition table:

```python
VALID_TRANSITIONS = {
    "draft": {"need_clarification", "modified", "prechecking", "ready", "cancelled"},
    "need_clarification": {"need_clarification", "modified", "cancelled"},
    "modified": {"prechecking", "need_clarification", "cancelled"},
    "prechecking": {"precheck_failed", "ready", "cancelled"},
    "precheck_failed": {"modified", "cancelled"},
    "ready": {"confirmed", "modified", "cancelled"},
    "confirmed": {"executing", "cancelled"},
    "executing": {"completed", "failed"},
    "failed": {"modified", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
```

- [ ] **Step 4: Verify model tests pass**

Run:

```powershell
pytest tests/test_execution_plan_model.py -q
```

Expected: all tests pass.

## Task 2: Clarification Manager

**Files:**
- Create: `tests/test_clarification_state.py`
- Create: `robot_modbus_lite/clarification_state.py`

- [ ] **Step 1: Write failing clarification tests**

Add tests:

```python
from robot_modbus_lite.clarification_state import ClarificationManager, PendingClarification
from robot_modbus_lite.execution_plan import ExecutionPlan


def test_manager_returns_first_question_and_keeps_queue_order():
    plan = ExecutionPlan.new(name="P", source="test")
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [
            PendingClarification.new(plan.plan_id, 1, "target", "目标位置是什么？", ("position",), now=10.0),
            PendingClarification.new(plan.plan_id, 1, "speed", "速度多少？", ("speed",), now=10.0),
        ],
    )

    current = manager.current_question(plan.plan_id)

    assert current is not None
    assert current.missing_field == "target"


def test_manager_expires_old_questions():
    plan = ExecutionPlan.new(name="P", source="test")
    manager = ClarificationManager(default_timeout_sec=5.0)
    manager.add_missing_fields(
        plan,
        [PendingClarification.new(plan.plan_id, None, "target", "目标位置是什么？", ("position",), now=10.0)],
    )

    expired = manager.expire(now=16.0)

    assert expired == [plan.plan_id]
    assert manager.current_question(plan.plan_id) is None


def test_manager_clear_removes_plan_questions():
    plan = ExecutionPlan.new(name="P", source="test")
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [PendingClarification.new(plan.plan_id, None, "target", "目标位置是什么？", ("position",), now=10.0)],
    )

    manager.clear(plan.plan_id)

    assert manager.current_question(plan.plan_id) is None
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
pytest tests/test_clarification_state.py -q
```

Expected: fails because module does not exist.

- [ ] **Step 3: Implement clarification manager**

Implement:

- `PendingClarification` frozen dataclass.
- `PendingClarification.new(plan_id, step_id, missing_field, question, accepted_answer_types, now, timeout_sec=60.0)`.
- `ClarificationManager.add_missing_fields(plan, fields)`.
- `ClarificationManager.current_question(plan_id)`.
- `ClarificationManager.expire(now)`.
- `ClarificationManager.clear(plan_id)`.

Do not parse answers in Phase 0; `apply_answer()` can return a typed “unsupported in phase0” result or be omitted until Phase 2. The queue and timeout behavior are the Phase 0 deliverable.

- [ ] **Step 4: Verify clarification tests pass**

Run:

```powershell
pytest tests/test_clarification_state.py -q
```

Expected: all tests pass.

## Task 3: DraftEditor Pure Editing

**Files:**
- Create: `tests/test_draft_editor.py`
- Create: `robot_modbus_lite/draft_editor.py`

- [ ] **Step 1: Write failing editor tests**

Add tests:

```python
from robot_modbus_lite.draft_editor import DraftEditor
from robot_modbus_lite.execution_plan import ExecutionPlan, ExecutionStep


def _plan():
    return ExecutionPlan.new(name="P", source="test").with_steps(
        [
            ExecutionStep(step_id=1, action="move", func_id=108, params={"spd_pct": 50.0}),
            ExecutionStep(step_id=2, action="delay", func_id=109, params={"delay_sec": 1.0}),
        ]
    )


def test_update_step_params_returns_new_plan_without_mutating_original():
    plan = _plan()
    editor = DraftEditor()

    result = editor.update_step_params(plan, 1, {"spd_pct": 30.0})

    assert result.ok
    assert result.plan.steps[0].params["spd_pct"] == 30.0
    assert plan.steps[0].params["spd_pct"] == 50.0


def test_delete_step_renumbers_remaining_steps():
    plan = _plan()
    editor = DraftEditor()

    result = editor.delete_step(plan, 1)

    assert result.ok
    assert len(result.plan.steps) == 1
    assert result.plan.steps[0].step_id == 1
    assert result.plan.steps[0].action == "delay"


def test_undo_reverts_once_and_second_undo_fails():
    plan = _plan()
    editor = DraftEditor()
    edited = editor.update_all_speed(plan, 20.0).plan

    reverted = editor.undo(edited)
    second = editor.undo(reverted.plan)

    assert reverted.ok
    assert reverted.plan.steps[0].params["spd_pct"] == 50.0
    assert not second.ok
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
pytest tests/test_draft_editor.py -q
```

Expected: fails because module does not exist.

- [ ] **Step 3: Implement DraftEditor**

Implement:

- `EditResult(ok: bool, plan: ExecutionPlan, message: str, changed: bool = False)`.
- `DraftEditor.update_step_params()`.
- `DraftEditor.update_all_speed()`, updating `spd_pct`, `acc_pct`, and `dec_pct` where present.
- `DraftEditor.delete_step()`, renumbering steps from 1.
- `DraftEditor.append_step()`, appending with next step id.
- `DraftEditor.undo()`, single-step undo only; no redo.
- Deep copy step params so original plan is never mutated.

- [ ] **Step 4: Verify editor tests pass**

Run:

```powershell
pytest tests/test_draft_editor.py -q
```

Expected: all tests pass.

## Task 4: ExecutionPlanService Skeleton

**Files:**
- Create: `tests/test_execution_plan_service.py`
- Create: `robot_modbus_lite/execution_plan_service.py`

- [ ] **Step 1: Write failing service tests**

Add tests:

```python
from robot_modbus_lite.execution_plan import ExecutionPlan
from robot_modbus_lite.execution_plan_service import ExecutionPlanService


def test_service_holds_pending_plan_and_editor():
    service = ExecutionPlanService()
    plan = ExecutionPlan.new(name="P", source="test")

    service.set_pending_plan(plan)
    result = service.edit_all_speed(30.0)

    assert result.ok
    assert service.pending_plan is not None
    assert service.pending_plan.steps == ()


def test_service_cancel_clears_pending_plan_and_clarifications():
    service = ExecutionPlanService()
    plan = ExecutionPlan.new(name="P", source="test")
    service.set_pending_plan(plan)

    service.cancel_pending_plan()

    assert service.pending_plan is None
    assert service.current_clarification() is None
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
pytest tests/test_execution_plan_service.py -q
```

Expected: fails because module does not exist.

- [ ] **Step 3: Implement service skeleton**

Implement:

- `ExecutionPlanService.pending_plan`.
- `set_pending_plan(plan)`.
- `cancel_pending_plan()`.
- `edit_all_speed(speed_pct)`, delegating to `DraftEditor`.
- `current_clarification()`, delegating to `ClarificationManager`.

Do not connect Qt or DeepSeek in Phase 0.

- [ ] **Step 4: Verify service tests pass**

Run:

```powershell
pytest tests/test_execution_plan_service.py -q
```

Expected: all tests pass.

## Task 5: Focused Regression

**Files:**
- Existing tests only.

- [ ] **Step 1: Run new Phase 0 tests together**

Run:

```powershell
pytest tests/test_execution_plan_model.py tests/test_clarification_state.py tests/test_draft_editor.py tests/test_execution_plan_service.py -q
```

Expected: all pass.

- [ ] **Step 2: Run existing complex flow regression**

Run:

```powershell
pytest tests/test_complex_flow_draft.py -q
```

Expected: all pass. This confirms Phase 0 did not change existing complex flow behavior.

- [ ] **Step 3: Run broader targeted suite**

Run:

```powershell
pytest tests/test_complex_flow_draft.py tests/test_operator_precheck_helpers.py tests/test_process_precheck.py -q
```

Expected: all pass, or failures are unrelated to Phase 0 additions and must be recorded before continuing.

## Acceptance Criteria

- Phase 0 adds pure model/service modules only.
- No GUI behavior changes.
- Existing complex flow tests continue passing.
- `ExecutionPlan` can wrap existing `flow_draft["expanded_steps"]`.
- `ClarificationManager` owns追问状态 outside `VoiceNlpAdapter`.
- `DraftEditor` edits plans immutably and supports single-step undo.
- `ExecutionPlanService` holds pending plan and delegates to editor/clarification manager.

## Assumptions

- Scope is Qt GUI first; Web API is not part of Phase 0.
- The old `_operator_pending_confirm_plan` and `_operator_pending_flow_draft` fields remain untouched in Phase 0.
- `StepPrecheckResult` is defined now but populated in later precheck integration phases.
- DeepSeek edit intent parsing is not implemented in Phase 0; it belongs to Phase 1.
