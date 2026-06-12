from robot_modbus_lite.agent_runtime.local_tool_registry import LocalToolRegistry
from robot_modbus_lite.agent_runtime.tool_calling_agent import build_local_tool_specs
from robot_modbus_lite.agent_runtime.tool_schemas import tool_input_schema, tool_output_schema, validate_tool_args, validate_tool_result
from robot_modbus_lite.agent_tools.tool_result import ToolResult


def test_tool_input_schema_exposes_required_fields():
    schema = tool_input_schema("parse_command_params")

    assert schema["type"] == "object"
    assert "text" in schema["properties"]
    assert schema["required"] == ["text"]


def test_every_exposed_tool_has_input_schema():
    missing = [spec.name for spec in build_local_tool_specs() if not spec.input_schema]

    assert missing == []


def test_validate_tool_args_coerces_valid_payload():
    result = validate_tool_args("parse_command_params", {"text": 123})

    assert result.ok is True
    assert result.state == "tool_args_valid"
    assert result.data["args"] == {"text": "123"}


def test_validate_tool_args_rejects_missing_required_field():
    result = validate_tool_args("parse_command_params", {})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["text"]


def test_validate_tool_args_rejects_blank_command_text():
    result = validate_tool_args("build_command_draft", {"text": "  "})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["text"]


def test_validate_tool_args_rejects_invalid_required_params_func_id():
    result = validate_tool_args("validate_required_params", {"func_id": 0, "params": {}})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["func_id"]


def test_validate_tool_args_rejects_blank_command_schema_lookup():
    result = validate_tool_args("lookup_command_schema", {})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["command_name"]


def test_validate_tool_args_rejects_blank_command_address_name():
    result = validate_tool_args("resolve_command_address", {"name": "  "})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["name"]


def test_validate_tool_args_rejects_empty_command_draft_before_dispatch():
    result = validate_tool_args("create_pending_confirm", {"draft": {}})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert "draft.draft_id" in result.errors[0]["fields"]
    assert "draft.func_id" in result.errors[0]["fields"]


def test_validate_tool_args_rejects_unconfirmed_draft_for_query_record_tool():
    result = validate_tool_args(
        "draft_to_query_record",
        {
            "draft": {
                "draft_id": "draft-1",
                "func_id": 109,
                "intent": "delay_blocking",
                "params": {"delay_sec": 2.0},
                "confirmed": False,
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["draft.confirmed"]


def test_validate_tool_args_rejects_invalid_draft_params_for_command_func_id():
    delay = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-delay",
                "func_id": 109,
                "intent": "delay_blocking",
                "params": {"delay_sec": 0},
            }
        },
    )
    io = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-io",
                "func_id": 120,
                "intent": "io_write",
                "params": {"io_no": 12, "io_action": 2},
            }
        },
    )

    assert delay.ok is False
    assert delay.state == "tool_args_invalid"
    assert delay.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert delay.errors[0]["fields"] == ["draft.params.delay_sec"]
    assert io.ok is False
    assert io.state == "tool_args_invalid"
    assert io.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(io.errors[0]["fields"]) == {"draft.params.io_no", "draft.params.io_action"}


def test_validate_tool_args_rejects_invalid_motion_draft_params_for_func_id():
    result = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-motion",
                "func_id": 108,
                "intent": "cartesian_move",
                "params": {
                    "target_x": 100.0,
                    "target_y": 0.0,
                    "target_z": 100.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "spd_pct": 150,
                },
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"draft.params.target_rz", "draft.params.spd_pct"}


def test_validate_tool_args_reports_motion_percent_range_message():
    result = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-motion",
                "func_id": 108,
                "intent": "cartesian_move",
                "params": {
                    "target_x": 0.0,
                    "target_y": 0.0,
                    "target_z": 50.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 150.0,
                    "acc_pct": 150.0,
                    "dec_pct": 150.0,
                },
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.message == "速度/加速度/减速度参数超出范围：当前 150%，允许 0~100%。请降低速度后重试。"
    assert set(result.errors[0]["fields"]) == {
        "draft.params.spd_pct",
        "draft.params.acc_pct",
        "draft.params.dec_pct",
    }


def test_validate_tool_args_rejects_invalid_continuous_path_draft_params_for_func_id():
    result = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-path",
                "func_id": 112,
                "intent": "continuous_path",
                "params": {
                    "target_x": 100.0,
                    "target_y": 0.0,
                    "target_z": 100.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 0,
                    "move_type": 9,
                },
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"draft.params.spd_pct", "draft.params.move_type"}


