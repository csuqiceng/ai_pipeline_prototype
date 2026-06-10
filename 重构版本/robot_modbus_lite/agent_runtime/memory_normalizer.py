from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory_store import AgentMemoryStore


@dataclass(frozen=True)
class MemoryNormalizationResult:
    text: str
    applied: tuple[dict[str, Any], ...] = ()


def apply_active_memory_to_text(store: AgentMemoryStore, text: str) -> MemoryNormalizationResult:
    current = str(text or "")
    applied: list[dict[str, Any]] = []
    memories = sorted(
        store.lookup_active(kind="asr_alias") + store.lookup_active(kind="text_alias"),
        key=lambda item: len(str(item.get("key", ""))),
        reverse=True,
    )
    for memory in memories:
        key = str(memory.get("key", "") or "")
        replacement = _replacement_text(memory.get("value"))
        if not key or replacement is None or key not in current:
            continue
        if any(key in str(item.get("replacement", "")) for item in applied):
            continue
        before = current
        current = current.replace(key, replacement)
        if current == before:
            continue
        applied_item = {
            "memory_id": str(memory.get("memory_id", "")),
            "kind": str(memory.get("kind", "")),
            "key": key,
            "replacement": replacement,
        }
        store.record_memory_applied(
            applied_item["memory_id"],
            context={"raw_text": str(text or ""), "normalized_text": current},
        )
        applied.append(applied_item)
    return MemoryNormalizationResult(text=current, applied=tuple(applied))


def _replacement_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("normalized", "replacement", "text"):
        replacement = value.get(key)
        if replacement is not None:
            return str(replacement)
    return None
