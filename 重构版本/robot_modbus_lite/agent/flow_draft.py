from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FlowDraftAgent:
    def __init__(self, *, parse_func: Callable[[str], Any]) -> None:
        self.parse_func = parse_func

    def apply(self, text: str) -> dict[str, object] | None:
        plan = self.parse_func(text)
        if str(getattr(plan, "source", "") or "") != "flow_draft":
            return None
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions:
            return None
        first_type = str(getattr(actions[0], "action_type", "") or "")
        if first_type not in {"flow_draft", "clarification", "unknown"}:
            return None
        if first_type == "unknown" and str(getattr(actions[0], "source", "") or "") != "flow_draft":
            return None
        return {
            "kind": "flow_draft_plan",
            "text": str(getattr(plan, "reason", "") or "已生成流程草案。"),
            "plan": plan,
            "generates_command": False,
        }
