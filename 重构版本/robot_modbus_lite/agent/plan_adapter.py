from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from robot_modbus_lite.agent.compound import CompoundStepMachine
from robot_modbus_lite.agent.drafts import draft_to_query_record
from robot_modbus_lite.agent.service import RestrictedAgentResult
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.semantic_response_policy import SemanticResponsePolicy, policy_for_level
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


INTENT_LEVELS: dict[str, int] = {
    "alarm_query": 2,
    "status_query": 2,
    "joint_jog": 3,
    "virtual_jog": 3,
    "move_linear": 3,
    "continuous_path": 3,
    "delay_blocking": 3,
    "delay_parallel": 3,
    "io": 3,
    "sys_estop": 5,
    "sys_pause": 4,
    "sys_resume": 4,
    "alarm_reset": 4,
}


@dataclass(frozen=True)
class AgentPlanAdapter:
    def policy_for_agent_result(self, intent: str) -> SemanticResponsePolicy:
        return policy_for_level(INTENT_LEVELS.get(str(intent), 0))

    def to_voice_plan(self, result: Any) -> VoiceNlpPlan:
        if getattr(result, "kind", "") in {"chat_answer", "position_query_answer", "memory_setting_answer"}:
            reason = str(getattr(result, "message", "") or "Agent 已生成解释文本。")
            return VoiceNlpPlan(
                actions=(VoiceNlpAction("chat", None, "agent_orchestrator", "", reason),),
                source="agent_orchestrator",
                raw_text="",
                reason=reason,
                semantic_level=1,
                semantic_label="解释层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
            )

        if getattr(result, "kind", "") in {"feedback_vote_recorded", "feedback_vote_rejected"}:
            payload = getattr(result, "payload", {}) or {}
            payload = dict(payload or {}) if isinstance(payload, dict) else {}
            tool_result = payload.get("tool_result")
            tool_result = dict(tool_result or {}) if isinstance(tool_result, dict) else {}
            reason = str(getattr(result, "message", "") or tool_result.get("message") or "用户反馈已记录。")
            raw_text = str(payload.get("raw_text", "") or "")
            return VoiceNlpPlan(
                actions=(VoiceNlpAction("chat", None, "agent_orchestrator", raw_text, reason),),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="反馈记录层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft={
                    "agent_kind": str(getattr(result, "kind", "") or ""),
                    "tool_name": str(payload.get("tool_name", "") or ""),
                    "tool_state": str(tool_result.get("state", "") or ""),
                    "safe_to_execute": False,
                },
            )

        if getattr(result, "kind", "") == "clarification" and not hasattr(result, "intent"):
            payload = getattr(result, "payload", {}) or {}
            understanding = payload.get("understanding") if isinstance(payload, dict) else {}
            raw_text = ""
            if isinstance(understanding, dict):
                raw_text = str(understanding.get("raw_text", "") or "")
            reason = str(getattr(result, "message", "") or "请补充信息后再生成指令。")
            return VoiceNlpPlan(
                actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", raw_text, reason),),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="澄清提示层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
            )

        structured_llm_kinds = {
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
        if getattr(result, "kind", "") in structured_llm_kinds:
            payload = getattr(result, "payload", {}) or {}
            understanding = payload.get("understanding") if isinstance(payload, dict) else {}
            raw_text = ""
            if isinstance(understanding, dict):
                raw_text = str(understanding.get("raw_text", "") or "")
            reason = str(getattr(result, "message", "") or "已结合上下文识别到用户意图，请继续补充或确认。")
            return VoiceNlpPlan(
                actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", raw_text, reason),),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="上下文解释层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft={"llm_context_intent": payload.get("llm_context_intent", {})} if isinstance(payload, dict) else {},
            )

        if getattr(result, "kind", "") == "compound_plan_draft":
            expanded_steps = _compound_expanded_steps(result)
            safe_to_execute = bool(expanded_steps) and len(expanded_steps) == len(getattr(result, "steps", ()) or ())
            step_count = len(getattr(result, "steps", ()) or ())
            if safe_to_execute:
                reason = f"已生成可执行复合指令草案：{step_count} 步，等待确认执行。"
            else:
                reason = f"已生成复合指令草案：{step_count} 步，当前仅展示，不自动执行。"
            flow_draft = {
                "agent_kind": "compound_plan_draft",
                "plan_id": getattr(result, "plan_id", ""),
                "flow_name": _compound_flow_name(getattr(result, "plan_id", "")),
                "created_at": getattr(result, "created_at", 0.0),
                "raw_text": getattr(result, "raw_text", ""),
                "steps": getattr(result, "steps", ()),
                "step_results": getattr(result, "step_results", ()),
                "expanded_steps": expanded_steps,
                "step_machine": _compound_step_machine_payload(CompoundStepMachine.from_plan(result)),
                "safe_to_execute": safe_to_execute,
            }
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "compound_plan",
                        getattr(result, "plan_id", ""),
                        "agent_orchestrator",
                        getattr(result, "raw_text", ""),
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=str(getattr(result, "raw_text", "") or ""),
                reason=reason,
                semantic_level=3,
                semantic_label="常规生产执行层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft=flow_draft,
            )

        if getattr(result, "kind", "") == "unsupported_compound":
            reason = str(getattr(result, "reason", "") or "暂不支持该复合指令。")
            return VoiceNlpPlan(
                actions=(VoiceNlpAction("unknown", None, "agent_orchestrator", getattr(result, "raw_text", ""), reason),),
                source="agent_orchestrator",
                raw_text=str(getattr(result, "raw_text", "") or ""),
                reason=reason,
                semantic_level=0,
                semantic_label="未识别层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
            )

        if getattr(result, "kind", "") == "position_memory_action":
            payload = getattr(result, "payload", {}) or {}
            reason = str(getattr(result, "message", "") or payload.get("text") or "Agent 已生成本地记忆操作。")
            raw_text = str(payload.get("raw_text", "") or "")
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        str(payload.get("action_type", "memory") or "memory"),
                        str(payload.get("target", "") or "") or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="解释层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
            )

        if getattr(result, "kind", "") == "atomic_template_action":
            payload = getattr(result, "payload", {}) or {}
            record = payload.get("record")
            if isinstance(record, QueryRecord):
                reason = str(getattr(result, "message", "") or payload.get("text") or "Agent 已生成原子模板动作。")
                raw_text = str(payload.get("raw_text", "") or "")
                requires_confirmation = bool(payload.get("requires_confirmation", True))
                return VoiceNlpPlan(
                    actions=(
                        VoiceNlpAction(
                            "atomic_template",
                            record.query_key,
                            "agent_orchestrator",
                            raw_text,
                            reason,
                        ),
                    ),
                    source="agent_orchestrator",
                    raw_text=raw_text,
                    reason=reason,
                    semantic_level=3,
                    semantic_label="常规生产执行层",
                    response_deadline_ms=500,
                    requires_precheck=True,
                    requires_confirmation=requires_confirmation,
                    priority="normal",
                    nlp_engine="agent_orchestrator",
                    atomic_records={record.query_key: record},
                )

        if getattr(result, "kind", "") == "dashboard_query_action":
            payload = getattr(result, "payload", {}) or {}
            reason = str(getattr(result, "message", "") or payload.get("text") or "Agent 已生成看板查询动作。")
            raw_text = str(payload.get("raw_text", "") or "")
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "query",
                        str(payload.get("target", "") or "") or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=2,
                semantic_label="工艺查询层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
            )

        if getattr(result, "kind", "") == "confirm_plan":
            payload = getattr(result, "payload", {}) or {}
            draft = payload.get("draft") if isinstance(payload, dict) else {}
            draft = dict(draft or {}) if isinstance(draft, dict) else {}
            tool_result = payload.get("tool_result") if isinstance(payload, dict) else {}
            tool_data = tool_result.get("data") if isinstance(tool_result, dict) else {}
            tool_data = dict(tool_data or {}) if isinstance(tool_data, dict) else {}
            draft_id = str(draft.get("draft_id") or tool_data.get("draft_id") or "")
            intent = str(draft.get("intent", "") or "")
            policy = self.policy_for_agent_result(intent)
            reason = str(getattr(result, "message", "") or tool_data.get("confirmation_text") or "已创建待确认计划。")
            raw_text = str(draft.get("raw_text", "") or payload.get("raw_text", "") or "") if isinstance(payload, dict) else ""
            flow_draft = _tool_confirm_flow_payload(draft=draft, tool_data=tool_data, precheck=payload.get("precheck") if isinstance(payload, dict) else {})
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "agent_draft",
                        draft_id or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=policy.semantic_level,
                semantic_label=policy.semantic_label,
                response_deadline_ms=policy.result_deadline_ms,
                requires_precheck=True,
                requires_confirmation=True,
                priority=policy.priority,
                nlp_engine="agent_orchestrator",
                flow_draft=flow_draft,
            )

        if getattr(result, "kind", "") == "confirm_result":
            payload = getattr(result, "payload", {}) or {}
            payload = dict(payload or {}) if isinstance(payload, dict) else {}
            tool_result = payload.get("tool_result")
            tool_result = dict(tool_result or {}) if isinstance(tool_result, dict) else {}
            tool_data = tool_result.get("data")
            tool_data = dict(tool_data or {}) if isinstance(tool_data, dict) else {}
            draft_id = str(tool_data.get("draft_id", "") or "")
            reason = str(getattr(result, "message", "") or tool_result.get("message") or "确认已通过。")
            raw_text = str(payload.get("raw_text", "") or "")
            flow_draft = {
                "agent_kind": "confirmed",
                "draft_id": draft_id,
                "tool_name": str(payload.get("tool_name", "") or ""),
                "tool_state": str(tool_result.get("state", "") or ""),
                "query_record": dict(tool_data.get("query_record", {}) or {}),
                "safe_to_execute": False,
            }
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "agent_confirmed",
                        draft_id or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="确认结果层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft=flow_draft,
            )

        if getattr(result, "kind", "") == "confirm_cancelled":
            payload = getattr(result, "payload", {}) or {}
            payload = dict(payload or {}) if isinstance(payload, dict) else {}
            tool_result = payload.get("tool_result")
            tool_result = dict(tool_result or {}) if isinstance(tool_result, dict) else {}
            tool_data = tool_result.get("data")
            tool_data = dict(tool_data or {}) if isinstance(tool_data, dict) else {}
            draft_id = str(tool_data.get("draft_id", "") or "")
            reason = str(getattr(result, "message", "") or tool_result.get("message") or "已取消待确认计划。")
            raw_text = str(payload.get("raw_text", "") or "")
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "agent_cancelled",
                        draft_id or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="确认结果层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft={
                    "agent_kind": "cancelled",
                    "draft_id": draft_id,
                    "tool_name": str(payload.get("tool_name", "") or ""),
                    "tool_state": str(tool_result.get("state", "") or ""),
                    "safe_to_execute": False,
                },
            )

        if getattr(result, "kind", "") in {"confirm_rejected", "followup_rejected"}:
            agent_kind = str(getattr(result, "kind", "") or "confirm_rejected")
            payload = getattr(result, "payload", {}) or {}
            payload = dict(payload or {}) if isinstance(payload, dict) else {}
            tool_result = payload.get("tool_result")
            tool_result = dict(tool_result or {}) if isinstance(tool_result, dict) else {}
            reason = str(getattr(result, "message", "") or tool_result.get("message") or "当前不能确认执行。")
            raw_text = str(payload.get("raw_text", "") or "")
            return VoiceNlpPlan(
                actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", raw_text, reason),),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="澄清提示层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft={
                    "agent_kind": agent_kind,
                    "tool_name": str(payload.get("tool_name", "") or ""),
                    "tool_state": str(tool_result.get("state", "") or ""),
                    "safe_to_execute": False,
                },
            )

        if getattr(result, "kind", "") == "precheck_failed" and not hasattr(result, "intent"):
            payload = getattr(result, "payload", {}) or {}
            draft = payload.get("draft") if isinstance(payload, dict) else {}
            draft = dict(draft or {}) if isinstance(draft, dict) else {}
            precheck = payload.get("precheck") if isinstance(payload, dict) else {}
            precheck = dict(precheck or {}) if isinstance(precheck, dict) else {}
            draft_id = str(draft.get("draft_id", "") or "")
            intent = str(draft.get("intent", "") or "")
            policy = self.policy_for_agent_result(intent)
            reason = str(getattr(result, "message", "") or precheck.get("summary") or "安全预检未通过。")
            raw_text = str(draft.get("raw_text", "") or payload.get("raw_text", "") or "") if isinstance(payload, dict) else ""
            flow_draft = _tool_precheck_failed_flow_payload(draft=draft, precheck=precheck)
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "agent_blocked",
                        draft_id or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=policy.semantic_level,
                semantic_label=policy.semantic_label,
                response_deadline_ms=policy.result_deadline_ms,
                requires_precheck=False,
                requires_confirmation=False,
                priority=policy.priority,
                nlp_engine="agent_orchestrator",
                flow_draft=flow_draft,
            )

        if getattr(result, "kind", "") == "flow_draft":
            payload = getattr(result, "payload", {}) or {}
            payload = dict(payload or {}) if isinstance(payload, dict) else {}
            plan = payload.get("plan")
            if isinstance(plan, VoiceNlpPlan):
                return plan
            tool_result = payload.get("tool_result")
            tool_result = dict(tool_result or {}) if isinstance(tool_result, dict) else {}
            tool_data = tool_result.get("data")
            tool_data = dict(tool_data or {}) if isinstance(tool_data, dict) else {}
            draft = payload.get("draft")
            if not isinstance(draft, dict):
                draft = tool_data.get("draft")
            draft = dict(draft or {}) if isinstance(draft, dict) else {}
            missing_fields = tool_data.get("missing_fields")
            if not isinstance(missing_fields, list):
                missing_fields = []
            reason = str(
                getattr(result, "message", "")
                or payload.get("text")
                or tool_result.get("message")
                or "已更新流程草案。"
            )
            raw_text = str(payload.get("raw_text", "") or "")
            flow_draft = dict(draft)
            flow_draft.update(
                {
                    "agent_kind": "flow_draft",
                    "intent": str(tool_data.get("intent", "") or ""),
                    "tool_name": str(payload.get("tool_name", "") or ""),
                    "tool_state": str(tool_result.get("state", "") or ""),
                    "missing_fields": [str(field) for field in missing_fields],
                    "safe_to_execute": False,
                }
            )
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "flow_draft",
                        str(flow_draft.get("flow_name", "") or "") or None,
                        "agent_orchestrator",
                        raw_text,
                        reason,
                    ),
                ),
                source="agent_orchestrator",
                raw_text=raw_text,
                reason=reason,
                semantic_level=1,
                semantic_label="流程草案层",
                response_deadline_ms=500,
                requires_precheck=False,
                requires_confirmation=False,
                priority="normal",
                nlp_engine="agent_orchestrator",
                flow_draft=flow_draft,
            )

        if getattr(result, "kind", "") == "flow_draft_plan":
            payload = getattr(result, "payload", {}) or {}
            plan = payload.get("plan")
            if isinstance(plan, VoiceNlpPlan):
                return plan

        if getattr(result, "kind", "") == "registered_flow_plan":
            payload = getattr(result, "payload", {}) or {}
            plan = payload.get("plan")
            if isinstance(plan, VoiceNlpPlan):
                return plan

        policy = self.policy_for_agent_result(result.intent)
        raw_text = ""
        normalized_text = ""
        if result.understanding is not None:
            raw_text = result.understanding.raw_text
            normalized_text = getattr(result.understanding, "normalized_text", "") or raw_text
        reason = result.message or _default_reason(result)

        if result.kind == "waiting_confirmation" and result.draft is not None:
            flow_draft = _agent_flow_payload(result)
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "agent_draft",
                        result.draft.draft_id,
                        "restricted_agent",
                        raw_text,
                        reason,
                    ),
                ),
                source="restricted_agent",
                raw_text=raw_text,
                normalized_text=normalized_text,
                reason=reason,
                semantic_level=policy.semantic_level,
                semantic_label=policy.semantic_label,
                response_deadline_ms=policy.result_deadline_ms,
                requires_precheck=policy.requires_precheck,
                requires_confirmation=True,
                priority=policy.priority,
                nlp_engine="restricted_agent",
                flow_draft=flow_draft,
            )

        if result.kind == "precheck_failed":
            flow_draft = _agent_flow_payload(result)
            return VoiceNlpPlan(
                actions=(
                    VoiceNlpAction(
                        "agent_blocked",
                        result.draft.draft_id if result.draft is not None else None,
                        "restricted_agent",
                        raw_text,
                        reason,
                    ),
                ),
                source="restricted_agent",
                raw_text=raw_text,
                normalized_text=normalized_text,
                reason=reason,
                semantic_level=policy.semantic_level,
                semantic_label=policy.semantic_label,
                response_deadline_ms=policy.result_deadline_ms,
                requires_precheck=False,
                requires_confirmation=False,
                priority=policy.priority,
                nlp_engine="restricted_agent",
                flow_draft=flow_draft,
            )

        if result.kind == "bypass":
            action_type, target = _bypass_action(result)
            bypass_policy = self.policy_for_agent_result(result.intent)
            return VoiceNlpPlan(
                actions=(VoiceNlpAction(action_type, target, "restricted_agent", raw_text, reason),),
                source="restricted_agent",
                raw_text=raw_text,
                normalized_text=normalized_text,
                reason=reason,
                semantic_level=bypass_policy.semantic_level,
                semantic_label=bypass_policy.semantic_label,
                response_deadline_ms=bypass_policy.result_deadline_ms,
                requires_precheck=bypass_policy.requires_precheck,
                requires_confirmation=bypass_policy.requires_confirmation,
                priority=bypass_policy.priority,
                nlp_engine="restricted_agent",
            )

        return VoiceNlpPlan(
            actions=(VoiceNlpAction("unknown", None, "restricted_agent", raw_text, reason),),
            source="restricted_agent",
            raw_text=raw_text,
            normalized_text=normalized_text,
            reason=reason,
            semantic_level=0,
            semantic_label="未识别层",
            response_deadline_ms=500,
            requires_precheck=False,
            requires_confirmation=False,
            priority="normal",
            nlp_engine="restricted_agent",
        )


