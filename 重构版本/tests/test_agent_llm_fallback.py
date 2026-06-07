from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent
from robot_modbus_lite.agent.llm_fallback import LlmFallbackAgent


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts = []

    def generate_chat(self, prompt, system_prompt=None):
        self.prompts.append((prompt, system_prompt))
        return self.response


def _understanding():
    return CommandUnderstandingAgent().understand("往左边去一点")


def test_llm_fallback_agent_accepts_candidate_text_only():
    client = FakeClient('{"kind":"candidate_text","text":"向左移动200","confidence":0.76}')
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("往左边去一点", _understanding())

    assert result == {"kind": "candidate_text", "text": "向左移动200", "confidence": 0.76}
    assert "不要输出 MODBUS" in client.prompts[0][1]


def test_llm_fallback_agent_accepts_clarification():
    client = FakeClient('{"kind":"clarification","text":"请说明移动方向和距离。"}')
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("往安全一点的位置挪一下", _understanding())

    assert result == {"kind": "clarification", "text": "请说明移动方向和距离。"}


def test_llm_fallback_agent_rejects_direct_params_payload():
    client = FakeClient('{"kind":"candidate","func_id":108,"params":{"target_x":1000}}')
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("往安全一点的位置挪一下", _understanding())

    assert result["kind"] == "rejected"
    assert result["reason"] == "llm_output_not_allowed"


def test_llm_fallback_agent_rejects_non_json_text():
    client = FakeClient("我觉得可以向左移动 200 毫米")
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("往左边去一点", _understanding())

    assert result["kind"] == "rejected"
    assert result["reason"] == "invalid_json"


def test_llm_fallback_agent_includes_runtime_context_in_prompt():
    client = FakeClient('{"kind":"clarification","text":"请补充位置A的坐标。"}')
    agent = LlmFallbackAgent(client=client, context_provider=lambda: "当前模式：flow_editing\n待编辑流程：测试")

    result = agent.apply("X100", _understanding())

    assert result["kind"] == "clarification"
    assert "当前模式：flow_editing" in client.prompts[0][0]


def test_llm_fallback_agent_accepts_structured_context_intent_without_execution_fields():
    client = FakeClient(
        '{"kind":"flow_append_step","target_flow":"测试","step_hint":"移动到位置A","missing_fields":["target_pose"],'
        '"suggested_reply":"我理解你要给测试流程追加一步，请补充位置A坐标。","confidence":0.88}'
    )
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("我想在测试流程后面添加一个移动到位置A", _understanding())

    assert result["kind"] == "flow_append_step"
    assert result["target_flow"] == "测试"
    assert result["missing_fields"] == ["target_pose"]
    assert "追加一步" in result["suggested_reply"]


def test_llm_fallback_agent_preserves_speech_reply_for_structured_intent():
    client = FakeClient(
        '{"kind":"chat_answer","suggested_reply":"完整回答会显示在界面。","speech_reply":"已显示完整回答。","confidence":0.9}'
    )
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("解释一下流程", _understanding())

    assert result["kind"] == "chat_answer"
    assert result["suggested_reply"] == "完整回答会显示在界面。"
    assert result["speech_reply"] == "已显示完整回答。"
    assert "speech_reply" in client.prompts[0][1]


def test_llm_fallback_agent_rejects_structured_intent_with_direct_params():
    client = FakeClient('{"kind":"confirm_modify","params":{"acc_pct":50},"suggested_reply":"已修改"}')
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("加速度改成50%", _understanding())

    assert result["kind"] == "rejected"
    assert result["reason"] == "llm_output_not_allowed"


def test_llm_fallback_agent_preserves_structured_context_fields_needed_by_policy_gate():
    client = FakeClient(
        '{"kind":"command_candidate","candidate_text":"小正，X100","query_text":"现在状态",'
        '"flow_name":"测试","step_index":2,"field":"acc_pct","value_text":"50%","confidence":0.91}'
    )
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("小正，去X100", _understanding())

    assert result["kind"] == "command_candidate"
    assert result["candidate_text"] == "小正，X100"
    assert result["query_text"] == "现在状态"
    assert result["flow_name"] == "测试"
    assert result["step_index"] == 2
    assert result["field"] == "acc_pct"
    assert result["value_text"] == "50%"


def test_llm_fallback_agent_rejects_nested_direct_execution_payload():
    client = FakeClient(
        '{"kind":"flow_modify_step","step_index":2,"changes":{"params":{"acc_pct":50}},'
        '"suggested_reply":"已修改","confidence":0.9}'
    )
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("把第二步加速度改成50%", _understanding())

    assert result["kind"] == "rejected"
    assert result["reason"] == "llm_output_not_allowed"


def test_llm_fallback_agent_rejects_explicit_low_confidence_payload():
    client = FakeClient('{"kind":"flow_query","target_flow":"测试","confidence":0.2}')
    agent = LlmFallbackAgent(client=client)

    result = agent.apply("看看那个流程", _understanding())

    assert result["kind"] == "rejected"
    assert result["reason"] == "low_confidence"
