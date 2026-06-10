import pytest

from robot_modbus_lite.agent.confirmation import ConfirmationError
from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent
from robot_modbus_lite.motion_plan import MotionPlanService
from robot_modbus_lite.agent.service import RestrictedAgentService
from robot_modbus_lite.safety_precheck import SafetyPrecheckService
from robot_modbus_lite.system_config import AxisRangeConfig


def _config():
    return AxisRangeConfig(
        x=(-100.0, 1200.0),
        y=(-200.0, 200.0),
        z=(0.0, 500.0),
        safe_r_min=0.0,
        safe_r_max=1200.0,
        safe_z_min=0.0,
        safe_z_max=500.0,
        safe_speed_max=80.0,
        safe_acc_max=70.0,
        safe_dec_max=60.0,
    )


def _controller_snapshot(**overrides):
    data = {
        "current_pose": {
            "target_x": 10.0,
            "target_y": 20.0,
            "target_z": 30.0,
            "target_rx": 1.0,
            "target_ry": 2.0,
            "target_rz": 3.0,
        },
        "safety_params": {"spd_pct": 40.0, "acc_pct": 45.0, "dec_pct": 50.0},
        "is_moving": False,
        "read_ok": True,
    }
    data.update(overrides)
    return ControllerSnapshot(**data)


def _runtime_snapshot(**overrides):
    data = {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }
    data.update(overrides)
    return data


def _service(controller_snapshot=None, runtime_snapshot=None):
    return RestrictedAgentService(
        controller_snapshot_provider=lambda: controller_snapshot or _controller_snapshot(),
        runtime_snapshot_provider=lambda: runtime_snapshot or _runtime_snapshot(),
        safety_review_agent=SafetyReviewAgent(l1_service=SafetyPrecheckService(_config())),
        status_signature_provider=lambda: "status1",
        safety_signature_provider=lambda: "safety1",
        clock=lambda: 100.0,
        confirm_timeout_sec=10.0,
    )


def _service_with_l1_config(config: AxisRangeConfig):
    return RestrictedAgentService(
        controller_snapshot_provider=lambda: _controller_snapshot(),
        runtime_snapshot_provider=lambda: _runtime_snapshot(),
        safety_review_agent=SafetyReviewAgent(l1_service=SafetyPrecheckService(config, max_sphere_radius=0.0)),
        status_signature_provider=lambda: "status1",
        safety_signature_provider=lambda: "safety1",
        clock=lambda: 100.0,
        confirm_timeout_sec=10.0,
    )


def test_parse_motion_with_missing_l2_engine_uses_confirmation_friendly_warning():
    service = RestrictedAgentService(
        controller_snapshot_provider=lambda: _controller_snapshot(),
        runtime_snapshot_provider=lambda: _runtime_snapshot(),
        safety_review_agent=SafetyReviewAgent(
            l1_service=SafetyPrecheckService(_config()),
            motion_plan_service=MotionPlanService(engine=None),
        ),
        status_signature_provider=lambda: "status1",
        safety_signature_provider=lambda: "safety1",
        clock=lambda: 100.0,
        confirm_timeout_sec=10.0,
    )

    result = service.parse("走到 X1000 Z300 速度60%")

    assert result.kind == "waiting_confirmation"
    assert result.precheck_result["status"] == "warning"
    assert result.precheck_result["summary"] == "L1安全检查通过；L2运动规划预演暂不可用，需现场确认。"
    assert "L1安全检查通过" in result.confirmation_text
    assert "L2运动规划预演暂不可用" in result.confirmation_text
    assert "确认执行？" in result.confirmation_text


def test_parse_motion_returns_waiting_confirmation_without_execution():
    service = _service()

    result = service.parse("走到 X1000 Z300 速度60%")

    assert result.kind == "waiting_confirmation"
    assert result.draft is not None
    assert result.draft.func_id == 108
    assert result.draft.precheck_result["valid"] is True
    assert result.confirmation_text.startswith("【复述确认】Func108")
    assert result.query_record is None


def test_parse_incremental_motion_returns_waiting_confirmation_without_execution():
    service = _service()

    result = service.parse("升高100")

    assert result.kind == "waiting_confirmation"
    assert result.draft is not None
    assert result.draft.params["target_z"] == 130.0
    assert result.draft.params["fuzzy_pos"] == 0
    assert result.draft.param_sources["target_z"] == "incremental"
    assert "增量计算" in result.confirmation_text
    assert result.query_record is None


def test_parse_continuous_path_returns_waiting_confirmation_and_query_record():
    service = _service()

    result = service.parse("规划路径走到 X1000 Z300")

    assert result.kind == "waiting_confirmation"
    assert result.intent == "continuous_path"
    assert result.func_id == 112
    assert result.draft is not None
    assert result.draft.func_id == 112
    assert result.precheck_result["valid"] is True
    assert result.confirmation_text.startswith("【复述确认】Func112")
    assert result.query_record is None
    record = service.confirm(result.draft.draft_id)
    assert record.func_num == 112
    assert record.params["target_x"] == 1000.0


