from __future__ import annotations

from typing import Any


def build_legacy_orchestrator_cache_signature(host: Any) -> tuple[object, ...]:
    restricted_enabled = _restricted_agent_enabled(host)
    restricted_service = host._operator_restricted_agent_service() if restricted_enabled else None
    return (
        bool(restricted_enabled),
        id(restricted_service),
        bool(_llm_fallback_enabled(host)),
        id(getattr(host, "_deepseek_client", None)),
        id(getattr(host, "_atomic_memory", None)),
        id(getattr(host, "_operator_agent_flow_draft_parse", None)),
        id(getattr(host, "_operator_agent_registered_flow_parse", None)),
    )


def build_legacy_orchestrator(host: Any) -> Any:
    from robot_modbus_lite.agent.atomic_template import AtomicTemplateAgent
    from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent
    from robot_modbus_lite.agent.dashboard_query import DashboardQueryAgent
    from robot_modbus_lite.agent.flow_draft import FlowDraftAgent
    from robot_modbus_lite.agent.llm_fallback import LlmFallbackAgent
    from robot_modbus_lite.agent.memory_setting import MemorySettingAgent
    from robot_modbus_lite.agent.orchestrator import AgentOrchestrator
    from robot_modbus_lite.agent.position_memory import PositionMemoryAgent
    from robot_modbus_lite.agent.position_query import PositionQueryAgent
    from robot_modbus_lite.agent.registered_flow import RegisteredFlowAgent

    restricted_service = None
    if _restricted_agent_enabled(host):
        restricted_service = host._operator_restricted_agent_service()
    return AgentOrchestrator(
        restricted_service=restricted_service,
        chat_agent=ChatExplanationAgent(),
        position_query_agent=PositionQueryAgent(lookup=host._operator_agent_position_lookup),
        memory_setting_agent=host._operator_agent_memory_setting_agent(MemorySettingAgent),
        position_memory_agent=PositionMemoryAgent(),
        atomic_template_agent=host._operator_agent_atomic_template_agent(AtomicTemplateAgent),
        dashboard_query_agent=DashboardQueryAgent(),
        flow_draft_agent=host._operator_agent_flow_draft_agent(FlowDraftAgent),
        registered_flow_agent=host._operator_agent_registered_flow_agent(RegisteredFlowAgent),
        llm_fallback_agent=host._operator_agent_llm_fallback_agent(LlmFallbackAgent),
        llm_fallback_enabled=_llm_fallback_enabled(host),
    )


def _restricted_agent_enabled(host: Any) -> bool:
    method = getattr(host, "_operator_restricted_agent_enabled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return False
    return bool(getattr(getattr(host, "axis_ranges", None), "restricted_agent_enabled", False))


def _llm_fallback_enabled(host: Any) -> bool:
    method = getattr(host, "_operator_agent_llm_fallback_enabled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return False
    return False
