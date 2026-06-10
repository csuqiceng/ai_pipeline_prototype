from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.agent_tools.command_tools import (
    apply_atomic_template,
    build_command_draft,
    build_system_action_draft,
    check_param_bounds,
    draft_to_query_record,
    lookup_command_schema,
    normalize_chinese_numbers,
    parse_command_intent,
    parse_command_params,
    resolve_command_address,
    validate_required_params,
)


def _snapshot():
    return ControllerSnapshot(
        current_pose={
            "target_x": 0.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
        },
        safety_params={"spd_pct": 30.0, "acc_pct": 20.0, "dec_pct": 20.0},
    )


def test_normalize_chinese_numbers_rewrites_axis_values():
    assert normalize_chinese_numbers("X 一百，Y 0，Z 负二十，速度 五十") == "X100，Y 0，Z-20，速度50"


def test_parse_command_params_reuses_command_understanding_with_normalized_text():
    result = parse_command_params("我觉得坐标是 X 一百， Y 0， Z 100，速度 50。")

    assert result.ok is True
    assert result.state == "command_params_parsed"
    assert result.data["intent"] == "move_linear"
    assert result.data["func_id"] == 108
    assert result.data["raw_text"] == "我觉得坐标是 X 一百， Y 0， Z 100，速度 50。"
    assert result.data["normalized_text"] == "我觉得坐标是 X100， Y 0， Z 100，速度50。"
    assert result.data["params"]["target_x"] == 100.0
    assert result.data["params"]["target_y"] == 0.0
    assert result.data["params"]["target_z"] == 100.0
    assert result.data["params"]["spd_pct"] == 50.0


def test_parse_command_params_returns_missing_state_for_unknown_text():
    result = parse_command_params("帮我推荐一些流程步骤")

    assert result.ok is False
    assert result.state == "unknown_intent"
    assert result.errors[0]["code"] == "UNKNOWN_INTENT"


def test_lookup_command_schema_returns_required_params_and_metadata():
    result = lookup_command_schema(108)

    assert result.ok is True
    assert result.state == "command_schema_loaded"
    assert result.data["schema"]["func_id"] == 108
    assert "target_x" in result.data["schema"]["required_params"]
    assert result.data["schema"]["generates_command"] is False


def test_validate_required_params_reports_missing_fields():
    result = validate_required_params(109, {})

    assert result.ok is False
    assert result.state == "missing_params"
    assert result.errors[0]["fields"] == ["delay_sec"]


def test_check_param_bounds_rejects_out_of_range_pose_and_speed():
    result = check_param_bounds(
        {"target_x": 120.0, "target_y": 0.0, "target_z": 250.0, "spd_pct": 80.0},
        bounds={"x": (-100.0, 100.0), "y": (-50.0, 50.0), "z": (0.0, 200.0), "safe_speed_max": 60.0},
    )

    assert result.ok is False
    assert result.state == "param_bounds_failed"
    assert {item["field"] for item in result.data["violations"]} == {"target_x", "target_z", "spd_pct"}


def test_resolve_command_address_returns_local_protocol_constant():
    result = resolve_command_address("absolute_motion_func")

    assert result.ok is True
    assert result.state == "command_address_resolved"
    assert result.data["name"] == "absolute_motion_func"
    assert result.data["value"] == 108


def test_build_system_action_draft_returns_non_executing_system_payload():
    result = build_system_action_draft("暂停")

    assert result.ok is True
    assert result.state == "system_action_draft_built"
    assert result.data["intent"] == "sys_pause"
    assert result.data["func_id"] == 104
    assert result.data["generates_command"] is False
    assert result.data["requires_execution_gate"] is True


def test_parse_command_intent_returns_structured_control_classification_without_params():
    result = parse_command_intent("我觉得坐标是 X 一百， Y 0， Z 100，速度 50。")

    assert result.ok is True
    assert result.state == "command_intent_parsed"
    assert result.data["intent"] == "move_linear"
    assert result.data["func_id"] == 108
    assert result.data["raw_text"] == "我觉得坐标是 X 一百， Y 0， Z 100，速度 50。"
    assert result.data["normalized_text"] == "我觉得坐标是 X100， Y 0， Z 100，速度50。"
    assert result.data["generates_command"] is False
    assert "params" not in result.data