def test_parse_alarm_query_bypasses_completion():
    result = _service().parse("当前报警是什么")

    assert result.kind == "bypass"
    assert result.intent == "alarm_query"
    assert result.draft is None


def test_parse_status_query_bypasses_completion():
    result = _service().parse("为什么不能动")

    assert result.kind == "bypass"
    assert result.intent == "status_query"
    assert result.draft is None


def test_parse_emergency_bypasses_completion():
    result = _service().parse("急停")

    assert result.kind == "bypass"
    assert result.intent == "sys_estop"
    assert result.func_id == 104


def test_parse_delay_and_io_return_waiting_confirmation():
    service = _service()

    delay = service.parse("等待2秒")
    io_on = service.parse("IO1开")

    assert delay.kind == "waiting_confirmation"
    assert delay.intent == "delay_blocking"
    assert delay.func_id == 109
    assert delay.draft.params == {"delay_sec": 2.0}
    assert io_on.kind == "waiting_confirmation"
    assert io_on.intent == "io"
    assert io_on.func_id == 120
    assert io_on.draft.params == {"io_no": 1, "io_action": 1}


def test_parse_joint_jog_returns_waiting_confirmation_and_confirm_record():
    service = _service()

    result = service.parse("小正，J1转到45度30%速度")

    assert result.kind == "waiting_confirmation"
    assert result.intent == "joint_jog"
    assert result.func_id == 106
    assert result.draft.params["axis_no"] == 0
    assert result.draft.params["pos_val"] == 45.0
    assert result.draft.params["spd_pct"] == 30.0
    assert "Func106" in result.confirmation_text

    record = service.confirm(result.draft.draft_id)

    assert record.func_num == 106
    assert record.params["axis_no"] == 0
    assert record.params["pos_val"] == 45.0


def test_parse_unclear_text_returns_clarification():
    result = _service().parse("往那边去一点")

    assert result.kind == "clarification"
    assert "请补充" in result.message


def test_parse_blocks_when_parameter_completion_fails():
    service = _service(controller_snapshot=_controller_snapshot(is_moving=True))

    result = service.parse("走到 X1000")

    assert result.kind == "blocked"
    assert "当前设备运动中" in result.message
    assert result.draft is None


def test_parse_blocks_when_precheck_fails():
    service = _service()

    result = service.parse("走到 X1300")

    assert result.kind == "precheck_failed"
    assert result.draft is not None
    assert result.precheck_result["valid"] is False
    assert "L1预检未通过" in result.message
    assert "建议" in result.message
    assert "请处理失败项后再执行计划" in result.message


def test_parse_blocks_inner_cylinder_and_hemisphere_space_model_failures():
    config = AxisRangeConfig(
        x=(-2000.0, 2000.0),
        y=(-2000.0, 2000.0),
        z=(0.0, 2000.0),
        safe_r_min=200.0,
        safe_r_max=2000.0,
        safe_z_min=0.0,
        safe_z_max=2000.0,
        safe_speed_max=80.0,
        safe_acc_max=70.0,
        safe_dec_max=60.0,
    )
    service = _service_with_l1_config(config)

    cylinder = service.parse("走到 X100 Y0 Z500")
    hemisphere = service.parse("走到 X50 Y0 Z750")

    assert cylinder.kind == "precheck_failed"
    assert cylinder.precheck_result["blocking_level"] == "L1"
    assert "内径超限" in cylinder.message
    assert hemisphere.kind == "precheck_failed"
    assert hemisphere.precheck_result["blocking_level"] == "L1"
    assert "内径超限" in hemisphere.message


def test_parse_blocks_base_angle_atan2_space_model_failure():
    service = _service()

    result = service.parse("走到 X-100 Y10 Z100")

    assert result.kind == "precheck_failed"
    assert result.precheck_result["blocking_level"] == "L1"
    assert "底座角度" in result.message


def test_confirm_returns_query_record_once():
    service = _service()
    parsed = service.parse("走到 X1000 Z300")

    record = service.confirm(parsed.draft.draft_id)

    assert record.query_key == f"agent:{parsed.draft.draft_id}"
    assert record.func_num == 108
    assert record.params["target_x"] == 1000.0
    with pytest.raises(ConfirmationError, match="已结束"):
        service.confirm(parsed.draft.draft_id)


def test_reject_blocks_later_confirm():
    service = _service()
    parsed = service.parse("走到 X1000 Z300")

    service.reject(parsed.draft.draft_id)

    with pytest.raises(ConfirmationError, match="已结束"):
        service.confirm(parsed.draft.draft_id)
