from __future__ import annotations

import json
from pathlib import Path

from .models import FlowDefinition


class FlowStoreError(ValueError):
    pass


def load_flows_json(path: str | Path) -> dict[str, FlowDefinition]:
    file_path = Path(path)
    if not file_path.exists():
        return {}

    payload = json.loads(file_path.read_text(encoding="utf-8"))
    records = payload.get("flows", [])
    if not isinstance(records, list):
        raise FlowStoreError("流程 JSON 缺少 flows 列表。")

    flows: dict[str, FlowDefinition] = {}
    for item in records:
        if not isinstance(item, dict):
            raise FlowStoreError(f"非法流程记录: {item!r}")
        name = str(item.get("name", "")).strip()
        raw_steps = item.get("steps", [])
        if not name:
            raise FlowStoreError("流程记录缺少 name。")
        if not isinstance(raw_steps, list):
            raise FlowStoreError(f"流程 steps 非法: {item!r}")
        flows[name] = FlowDefinition(name=name, steps=tuple(str(step).strip() for step in raw_steps if str(step).strip()))
    return flows


def save_flows_json(path: str | Path, flows: dict[str, FlowDefinition]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "flows": [
            {
                "name": flow.name,
                "steps": list(flow.steps),
            }
            for flow in sorted(flows.values(), key=lambda item: item.name)
        ]
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
