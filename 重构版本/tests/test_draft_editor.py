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


def test_update_all_speed_updates_speed_acc_and_dec_when_present():
    plan = ExecutionPlan.new(name="P", source="test").with_steps(
        [
            ExecutionStep(
                step_id=1,
                action="move",
                func_id=108,
                params={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
            )
        ]
    )
    editor = DraftEditor()

    result = editor.update_all_speed(plan, 20.0)

    assert result.ok
    assert result.plan.steps[0].params["spd_pct"] == 20.0
    assert result.plan.steps[0].params["acc_pct"] == 20.0
    assert result.plan.steps[0].params["dec_pct"] == 20.0


def test_undo_reverts_once_and_second_undo_fails():
    plan = _plan()
    editor = DraftEditor()
    edited = editor.update_all_speed(plan, 20.0).plan

    reverted = editor.undo(edited)
    second = editor.undo(reverted.plan)

    assert reverted.ok
    assert reverted.plan.steps[0].params["spd_pct"] == 50.0
    assert not second.ok


def test_append_step_assigns_next_step_id():
    plan = _plan()
    editor = DraftEditor()

    result = editor.append_step(plan, ExecutionStep(step_id=99, action="home", func_id=104, params={}))

    assert result.ok
    assert result.plan.steps[-1].step_id == 3
    assert result.plan.steps[-1].action == "home"
