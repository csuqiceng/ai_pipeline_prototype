from robot_modbus_lite.agent.dashboard_query import DashboardQueryAgent


def test_dashboard_query_agent_matches_v21_board_query():
    result = DashboardQueryAgent().answer("通讯正常吗")

    assert result is not None
    assert result["kind"] == "dashboard_query_action"
    assert result["action_type"] == "query"
    assert result["target"] == "communication_faults"
    assert result["generates_command"] is False


def test_dashboard_query_agent_matches_safety_boundary_query():
    result = DashboardQueryAgent().answer("当前位置安全吗")

    assert result is not None
    assert result["target"] == "safety_boundary"
    assert "看板3" in result["text"]


def test_dashboard_query_agent_does_not_intercept_cartesian_motion():
    assert DashboardQueryAgent().answer("让机械手走到X1000 Y200 Z800") is None
