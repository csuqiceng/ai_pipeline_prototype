from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent


def test_chat_explains_l2_without_generating_command():
    result = ChatExplanationAgent().answer("L2是什么")

    assert result is not None
    assert result["kind"] == "chat_answer"
    assert "运动规划预演" in result["text"]
    assert result["generates_command"] is False


def test_chat_does_not_intercept_control_text_with_confirmation_keyword():
    result = ChatExplanationAgent().answer("确认执行走到X1000")

    assert result is None


def test_chat_explains_why_confirmation_is_required():
    result = ChatExplanationAgent().answer("为什么要确认")

    assert result is not None
    assert result["kind"] == "chat_answer"
    assert "核对" in result["text"]
    assert result["generates_command"] is False


def test_chat_explains_prompt_failure_meaning():
    result = ChatExplanationAgent().answer("现在这个提示是不是失败")

    assert result is not None
    assert result["kind"] == "chat_answer"
    assert "不会触发机械手动作" in result["text"]
    assert result["generates_command"] is False


def test_chat_explains_atomic_capabilities_without_generating_command():
    result = ChatExplanationAgent().answer("支持哪些原子命令")

    assert result is not None
    assert result["kind"] == "chat_answer"
    assert "二次原子函数能力" in result["text"]
    assert "J 类关节命令" in result["text"]
    assert "不会触发机械手动作" in result["text"]
    assert result["generates_command"] is False


def test_chat_explains_identity_and_usage_without_generating_command():
    identity = ChatExplanationAgent().answer("你是谁")
    usage = ChatExplanationAgent().answer("怎么使用")

    assert identity is not None
    assert identity["kind"] == "chat_answer"
    assert "机械手自然语言交互助手" in identity["text"]
    assert identity["generates_command"] is False
    assert usage is not None
    assert usage["kind"] == "chat_answer"
    assert "确认" in usage["text"]
    assert usage["generates_command"] is False
