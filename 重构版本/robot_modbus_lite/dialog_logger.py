from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class DialogLogger:
    def __init__(self, directory: str | Path, *, clock: Callable[[], datetime] | None = None):
        self.directory = Path(directory)
        self.clock = clock or datetime.now

    def _path(self) -> Path:
        now = self.clock()
        return self.directory / f"dialog_{now:%Y-%m-%d}.jsonl"

    def append(self, *, role: str, text: str, result: str, extra: dict[str, Any] | None = None) -> Path:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": self.clock().isoformat(),
            "role": role,
            "text": text,
            "result": result,
            "extra": extra or {},
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
