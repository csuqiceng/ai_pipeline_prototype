from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent


def test_understands_full_cartesian_move_without_model():
    result = CommandUnderstandingAgent().understand("走到 X1000 Y200 Z300 RX1 RY2 RZ3 速度50%")

    assert result.intent == "move_linear"
    assert result.func_id == 108
    assert result.extracted_params == {
        "target_x": 1000.0,
        "target_y": 200.0,
        "target_z": 300.0,
        "target_rx": 1.0,
        "target_ry": 2.0,
        "target_rz": 3.0,
        "spd_pct": 50.0,
        "position_increment": 0,
    }
    assert result.confidence >= 0.85
    assert result.needs_model is False


def test_understands_partial_cartesian_move_for_inheritance():
    result = CommandUnderstandingAgent().understand("小正 走到 X1000 Z300")

    assert result.intent == "move_linear"
    assert result.func_id == 108
    assert result.extracted_params == {
        "target_x": 1000.0,
        "target_z": 300.0,
        "position_increment": 0,
    }
    assert result.needs_model is False


def test_understands_lowercase_equal_comma_cartesian_move_for_inheritance():
    result = CommandUnderstandingAgent().understand("小正，移动到x=1000,y=0,z=1500")

    assert result.intent == "move_linear"
    assert result.func_id == 108
    assert result.extracted_params == {
        "target_x": 1000.0,
        "target_y": 0.0,
        "target_z": 1500.0,
        "position_increment": 0,
    }
    assert result.needs_model is False


def test_understands_incremental_cartesian_move_without_model():
    left = CommandUnderstandingAgent().understand("向左移动200")
    up = CommandUnderstandingAgent().understand("升高100")

    assert left.intent == "move_linear"
    assert left.func_id == 108
    assert left.extracted_params == {"delta_x": 200.0, "position_increment": 1}
    assert left.needs_model is False
    assert up.intent == "move_linear"
    assert up.func_id == 108
    assert up.extracted_params == {"delta_z": 100.0, "position_increment": 1}
    assert up.needs_model is False


def test_understands_absolute_cartesian_move_marks_position_increment_zero():
    result = CommandUnderstandingAgent().understand("走到X1000")

    assert result.intent == "move_linear"
    assert result.func_id == 108
    assert result.extracted_params["target_x"] == 1000.0
    assert result.extracted_params["position_increment"] == 0


def test_understands_chinese_numbers_in_cartesian_parameters_without_model():
    raw_text = "我觉得坐标是 X 一百 Y零 Z一百 速度 五十"
    result = CommandUnderstandingAgent().understand(raw_text)

    assert result.intent == "move_linear"
    assert result.func_id == 108
    assert result.raw_text == raw_text
    assert result.normalized_text == "我觉得坐标是 X100 Y0 Z100 速度50"
    assert result.extracted_params == {
        "target_x": 100.0,
        "target_y": 0.0,
        "target_z": 100.0,
        "spd_pct": 50.0,
        "position_increment": 0,
    }
    assert result.needs_model is False


def test_understands_asr_axis_aliases_before_chinese_number_parsing():
    raw_text = "我觉得坐标是 艾克斯 一百，歪 零，Z一百，速度 五十"
    result = CommandUnderstandingAgent().understand(raw_text)

    assert result.intent == "move_linear"
    assert result.func_id == 108
    assert result.raw_text == raw_text
    assert result.normalized_text == "我觉得坐标是 X100，Y0，Z100，速度50"
    assert result.extracted_params["target_x"] == 100.0
    assert result.extracted_params["target_y"] == 0.0
    assert result.extracted_params["target_z"] == 100.0
    assert result.extracted_params["spd_pct"] == 50.0


def test_understands_continuous_path_motion_as_func112_executable_candidate():
    result = CommandUnderstandingAgent().understand("规划路径走到X1000 Y200 Z300")

    assert result.intent == "continuous_path"
    assert result.func_id == 112
    assert result.extracted_params["target_x"] == 1000.0
    assert result.extracted_params["target_y"] == 200.0
    assert result.extracted_params["target_z"] == 300.0
    assert result.extracted_params["position_increment"] == 0
    assert result.needs_model is False


