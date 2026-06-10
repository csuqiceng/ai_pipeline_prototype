from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_models import AtomicElements
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver
from robot_modbus_lite.models import QueryRecord


TemplateLookup = Callable[[AtomicElements], QueryRecord | None]


class AtomicTemplateAgent:
    def __init__(
        self,
        *,
        memory: AtomicMemory,
        parser: AtomicParser | None = None,
        resolver: AtomicResolver | None = None,
        template_lookup: TemplateLookup | None = None,
    ) -> None:
        self.memory = memory
        self.parser = parser or AtomicParser()
        self.resolver = resolver or AtomicResolver(memory)
        self.template_lookup = template_lookup

    def apply(self, text: str) -> dict[str, object] | None:
        elements = self.parser.parse(text)
        if not self._is_supported_template(elements):
            return None

        snapshot = self._memory_snapshot()
        resolved = self.resolver.resolve(elements)
        self._restore_memory_snapshot(snapshot)
        if resolved.kind != "template":
            table_record = self._lookup_template_record(elements)
            if table_record is None:
                return None
            return self._record_payload(
                text=text,
                record=table_record,
                reason=str(getattr(table_record, "description", "") or f"命中模板：{table_record.query_key}"),
                requires_confirmation=True,
                risk_level="high",
            )
        record = resolved.params.get("record")
        if not isinstance(record, QueryRecord):
            return None
        return self._record_payload(
            text=text,
            record=record,
            reason=resolved.reason,
            requires_confirmation=bool(resolved.requires_confirmation),
            risk_level=resolved.risk_level,
        )

    def _lookup_template_record(self, elements: AtomicElements) -> QueryRecord | None:
        if self.template_lookup is None:
            return None
        try:
            record = self.template_lookup(elements)
        except Exception:
            return None
        return record if isinstance(record, QueryRecord) else None

    @staticmethod
    def _record_payload(
        *,
        text: str,
        record: QueryRecord,
        reason: str,
        requires_confirmation: bool,
        risk_level: Any,
    ) -> dict[str, object]:
        return {
            "kind": "atomic_template_action",
            "action_type": "atomic_template",
            "target": record.query_key,
            "text": reason,
            "raw_text": text,
            "record": record,
            "requires_confirmation": bool(requires_confirmation),
            "risk_level": risk_level,
            "generates_robot_command": True,
        }

    @staticmethod
    def query_table_position_template_lookup(table: Any) -> TemplateLookup:
        def lookup(elements: AtomicElements) -> QueryRecord | None:
            if elements.family != "position":
                return None
            name = str(elements.name or "")
            if not name.lower().startswith("move:"):
                return None
            position_name = name.split(":", 1)[1].strip()
            if not position_name:
                return None
            return AtomicTemplateAgent._find_query_table_position_record(table, position_name)

        return lookup

    @staticmethod
    def _find_query_table_position_record(table: Any, position_name: str) -> QueryRecord | None:
        if not isinstance(table, dict):
            return None
        normalized_position = AtomicTemplateAgent._normalize_position_token(position_name)
        raw_position = position_name.strip()
        candidates = {f"位置{normalized_position}", f"位置{raw_position}"}
        if len(normalized_position) > 1:
            candidates.add(normalized_position)
        if len(raw_position) > 1:
            candidates.add(raw_position)
        normalized_candidates = {AtomicTemplateAgent._normalize_template_text(item) for item in candidates if item}
        for key, record in table.items():
            if not isinstance(record, QueryRecord):
                continue
            values = [
                str(key or ""),
                str(getattr(record, "query_key", "") or ""),
                str(getattr(record, "description", "") or ""),
                str(getattr(record, "keywords", "") or ""),
            ]
            for value in values:
                normalized = AtomicTemplateAgent._normalize_template_text(value)
                if any(candidate and candidate in normalized for candidate in normalized_candidates):
                    return record
        return None

    @staticmethod
    def _normalize_position_token(value: str) -> str:
        token = re.sub(r"\s+", "", str(value or ""))
        return token.upper() if re.fullmatch(r"[A-Za-z0-9]+", token) else token

    @staticmethod
    def _normalize_template_text(value: str) -> str:
        text = re.sub(r"\s+", "", str(value or ""))
        return text.upper()

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
