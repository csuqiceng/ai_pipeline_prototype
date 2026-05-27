"""Configurable wake-word helpers for voice and text commands."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "voice_wake_words.json"
DEFAULT_WAKE_WORDS = ("小正", "小郑", "校正", "小镇", "小政", "小真", "小针", "小郭")


def load_wake_words(path: str | Path | None = None) -> tuple[str, ...]:
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not source.exists():
        return DEFAULT_WAKE_WORDS
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_WAKE_WORDS
    words: list[str] = []
    if isinstance(payload, dict):
        primary = str(payload.get("primary") or "").strip()
        if primary:
            words.append(primary)
        aliases = payload.get("aliases", [])
        if isinstance(aliases, list):
            words.extend(str(item).strip() for item in aliases if str(item).strip())
    elif isinstance(payload, list):
        words.extend(str(item).strip() for item in payload if str(item).strip())
    return _unique_words(words) or DEFAULT_WAKE_WORDS


def strip_wake_word(text: str, *, words: Iterable[str] | None = None) -> str | None:
    compact = str(text or "").strip()
    for wake_word in sorted(tuple(words or load_wake_words()), key=len, reverse=True):
        if compact.startswith(wake_word):
            return compact[len(wake_word):].lstrip(" ，,。:：") or ""
    return None


def strip_wake_word_from_compact(text: str, *, words: Iterable[str] | None = None) -> str:
    compact = re.sub(r"\s+", "", text or "")
    stripped = strip_wake_word(compact, words=words)
    return compact if stripped is None else stripped


def configured_wake_words() -> tuple[str, ...]:
    return load_wake_words()


def _unique_words(words: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for word in words:
        clean = str(word or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return tuple(result)
