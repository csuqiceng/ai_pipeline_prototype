from robot_modbus_lite.agent.registered_flow import RegisteredFlowAgent
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


def test_registered_flow_agent_delegates_flow_plan():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "打招呼", "rule", "执行打招呼", "命中流程规则"),),
        source="rule",
        raw_text="执行打招呼",
        reason="命中流程规则",
    )
    calls = []

    result = RegisteredFlowAgent(parse_func=lambda text: calls.append(text) or plan).apply("执行打招呼")

    assert result is not None
    assert result["kind"] == "registered_flow_plan"
    assert result["plan"] is plan
    assert calls == ["执行打招呼"]


def test_registered_flow_agent_ignores_non_flow_plan():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("unknown", None, "rule", "你好", "闲聊"),),
        source="rule",
        raw_text="你好",
        reason="闲聊",
    )

    assert RegisteredFlowAgent(parse_func=lambda _text: plan).apply("你好") is None
