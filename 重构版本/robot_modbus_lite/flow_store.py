"""流程定义文件的读取、校验和写入。"""

from __future__ import annotations

import json
from pathlib import Path

from .flow_registry import FlowEntry, FlowStep
from .models import FlowDefinition


class FlowStoreError(ValueError):
    """流程文件读取或写入失败时抛出。"""
    pass


def load_flows_json(path: str | Path) -> dict[str, FlowDefinition]:
    """加载配置文件。"""
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
        flows[name] = FlowDefinition(
            name=name,
            steps=tuple(str(step).strip() for step in raw_steps if str(step).strip()),
            step_delay_ms=max(0, int(item.get("step_delay_ms", 1000))),
        )
    return flows


def save_flows_json(path: str | Path, flows: dict[str, FlowDefinition]) -> None:
    """保存配置文件。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "flows": [
            {
                "name": flow.name,
                "steps": list(flow.steps),
                "step_delay_ms": int(flow.step_delay_ms),
            }
            for flow in sorted(flows.values(), key=lambda item: item.name)
        ]
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def flow_definition_to_entry(flow: FlowDefinition) -> FlowEntry:
    steps = [
        FlowStep(
            step_id=index + 1,
            action=str(step),
            func_id=0,
            params={"query_key": str(step)},
            description=str(step),
        )
        for index, step in enumerate(flow.steps)
    ]
    return FlowEntry(name=flow.name, steps=steps, step_delay_ms=flow.step_delay_ms)


def flow_entry_to_definition(entry: FlowEntry) -> FlowDefinition:
    steps = tuple(
        str(step.params.get("query_key") or step.description or step.action)
        for step in entry.steps
        if str(step.params.get("query_key") or step.description or step.action).strip()
    )
    return FlowDefinition(name=entry.name, steps=steps, step_delay_ms=entry.step_delay_ms)
