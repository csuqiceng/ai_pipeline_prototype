from __future__ import annotations

from collections.abc import Callable
from typing import Any


class RegisteredFlowAgent:
    def __init__(self, *, parse_func: Callable[[str], Any]) -> None:
        self.parse_func = parse_func

    def apply(self, text: str) -> dict[str, object] | None:
        plan = self.parse_func(text)
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions:
            return None
        if not all(str(getattr(action, "action_type", "") or "") == "flow" for action in actions):
            return None
        return {
            "kind": "registered_flow_plan",
            "text": str(getattr(plan, "reason", "") or "命中已登记流程。"),
            "plan": plan,
            "generates_command": True,
        }
