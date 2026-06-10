from robot_modbus_lite.agent_tools.status_tools import (
    get_alarm,
    get_axis_status,
    get_execution_progress,
    query_dashboard_section,
    query_saved_position,
)


def test_query_saved_position_returns_structured_pose():
    result = query_saved_position("位置A的坐标是多少", lookup=lambda name: (1, 2, 3, 4, 5, 6))

    assert result.ok is True
    assert result.state == "position_found"
    assert result.data["position_name"] == "A"
    assert result.data["pose"] == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert "位置A坐标" in result.message


def test_query_saved_position_reports_not_found():
    result = query_saved_position("位置A的坐标是多少", lookup=lambda name: None)

    assert result.ok is False
    assert result.state == "position_not_found"
    assert result.errors[0]["code"] == "POSITION_NOT_FOUND"
    assert result.data["position_name"] == "A"


def test_query_dashboard_section_returns_matched_scope():
    result = query_dashboard_section("为什么不能执行，建议怎么处理")

    assert result.ok is True
    assert result.state == "dashboard_section_matched"
    assert result.data["target"]
    assert result.data["raw_text"] == "为什么不能执行，建议怎么处理"


def test_get_axis_status_returns_structured_axis_detail_from_snapshot():
    result = get_axis_status({"hardware": {"axis_status": [0, 1 << 3, 0, 0, 0, 0]}}, axis=2)

    assert result.ok is True
    assert result.state == "axis_status_loaded"
    assert result.data["axis"] == 2
    assert result.data["has_error"] is True
    assert result.data["axes"][0]["messages"][0]["code"] == "drive_alarm"
    assert result.data["generates_command"] is False


def test_get_execution_progress_reads_structured_snapshot_progress():
    result = get_execution_progress({"execution": {"progress": 45, "status": "running"}})

    assert result.ok is True
    assert result.state == "execution_progress_loaded"
    assert result.data["progress"] == 45
    assert result.data["status"] == "running"
    assert result.data["generates_command"] is False


def test_get_execution_progress_reports_unavailable_without_progress():
    result = get_execution_progress({"motion": {"running_state": "idle"}})

    assert result.ok is False
    assert result.state == "execution_progress_unavailable"


def test_get_alarm_returns_alarm_explanation_from_snapshot():
    result = get_alarm(
        {
            "safety": {"long34": 1 << 25, "long36": 1, "long38": 0},
            "motion": {"current_func": 108},
            "hardware": {"axis_status": []},
        }
    )

    assert result.ok is True
    assert result.state == "alarm_loaded"
    assert result.data["alarm"]["severity"] == "critical"
    assert result.data["alarm"]["can_move"] is False
    assert "急停" in result.message
    assert result.data["generates_command"] is False