def _agent_flow_payload(result: RestrictedAgentResult) -> dict[str, object]:
    draft = result.draft
    payload: dict[str, object] = {
        "agent_kind": result.kind,
        "intent": result.intent,
        "func_id": result.func_id,
        "confirmation_text": result.confirmation_text,
        "precheck_result": dict(result.precheck_result or {}),
        "safe_to_execute": False,
    }
    if draft is not None:
        payload.update(
            {
                "draft_id": draft.draft_id,
                "params": dict(draft.params),
                "param_sources": dict(draft.param_sources),
                "raw_text": draft.raw_text,
                "confidence": draft.confidence,
            }
        )
    return payload


def _tool_confirm_flow_payload(*, draft: dict[str, Any], tool_data: dict[str, Any], precheck: Any) -> dict[str, object]:
    precheck_result = dict(precheck or {}) if isinstance(precheck, dict) else dict(draft.get("precheck_result", {}) or {})
    return {
        "agent_kind": "waiting_confirmation",
        "intent": str(draft.get("intent", "") or ""),
        "func_id": draft.get("func_id"),
        "draft_id": str(draft.get("draft_id") or tool_data.get("draft_id") or ""),
        "params": dict(draft.get("params", {}) or {}),
        "param_sources": dict(draft.get("param_sources", {}) or {}),
        "raw_text": str(draft.get("raw_text", "") or ""),
        "confidence": float(draft.get("confidence", 0.0) or 0.0),
        "confirmation_text": str(tool_data.get("confirmation_text", "") or ""),
        "precheck_result": precheck_result,
        "safe_to_execute": False,
    }


