"""LLM fallback wrapper with a narrow, non-executable output schema."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


class LlmFallbackAgent:
    """Ask a model to clarify or rewrite vague control text, never to execute it."""

    def __init__(self, *, client: Any, context_provider: Callable[[], str] | None = None) -> None:
        self.client = client
        self.context_provider = context_provider

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
            "你是机械手自然语言上下文解释模块。只允许输出 JSON。"
            "不要输出 MODBUS、QueryRecord、func_id、寄存器地址或最终执行参数。"
            "允许格式包括："
            '{"kind":"candidate_text","text":"可由本地规则重新解析的明确中文指令","confidence":0.0}'
            "、"
            '{"kind":"clarification","text":"向操作者反问的问题"}'
            "、"
            '{"kind":"chat_answer","suggested_reply":"界面完整回答","speech_reply":"30字以内语音摘要","confidence":0.0}'
            "、"
            '{"kind":"flow_create|flow_append_step|flow_modify_step|flow_list|flow_query|confirm_modify|dashboard_query|command_candidate|suggestion",'
            '"flow_name":"流程名","target_flow":"流程名","step_index":1,"step_hint":"步骤描述",'
            '"field":"参数名","value_text":"参数值","query_text":"状态问题","candidate_text":"候选自然语言指令",'
            '"missing_fields":["字段"],"suggested_reply":"给操作者看的完整建议","speech_reply":"30字以内语音摘要","confidence":0.0}。'
            "candidate_text 只能是明确方向/坐标/延时/IO/系统动作的自然语言改写。"
            "结构化意图只能表达用户意图、缺失信息和建议，不允许携带可直接执行的参数。"
            "speech_reply 只能是短自然语言摘要，不得包含长参数表或完整流程步骤。"
        )

    def _build_prompt(self, text: str, understanding: Any) -> str:
        intent = str(getattr(understanding, "intent", "") or "")
        clarification = str(getattr(understanding, "clarification", "") or "")
        context = ""
        if callable(self.context_provider):
            try:
                context = str(self.context_provider() or "").strip()
            except Exception:
                context = ""
        context_block = f"运行时上下文：\n{context}\n" if context else ""
        return (
            context_block
            + f"原始输入：{text}\n"
            f"本地规则初判 intent={intent}，clarification={clarification}\n"
            "先结合上下文判断用户真实意图。"
            "如果能改写成一条明确、可由本地规则重新解析的自然语言指令，输出 candidate_text；"
            "如果是在创建/编辑流程、查询流程列表、修改待确认计划、查询状态或普通咨询，输出对应结构化 kind；"
            "注意 flow_list 表示列出所有流程或统计流程数量，flow_query 表示查看某个具体流程详情；"
            "如果缺信息，输出 clarification。"
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
        if LlmFallbackAgent._contains_forbidden_key(payload, forbidden):
            return {"kind": "rejected", "reason": "llm_output_not_allowed"}
        kind = str(payload.get("kind", "") or "")
        if LlmFallbackAgent._has_explicit_low_confidence(payload):
            return {"kind": "rejected", "reason": "low_confidence"}
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
        structured_kinds = {
            "chat_answer",
            "flow_create",
            "flow_append_step",
            "flow_modify_step",
            "flow_list",
            "flow_query",
            "confirm_modify",
            "dashboard_query",
            "command_candidate",
            "suggestion",
        }
        if kind in structured_kinds:
            result: dict[str, Any] = {"kind": kind}
            for key in (
                "target_flow",
                "flow_name",
                "step_hint",
                "target",
                "operation",
                "field",
                "value_text",
                "query_text",
                "candidate_text",
                "text",
            ):
                value = str(payload.get(key, "") or "").strip()
                if value:
                    result[key] = value
            step_index = LlmFallbackAgent._optional_int(payload.get("step_index", payload.get("step_no", payload.get("step"))))
            if step_index is not None:
                result["step_index"] = step_index
            missing = payload.get("missing_fields", [])
            if isinstance(missing, list):
                result["missing_fields"] = [str(item) for item in missing if str(item or "").strip()]
            reply = str(payload.get("suggested_reply", "") or payload.get("text", "") or "").strip()
            if reply:
                result["suggested_reply"] = reply
                result["text"] = reply
            speech_reply = str(payload.get("speech_reply", "") or "").strip()
            if speech_reply:
                result["speech_reply"] = speech_reply
            try:
                result["confidence"] = float(payload.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                result["confidence"] = 0.0
            if kind in {"chat_answer", "suggestion"} and not reply:
                return {"kind": "rejected", "reason": "empty_structured_reply"}
            return result
        return {"kind": "rejected", "reason": "llm_output_not_allowed"}

    @staticmethod
    def _contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    return True
                if LlmFallbackAgent._contains_forbidden_key(item, forbidden):
                    return True
        elif isinstance(value, list):
            return any(LlmFallbackAgent._contains_forbidden_key(item, forbidden) for item in value)
        return False

    @staticmethod
    def _has_explicit_low_confidence(payload: dict[str, Any], *, threshold: float = 0.5) -> bool:
        if "confidence" not in payload:
            return False
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            return True
        return confidence < threshold

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
