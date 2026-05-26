import pytest

from robot_modbus_lite.execution_plan import (
    ExecutionPlan,
    ExecutionPlanStatus,
    InvalidPlanTransition,
    execution_plan_to_flow_draft,
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
                "params": {
                    "target_x": 1475.0,
                    "target_y": 0.0,
                    "target_z": 1545.0,
                    "spd_pct": 50.0,
                },
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
    assert plan.steps[0].description == "移动到 home"
    assert plan.steps[1].params["axis_no"] == 8


def test_execution_plan_transition_rejects_invalid_jump_to_executing():
    plan = ExecutionPlan.new(name="P", source="test")

    with pytest.raises(InvalidPlanTransition):
        plan.transition_to(ExecutionPlanStatus.EXECUTING)


def test_execution_plan_transition_allows_draft_to_modified_without_mutating_original():
    plan = ExecutionPlan.new(name="P", source="test")
    updated = plan.transition_to(ExecutionPlanStatus.MODIFIED)

    assert updated.status == ExecutionPlanStatus.MODIFIED
    assert plan.status == ExecutionPlanStatus.DRAFT


def test_execution_plan_to_flow_draft_preserves_steps():
    plan = flow_draft_to_execution_plan(_flow_draft(), source="flow_draft")

    draft = execution_plan_to_flow_draft(plan)

    assert draft["flow_name"] == "打招呼"
    assert draft["expanded_steps"][0]["func_id"] == 108
    assert draft["expanded_steps"][1]["params"]["pos_val"] == 50.0