def _tool_precheck_failed_flow_payload(*, draft: dict[str, Any], precheck: dict[str, Any]) -> dict[str, object]:
    return {
        "agent_kind": "precheck_failed",
        "intent": str(draft.get("intent", "") or ""),
        "func_id": draft.get("func_id"),
        "draft_id": str(draft.get("draft_id", "") or ""),
        "params": dict(draft.get("params", {}) or {}),
        "param_sources": dict(draft.get("param_sources", {}) or {}),
        "raw_text": str(draft.get("raw_text", "") or ""),
        "confidence": float(draft.get("confidence", 0.0) or 0.0),
        "precheck_result": dict(precheck or {}),
        "safe_to_execute": False,
    }


def _compound_step_machine_payload(machine: CompoundStepMachine) -> dict[str, object]:
    return {
        "plan_id": machine.plan_id,
        "status": machine.status,
        "current_index": machine.current_index,
        "current_step_text": machine.current_step_text,
        "reason": machine.reason,
        "steps": tuple(
            {
                "index": step.index,
                "text": step.text,
                "status": step.status,
                "reason": step.reason,
            }
            for step in machine.steps
        ),
    }


def _compound_expanded_steps(result: Any) -> list[dict[str, object]]:
    expanded: list[dict[str, object]] = []
    step_results = tuple(getattr(result, "step_results", ()) or ())
    if not step_results:
        return []
    for index, step_result in enumerate(step_results, start=1):
        if str(getattr(step_result, "kind", "") or "") != "waiting_confirmation":
            return []
        draft = getattr(step_result, "draft", None)
        if draft is None:
            return []
        try:
            record = draft_to_query_record(draft)
        except Exception:
            return []
        expanded.append(
            {
                "step_id": index,
                "action": str(getattr(draft, "intent", "") or f"step_{index}"),
                "func_id": int(record.func_num),
                "description": str(getattr(draft, "raw_text", "") or record.description or f"复合步骤{index}"),
                "params": dict(record.params),
            }
        )
    return expanded


def _compound_flow_name(plan_id: object) -> str:
    raw = str(plan_id or "compound").replace(":", "_").replace("-", "_")
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    return f"agent_{cleaned}".strip("_")


def _bypass_action(result: RestrictedAgentResult) -> tuple[str, str | None]:
    if result.intent == "alarm_query":
        return "query", "alarm_query"
    if result.intent == "status_query":
        return "query", "status_query"
    if result.intent.startswith("sys_") or result.intent == "alarm_reset":
        return "system", result.intent
    return "unknown", None


def _default_reason(result: RestrictedAgentResult) -> str:
    if result.kind == "waiting_confirmation":
        return "Agent 已生成待确认草稿。"
    if result.kind == "precheck_failed":
        return "Agent 安全预检未通过。"
    if result.kind == "bypass":
        return "Agent 规则旁路。"
    return "Agent 未生成可执行草稿。"
