from robot_modbus_lite.clarification_state import ClarificationManager, PendingClarification
from robot_modbus_lite.execution_plan import ExecutionPlan, ExecutionPlanStatus, ExecutionStep


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
        [
            PendingClarification.new(
                plan.plan_id,
                None,
                "target",
                "目标位置是什么？",
                ("position",),
                now=10.0,
                timeout_sec=5.0,
            )
        ],
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


def test_manager_deduplicates_same_step_field_questions():
    plan = ExecutionPlan.new(name="P", source="test")
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [PendingClarification.new(plan.plan_id, 1, "target_pose", "目标坐标是什么？", ("pose",), now=10.0)],
    )
    manager.add_missing_fields(
        plan,
        [PendingClarification.new(plan.plan_id, 1, "target_pose", "目标坐标是什么？", ("pose",), now=11.0)],
    )

    manager.apply_answer(plan.transition_to(ExecutionPlanStatus.NEED_CLARIFICATION), "1,2,3,4,5,6")

    assert manager.current_question(plan.plan_id) is None


def test_apply_answer_fills_missing_target_pose_and_pops_question():
    plan = ExecutionPlan.new(name="demo", source="test").with_steps(
        [ExecutionStep(step_id=1, action="move", func_id=108, params={"spd_pct": 50.0})]
    ).transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [
            PendingClarification.new(
                plan.plan_id,
                1,
                "target_pose",
                "目标坐标是多少？",
                ("pose",),
                now=10.0,
            )
        ],
    )

    result = manager.apply_answer(plan, "900，0，1000，0，0，0")

    assert result.applied is True
    assert result.plan.status == ExecutionPlanStatus.MODIFIED
    assert result.message == "已补齐第1步目标坐标。"
    params = result.plan.steps[0].params
    assert params["target_x"] == 900.0
    assert params["target_y"] == 0.0
    assert params["target_z"] == 1000.0
    assert params["target_rx"] == 0.0
    assert params["target_ry"] == 0.0
    assert params["target_rz"] == 0.0
    assert params["fuzzy_pos"] == 0
    assert manager.current_question(plan.plan_id) is None


def test_apply_answer_fills_missing_speed_and_keeps_next_question():
    plan = ExecutionPlan.new(name="demo", source="test").with_steps(
        [ExecutionStep(step_id=1, action="move", func_id=108, params={})]
    ).transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [
            PendingClarification.new(plan.plan_id, 1, "speed", "速度多少？", ("speed",), now=10.0),
            PendingClarification.new(plan.plan_id, 1, "move_type", "移动方式？", ("move_type",), now=10.0),
        ],
    )

    result = manager.apply_answer(plan, "30%")

    assert result.applied is True
    assert result.plan.status == ExecutionPlanStatus.NEED_CLARIFICATION
    assert result.plan.steps[0].params["spd_pct"] == 30.0
    assert result.plan.steps[0].params["acc_pct"] == 30.0
    assert result.plan.steps[0].params["dec_pct"] == 30.0
    assert manager.current_question(plan.plan_id).missing_field == "move_type"


def test_apply_answer_rejects_unparseable_answer_without_popping_question():
    plan = ExecutionPlan.new(name="demo", source="test").with_steps(
        [ExecutionStep(step_id=1, action="move", func_id=108, params={})]
    ).transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [PendingClarification.new(plan.plan_id, 1, "speed", "速度多少？", ("speed",), now=10.0)],
    )

    result = manager.apply_answer(plan, "快一点吧")

    assert result.applied is False
    assert result.plan is plan
    assert "无法识别" in result.message
    assert manager.current_question(plan.plan_id).missing_field == "speed"


def test_apply_answer_fills_missing_delay_seconds():
    plan = ExecutionPlan.new(name="demo", source="test").with_steps(
        [ExecutionStep(step_id=1, action="delay", func_id=110, params={})]
    ).transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [PendingClarification.new(plan.plan_id, 1, "delay_sec", "延时多久？", ("duration",), now=10.0)],
    )

    result = manager.apply_answer(plan, "2.5秒")

    assert result.applied is True
    assert result.plan.status == ExecutionPlanStatus.MODIFIED
    assert result.plan.steps[0].params["delay_sec"] == 2.5
    assert manager.current_question(plan.plan_id) is None


def test_apply_answer_fills_missing_io_number_and_action():
    plan = ExecutionPlan.new(name="demo", source="test").with_steps(
        [ExecutionStep(step_id=1, action="io", func_id=120, params={})]
    ).transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    manager = ClarificationManager()
    manager.add_missing_fields(
        plan,
        [
            PendingClarification.new(plan.plan_id, 1, "io_no", "IO编号是多少？", ("io_no",), now=10.0),
            PendingClarification.new(plan.plan_id, 1, "io_action", "打开还是关闭？", ("io_action",), now=10.0),
        ],
    )

    first = manager.apply_answer(plan, "IO3")
    second = manager.apply_answer(first.plan, "打开")

    assert first.applied is True
    assert first.plan.status == ExecutionPlanStatus.NEED_CLARIFICATION
    assert first.plan.steps[0].params["io_no"] == 3
    assert second.applied is True
    assert second.plan.status == ExecutionPlanStatus.MODIFIED
    assert second.plan.steps[0].params["io_action"] == 1
    assert manager.current_question(plan.plan_id) is None
