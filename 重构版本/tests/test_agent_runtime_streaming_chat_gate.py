from types import SimpleNamespace

from robot_modbus_lite.agent_runtime.streaming_chat_gate import text_requires_agent_route


class StubUnderstandingAgent:
    def __init__(self, intent):
        self.intent = intent

    def understand(self, text):
        return SimpleNamespace(intent=self.intent)


def test_text_requires_agent_route_allows_status_and_alarm_queries():
    assert text_requires_agent_route("当前状态", understanding_agent=StubUnderstandingAgent("status_query")) is False
    assert text_requires_agent_route("报警是什么", understanding_agent=StubUnderstandingAgent("alarm_query")) is False


def test_text_requires_agent_route_blocks_control_intents():
    assert text_requires_agent_route("小正，走到 X100", understanding_agent=StubUnderstandingAgent("linear_move")) is True


def test_text_requires_agent_route_blocks_flow_keywords_when_intent_unknown():
    agent = StubUnderstandingAgent("unknown")

    for text in ("创建流程", "添加步骤移动到位置A", "确认执行", "执行流程", "保存流程"):
        assert text_requires_agent_route(text, understanding_agent=agent) is True


def test_text_requires_agent_route_blocks_multiturn_business_keywords_when_intent_unknown():
    agent = StubUnderstandingAgent("unknown")

    for text in ("取消确认", "取消指令", "确认保存", "取消流程草案", "新流程叫测试", "看看流程草案"):
        assert text_requires_agent_route(text, understanding_agent=agent) is True


def test_text_requires_agent_route_blocks_contextual_flow_param_edits_when_intent_unknown():
    agent = StubUnderstandingAgent("unknown")

    for text in ("那就改成20%", "我要加速度改成20%", "把第一步速度改成30%", "这里面还是30%"):
        assert text_requires_agent_route(text, understanding_agent=agent) is True


def test_text_requires_agent_route_allows_unknown_non_business_chat():
    assert text_requires_agent_route("你好", understanding_agent=StubUnderstandingAgent("unknown")) is False


def test_text_requires_agent_route_allows_chat_when_classifier_fails():
    class FailingAgent:
        def understand(self, text):
            raise RuntimeError("classifier failed")

    assert text_requires_agent_route("你好", understanding_agent=FailingAgent()) is False
