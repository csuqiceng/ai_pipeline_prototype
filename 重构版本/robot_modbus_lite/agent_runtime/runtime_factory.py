from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .langchain_runner import LangChainToolRunner, build_default_langgraph_app
from .local_tool_registry import LocalToolRegistry
from .local_tool_runner import LocalToolCallingRunner
from .tool_calling_agent import ToolCallingAgentRuntime, ToolCallingRunner, _langchain_runtime_available


LangChainRunnerFactory = Callable[[LocalToolRegistry], ToolCallingRunner]


def create_tool_calling_runtime(
    tool_registry: LocalToolRegistry,
    *,
    langchain_available: bool | None = None,
    langchain_runner_factory: LangChainRunnerFactory | None = None,
    langchain_graph_app: object | None = None,
    tool_decider: Callable[[dict[str, Any]], Any] | None = None,
) -> ToolCallingAgentRuntime:
    available = _langchain_runtime_available() if langchain_available is None else bool(langchain_available)
    runner: ToolCallingRunner
    if available and langchain_runner_factory is not None:
        runner = langchain_runner_factory(tool_registry)
    elif available and langchain_graph_app is not None:
        runner = LangChainToolRunner(tool_registry, graph_app=langchain_graph_app)
    elif available:
        runner = LangChainToolRunner(tool_registry, graph_app=build_default_langgraph_app(tool_registry, tool_decider=tool_decider))
    else:
        runner = LocalToolCallingRunner(tool_registry)
    return ToolCallingAgentRuntime(
        langchain_available=available,
        runner=runner,
        tool_registry=tool_registry,
    )
