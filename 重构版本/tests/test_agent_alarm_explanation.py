from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.agent.alarm_explanation import AlarmExplanationAgent


def test_parse_six_safety_limits_exposes_pose_angles():
    values = [0.0] * 22
    values[16] = 11.0
    values[17] = 22.0
    values[18] = 33.0
    values[19] = 44.0

    parsed = RobotModbusService("unused.csv", table={}).parse_six_safety_limits(values)

    assert parsed["pose_upper_angle"] == 11.0
    assert parsed["pose_lower_angle"] == 22.0
    assert parsed["pose_cw_angle"] == 33.0
    assert parsed["pose_ccw_angle"] == 44.0


def test_alarm_explanation_reports_estop_before_normal_status():
    result = AlarmExplanationAgent().explain(long34=1 << 25, long36=0, long38=0, axis_status=[], current_func=108)

    assert result["severity"] == "critical"
    assert "急停" in result["summary"]
    assert result["can_move"] is False
    assert result["func_name_zh"] == "直线插补"


def test_alarm_explanation_reports_estop_source_from_long36():
    result = AlarmExplanationAgent().explain(long34=1 << 25, long36=(1 << 3) | 1, long38=0, axis_status=[], current_func=108)

    assert "硬件急停按钮" in result["detail"]
    assert "上位机急停" in result["detail"]
    assert "硬件急停按钮" in result["suggestions"][0]


def test_alarm_explanation_reports_drive_alarm_as_estop_cause():
    result = AlarmExplanationAgent().explain(
        long34=1 << 25,
        long36=0,
        long38=1 << 7,
        axis_status=[0, 1 << 3, 0, 0, 0, 0],
        current_func=108,
    )

    assert result["severity"] == "critical"
    assert "急停" in result["summary"]
    assert "驱动器故障" in result["detail"]
    assert "J2" in result["detail"]
    assert result["affected_axes"] == [2]


def test_alarm_explanation_reports_ethercat_alarm_even_without_axis_detail():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long36=0,
        long38=1 << 6,
        axis_status=[],
        current_func=108,
    )

    assert result["severity"] == "critical"
    assert "EtherCAT" in result["summary"]
    assert result["can_move"] is False


def test_alarm_explanation_uses_axis_detail_for_drive_alarm():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long36=0,
        long38=1 << 7,
        axis_status=[0, 1 << 3, 0, 0, 0, 0],
        current_func=108,
    )

    assert result["severity"] == "critical"
    assert result["affected_axes"] == [2]
    assert "J2" in result["summary"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_outer_radius_alarm_direction():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=1 << 0,
        safety_values={
            "safe_r_min": 200.0,
            "safe_r_max": 1800.0,
            "current_r": 1900.0,
        },
        current_func=108,
    )

    assert result["severity"] == "critical"
    assert "伸太远" in result["summary"]
    assert "超出100.0mm" in result["detail"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_inner_radius_alarm_direction():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=1 << 0,
        safety_values={
            "safe_r_min": 200.0,
            "safe_r_max": 1800.0,
            "current_r": 150.0,
        },
        current_func=108,
    )

    assert "收太近" in result["summary"]
    assert "不足50.0mm" in result["detail"]


def test_alarm_explanation_reports_height_alarm_direction():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=1 << 1,
        safety_values={
            "safe_z_min": 0.0,
            "safe_z_max": 2500.0,
            "current_z": 2600.0,
        },
        current_func=108,
    )

    assert "太高" in result["summary"]
    assert "超出100.0mm" in result["detail"]


def test_alarm_explanation_reports_speed_clamp_bits():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=(1 << 3) | (1 << 4) | (1 << 5),
        safety_values={
            "safe_speed_max": 60.0,
            "safe_acc_max": 50.0,
            "safe_dec_max": 40.0,
        },
        current_func=108,
    )

    assert result["severity"] == "warning"
    assert "速度已自动降速到安全上限60.0%" in result["detail"]
    assert "加速度已自动降低到安全上限50.0%" in result["detail"]
    assert "减速度已自动降低到安全上限40.0%" in result["detail"]
    assert result["can_move"] is True


