from robot_modbus_lite.agent.position_memory import PositionMemoryAgent


def test_position_memory_agent_builds_save_action_without_mutating_state():
    result = PositionMemoryAgent().apply("小正，保存当前位置为位置A")

    assert result is not None
    assert result["kind"] == "position_memory_action"
    assert result["action_type"] == "memory"
    assert result["target"] == "position_save:A"
    assert result["generates_robot_command"] is False
    assert "保存当前位置为位置A" in result["text"]


def test_position_memory_agent_builds_delete_action_without_mutating_state():
    result = PositionMemoryAgent().apply("小正，删除位置A")

    assert result is not None
    assert result["target"] == "position_delete:A"
    assert "删除位置A" in result["text"]


def test_position_memory_agent_does_not_intercept_query_or_move():
    agent = PositionMemoryAgent()

    assert agent.apply("位置A坐标是多少") is None
    assert agent.apply("小正，移动到位置A") is None