def test_validate_tool_args_rejects_invalid_motion_mode_fields_for_func_id():
    result = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-motion-mode",
                "func_id": 108,
                "intent": "cartesian_move",
                "params": {
                    "target_x": 100.0,
                    "target_y": 0.0,
                    "target_z": 100.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "move_type": 99,
                    "position_increment": 2,
                },
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"draft.params.move_type", "draft.params.position_increment"}


def test_validate_tool_args_rejects_invalid_jog_draft_params_for_func_id():
    result = validate_tool_args(
        "create_pending_confirm",
        {
            "draft": {
                "draft_id": "draft-jog",
                "func_id": 107,
                "intent": "virtual_jog",
                "params": {
                    "axis_no": 99,
                    "pos_val": "bad",
                    "spd_pct": 0,
                    "acc_pct": 101,
                },
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {
        "draft.params.axis_no",
        "draft.params.pos_val",
        "draft.params.spd_pct",
        "draft.params.acc_pct",
    }


def test_validate_tool_args_rejects_blank_position_alias_name():
    result = validate_tool_args("save_position_alias", {"name": "  ", "pose": (1, 2, 3, 4, 5, 6)})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["name"]


def test_validate_tool_args_rejects_short_position_alias_pose():
    result = validate_tool_args("save_position_alias", {"name": "A", "pose": (1, 2, 3)})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["pose"]


def test_validate_tool_args_rejects_invalid_position_alias_motion_defaults():
    result = validate_tool_args(
        "save_position_alias",
        {
            "name": "A",
            "pose": (1, 2, 3, 4, 5, 6),
            "spd": 0,
            "move_type": 9,
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"spd", "move_type"}


def test_validate_tool_args_rejects_param_bounds_without_schema_version():
    result = validate_tool_args(
        "check_param_bounds",
        {
            "params": {"target_x": 120.0},
            "bounds": {"x": (-100.0, 100.0)},
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["bounds.schema_version"]


def test_validate_tool_args_rejects_invalid_motion_percent_params():
    result = validate_tool_args(
        "check_param_bounds",
        {
            "params": {"spd_pct": 0, "acc_pct": 101, "dec_pct": -1},
            "bounds": {"schema_version": "safety-bounds-v1", "safe_speed_max": 80.0},
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"params.spd_pct", "params.acc_pct", "params.dec_pct"}


def test_validate_tool_args_rejects_invalid_memory_review_status():
    result = validate_tool_args("query_memory_review", {"status": "pending", "kind": "asr_alias"})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["status"]


def test_validate_tool_args_rejects_invalid_memory_candidate_identity():
    result = validate_tool_args(
        "create_memory_candidate",
        {
            "kind": "  ",
            "key": "  ",
            "value": {"normalized": "位置A"},
            "confidence": 1.5,
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"kind", "key", "confidence"}


def test_validate_tool_args_rejects_blank_pending_draft_id():
    result = validate_tool_args("confirm_pending_plan", {"draft_id": "  "})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["draft_id"]


def test_validate_tool_args_rejects_blank_memory_ids_and_active_kind():
    approve = validate_tool_args("approve_memory_candidate", {"memory_id": "  "})
    applied = validate_tool_args("record_memory_applied", {"memory_id": "  "})
    active = validate_tool_args("lookup_active_memory", {"kind": "  "})

    assert approve.ok is False
    assert approve.errors[0]["fields"] == ["memory_id"]
    assert applied.ok is False
    assert applied.errors[0]["fields"] == ["memory_id"]
    assert active.ok is False
    assert active.errors[0]["fields"] == ["kind"]


def test_validate_tool_args_rejects_invalid_feedback_vote_payload():
    result = validate_tool_args(
        "record_feedback_vote",
        {
            "interaction_id": "  ",
            "target_type": "controller",
            "target_id": "  ",
            "vote": "maybe",
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {"interaction_id", "target_type", "target_id", "vote"}


def test_validate_tool_args_rejects_blank_registered_flow_execution_name():
    result = validate_tool_args("prepare_registered_flow_execution", {"flow_name": "  "})

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["flow_name"]


def test_validate_tool_args_rejects_invalid_save_flow_draft_step_params():
    result = validate_tool_args(
        "save_flow_draft",
        {
            "draft": {
                "flow_name": "测试流程",
                "expanded_steps": [
                    {
                        "step_id": 1,
                        "action": "输出",
                        "func_id": 120,
                        "params": {"io_no": 12, "io_action": 2},
                    }
                ],
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {
        "draft.expanded_steps.0.params.io_no",
        "draft.expanded_steps.0.params.io_action",
    }


def test_validate_tool_args_rejects_invalid_save_flow_draft_path_step_params():
    result = validate_tool_args(
        "save_flow_draft",
        {
            "draft": {
                "flow_name": "测试流程",
                "expanded_steps": [
                    {
                        "step_id": 1,
                        "action": "绕行",
                        "func_id": 112,
                        "params": {
                            "target_x": 100.0,
                            "target_y": 0.0,
                            "target_z": 100.0,
                            "target_rx": 0.0,
                            "target_ry": 0.0,
                            "target_rz": 0.0,
                            "acc_pct": 101,
                            "position_increment": 3,
                        },
                    }
                ],
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert set(result.errors[0]["fields"]) == {
        "draft.expanded_steps.0.params.acc_pct",
        "draft.expanded_steps.0.params.position_increment",
    }


def test_validate_tool_args_rejects_invalid_save_flow_draft_jog_step_params():
    result = validate_tool_args(
        "save_flow_draft",
        {
            "draft": {
                "flow_name": "测试流程",
                "expanded_steps": [
                    {
                        "step_id": 1,
                        "action": "点头",
                        "func_id": 106,
                        "params": {"axis_no": 0, "pos_val": "bad", "dec_pct": 120},
                    }
                ],
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["draft.expanded_steps.0.func_id"]


def test_validate_tool_args_rejects_save_flow_draft_jog_step_even_when_params_valid():
    result = validate_tool_args(
        "save_flow_draft",
        {
            "draft": {
                "flow_name": "测试流程",
                "expanded_steps": [
                    {
                        "step_id": 1,
                        "action": "关节点动",
                        "func_id": 106,
                        "params": {
                            "axis_no": 1,
                            "pos_val": 5,
                            "spd_pct": 30,
                            "acc_pct": 30,
                            "dec_pct": 30,
                        },
                    }
                ],
            }
        },
    )

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.errors[0]["fields"] == ["draft.expanded_steps.0.func_id"]


def test_validate_tool_args_allows_incomplete_set_flow_draft_for_clarification():
    result = validate_tool_args(
        "set_flow_draft",
        {
            "draft": {
                "flow_name": "测试流程",
                "expanded_steps": [
                    {
                        "step_id": 1,
                        "action": "移动到位置A",
                        "func_id": 108,
                        "params": {"spd_pct": 50.0},
                    }
                ],
            }
        },
    )

    assert result.ok is True
    assert result.state == "tool_args_valid"


def test_local_tool_registry_validates_before_dispatch():
    registry = LocalToolRegistry()

    result = registry.call("parse_command_params")

    assert result.ok is False
    assert result.state == "tool_args_invalid"
    assert result.errors[0]["code"] == "TOOL_ARGS_INVALID"
    assert result.data["tool_name"] == "parse_command_params"


def test_tool_output_schema_exposes_tool_result_contract():
    schema = tool_output_schema("parse_command_params")

    assert schema["type"] == "object"
    assert schema["required"] == ["ok", "state"]
    assert set(schema["properties"]) == {"ok", "state", "message", "data", "errors"}


def test_validate_tool_result_rejects_non_tool_result_output():
    result = validate_tool_result("parse_command_params", {"ok": True, "state": "bad"})

    assert result.ok is False
    assert result.state == "tool_output_invalid"
    assert result.errors[0]["code"] == "TOOL_OUTPUT_INVALID"
    assert result.data["tool_name"] == "parse_command_params"


def test_validate_tool_result_rejects_empty_state():
    result = validate_tool_result("parse_command_params", ToolResult.success(state=""))

    assert result.ok is False
    assert result.state == "tool_output_invalid"
    assert result.errors[0]["code"] == "TOOL_OUTPUT_INVALID"


def test_local_tool_registry_validates_tool_output_before_returning():
    registry = LocalToolRegistry()
    registry._tools["parse_command_params"] = lambda **_kwargs: {"ok": True, "state": "bad"}

    result = registry.call("parse_command_params", text="X100")

    assert result.ok is False
    assert result.state == "tool_output_invalid"
    assert result.errors[0]["code"] == "TOOL_OUTPUT_INVALID"
    assert result.data["tool_name"] == "parse_command_params"
