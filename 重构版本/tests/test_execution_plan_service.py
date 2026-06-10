from robot_modbus_lite.clarification_state import PendingClarification
from robot_modbus_lite.execution_plan import ExecutionPlan, ExecutionPlanStatus, ExecutionStep
from robot_modbus_lite.execution_plan_service import ExecutionPlanService


def test_service_holds_pending_plan_and_editor():
    service = ExecutionPlanService()
    plan = ExecutionPlan.new(name="P", source="test").with_steps(
        [ExecutionStep(step_id=1, action="move", func_id=108, params={"spd_pct": 50.0})]
    )

    service.set_pending_plan(plan)
    result = service.edit_all_speed(30.0)

    assert result.ok
    assert service.pending_plan is not None
    assert service.pending_plan.steps[0].params["spd_pct"] == 30.0


def test_service_cancel_clears_pending_plan_and_clarifications():
    service = ExecutionPlanService()
    plan = ExecutionPlan.new(name="P", source="test")
    service.set_pending_plan(plan)
    service.add_clarifications(
        [
            PendingClarification.new(
                plan.plan_id,
                None,
                "target",
                "目标位置是什么？",
                ("position",),
                now=10.0,
            )
        ]
    )

    service.cancel_pending_plan()

    assert service.pending_plan is None
    assert service.current_clarification() is None


def test_service_current_clarification_returns_pending_question():
    service = ExecutionPlanService()
    plan = ExecutionPlan.new(name="P", source="test")
    service.set_pending_plan(plan)
    service.add_clarifications(
        [
            PendingClarification.new(
                plan.plan_id,
                None,
                "target",
                "目标位置是什么？",
                ("position",),
                now=10.0,
            )
        ]
    )

    current = service.current_clarification()

    assert current is not None
    assert current.missing_field == "target"


def test_service_apply_clarification_answer_updates_pending_plan():
    plan = ExecutionPlan.new(name="P", source="test").with_steps(
        [ExecutionStep(step_id=1, action="move", func_id=108, params={})]
    ).transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    service = ExecutionPlanService()
    service.set_pending_plan(plan)
    service.add_clarifications(
        [PendingClarification.new(plan.plan_id, 1, "target_pose", "目标坐标是多少？", ("pose",), now=10.0)]
    )

    result = service.apply_clarification_answer("900,0,1000,0,0,0")

    assert result.applied is True
    assert service.pending_plan is result.plan
    assert service.pending_plan.status == ExecutionPlanStatus.MODIFIED
    assert service.pending_plan.steps[0].params["target_x"] == 900.0
    assert service.current_clarification() is None


def test_service_set_pending_flow_draft_creates_target_pose_clarification():
    service = ExecutionPlanService()
    service.set_pending_flow_draft(
        {
            "flow_name": "缺目标流程",
            "expanded_steps": [
                {"step_id": 1, "action": "移动", "func_id": 108, "params": {"spd_pct": 50.0}},
            ],
        }
    )

    assert service.pending_plan.status == ExecutionPlanStatus.NEED_CLARIFICATION
    current = service.current_clarification()
    assert current is not None
    assert current.step_id == 1
    assert current.missing_field == "target_pose"
    assert "第1步" in current.question


def test_service_set_pending_flow_draft_creates_delay_clarification():
    service = ExecutionPlanService()
    service.set_pending_flow_draft(
        {
            "flow_name": "缺延时流程",
            "expanded_steps": [
                {"step_id": 1, "action": "等待", "func_id": 110, "params": {}},
            ],
        }
    )

    assert service.pending_plan.status == ExecutionPlanStatus.NEED_CLARIFICATION
    current = service.current_clarification()
    assert current is not None
    assert current.step_id == 1
    assert current.missing_field == "delay_sec"
    assert "延时" in current.question


def test_service_apply_delay_clarification_accepts_chinese_duration():
    service = ExecutionPlanService()
    service.set_pending_flow_draft(
        {
            "flow_name": "缺延时流程",
            "expanded_steps": [
                {"step_id": 1, "action": "等待", "func_id": 110, "params": {}},
            ],
        }
    )

    result = service.apply_clarification_answer("两秒")

    assert result.applied is True
    assert service.pending_plan.steps[0].params["delay_sec"] == 2.0
    assert service.current_clarification() is None


def test_service_set_pending_flow_draft_creates_io_clarifications():
    service = ExecutionPlanService()
    service.set_pending_flow_draft(
        {
            "flow_name": "缺IO流程",
            "expanded_steps": [
                {"step_id": 1, "action": "IO控制", "func_id": 120, "params": {}},
            ],
        }
    )

    assert service.pending_plan.status == ExecutionPlanStatus.NEED_CLARIFICATION
    first = service.current_clarification()
    assert first is not None
    assert first.step_id == 1
    assert first.missing_field == "io_no"
    assert "IO编号" in first.question


def test_service_defaults_missing_move_type_and_records_notice():
    service = ExecutionPlanService()
    service.set_pending_flow_draft(
        {
            "flow_name": "缺运动方式流程",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动",
                    "func_id": 108,
                    "params": {
                        "target_x": 1.0,
                        "target_y": 2.0,
                        "target_z": 3.0,
                        "target_rx": 4.0,
                        "target_ry": 5.0,
                        "target_rz": 6.0,
                    },
                },
            ],
        }
    )

    assert service.current_clarification() is None
    assert service.pending_plan.status == ExecutionPlanStatus.DRAFT
    assert service.pending_plan.steps[0].params["move_type"] == 0
    assert service.default_notices == ["第1步未指定移动方式，已默认使用直线插补(move_type=0)。"]
