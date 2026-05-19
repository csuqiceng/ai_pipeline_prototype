"""NLP service wrapper for the Web API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .flow_store import load_flows_json
from .query_table import load_query_table_json
from .runtime_paths import resolve_runtime_data_file
from .voice_nlp_adapter import VoiceNlpAdapter, VoiceNlpPlan


class WebNlpService:
    """Loads existing template and flow data, then exposes Web-friendly parse results."""

    def __init__(self) -> None:
        self._adapter = self._build_adapter()

    def parse(self, text: str, *, use_deepseek: bool = False) -> dict[str, Any]:
        plan = self._adapter.parse(text, use_deepseek=use_deepseek)
        return self._to_dict(plan)

    def _build_adapter(self) -> VoiceNlpAdapter:
        table = load_query_table_json(resolve_runtime_data_file("query_table.json"))
        flows = load_flows_json(resolve_runtime_data_file("flows.json"))
        return VoiceNlpAdapter(table, flows.keys())

    def _to_dict(self, plan: VoiceNlpPlan) -> dict[str, Any]:
        return {
            "source": plan.source,
            "raw_text": plan.raw_text,
            "reason": plan.reason,
            "actions": [asdict(action) for action in plan.actions],
        }