def test_alarm_explanation_reports_paused_precise_action_hint():
    result = AlarmExplanationAgent().explain(long34=1 << 26, long36=4, long38=0, current_func=108)

    assert result["severity"] == "warning"
    assert "暂停" in result["summary"]
    assert "无法执行新指令" in result["detail"]
    assert "LONG(36)=4" in result["detail"]
    assert "继续" in result["suggestions"][0]
    assert result["can_move"] is False


def test_alarm_explanation_reports_current_function_execution_state():
    result = AlarmExplanationAgent().explain(long34=(1 << 28) | (1 << 6), long38=0, current_func=108)

    assert result["severity"] == "info"
    assert "正在执行：直线插补" in result["summary"]
    assert "EXEC" in result["detail"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_function_done_state():
    result = AlarmExplanationAgent().explain(long34=(1 << 28) | (2 << 6), long38=0, current_func=108)

    assert result["severity"] == "ok"
    assert "运动完成" in result["summary"]
    assert "DONE" in result["detail"]
    assert result["can_move"] is True


def test_alarm_explanation_reports_function_error_state():
    result = AlarmExplanationAgent().explain(long34=(1 << 28) | (3 << 6), long38=0, current_func=108)

    assert result["severity"] == "critical"
    assert "运动失败" in result["summary"]
    assert "ERR" in result["detail"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_reset_failed_when_alarm_detail_remains():
    result = AlarmExplanationAgent().explain(long34=1 << 28, long38=1 << 7, axis_status=[1 << 3, 0, 0, 0, 0, 0], current_func=104)

    assert "J1" in result["summary"]
    assert "驱动器故障" in result["summary"]
    assert "复位未成功" in result["detail"]


def test_alarm_explanation_reports_reset_failed_when_long38_remains_without_axis_detail():
    result = AlarmExplanationAgent().explain(long34=1 << 28, long38=1 << 2, axis_status=[], current_func=104)

    assert result["severity"] == "critical"
    assert "复位未成功" in result["summary"]
    assert "LONG(38)=4" in result["detail"]
    assert result["can_move"] is False


def test_alarm_explanation_blocks_unrecognized_long38_alarm_bits():
    result = AlarmExplanationAgent().explain(long34=1 << 28, long38=1 << 2, axis_status=[], current_func=108)

    assert result["severity"] == "critical"
    assert "LONG(38)" in result["summary"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_not_ready_without_global_alarm():
    result = AlarmExplanationAgent().explain(long34=0, long38=0, current_func=108)

    assert result["severity"] == "warning"
    assert "未就绪" in result["summary"]
    assert "LONG(34) bit28" in result["detail"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_servo_and_axis_enable_status():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=0,
        current_func=108,
        hardware_values={
            "axis_enabled": [1, 0, 1, 1, 1, 1],
            "any_axis_moving": 0,
            "ethercat_initialized": 1,
            "servo_enabled": 0,
            "wdog": 1,
        },
    )

    assert result["severity"] == "warning"
    assert "伺服未使能" in result["summary"]
    assert "J2轴未使能" in result["detail"]
    assert result["can_move"] is False


def test_alarm_explanation_reports_axis_alarm_flags_when_axis_status_unavailable():
    result = AlarmExplanationAgent().explain(
        long34=(1 << 28) | (1 << 24),
        long38=0,
        current_func=108,
        axis_status=[],
        hardware_values={
            "axis_alarm_flags": [0, 1, 0, 0, 0, 1],
            "servo_enabled": 1,
            "ethercat_initialized": 1,
        },
    )

    assert result["severity"] == "critical"
    assert "J2" in result["summary"]
    assert "J6" in result["summary"]
    assert "AXISSTATUS" in result["detail"]
    assert result["affected_axes"] == [2, 6]
    assert result["can_move"] is False


def test_alarm_explanation_reports_hardware_ok_when_enabled():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=0,
        current_func=108,
        hardware_values={
            "axis_enabled": [1, 1, 1, 1, 1, 1],
            "any_axis_moving": 0,
            "ethercat_initialized": 1,
            "servo_enabled": 1,
            "wdog": 1,
        },
    )

    assert result["severity"] == "ok"
    assert result["can_move"] is True


def test_alarm_explanation_ignores_unknown_hardware_values():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long38=0,
        current_func=108,
        hardware_values={
            "servo_enabled": "-",
            "ethercat_initialized": None,
        },
    )

    assert result["severity"] == "ok"
    assert result["can_move"] is True
