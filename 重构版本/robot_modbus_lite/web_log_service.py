"""Small JSONL log service for the local Web API."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .runtime_paths import runtime_dir


class WebLogService:
    """Thread-safe JSONL logging independent from Qt widgets."""

    def __init__(self, log_dir: Path | None = None) -> None:
        self._lock = Lock()
        self._log_dir = log_dir or runtime_dir() / "data" / "exported_logs"
        self._session_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        self._path = self._log_dir / f"session_{self._session_id}.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def append(self, category: str, action: str, result: str, detail: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "session_id": self._session_id,
            "category": category,
            "action": action,
            "result": result,
            "detail": detail,
        }
        if extra:
            entry.update(extra)

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        with self._lock:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append(payload)
        return entries
