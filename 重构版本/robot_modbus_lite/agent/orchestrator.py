from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent
from robot_modbus_lite.agent.compound import CompoundCommandCoordinator


@dataclass(frozen=True)
class AgentOrchestratorResult:
    kind: str
    message: str = ""
    payload: Any = None


class AgentOrchestrator:
    def __init__(
        self,
        *,
        restricted_service: Any,
        chat_agent: Any = None,
        understanding_agent: CommandUnderstandingAgent | None = None,
        compound_coordinator: CompoundCommandCoordinator | None = None,
        position_query_agent: Any = None,
        memory_setting_agent: Any = None,
        position_memory_agent: Any = None,
        atomic_template_agent: Any = None,
        dashboard_query_agent: Any = None,
        flow_draft_agent: Any = None,
        registered_flow_agent: Any = None,
        llm_fallback_agent: Any = None,
        llm_fallback_enabled: bool = False,
    ) -> None:
        self.restricted_service = restricted_service
        self.chat_agent = chat_agent
        self.position_query_agent = position_query_agent
        self.memory_setting_agent = memory_setting_agent
        self.position_memory_agent = position_memory_agent
        self.atomic_template_agent = atomic_template_agent
        self.dashboard_query_agent = dashboard_query_agent
        self.flow_draft_agent = flow_draft_agent
        self.registered_flow_agent = registered_flow_agent
        self.llm_fallback_agent = llm_fallback_agent
        self.llm_fallback_enabled = bool(llm_fallback_enabled)
        self.understanding_agent = understanding_agent or CommandUnderstandingAgent()
        self.compound_coordinator = compound_coordinator or CompoundCommandCoordinator(
            restricted_service=restricted_service,
            understanding_agent=self.understanding_agent,
        )

    def handle(self, text: str) -> AgentOrchestratorResult:
        understanding = self.understanding_agent.understand(text)
        intent = str(getattr(understanding, "intent", "") or "")
        compound_plan = self.compound_coordinator.plan(text)
        if compound_plan.kind == "compound_plan_draft":
            return AgentOrchestratorResult(
                kind="compound_plan_draft",
                message="已生成复合指令草案，等待确认。",
                payload=compound_plan,
            )
        if compound_plan.kind == "unsupported_compound":
            return AgentOrchestratorResult(
                kind="unsupported_compound",
                message=compound_plan.reason or "暂不支持该复合指令。",
                payload=compound_plan,
            )
        if self.memory_setting_agent is not None:
            memory_answer = self.memory_setting_agent.apply(text)
            if memory_answer is not None:
                return AgentOrchestratorResult(
                    kind=str(memory_answer.get("kind", "memory_setting_answer")),
                    message=str(memory_answer.get("text", "")),
                    payload=memory_answer,
                )
        if self.position_memory_agent is not None:
            position_memory_answer = self.position_memory_agent.apply(text)
            if position_memory_answer is not None:
                return AgentOrchestratorResult(
                    kind=str(position_memory_answer.get("kind", "position_memory_action")),
                    message=str(position_memory_answer.get("text", "")),
                    payload=position_memory_answer,
                )
        if self.atomic_template_agent is not None:
            atomic_template_answer = self.atomic_template_agent.apply(text)
            if atomic_template_answer is not None:
                return AgentOrchestratorResult(
                    kind=str(atomic_template_answer.get("kind", "atomic_template_action")),
                    message=str(atomic_template_answer.get("text", "")),
                    payload=atomic_template_answer,
                )
        if self.dashboard_query_agent is not None:
            dashboard_query_answer = self.dashboard_query_agent.answer(text)
            if dashboard_query_answer is not None:
                return AgentOrchestratorResult(
                    kind=str(dashboard_query_answer.get("kind", "dashboard_query_action")),
                    message=str(dashboard_query_answer.get("text", "")),
                    payload=dashboard_query_answer,
                )
        if self.flow_draft_agent is not None:
            flow_draft_answer = self.flow_draft_agent.apply(text)
            if flow_draft_answer is not None:
                return AgentOrchestratorResult(
                    kind=str(flow_draft_answer.get("kind", "flow_draft_plan")),
                    message=str(flow_draft_answer.get("text", "")),
                    payload=flow_draft_answer,
                )
        if self.registered_flow_agent is not None:
            registered_flow_answer = self.registered_flow_agent.apply(text)
            if registered_flow_answer is not None:
                return AgentOrchestratorResult(
                    kind=str(registered_flow_answer.get("kind", "registered_flow_plan")),
                    message=str(registered_flow_answer.get("text", "")),
                    payload=registered_flow_answer,
                )
        if bool(getattr(understanding, "needs_model", False)):
            llm_result = self._try_llm_fallback(text, understanding)
            if llm_result is not None:
                return llm_result
            message = str(getattr(understanding, "clarification", "") or "请补充明确的指令参数。")
            return AgentOrchestratorResult(
                kind="clarification",
                message=message,
                payload={
                    "needs_model": True,
                    "understanding": self._serialize_understanding(understanding, text),
                },
            )
        if intent == "unknown" and self.chat_agent is not None:
            llm_result = self._try_llm_fallback(text, understanding)
            if llm_result is not None and not self._is_llm_rejected_result(llm_result):
                return llm_result
            if self.position_query_agent is not None:
                position_answer = self.position_query_agent.answer(text)
                if position_answer is not None:
                    return AgentOrchestratorResult(
                        kind=str(position_answer.get("kind", "position_query_answer")),
                        message=str(position_answer.get("text", "")),
                        payload=position_answer,
                    )
            chat_answer = self.chat_agent.answer(text)
            if chat_answer is not None:
                return AgentOrchestratorResult(
                    kind="chat_answer",
                    message=str(chat_answer.get("text", "")),
                    payload=chat_answer,
                )
        if self._should_keep_legacy_atomic_step(text, understanding):
            return AgentOrchestratorResult(
                kind="fallback_legacy",
                message="交回旧 NLP 路径。",
                payload={
                    "reason": "legacy_vertical_atomic_step",
                    "needs_model": False,
                    "understanding": self._serialize_understanding(understanding, text),
                },
            )
        if self._should_route_to_restricted_agent(understanding) and self.restricted_service is not None:
            return AgentOrchestratorResult(
                kind="restricted_agent",
                payload=self.restricted_service.parse(text),
            )
        return AgentOrchestratorResult(
            kind="fallback_legacy",
            message="交回旧 NLP 路径。",
            payload={
                "reason": "chat_agent_disabled_or_no_route",
                "needs_model": bool(getattr(understanding, "needs_model", False)),
                "understanding": self._serialize_understanding(understanding, text),
            },
        )

    @staticmethod
    def _is_llm_rejected_result(result: AgentOrchestratorResult) -> bool:
        payload = getattr(result, "payload", None)
        return isinstance(payload, dict) and bool(payload.get("llm_fallback_rejected", False))

    def _try_llm_fallback(self, text: str, understanding: Any) -> AgentOrchestratorResult | None:
        if not self.llm_fallback_enabled or self.llm_fallback_agent is None:
            return None
        raw_payload = self.llm_fallback_agent.apply(text, understanding)
        if not isinstance(raw_payload, dict):
            return None
        kind = str(raw_payload.get("kind", "") or "")
        if kind == "clarification":
            message = str(raw_payload.get("text", "") or raw_payload.get("message", "") or "")
            if not message:
                return None
            return AgentOrchestratorResult(
                kind="clarification",
                message=message,
                payload={
                    "needs_model": True,
                    "understanding": self._serialize_understanding(understanding, text),
                    "llm_fallback": dict(raw_payload),
                },
            )
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
            message = str(
                raw_payload.get("suggested_reply")
                or raw_payload.get("text")
                or raw_payload.get("message")
                or getattr(understanding, "clarification", "")
                or "已结合上下文识别到用户意图，请继续补充或确认。"
            )
            return AgentOrchestratorResult(
                kind=kind,
                message=message,
                payload={
                    "needs_model": True,
                    "understanding": self._serialize_understanding(understanding, text),
                    "llm_context_intent": dict(raw_payload),
                    "generates_command": False,
                },
            )
        if kind != "candidate_text":
            return self._llm_fallback_rejected(text, understanding, raw_payload)
        candidate_text = str(raw_payload.get("text", "") or "").strip()
        if not candidate_text:
            return self._llm_fallback_rejected(text, understanding, raw_payload)
        compound_plan = self.compound_coordinator.plan(candidate_text)
        if compound_plan.kind == "compound_plan_draft":
            return AgentOrchestratorResult(
                kind="compound_plan_draft",
                message="LLM 兜底候选已通过复合指令规则复核，生成复合计划草案。",
                payload=compound_plan,
            )
        if compound_plan.kind == "unsupported_compound":
            return self._llm_fallback_rejected(text, understanding, raw_payload)
        candidate = self.understanding_agent.understand(candidate_text)
        if bool(getattr(candidate, "needs_model", False)) or not self._should_route_to_restricted_agent(candidate):
            return self._llm_fallback_rejected(text, understanding, raw_payload)
        if self.restricted_service is None:
            return None
        return AgentOrchestratorResult(
            kind="restricted_agent",
            message="LLM 兜底候选已通过规则复核，进入受限 Agent 链路。",
            payload=self.restricted_service.parse(candidate_text),
        )

    def _llm_fallback_rejected(
        self,
        text: str,
        understanding: Any,
        raw_payload: dict[str, Any],
    ) -> AgentOrchestratorResult:
        message = str(getattr(understanding, "clarification", "") or "请补充明确的指令参数。")
        return AgentOrchestratorResult(
            kind="clarification",
            message=message,
            payload={
                "needs_model": True,
                "understanding": self._serialize_understanding(understanding, text),
                "llm_fallback_rejected": True,
                "llm_fallback": dict(raw_payload),
            },
        )

    @staticmethod
    def _should_route_to_restricted_agent(understanding: Any) -> bool:
        intent = str(getattr(understanding, "intent", "") or "")
        return intent in {
            "alarm_query",
            "status_query",
            "joint_jog",
            "virtual_jog",
            "move_linear",
            "continuous_path",
            "delay_blocking",
            "delay_parallel",
            "io",
            "sys_estop",
            "sys_pause",
            "sys_resume",
            "sys_cancel",
            "alarm_reset",
        }

    @staticmethod
    def _should_keep_legacy_atomic_step(text: str, understanding: Any) -> bool:
        if str(getattr(understanding, "intent", "") or "") != "move_linear":
            return False
        params = dict(getattr(understanding, "extracted_params", {}) or {})
        if set(params) - {"delta_z", "position_increment"}:
            return False
        if "delta_z" not in params:
            return False
        compact = re.sub(r"\s+", "", str(getattr(understanding, "normalized_text", "") or text or ""))
        return bool(re.search(r"Z(?:上升|升高|下降|降低)-?\d", compact, flags=re.IGNORECASE))

    @staticmethod
    def _serialize_understanding(understanding: Any, text: str) -> dict[str, Any]:
        return {
            "raw_text": str(getattr(understanding, "raw_text", "") or text or ""),
            "intent": str(getattr(understanding, "intent", "") or ""),
            "func_id": getattr(understanding, "func_id", None),
            "confidence": float(getattr(understanding, "confidence", 0.0) or 0.0),
            "clarification": str(getattr(understanding, "clarification", "") or ""),
            "bypass_completion": bool(getattr(understanding, "bypass_completion", False)),
        }
