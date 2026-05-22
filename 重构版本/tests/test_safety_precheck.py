from robot_modbus_lite.safety_precheck import SafetyPrecheckService
from robot_modbus_lite.system_config import AxisRangeConfig


def make_config() -> AxisRangeConfig:
    return AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-200.0, 200.0),
        z=(0.0, 300.0),
        safe_r_min=0.0,
        safe_r_max=500.0,
        safe_z_min=10.0,
        safe_z_max=280.0,
        safe_speed_max=80.0,
        safe_acc_max=70.0,
        safe_dec_max=60.0,
    )


def make_snapshot() -> dict:
    return {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }


def test_l1_precheck_passes_for_safe_snapshot_and_plan():
    service = SafetyPrecheckService(make_config())

    result = service.run_l1(
        make_snapshot(),
        {
            "plan_id": "p1",
            "target": {"x": 10.0, "y": 20.0, "z": 120.0},
            "speed": {"spd_pct": 50.0, "acc_pct": 40.0, "dec_pct": 30.0},
        },
    )

    assert result["status"] == "pass"
    assert all(item["status"] == "pass" for item in result["items"])


def test_l1_precheck_fails_when_estop_is_active():
    snapshot = make_snapshot()
    snapshot["safety"]["estop"] = True
    service = SafetyPrecheckService(make_config())

    result = service.run_l1(snapshot, {"plan_id": "p1"})

    assert result["status"] == "fail"
    assert find_item(result, "estop")["status"] == "fail"


def test_l1_precheck_fails_when_target_exceeds_soft_limit():
    service = SafetyPrecheckService(make_config())

    result = service.run_l1(make_snapshot(), {"plan_id": "p1", "target": {"x": 150.0}})

    assert result["status"] == "fail"
    assert find_item(result, "target_x_range")["status"] == "fail"


def test_l1_precheck_fails_when_speed_exceeds_limit():
    service = SafetyPrecheckService(make_config())

    result = service.run_l1(make_snapshot(), {"plan_id": "p1", "speed": {"spd_pct": 90.0}})

    assert result["status"] == "fail"
    assert find_item(result, "speed_pct")["status"] == "fail"


def test_l1_precheck_fails_when_joint_target_exceeds_soft_limit():
    config = AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-200.0, 200.0),
        z=(0.0, 300.0),
        joint_limits=(
            (-180.0, 180.0),
            (-90.0, 90.0),
            (-120.0, 120.0),
            (-180.0, 180.0),
            (-120.0, 120.0),
            (-360.0, 360.0),
        ),
    )
    service = SafetyPrecheckService(config)

    result = service.run_l1(make_snapshot(), {"plan_id": "p1", "target": {"joints": (0, 100, 0, 0, 0, 0)}})

    assert result["status"] == "fail"
    item = find_item(result, "target_j2_range")
    assert item["status"] == "fail"
    assert "J2=100.0" in item["message"]


def test_l1_precheck_ignores_joint_limits_when_not_configured():
    service = SafetyPrecheckService(make_config())

    result = service.run_l1(make_snapshot(), {"plan_id": "p1", "target": {"joints": (0, 999, 0, 0, 0, 0)}})

    assert not any(item["id"].startswith("target_j") for item in result["items"])


def find_item(result: dict, item_id: str) -> dict:
    return next(item for item in result["items"] if item["id"] == item_id)
