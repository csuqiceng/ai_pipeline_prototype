from __future__ import annotations

from dataclasses import dataclass


class PermissionDenied(PermissionError):
    """Raised when a backend operation is not allowed for the active actor."""


_ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "operator": frozenset(
        {
            "position.read",
            "position.create",
            "position.update",
            "flow.read",
            "flow.create",
            "flow.update",
            "flow.execute",
            "alarm.ack",
            "dashboard.read",
        }
    ),
    "engineer": frozenset(
        {
            "position.read",
            "position.create",
            "position.update",
            "position.delete",
            "flow.read",
            "flow.create",
            "flow.update",
            "flow.delete",
            "flow.confirm",
            "flow.rehearsal",
            "flow.execute",
            "alarm.ack",
            "alarm.reset",
            "dashboard.read",
            "system_config.update",
        }
    ),
    "system": frozenset(
        {
            "position.read",
            "position.create",
            "position.update",
            "position.delete",
            "flow.read",
            "flow.create",
            "flow.update",
            "flow.delete",
            "flow.confirm",
            "flow.rehearsal",
            "flow.execute",
            "alarm.ack",
            "alarm.reset",
            "dashboard.read",
            "system_config.update",
        }
    ),
}


@dataclass(frozen=True)
class PermissionService:
    actor: str = "operator"

    def normalized_actor(self) -> str:
        actor = (self.actor or "operator").strip().lower()
        return actor if actor in _ROLE_ACTIONS else "operator"

    def allowed(self, action: str) -> bool:
        action_key = str(action or "").strip()
        return action_key in _ROLE_ACTIONS[self.normalized_actor()]

    def require(self, action: str) -> None:
        action_key = str(action or "").strip()
        if not self.allowed(action_key):
            raise PermissionDenied(
                f"{self.normalized_actor()} is not allowed to perform {action_key}"
            )
