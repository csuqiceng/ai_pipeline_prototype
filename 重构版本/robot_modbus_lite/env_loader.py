"""Shared local .env loading helpers."""

from __future__ import annotations

import os
from pathlib import Path


def expected_env_locations() -> list[Path]:
    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent
    return [package_root / ".env", project_root / ".env"]


def load_local_env_file(*, extra_paths: list[Path] | None = None) -> None:
    paths = list(extra_paths or []) + expected_env_locations()
    for env_path in paths:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            normalized = line.strip()
            if not normalized or normalized.startswith("#") or "=" not in normalized:
                continue
            key, value = normalized.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value
