from __future__ import annotations

from typing import Any

from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter


def result_requires_legacy_fallback(result: Any) -> bool:
    return str(getattr(result, "kind", "") or "") == "fallback_legacy"


def voice_plan_from_agent_result(result: Any):
    if result_requires_legacy_fallback(result):
        return None
    adapter = AgentPlanAdapter()
    if str(getattr(result, "kind", "") or "") == "compound_plan_draft":
        return adapter.to_voice_plan(compound_plan_adapter_payload(getattr(result, "payload", None)))
    if str(getattr(result, "kind", "") or "") in {"restricted_agent", "unsupported_compound"}:
        return adapter.to_voice_plan(getattr(result, "payload", None))
    return adapter.to_voice_plan(result)


def compound_plan_adapter_payload(payload: Any) -> Any:
    if getattr(payload, "kind", "") == "compound_plan_draft":
        return payload
    if not isinstance(payload, dict):
        return payload
    tool_result = payload.get("tool_result")
    data = tool_result.get("data") if isinstance(tool_result, dict) else None
    if not isinstance(data, dict) or str(data.get("kind", "")) != "compound_plan_draft":
        return payload
    from robot_modbus_lite.agent.compound import CompoundPlanResult

    return CompoundPlanResult(
        kind="compound_plan_draft",
        plan_id=str(data.get("plan_id", "") or ""),
        raw_text=str(data.get("raw_text", "") or ""),
        created_at=float(data.get("created_at", 0.0) or 0.0),
        steps=tuple(str(step) for step in data.get("steps", []) or []),
        step_results=tuple(data.get("step_results", []) or []),
        reason=str(data.get("reason", "") or ""),
    )
