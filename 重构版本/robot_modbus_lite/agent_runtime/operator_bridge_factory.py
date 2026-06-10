from __future__ import annotations

from pathlib import Path
from typing import Any

from .deepseek_tool_decider import DeepSeekToolDecider
from .operator_bridge import OperatorAgentRuntimeBridge


def build_operator_bridge_cache_signature(host: Any, runtime_root: Any) -> tuple[object, ...]:
    return (
        str(runtime_root or ""),
        bool(_operator_agent_llm_fallback_enabled(host)),
        id(getattr(host, "_deepseek_client", None)),
        id(getattr(host, "__dict__", {}).get("_operator_agent_memory_store")),
        id(getattr(host, "__dict__", {}).get("_operator_agent_flow_draft_parse")),
    )


def build_operator_tool_decider(host: Any) -> DeepSeekToolDecider | None:
    if not _operator_agent_llm_fallback_enabled(host):
        return None
    client = getattr(host, "_deepseek_client", None)
    if client is None:
        return None
    return DeepSeekToolDecider(client)


def build_operator_runtime_bridge(host: Any, *, runtime_root: str | Path | None = None) -> OperatorAgentRuntimeBridge:
    log_func = getattr(host, "_append_log", None) if hasattr(host, "_append_log") else None
    memory_store = None
    memory_store_override = getattr(host, "__dict__", {}).get("_operator_agent_memory_store")
    if callable(memory_store_override):
        memory_store = memory_store_override()
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=runtime_root,
        log_func=log_func,
        memory_store=memory_store,
        atomic_memory_provider=lambda: _operator_atomic_memory(host),
        restricted_service_provider=host._operator_restricted_agent_service,
        flow_service_provider=lambda: getattr(host, "service", None),
        execution_plan_service_provider=host._operator_execution_plan_service,
        controller_snapshot_provider=host._operator_controller_snapshot_provider,
        position_registry_provider=(host._position_registry if hasattr(host, "_position_registry") else None),
        safety_review_agent_provider=host._operator_safety_review_agent,
        runtime_snapshot_provider=lambda: host._operator_dashboard_snapshot_dict(refresh=True),
        start_pose_provider=host._operator_current_pose_tuple,
        confirmation_agent_provider=host._operator_confirmation_agent,
        clock=host._operator_now_seconds,
        status_signature_provider=host._operator_restricted_agent_status_signature,
        safety_signature_provider=host._operator_restricted_agent_safety_signature,
        flow_draft_parse_func=host._operator_flow_draft_parse_func(),
        control_tools_enabled_provider=getattr(host, "_operator_restricted_agent_enabled", lambda: True),
        tool_decider=build_operator_tool_decider(host),
    )
    runtime_override = getattr(host, "__dict__", {}).get("_operator_tool_calling_agent_runtime")
    if callable(runtime_override):
        bridge._tool_calling_runtime = runtime_override()
    return bridge


def _operator_agent_llm_fallback_enabled(host: Any) -> bool:
    method = getattr(host, "_operator_agent_llm_fallback_enabled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            return False
    check = getattr(host, "nlp_use_deepseek_check", None)
    if check is None or not hasattr(check, "isChecked"):
        return False
    try:
        return bool(check.isChecked()) and getattr(host, "_deepseek_client", None) is not None
    except Exception:
        return False


def _operator_atomic_memory(host: Any) -> Any:
    memory = getattr(host, "_atomic_memory", None)
    if memory is None:
        return None
    position_registry = getattr(host, "_position_registry", None)
    if callable(position_registry):
        try:
            memory.position_registry = position_registry()
        except Exception:
            pass
    return memory
