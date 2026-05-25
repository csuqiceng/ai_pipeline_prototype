from types import SimpleNamespace

from robot_modbus_lite.dashboard import DashboardCache


def test_dashboard_snapshot_reads_operator_realtime_fields():
    source = SimpleNamespace(
        robot_x="100.0",
        robot_y="20.0",
        robot_z="300.0",
        robot_r="1.0 / 2.0 / 3.0",
        robot_joints=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        robot_speed="30%",
        alarm_code="ERR_000",
        alarm_text="系统正常",
        estop_active=False,
        pause_active=False,
        busy="空闲",
        run_state="空闲",
        current_func_text="空闲",
        motion_percent="25%",
        result="0",
        io_status="0",
        servo_enable="1",
        claw_enable="1",
        claw_brake="0",
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )
    cache = DashboardCache()

    snapshot = cache.update_from_source(source)

    assert snapshot.position["x"] == "100.0"
    assert snapshot.position["joints"] == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert snapshot.safety["alarm_active"] is False
    assert snapshot.connection["realtime_feedback"] == "online"
    assert snapshot.motion["motion_percent"] == "25%"
    assert snapshot.hardware["servo_enable"] == "1"
    assert snapshot.hardware["claw_enable"] == "1"


def test_dashboard_detects_alarm_active_from_alarm_code():
    source = SimpleNamespace(
        robot_x="-",
        robot_y="-",
        robot_z="-",
        robot_r="-",
        robot_joints=(),
        robot_speed="-",
        alarm_code="ERR_123",
        alarm_text="驱动器报警",
        estop_active=True,
        pause_active=True,
        busy="忙",
        run_state="执行中",
        current_func_text="FUNC108",
        motion_percent="-",
        result="9",
        io_status="128",
        servo_enable="0",
        claw_enable="0",
        claw_brake="1",
        monitor_label=SimpleNamespace(text=lambda: "离线"),
    )
    cache = DashboardCache()

    snapshot = cache.update_from_source(source)

    assert snapshot.safety["alarm_active"] is True
    assert snapshot.safety["estop"] is True
    assert snapshot.connection["realtime_feedback"] == "offline"
    assert snapshot.safety["alarm_detection"]["active"] is True
    assert "E_STOP" in snapshot.safety["alarm_detection"]["codes"]


def test_dashboard_exposes_long38_alarm_detection():
    source = SimpleNamespace(
        robot_x="-",
        robot_y="-",
        robot_z="-",
        robot_r="-",
        robot_joints=(),
        robot_speed="-",
        alarm_code="ERR_8",
        alarm_text="速度超限",
        six_long38=8,
        estop_active=False,
        pause_active=False,
        busy="空闲",
        run_state="空闲",
        current_func_text="-",
        motion_percent="-",
        result="9",
        io_status="0",
        servo_enable="1",
        claw_enable="1",
        claw_brake="0",
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )

    snapshot = DashboardCache().update_from_source(source)

    detection = snapshot.boards["device_status"]["alarm_detection"]
    assert detection["codes"] == ["OVER_SPEED"]
    assert detection["severity"] == "critical"


def test_dashboard_snapshot_exposes_v21_seven_boards_with_50ms_refresh():
    source = SimpleNamespace(
        robot_x="100.0",
        robot_y="20.0",
        robot_z="300.0",
        robot_r="102.5",
        robot_joints=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        robot_speed="30%",
        alarm_code="0",
        alarm_text="系统正常",
        estop_active=False,
        pause_active=False,
        busy="空闲",
        run_state="空闲",
        current_func_text="空闲",
        motion_percent="25%",
        result="0",
        io_status="0",
        servo_enable="1",
        claw_enable="1",
        claw_brake="0",
        flow_status="待机",
        flow_current_step="-",
        current_flow_name="",
        _operator_last_precheck_result={"status": "pass", "items": []},
        _operator_last_motion_plan_result={"status": "pass", "selected_fstatus": 3, "items": []},
        _operator_last_process_precheck_result={"status": "pass", "items": []},
        axis_ranges=SimpleNamespace(
            x=(-500.0, 500.0),
            y=(-500.0, 500.0),
            z=(0.0, 700.0),
            safe_r_min=50.0,
            safe_r_max=700.0,
            safe_z_min=10.0,
            safe_z_max=650.0,
            safe_speed_max=120.0,
            safe_acc_max=80.0,
            safe_dec_max=80.0,
            joint_limits=(
                (-180.0, 180.0),
                (-90.0, 90.0),
                (-120.0, 120.0),
                (-180.0, 180.0),
                (-120.0, 120.0),
                (-360.0, 360.0),
            ),
        ),
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )
    cache = DashboardCache()

    snapshot = cache.update_from_source(source)
    data = snapshot.to_dict()

    assert data["refresh_ms"] == 50
    assert set(data["boards"]) == {
        "device_status",
        "action_feasibility",
        "safety_boundary",
        "motion_limits",
        "process_preview",
        "process_adaptation",
        "communication_faults",
    }
    assert data["boards"]["device_status"]["alarm"] is False
    assert data["boards"]["action_feasibility"]["precheck_status"] == "pass"
    assert data["boards"]["safety_boundary"]["safe_r_range"] == (50.0, 700.0)
    assert data["boards"]["safety_boundary"]["joint_limits"][1] == (-90.0, 90.0)
    assert data["boards"]["motion_limits"]["safe_speed_max"] == 120.0
    assert data["boards"]["process_preview"]["l3_status"] == "pass"
    assert data["boards"]["process_preview"]["progress_percent"] == 100
    assert data["boards"]["process_adaptation"]["l2_status"] == "pass"
    assert data["boards"]["process_adaptation"]["fstatus"] == 3
    assert data["boards"]["communication_faults"]["ecat_ok"] is True


