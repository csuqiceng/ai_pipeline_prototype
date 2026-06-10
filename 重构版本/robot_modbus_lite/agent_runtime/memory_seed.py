from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .memory_store import AgentMemoryStore


def import_json_seed_memories(store: AgentMemoryStore, data_dir: str | Path) -> dict[str, int]:
    """Import read-only JSON normalization seeds into the SQLite memory store."""
    base = Path(data_dir)
    imported = 0
    skipped = 0
    nlp_words = base / "nlp_standard_words.json"
    if nlp_words.exists():
        result = _import_nlp_standard_words(store, nlp_words)
        imported += result["imported"]
        skipped += result["skipped"]
    return {"imported": imported, "skipped": skipped}


def _import_nlp_standard_words(store: AgentMemoryStore, path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"imported": 0, "skipped": 0}
    words = payload.get("words", []) if isinstance(payload, dict) else []
    imported = 0
    skipped = 0
    for item in words:
        if not isinstance(item, dict):
            continue
        standard = str(item.get("standard", "") or "").strip()
        if not standard:
            continue
        aliases = _seed_aliases(item)
        for alias in aliases:
            if not alias or alias == standard:
                skipped += 1
                continue
            if store.lookup_memories(kind="text_alias", key=alias):
                skipped += 1
                continue
            candidate = store.create_candidate(
                kind="text_alias",
                key=alias,
                value={"normalized": standard, "seed_file": path.name},
                source=f"json_seed:{path.name}",
                confidence=1.0,
            )
            store.approve_memory(candidate["memory_id"], reviewer="json_seed")
            imported += 1
    return {"imported": imported, "skipped": skipped}


def _seed_aliases(item: dict[str, Any]) -> tuple[str, ...]:
    aliases: list[str] = []
    for field in ("homophones", "sichuan_variants"):
        values = item.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            alias = str(value or "").strip()
            if alias and alias not in aliases:
                aliases.append(alias)
    return tuple(aliases)
