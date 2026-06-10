from __future__ import annotations

from typing import Any


def build_non_execution_detail(result: str) -> dict[str, Any]:
    return {
        "modbus_write": {},
        "non_execution_result": str(result or "non_execution"),
    }


def should_finalize_pending_nlp(nlp_result: dict[str, Any]) -> bool:
    if not nlp_result:
        return True
    return str(nlp_result.get("engine", "") or "") == "pending" or str(nlp_result.get("intent", "") or "") == "pending"


def build_non_execution_nlp_payload(result: str) -> dict[str, Any]:
    label = str(result or "non_execution")
    intent = "chat" if label in {"chat", "streaming_chat"} else label
    return {
        "semantic_level": 1,
        "semantic_label": "非执行结果层",
        "response_deadline_ms": 500,
        "progress_interval_ms": 0,
        "requires_precheck": False,
        "requires_confirmation": False,
        "priority": "normal",
        "intent": intent,
        "func_id": None,
        "params": {},
        "confidence": 1.0,
        "engine": label,
        "tokens": [],
        "action_type": "chat" if intent == "chat" else "non_execution",
        "target": None,
        "reason": "非执行路径归档收尾",
    }


def finalize_non_execution_nlp(
    writer: Any,
    *,
    msg_id: str,
    result: str,
    current_nlp: dict[str, Any],
) -> bool:
    if not msg_id or not should_finalize_pending_nlp(current_nlp):
        return False
    updated = writer.update_nlp_result(msg_id, build_non_execution_nlp_payload(result))
    return True if updated is None else bool(updated)
