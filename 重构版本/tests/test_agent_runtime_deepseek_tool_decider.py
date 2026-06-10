from robot_modbus_lite.agent_runtime.deepseek_tool_decider import DeepSeekToolDecider
from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent_runtime.tool_calling_agent import build_local_tool_specs


def _payload(text="你好"):
    return {
        "text": text,
        "session_state": SessionState(thread_id="session-1").to_dict(),
        "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
    }


def test_deepseek_tool_decider_uses_parse_json_decision():
    calls = []

    class Client:
        def parse_json(self, prompt, **_kwargs):
            calls.append(prompt)
            return {"tool_name": "explain_text", "args": {"text": "你好"}}

    decider = DeepSeekToolDecider(Client())

    decision = decider(_payload())

    assert decision == {"tool_name": "explain_text", "args": {"text": "你好"}}
    assert "explain_text" in calls[0]
    assert "session-1" in calls[0]
    assert "tool_call_id" in calls[0]
    assert "idempotency_key" in calls[0]
    assert "input_schema" in calls[0]
    assert "output_schema" in calls[0]
    assert '"required": ["text"]' in calls[0]
    assert '"required": ["ok", "state"]' in calls[0]


def test_deepseek_tool_decider_preserves_tool_call_id_for_side_effect_tool():
    class Client:
        def parse_json(self, prompt, **_kwargs):
            return {
                "tool_name": "create_memory_candidate",
                "tool_call_id": "call-1",
                "args": {
                    "kind": "asr_alias",
                    "key": "位置诶",
                    "value": {"normalized": "位置A"},
                },
            }

    decider = DeepSeekToolDecider(Client())

    decision = decider(_payload("记住位置诶就是位置A"))

    assert decision == {
        "tool_name": "create_memory_candidate",
        "tool_call_id": "call-1",
        "args": {
            "kind": "asr_alias",
            "key": "位置诶",
            "value": {"normalized": "位置A"},
        },
    }


def test_deepseek_tool_decider_rejects_side_effect_tool_without_tool_call_id():
    class Client:
        def parse_json(self, prompt, **_kwargs):
            return {
                "tool_name": "create_memory_candidate",
                "args": {
                    "kind": "asr_alias",
                    "key": "位置诶",
                    "value": {"normalized": "位置A"},
                },
            }

    decider = DeepSeekToolDecider(Client())

    assert decider(_payload("记住位置诶就是位置A")) is None


def test_deepseek_tool_decider_parses_fenced_generate_chat_json():
    class Client:
        def generate_chat(self, prompt, **_kwargs):
            return '```json\n{"tool_name":"query_dashboard_section","args":{"text":"当前状态"}}\n```'

    decider = DeepSeekToolDecider(Client())

    decision = decider(_payload("当前状态"))

    assert decision == {"tool_name": "query_dashboard_section", "args": {"text": "当前状态"}}


def test_deepseek_tool_decider_rejects_unknown_tool():
    class Client:
        def parse_json(self, prompt, **_kwargs):
            return {"tool_name": "write_modbus", "args": {"address": 1}}

    decider = DeepSeekToolDecider(Client())

    assert decider(_payload()) is None


def test_deepseek_tool_decider_rejects_non_object_args():
    class Client:
        def parse_json(self, prompt, **_kwargs):
            return {"tool_name": "explain_text", "args": ["bad"]}

    decider = DeepSeekToolDecider(Client())

    assert decider(_payload()) is None
