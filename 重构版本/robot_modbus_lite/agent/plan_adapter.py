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
        if result.understanding is not None:
            raw_text = result.understanding.raw_text
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
