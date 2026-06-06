from robot_modbus_lite.agent.position_query import PositionQueryAgent


def test_position_query_answers_existing_position_without_command():
    agent = PositionQueryAgent(
        lookup=lambda name: {
            "A": (350.0, 200.0, 500.0, 0.0, 90.0, 0.0),
        }.get(name)
    )

    result = agent.answer("位置A坐标是多少")

    assert result is not None
    assert result["kind"] == "position_query_answer"
    assert result["position_name"] == "A"
    assert "X=350.0" in result["text"]
    assert result["generates_command"] is False


def test_position_query_reports_missing_position_without_command():
    agent = PositionQueryAgent(lookup=lambda name: None)

    result = agent.answer("位置A坐标是多少")

    assert result is not None
    assert result["kind"] == "position_query_answer"
    assert "位置A不存在" in result["text"]
    assert result["generates_command"] is False


def test_position_query_does_not_intercept_move_or_save_position():
    agent = PositionQueryAgent(lookup=lambda name: (1, 2, 3, 4, 5, 6))

    assert agent.answer("移动到位置A") is None
    assert agent.answer("保存当前位置为位置A") is None