def test_parse_command_intent_returns_unknown_without_side_effects():
    result = parse_command_intent("帮我推荐一些流程步骤")

    assert result.ok is False
    assert result.state == "unknown_intent"
    assert result.errors[0]["code"] == "UNKNOWN_INTENT"
    assert result.data["generates_command"] is False


def test_build_command_draft_reuses_understanding_and_parameter_completion():
    result = build_command_draft("移动到 X 一百，Y 0，Z 100，速度 50", snapshot_provider=_snapshot)

    assert result.ok is True
    assert result.state == "command_draft_built"
    assert result.data["draft"]["intent"] == "move_linear"
    assert result.data["draft"]["func_id"] == 108
    assert result.data["draft"]["raw_text"] == "移动到 X100，Y 0，Z 100，速度50%"
    assert result.data["draft"]["params"]["target_x"] == 100.0
    assert result.data["draft"]["params"]["target_y"] == 0.0
    assert result.data["draft"]["params"]["target_z"] == 100.0
    assert result.data["draft"]["params"]["target_rx"] == 0.0
    assert result.data["draft"]["params"]["spd_pct"] == 50.0
    assert result.data["draft"]["params"]["acc_pct"] == 20.0
    assert result.data["draft"]["param_sources"]["target_rx"] == "inherited"
    assert result.data["draft"]["param_sources"]["acc_pct"] == "controller"
    assert result.data["generates_command"] is False


def test_build_command_draft_returns_unknown_intent_without_side_effects():
    result = build_command_draft("帮我推荐一些流程步骤", snapshot_provider=_snapshot)

    assert result.ok is False
    assert result.state == "unknown_intent"
    assert result.errors[0]["code"] == "UNKNOWN_INTENT"
    assert result.data["generates_command"] is False


def test_build_command_draft_reports_completion_failure_when_snapshot_is_missing():
    result = build_command_draft("移动到 X 100，Y 0，Z 100，速度 50")

    assert result.ok is False
    assert result.state == "command_draft_needs_clarification"
    assert result.errors[0]["code"] == "COMMAND_DRAFT_INCOMPLETE"
    assert result.data["generates_command"] is False


def test_draft_to_query_record_rejects_unconfirmed_draft():
    result = draft_to_query_record(
        {
            "draft_id": "draft-1",
            "func_id": 109,
            "intent": "delay_blocking",
            "params": {"delay_sec": 2.0},
            "param_sources": {"delay_sec": "specified"},
            "raw_text": "等待2秒",
            "confidence": 0.95,
            "confirmed": False,
        }
    )

    assert result.ok is False
    assert result.state == "command_draft_not_confirmed"
    assert result.errors[0]["code"] == "COMMAND_DRAFT_NOT_CONFIRMED"
    assert "query_record" not in result.data


def test_draft_to_query_record_converts_confirmed_draft_only():
    result = draft_to_query_record(
        {
            "draft_id": "draft-1",
            "func_id": 109,
            "intent": "delay_blocking",
            "params": {"delay_sec": 2.0},
            "param_sources": {"delay_sec": "specified"},
            "raw_text": "等待2秒",
            "confidence": 0.95,
            "confirmed": True,
        }
    )

    assert result.ok is True
    assert result.state == "query_record_built"
    assert result.data["query_record"]["query_key"] == "agent:draft-1"
    assert result.data["query_record"]["func_num"] == 109
    assert result.data["query_record"]["params"]["delay_sec"] == 2.0


def test_apply_atomic_template_returns_structured_record_without_mutating_memory():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))
    before_stack = list(memory.position_stack)

    result = apply_atomic_template("小正，移动到位置A", memory=memory)

    assert result.ok is True
    assert result.state == "atomic_template_applied"
    assert result.data["target"] == "atomic:position:A"
    assert result.data["query_record"]["func_num"] == 108
    assert result.data["query_record"]["params"]["target_x"] == 350.0
    assert result.data["requires_confirmation"] is True
    assert result.data["generates_command"] is False
    assert memory.position_stack == before_stack


def test_apply_atomic_template_reports_no_match_without_side_effects():
    result = apply_atomic_template("保存当前位置为位置A", memory=AtomicMemory())

    assert result.ok is False
    assert result.state == "atomic_template_not_matched"
    assert result.errors[0]["code"] == "ATOMIC_TEMPLATE_NOT_MATCHED"
    assert result.data["generates_command"] is False
