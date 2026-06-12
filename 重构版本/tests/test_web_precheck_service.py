from robot_modbus_lite.web_precheck_service import WebPrecheckService
from robot_modbus_lite.kinematics_engine import InverseKinematicsResult


class FakeKinematicsEngine:
    def inverse(self, pose, fstatus: int):
        return InverseKinematicsResult(True, (0.0, 10.0, 20.0, 30.0, 0.0, 0.0), fstatus)


def _snapshot() -> dict:
    return {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 300.0, "z": 100.0}},
    }


def test_web_precheck_service_keeps_l1_response_shape():
    result = WebPrecheckService().run_l1(_snapshot(), {"plan_id": "web-1"})

    assert result["plan_id"] == "web-1"
    assert result["status"] in {"pass", "fail"}
    assert isinstance(result["items"], list)
    assert "suggestion" in result


def test_web_precheck_service_run_plan_includes_robot_safety_for_pose_target(tmp_path):
    service = WebPrecheckService(config_path=tmp_path / "system_config.json", kinematics_engine=FakeKinematicsEngine())

    result = service.run_plan(
        _snapshot(),
        {
            "plan_id": "web-pose",
            "func_id": 108,
            "target": {
                "x": 300.0,
                "y": 0.0,
                "z": 500.0,
                "rx": 0.0,
                "ry": 0.0,
                "rz": 0.0,
            },
            "speed": {"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
        },
    )

    assert result["status"] == "pass"
    assert result["valid"] is True
    assert result["robot_safety"]["position_ok"] is True
    assert result["robot_safety"]["ik_ok"] is True
    assert result["robot_safety"]["pose_ok"] is True
    assert result["selected_fstatus"] == 0


def test_web_precheck_service_uses_kinematics_engine_provider(tmp_path):
    calls = []

    def provider():
        calls.append("called")
        return FakeKinematicsEngine()

    service = WebPrecheckService(config_path=tmp_path / "system_config.json", kinematics_engine_provider=provider)

    result = service.run_plan(
        _snapshot(),
        {
            "plan_id": "web-provider",
            "func_id": 108,
            "target": {"x": 300.0, "y": 0.0, "z": 500.0, "rx": 0.0, "ry": 0.0, "rz": 0.0},
            "speed": {"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
        },
    )

    assert calls == ["called"]
    assert result["robot_safety"]["ik_ok"] is True
