from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent_runtime.local_tool_registry import LocalToolRegistry
from robot_modbus_lite.agent_runtime.local_tool_runner import LocalToolCallingRunner
from robot_modbus_lite.agent_runtime.langchain_runner import LangChainToolRunner
from robot_modbus_lite.agent_runtime.runtime_factory import create_tool_calling_runtime
from robot_modbus_lite.agent_runtime.session_state import SessionState


def test_runtime_factory_uses_local_runner_when_langchain_unavailable():
    registry = LocalToolRegistry()

    runtime = create_tool_calling_runtime(
        registry,
        langchain_available=False,
    )

    assert isinstance(runtime.runner, LocalToolCallingRunner)
    assert runtime.tool_registry is registry


def test_runtime_factory_uses_langchain_runner_factory_when_available():
    registry = LocalToolRegistry()
    calls = []

    def runner_factory(tool_registry):
        calls.append(tool_registry)

        def runner(text, session_state, tool_specs):
            return AgentOrchestratorResult(kind="chat_answer", message="langchain")

        return runner

    runtime = create_tool_calling_runtime(
        registry,
        langchain_available=True,
        langchain_runner_factory=runner_factory,
    )

    result = runtime.handle("你好", session_state=SessionState(thread_id="session-1"))

    assert calls == [registry]
    assert result.message == "langchain"


def test_runtime_factory_builds_default_langgraph_runner_when_langchain_available():
    runtime = create_tool_calling_runtime(
        LocalToolRegistry(),
        langchain_available=True,
        langchain_runner_factory=None,
    )

    assert isinstance(runtime.runner, LangChainToolRunner)
    assert runtime.runner.graph_app is not None
    result = runtime.handle("你好", session_state=SessionState(thread_id="session-1"))
    assert result.kind == "chat_answer"


def test_runtime_factory_passes_tool_decider_to_default_langgraph_runner():
    def tool_decider(_payload):
        return {"tool_name": "explain_text", "args": {"text": "你好"}}

    runtime = create_tool_calling_runtime(
        LocalToolRegistry(),
        langchain_available=True,
        tool_decider=tool_decider,
    )

    result = runtime.handle("移动到 X100", session_state=SessionState(thread_id="session-1"))

    assert isinstance(runtime.runner, LangChainToolRunner)
    assert result.kind == "chat_answer"
    assert result.payload["tool_name"] == "explain_text"


def test_runtime_factory_uses_langchain_runner_when_graph_app_provided():
    registry = LocalToolRegistry()

    class GraphApp:
        def invoke(self, payload):
            return {"kind": "chat_answer", "message": "graph"}

    runtime = create_tool_calling_runtime(
        registry,
        langchain_available=True,
        langchain_graph_app=GraphApp(),
    )

    assert isinstance(runtime.runner, LangChainToolRunner)
