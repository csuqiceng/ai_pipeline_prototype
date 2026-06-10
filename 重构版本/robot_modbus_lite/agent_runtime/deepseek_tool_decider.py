from __future__ import annotations

import json
import re
from typing import Any


class DeepSeekToolDecider:
    """Use a chat model only to choose a local tool call.

    The returned decision is intentionally narrow: ``tool_name`` plus JSON
    object ``args``. Actual state changes still happen in LocalToolRegistry.
    """

    def __init__(self, client: Any, *, model: str | None = None) -> None:
        self.client = client
        self.model = model

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        prompt = _build_tool_decision_prompt(payload)
        decision = self._request_decision(prompt)
        return _validated_decision(decision, payload)

    def _request_decision(self, prompt: str) -> dict[str, Any] | None:
        try:
            if hasattr(self.client, "parse_json"):
                parsed = self.client.parse_json(prompt, model=self.model)
                return parsed if isinstance(parsed, dict) else None
            if hasattr(self.client, "generate_chat"):
                text = self.client.generate_chat(
                    prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    model=self.model,
                )
                return _parse_json_object(str(text or ""))
        except Exception:
            return None
        return None


_SYSTEM_PROMPT = (
    "你是机械手自然语言系统的工具选择器。"
    "你只能返回 JSON，不能输出解释文本。"
    "你不执行动作，只选择本地工具。"
)


def _build_tool_decision_prompt(payload: dict[str, Any]) -> str:
    text = str(payload.get("text", "") or "")
    session_state = payload.get("session_state") if isinstance(payload.get("session_state"), dict) else {}
    tool_specs = payload.get("tool_specs") if isinstance(payload.get("tool_specs"), list) else []
    tools = []
    for spec in tool_specs:
        if not isinstance(spec, dict):
            continue
        tools.append(
            {
                "name": str(spec.get("name", "") or ""),
                "group": str(spec.get("group", "") or ""),
                "description": str(spec.get("description", "") or ""),
                "side_effect": bool(spec.get("side_effect", False)),
                "input_schema": spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {},
                "output_schema": spec.get("output_schema") if isinstance(spec.get("output_schema"), dict) else {},
            }
        )
    request = {
        "user_text": text,
        "session_state": session_state,
        "available_tools": tools,
        "return_schema": {
            "tool_name": "one available tool name, or empty string when unsure",
            "tool_call_id": "stable idempotency key for side_effect tools; empty string for read-only tools",
            "idempotency_key": "optional alias of tool_call_id",
            "args": "JSON object arguments for that tool",
        },
        "rules": [
            "Only choose from available_tools.",
            "Do not invent robot facts.",
            "Do not claim anything was executed.",
            "For any tool with side_effect=true, include a stable tool_call_id derived from the user_text, tool_name, and key args.",
            "For read-only tools, tool_call_id may be empty.",
            "If unsure, return {\"tool_name\":\"\", \"args\":{}}.",
        ],
    }
    return json.dumps(request, ensure_ascii=False, sort_keys=True)


def _validated_decision(decision: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(decision, dict):
        return None
    tool_name = str(decision.get("tool_name", "") or decision.get("name", "") or "")
    if not tool_name:
        return None
    allowed = _allowed_tool_names(payload)
    if tool_name not in allowed:
        return None
    args = decision.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    tool_call_id = str(decision.get("tool_call_id", "") or decision.get("idempotency_key", "") or "")
    if _tool_has_side_effect(payload, tool_name) and not tool_call_id:
        return None
    result = {"tool_name": tool_name, "args": dict(args)}
    if tool_call_id:
        result["tool_call_id"] = tool_call_id
    return result


def _allowed_tool_names(payload: dict[str, Any]) -> set[str]:
    tool_specs = payload.get("tool_specs")
    if not isinstance(tool_specs, list):
        return set()
    names: set[str] = set()
    for spec in tool_specs:
        if isinstance(spec, dict):
            name = str(spec.get("name", "") or "")
            if name:
                names.add(name)
    return names


def _tool_has_side_effect(payload: dict[str, Any], tool_name: str) -> bool:
    tool_specs = payload.get("tool_specs")
    if not isinstance(tool_specs, list):
        return False
    for spec in tool_specs:
        if not isinstance(spec, dict):
            continue
        if str(spec.get("name", "") or "") == str(tool_name or ""):
            return bool(spec.get("side_effect", False))
    return False


def _parse_json_object(text: str) -> dict[str, Any] | None:
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        parsed = _loads_object(code_block.group(1).strip())
        if parsed is not None:
            return parsed
    depth = 0
    start = None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                parsed = _loads_object(text[start : index + 1])
                if parsed is not None:
                    return parsed
                start = None
    return None


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
