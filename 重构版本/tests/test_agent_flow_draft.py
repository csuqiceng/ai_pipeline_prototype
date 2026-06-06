from robot_modbus_lite.agent.flow_draft import FlowDraftAgent
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


def test_flow_draft_agent_delegates_flow_draft_plan():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "打招呼", "flow_draft", "小正，创建流程", "已生成流程草案。"),),
        source="flow_draft",
        raw_text="小正，创建流程",
        reason="已生成流程草案。",
        flow_draft={"flow_name": "打招呼", "expanded_steps": [{"step_id": 1}]},
    )
    calls = []

    result = FlowDraftAgent(parse_func=lambda text: calls.append(text) or plan).apply("小正，创建流程")

    assert result is not None
    assert result["kind"] == "flow_draft_plan"
    assert result["plan"] is plan
    assert calls == ["小正，创建流程"]


def test_flow_draft_agent_delegates_flow_clarification_plan():
    plan = VoiceNlpPlan(
        actions=(
            VoiceNlpAction(
                "clarification",
                "gesture_mapping:点头",
                "flow_draft",
                "小正，创建流程",
                "需要补充动作映射：点头",
            ),
        ),
        source="flow_draft",
        raw_text="小正，创建流程",
        reason="需要补充动作映射：点头",
        flow_draft={"missing": ["点头"]},
    )

    result = FlowDraftAgent(parse_func=lambda _text: plan).apply("小正，创建流程")

    assert result is not None
    assert result["plan"] is plan


def test_flow_draft_agent_ignores_non_flow_plan():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("unknown", None, "rule", "你好", "闲聊"),),
        source="rule",
        raw_text="你好",
        reason="闲聊",
    )

    assert FlowDraftAgent(parse_func=lambda _text: plan).apply("你好") is None
