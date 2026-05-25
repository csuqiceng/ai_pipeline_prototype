from robot_modbus_lite.deepseek_client import DeepSeekClient


class FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)


def test_deepseek_client_stream_yields_content_and_ignores_reasoning(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeStreamResponse(
            [
                'data: {"choices":[{"delta":{"reasoning_content":"内部分析"}}]}',
                'data: {"choices":[{"delta":{"content":"我是"}}]}',
                'data: {"choices":[{"delta":{"content":"问答助手"}}]}',
                "data: [DONE]",
            ]
        )

    monkeypatch.setattr("robot_modbus_lite.deepseek_client.requests.post", fake_post)
    client = DeepSeekClient(api_key="test-key", model="deepseek-v4-flash")

    chunks = list(client.generate_chat_stream("你是谁", system_prompt="只输出最终答案"))

    assert chunks == ["我是", "问答助手"]
    assert calls[0][1]["json"]["stream"] is True
    assert calls[0][1]["stream"] is True
