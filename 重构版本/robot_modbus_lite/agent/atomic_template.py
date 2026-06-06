from __future__ import annotations

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_models import AtomicElements
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver
from robot_modbus_lite.models import QueryRecord


class AtomicTemplateAgent:
    def __init__(
        self,
        *,
        memory: AtomicMemory,
        parser: AtomicParser | None = None,
        resolver: AtomicResolver | None = None,
    ) -> None:
        self.memory = memory
        self.parser = parser or AtomicParser()
        self.resolver = resolver or AtomicResolver(memory)

    def apply(self, text: str) -> dict[str, object] | None:
        elements = self.parser.parse(text)
        if not self._is_supported_template(elements):
            return None

        snapshot = self._memory_snapshot()
        resolved = self.resolver.resolve(elements)
        self._restore_memory_snapshot(snapshot)
        if resolved.kind != "template":
            return None
        record = resolved.params.get("record")
        if not isinstance(record, QueryRecord):
            return None
        return {
            "kind": "atomic_template_action",
            "action_type": "atomic_template",
            "target": record.query_key,
            "text": resolved.reason,
            "raw_text": text,
            "record": record,
            "requires_confirmation": bool(resolved.requires_confirmation),
            "risk_level": resolved.risk_level,
            "generates_robot_command": True,
        }

    @staticmethod
    def _is_supported_template(elements: AtomicElements) -> bool:
        if elements.family == "position":
            return str(elements.name or "").startswith("move:")
        if elements.family == "rest_pose":
            return True
        if elements.family == "history":
            return str(elements.name or "") in {"repeat", "continue", "back"}
        return False

    def _memory_snapshot(self) -> dict[str, object]:
        return {
            "last_record": self.memory.last_record,
            "last_command_params": self.memory.last_command_params,
            "last_direction": self.memory.last_direction,
            "last_step": self.memory.last_step,
            "position_stack": list(self.memory.position_stack),
        }

    def _restore_memory_snapshot(self, snapshot: dict[str, object]) -> None:
        self.memory.last_record = snapshot["last_record"]  # type: ignore[assignment]
        self.memory.last_command_params = snapshot["last_command_params"]  # type: ignore[assignment]
        self.memory.last_direction = snapshot["last_direction"]  # type: ignore[assignment]
        self.memory.last_step = snapshot["last_step"]  # type: ignore[assignment]
        self.memory.position_stack = list(snapshot["position_stack"])  # type: ignore[arg-type]
