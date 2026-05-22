"""Convert existing VoiceNlpPlan objects to V2.1 type-A command intents."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from .json_schema import CommandIntent
from .models import QueryRecord


SYSTEM_FUNC_ID = {
    "sys_estop": 104,
    "sys_pause": 104,
    "sys_resume": 104,
    "sys_cancel": 104,
    "alarm_reset": 104,
}


def command_intent_from_plan(
    plan: Any,
    *,
    table: dict[str, QueryRecord],
    msg_id: str | None = None,
    timestamp: str | None = None,
    source: str = "text",
) -> CommandIntent:
    actions = tuple(getattr(plan, "actions", ()) or ())
    first = actions[0] if actions else None
    action_type = str(getattr(first, "action_type", "unknown") or "unknown") if first else "unknown"
    target = getattr(first, "target", None) if first else None
    atomic_records = getattr(plan, "atomic_records", {}) or {}
    record = atomic_records.get(str(target)) if action_type == "atomic_template" and target else None
    if record is None:
        record = table.get(str(target)) if target else None

    func_id: int | None = None
    params: dict[str, Any] = {}
    semantic_level = int(getattr(plan, "semantic_level", 0) or 0)
    intent = "unknown"
    confidence = 0.0
    is_emergency = False
    priority = "normal"

    if action_type in {"template", "atomic_template"} and record is not None:
        func_id = int(record.func_num)
        params = dict(record.params)
        semantic_level = semantic_level or 3
        intent = "command"
        confidence = 0.8
    elif action_type == "flow":
        params = {"flow_name": str(target or "")}
        semantic_level = semantic_level or 3
        intent = "command"
        confidence = 0.8
    elif action_type == "system":
        action_key = str(target or "")
        func_id = SYSTEM_FUNC_ID.get(action_key)
        params = {"action_key": action_key}
        semantic_level = semantic_level or (5 if action_key == "sys_estop" else 4)
        intent = "command"
        confidence = 0.9
        is_emergency = action_key == "sys_estop"
        priority = "high" if is_emergency else "normal"
    elif action_type == "query":
        params = {"board_key": str(target or "")}
        semantic_level = semantic_level or 2
        intent = "query"
        confidence = 0.75
    elif semantic_level == 1:
        intent = "chat"
        confidence = 0.5

    return CommandIntent(
        msg_id=msg_id or f"intent-{uuid.uuid4().hex}",
        timestamp=timestamp or datetime.now().isoformat(timespec="milliseconds"),
        source=source,
        raw_text=str(getattr(plan, "raw_text", "") or ""),
        semantic_level=semantic_level,
        intent=intent,
        func_id=func_id,
        confidence=confidence,
        params=params,
        fuzzy={
            "pos": int(float(params.get("fuzzy_pos", 0) or 0)),
            "spd": int(float(params.get("fuzzy_spd", 0) or 0)),
            "acc": int(float(params.get("fuzzy_acc", 0) or 0)),
            "dec": int(float(params.get("fuzzy_dec", 0) or 0)),
        },
        emergency_code=None,
        is_emergency=is_emergency,
        priority=priority,
    )
