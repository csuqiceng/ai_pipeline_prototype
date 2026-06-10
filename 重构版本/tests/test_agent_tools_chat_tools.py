from robot_modbus_lite.agent_tools.chat_tools import explain_text


def test_explain_text_wraps_chat_explanation_agent():
    result = explain_text("你能做什么")

    assert result.ok is True
    assert result.state == "chat_explained"
    assert result.data["kind"] == "chat_answer"
    assert "不会触发机械手动作" in result.message


def test_explain_text_answers_greeting_without_fallback():
    result = explain_text("你好")

    assert result.ok is True
    assert result.state == "chat_explained"
    assert result.data["kind"] == "chat_answer"
    assert "机械手" in result.message
    assert "不会触发机械手动作" in result.message


def test_explain_text_rejects_control_text_for_business_tool():
    result = explain_text("移动到 X100")

    assert result.ok is False
    assert result.state == "requires_business_tool"
    assert result.errors[0]["code"] == "REQUIRES_BUSINESS_TOOL"
