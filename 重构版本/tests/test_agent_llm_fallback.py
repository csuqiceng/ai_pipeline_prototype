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
