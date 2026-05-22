from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


def make_adapter(memory: AtomicMemory | None = None) -> VoiceNlpAdapter:
    return VoiceNlpAdapter(table={}, flow_names=(), atomic_memory=memory or AtomicMemory())


def test_voice_nlp_adapter_parses_atomic_virtual_command():
    plan = make_adapter().parse("小正，上升3毫米")

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.semantic_level == 3
    assert plan.requires_confirmation is True
    record = plan.atomic_records[plan.actions[0].target]
    assert isinstance(record, QueryRecord)
    assert record.func_num == 107
    assert record.params["axis_no"] == 8
    assert record.params["pos_val"] == 3.0


def test_voice_nlp_adapter_keeps_dashboard_query_before_atomic():
    plan = make_adapter().parse("小正，查一下安全范围")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "safety_boundary"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_marks_atomic_capability_query():
    plan = make_adapter().parse("小正，现在支持哪些原子命令")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "atomic_capabilities"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_updates_atomic_memory():
    memory = AtomicMemory()
    plan = make_adapter(memory).parse("小正，速度60%")

    assert plan.actions[0].action_type == "memory"
    assert memory.current_speed == 60.0
    assert plan.requires_confirmation is False


def test_voice_nlp_adapter_ignores_unsupported_atomic_and_falls_back_unknown():
    plan = make_adapter().parse("小正，画个圆")

    assert plan.actions[0].action_type == "unknown"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_parses_named_position_move():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    plan = make_adapter(memory).parse("小正，移动到位置A")

    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.func_num == 108
    assert record.params["target_x"] == 350.0
    assert record.params["target_z"] == 500.0


def test_voice_nlp_adapter_parses_save_position_request():
    plan = make_adapter().parse("小正，保存当前位置为位置A")

    assert plan.actions[0].action_type == "memory"
    assert plan.actions[0].target == "position_save:A"
    assert plan.requires_confirmation is False


def test_voice_nlp_adapter_repeats_last_atomic_command_from_memory():
    memory = AtomicMemory()
    adapter = make_adapter(memory)
    first = adapter.parse("小正，上升3毫米")

    repeated = adapter.parse("小正，再走一次")

    assert first.actions[0].action_type == "atomic_template"
    assert repeated.actions[0].action_type == "atomic_template"
    record = repeated.atomic_records[repeated.actions[0].target]
    assert record.func_num == 107
    assert record.params["axis_no"] == 8
    assert record.params["pos_val"] == 3.0


def test_voice_nlp_adapter_parses_multiple_atomic_templates_in_order():
    plan = make_adapter().parse("小正，上升3毫米然后IO1开")

    assert [action.action_type for action in plan.actions] == ["atomic_template", "atomic_template"]
    assert len(plan.atomic_records) == 2
    first = plan.atomic_records[plan.actions[0].target]
    second = plan.atomic_records[plan.actions[1].target]
    assert first.func_num == 107
    assert first.params["axis_no"] == 8
    assert first.params["pos_val"] == 3.0
    assert second.func_num == 120
    assert second.params["io_no"] == 1
    assert second.params["io_action"] == 1


def test_voice_nlp_adapter_parses_three_step_atomic_sequence():
    plan = make_adapter().parse("小正，J1转到45度然后等待2秒然后IO1关")

    assert [action.action_type for action in plan.actions] == [
        "atomic_template",
        "atomic_template",
        "atomic_template",
    ]
    records = [plan.atomic_records[action.target] for action in plan.actions]
    assert [record.func_num for record in records] == [106, 110, 120]
    assert records[0].params["pos_val"] == 45.0
    assert records[1].params["delay_sec"] == 2.0
    assert records[2].params["io_action"] == 0


def test_voice_nlp_adapter_rejects_partial_atomic_sequence():
    plan = make_adapter().parse("小正，上升3毫米然后画个圆")

    assert plan.actions[0].action_type == "unknown"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_rejects_unsupported_complex_atomic_commands():
    adapter = make_adapter()

    loop_plan = adapter.parse("小正，上升3毫米重复3次")
    parallel_plan = adapter.parse("小正，同时上升3毫米并且IO1开")
    conditional_plan = adapter.parse("小正，如果没有报警就上升3毫米")

    for plan in (loop_plan, parallel_plan, conditional_plan):
        assert plan.actions[0].action_type == "unknown"
        assert plan.atomic_records == {}
        assert "暂不支持" in plan.reason


def test_voice_nlp_adapter_rejects_func11_continuous_interpolation_commands():
    adapter = make_adapter()

    path_plan = adapter.parse("小正，连续路径经过位置A和位置B")
    interpolation_plan = adapter.parse("小正，插补到X100Y200Z300")
    trajectory_plan = adapter.parse("小正，执行轨迹A")

    for plan in (path_plan, interpolation_plan, trajectory_plan):
        assert plan.actions[0].action_type == "unknown"
        assert plan.atomic_records == {}
        assert "Func11" in plan.reason
        assert "暂不支持" in plan.reason


def test_atomic_confirm_mode_beginner_confirms_all_atomic_templates():
    memory = AtomicMemory(confirm_mode="beginner")

    delay_plan = make_adapter(memory).parse("小正，等待2秒")

    assert delay_plan.actions[0].action_type == "atomic_template"
    assert delay_plan.requires_confirmation is True


def test_atomic_confirm_mode_skilled_only_confirms_high_risk_motion():
    memory = AtomicMemory(confirm_mode="skilled")
    adapter = make_adapter(memory)

    delay_plan = adapter.parse("小正，等待2秒")
    io_plan = adapter.parse("小正，IO1开")
    motion_plan = adapter.parse("小正，上升3毫米")

    assert delay_plan.requires_confirmation is False
    assert io_plan.requires_confirmation is False
    assert motion_plan.requires_confirmation is True


def test_atomic_confirm_mode_expert_still_confirms_high_risk_motion():
    memory = AtomicMemory(confirm_mode="expert")
    adapter = make_adapter(memory)

    io_plan = adapter.parse("小正，IO1开")
    motion_plan = adapter.parse("小正，上升3毫米")

    assert io_plan.requires_confirmation is False
    assert motion_plan.requires_confirmation is True