def test_dashboard_cache_exposes_l2_midpoint_and_rejected_fstatus_details():
    source = SimpleNamespace(
        robot_x="-",
        robot_y="-",
        robot_z="-",
        robot_r="-",
        robot_joints=(),
        robot_speed="-",
        alarm_code="0",
        alarm_text="系统正常",
        estop_active=False,
        pause_active=False,
        busy="空闲",
        run_state="空闲",
        current_func_text="空闲",
        motion_percent="-",
        result="0",
        io_status="0",
        servo_enable="-",
        claw_enable="-",
        claw_brake="-",
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
        _operator_last_motion_plan_result={
            "status": "fail",
            "selected_fstatus": None,
            "rejected_fstatuses": (0, 1),
            "need_midpoint": True,
            "midpoint_pose": (10, 20, 30, 0, 5, 0),
            "midpoint_fstatus": 3,
            "singularity": True,
            "suggestion": "建议经中点绕行。",
        },
    )
    cache = DashboardCache()

    adaptation = cache.update_from_source(source).boards["process_adaptation"]

    assert adaptation["need_midpoint"] is True
    assert adaptation["midpoint_pose"] == (10, 20, 30, 0, 5, 0)
    assert adaptation["midpoint_fstatus"] == 3
    assert adaptation["rejected_fstatuses"] == (0, 1)


def test_dashboard_snapshot_exposes_dpos_mpos_axis_status_and_motion_type():
    source = SimpleNamespace(
        robot_x="100.0",
        robot_y="20.0",
        robot_z="300.0",
        robot_r="1.0 / 2.0 / 3.0",
        robot_joints=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        robot_mpos_joints=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        robot_mpos_pose=(100.0, 20.0, 300.0, 1.0, 2.0, 3.0),
        robot_dpos_joints=(1.1, 2.1, 3.1, 4.1, 5.1, 6.1),
        robot_dpos_pose=(101.0, 21.0, 301.0, 1.1, 2.1, 3.1),
        axis_status=(10, 11, 12, 13, 14, 15),
        motion_type=(20, 21, 22, 23, 24, 25),
        robot_speed="30%",
        alarm_code="0",
        alarm_text="系统正常",
        estop_active=False,
        pause_active=False,
        busy="空闲",
        run_state="空闲",
        current_func_text="空闲",
        motion_percent="25%",
        result="0",
        io_status="0",
        servo_enable="1",
        claw_enable="1",
        claw_brake="0",
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )
    cache = DashboardCache()

    data = cache.update_from_source(source).to_dict()

    assert data["position"]["mpos_j"] == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert data["position"]["dpos_j"] == (1.1, 2.1, 3.1, 4.1, 5.1, 6.1)
    assert data["position"]["mpos_c"] == (100.0, 20.0, 300.0, 1.0, 2.0, 3.0)
    assert data["position"]["dpos_c"] == (101.0, 21.0, 301.0, 1.1, 2.1, 3.1)
    assert data["motion"]["axis_status"] == (10, 11, 12, 13, 14, 15)
    assert data["motion"]["motion_type"] == (20, 21, 22, 23, 24, 25)
    assert data["boards"]["device_status"]["mpos_j"] == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert data["boards"]["device_status"]["dpos_j"] == (1.1, 2.1, 3.1, 4.1, 5.1, 6.1)
    assert data["boards"]["motion_limits"]["axis_status"] == (10, 11, 12, 13, 14, 15)
    assert data["boards"]["motion_limits"]["motion_type"] == (20, 21, 22, 23, 24, 25)


def test_dashboard_snapshot_marks_realtime_feedback_stale_by_age():
    now = [1000.0]
    source = SimpleNamespace(
        robot_x="100.0",
        robot_y="20.0",
        robot_z="300.0",
        robot_r="102.5",
        robot_joints=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        robot_speed="30%",
        alarm_code="0",
        alarm_text="系统正常",
        estop_active=False,
        pause_active=False,
        busy="空闲",
        run_state="空闲",
        current_func_text="空闲",
        motion_percent="25%",
        result="0",
        io_status="0",
        servo_enable="1",
        claw_enable="1",
        claw_brake="0",
        _last_feedback_monotonic_sec=999.9,
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )
    cache = DashboardCache(clock=lambda: now[0], stale_after_ms=200)

    fresh = cache.update_from_source(source)
    now[0] = 1000.25
    stale = cache.update_from_source(source)

    assert fresh.connection["feedback_age_ms"] == 100
    assert fresh.connection["feedback_fresh"] is True
    assert fresh.boards["communication_faults"]["feedback_age_ms"] == 100
    assert fresh.boards["communication_faults"]["feedback_fresh"] is True
    assert stale.connection["feedback_age_ms"] == 350
    assert stale.connection["feedback_fresh"] is False
    assert stale.boards["communication_faults"]["feedback_fresh"] is False
