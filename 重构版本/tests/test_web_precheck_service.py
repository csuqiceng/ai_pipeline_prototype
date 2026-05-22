from robot_modbus_lite.web_precheck_service import WebPrecheckService


def test_web_precheck_service_keeps_l1_response_shape():
    result = WebPrecheckService().run_l1(
        {
            "safety": {"estop": False, "alarm_active": False, "paused": False},
            "connection": {"controller": "online", "realtime_feedback": "online"},
            "motion": {"running_state": "idle", "active_plan_id": None},
            "position": {"cartesian": {"r": 0.0, "z": 0.0}},
        },
        {"plan_id": "web-1"},
    )

    assert result["plan_id"] == "web-1"
    assert result["status"] in {"pass", "fail"}
    assert isinstance(result["items"], list)
    assert "suggestion" in result
