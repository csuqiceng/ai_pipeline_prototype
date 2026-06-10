from __future__ import annotations

import re
from dataclasses import dataclass

from .memory_store import AgentMemoryStore, ForbiddenMemoryCandidateError


@dataclass(frozen=True)
class FeedbackLearningResult:
    created_count: int
    skipped_count: int
    created: tuple[dict[str, object], ...] = ()


def learn_memory_candidates_from_feedback(store: AgentMemoryStore) -> FeedbackLearningResult:
    created: list[dict[str, object]] = []
    skipped = 0
    for vote in store.list_feedback_votes():
        correction = _extract_alias_correction(str(vote.get("note", "") or ""))
        if correction is None:
            skipped += 1
            continue
        alias, normalized = correction
        if store.lookup_memories(kind="asr_alias", key=alias):
            skipped += 1
            continue
        try:
            memory = store.create_candidate(
                kind="asr_alias",
                key=alias,
                value={"normalized": normalized},
                source=f"feedback:{vote.get('interaction_id', '')}",
                confidence=0.6,
            )
        except ForbiddenMemoryCandidateError:
            skipped += 1
            continue
        created.append(memory)
    return FeedbackLearningResult(
        created_count=len(created),
        skipped_count=skipped,
        created=tuple(created),
    )


def _extract_alias_correction(note: str) -> tuple[str, str] | None:
    clean = str(note or "").strip()
    if not clean:
        return None
    patterns = (
        r"把\s*(?P<alias>[^，,。；;=\s]+)\s*识别为\s*(?P<normalized>[^，,。；;\s]+)",
        r"(?P<alias>[^，,。；;=\s]+)\s*=\s*(?P<normalized>[^，,。；;\s]+)",
        r"(?P<alias>[^，,。；;\s]+)\s*应该(?:是|识别为)\s*(?P<normalized>[^，,。；;\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, clean)
        if not match:
            continue
        alias = str(match.group("alias") or "").strip()
        normalized = str(match.group("normalized") or "").strip()
        if alias and normalized and alias != normalized:
            return alias, normalized
    return None