def test_understands_emergency_fast_path_intent():
    result = CommandUnderstandingAgent().understand("急停")

    assert result.intent == "sys_estop"
    assert result.func_id == 104
    assert result.needs_model is False
    assert result.bypass_completion is True


def test_understands_cancel_current_action_alias():
    result = CommandUnderstandingAgent().understand("取消当前动作")

    assert result.intent == "sys_cancel"
    assert result.func_id == 104
    assert result.needs_model is False
    assert result.bypass_completion is True


def test_understands_asr_phonetic_system_action_alias():
    result = CommandUnderstandingAgent().understand("夫位")

    assert result.intent == "alarm_reset"
    assert result.func_id == 104
    assert result.raw_text == "夫位"
    assert result.normalized_text == "复位"
    assert result.bypass_completion is True


def test_understands_alarm_query_as_read_only():
    result = CommandUnderstandingAgent().understand("当前报警是什么")

    assert result.intent == "alarm_query"
    assert result.func_id is None
    assert result.needs_model is False
    assert result.bypass_completion is True


def test_understands_status_query_as_read_only():
    for text in ("当前状态怎么样", "为什么不能动", "运动完成了吗"):
        result = CommandUnderstandingAgent().understand(text)

        assert result.intent == "status_query"
        assert result.func_id is None
        assert result.needs_model is False
        assert result.bypass_completion is True


def test_understands_delay_and_io_commands_without_model():
    delay = CommandUnderstandingAgent().understand("等待2秒")
    parallel_delay = CommandUnderstandingAgent().understand("并行延时5秒")
    io_on = CommandUnderstandingAgent().understand("IO1开")

    assert delay.intent == "delay_blocking"
    assert delay.func_id == 109
    assert delay.extracted_params == {"delay_sec": 2.0}
    assert delay.needs_model is False
    assert parallel_delay.intent == "delay_parallel"
    assert parallel_delay.func_id == 110
    assert parallel_delay.extracted_params == {"delay_sec": 5.0}
    assert io_on.intent == "io"
    assert io_on.func_id == 120
    assert io_on.extracted_params == {"io_no": 1, "io_action": 1}


def test_understands_joint_and_virtual_jog_without_model():
    joint = CommandUnderstandingAgent().understand("小正，J1转到45度30%速度")
    virtual = CommandUnderstandingAgent().understand("小正，RY反转15度")
    linear_virtual = CommandUnderstandingAgent().understand("小正，上升3毫米")

    assert joint.intent == "joint_jog"
    assert joint.func_id == 106
    assert joint.extracted_params == {
        "axis_no": 0,
        "pos_val": 45.0,
        "spd_pct": 30.0,
        "fuzzy_pos": 0,
        "fuzzy_spd": 0,
    }
    assert joint.needs_model is False
    assert virtual.intent == "virtual_jog"
    assert virtual.func_id == 107
    assert virtual.extracted_params == {
        "axis_no": 10,
        "pos_val": -15.0,
        "fuzzy_pos": 1,
        "fuzzy_spd": 1,
    }
    assert linear_virtual.intent == "virtual_jog"
    assert linear_virtual.func_id == 107
    assert linear_virtual.extracted_params == {
        "axis_no": 8,
        "pos_val": 3.0,
        "fuzzy_pos": 1,
        "fuzzy_spd": 1,
    }


def test_rejects_compound_command_instead_of_dropping_later_steps():
    result = CommandUnderstandingAgent().understand("走到 X1000 然后 IO1开")

    assert result.intent == "unknown"
    assert result.bypass_completion is True
    assert result.needs_model is False
    assert "复合指令" in result.clarification


def test_unclear_control_text_requires_model_or_clarification():
    result = CommandUnderstandingAgent().understand("往那边去一点")

    assert result.intent == "unknown"
    assert result.func_id is None
    assert result.raw_text == "往那边去一点"
    assert result.normalized_text == "往那边去一点"
    assert result.confidence < 0.5
    assert result.needs_model is True
    assert "请补充" in result.clarification


def test_unknown_control_text_keeps_normalized_parameter_trace():
    result = CommandUnderstandingAgent().understand("走到 X一百 然后 IO1开")

    assert result.intent == "unknown"
    assert result.raw_text == "走到 X一百 然后 IO1开"
    assert result.normalized_text == "移动 X100 然后 IO1打开"
    assert "复合指令" in result.clarification
