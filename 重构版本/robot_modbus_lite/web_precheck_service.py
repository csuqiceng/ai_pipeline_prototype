"""Safety precheck service used by the Web API."""

from __future__ import annotations

from typing import Any

from .runtime_paths import resolve_runtime_data_file
from .safety_precheck import SafetyPrecheckService
from .system_config import load_system_config


class WebPrecheckService:
    """Runs controller-independent safety checks before a plan can execute."""

    def run_l1(self, snapshot: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
        config = load_system_config(resolve_runtime_data_file("system_config.json"))
        return SafetyPrecheckService(config).run_l1(snapshot, plan)
