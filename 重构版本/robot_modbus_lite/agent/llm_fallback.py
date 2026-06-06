"""LLM fallback wrapper with a narrow, non-executable output schema."""

from __future__ import annotations

import json
import re
from typing import Any


class LlmFallbackAgent:
    """Ask a model to clarify or rewrite vague control text, never to execute it."""

    def __init__(self, *, client: Any) -> None:
        self.client = client

    def apply(self, text: str, understanding: Any) -> dict[str, Any]:
        try:
            response = self.client.generate_chat(
                self._build_prompt(text, understanding),
                system_prompt=self._system_prompt(),
            )
        except Exception as exc:
            return {"kind": "rejected", "reason": "model_error", "detail": str(exc)}
        payload = self._parse_json_object(str(response or ""))
        if payload is None:
            return {"kind": "rejected", "reason": "invalid_json"}
        return self._validate_payload(payload)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是机械手自然语言理解兜底模块。只允许输出 JSON。"
            "不要输出 MODBUS、QueryRecord、func_id、寄存器地址或最终执行参数。"
            "允许格式只有："
            '{"kind":"candidate_text","text":"可由本地规则重新解析的明确中文指令","confidence":0.0}'
            "或"
            '{"kind":"clarification","text":"向操作者反问的问题"}。'
            "candidate_text 只能是明确方向/坐标/延时/IO/系统动作的自然语言改写。"
        )

    @staticmethod
    def _build_prompt(text: str, understanding: Any) -> str:
        intent = str(getattr(understanding, "intent", "") or "")
        clarification = str(getattr(understanding, "clarification", "") or "")
        return (
            f"原始输入：{text}\n"
            f"本地规则初判 intent={intent}，clarification={clarification}\n"
            "如果能改写成一条明确、可由本地规则重新解析的自然语言指令，输出 candidate_text；"
            "否则输出 clarification。"
        )

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        try:
            payload = json.loads(stripped)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"func_id", "params", "query_record", "modbus", "writes", "registers"}
        if any(key in payload for key in forbidden):
            return {"kind": "rejected", "reason": "llm_output_not_allowed"}
        kind = str(payload.get("kind", "") or "")
        if kind == "candidate_text":
            text = str(payload.get("text", "") or "").strip()
            if not text:
                return {"kind": "rejected", "reason": "empty_candidate_text"}
            confidence = float(payload.get("confidence", 0.0) or 0.0)
            return {"kind": "candidate_text", "text": text, "confidence": confidence}
        if kind == "clarification":
            text = str(payload.get("text", "") or "").strip()
            if not text:
                return {"kind": "rejected", "reason": "empty_clarification"}
            return {"kind": "clarification", "text": text}
        return {"kind": "rejected", "reason": "llm_output_not_allowed"}
