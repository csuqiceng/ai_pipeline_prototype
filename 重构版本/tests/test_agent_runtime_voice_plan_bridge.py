from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent_runtime.voice_plan_bridge import (
    compound_plan_adapter_payload,
    result_requires_legacy_fallback,
    voice_plan_from_agent_result,
)


def test_result_requires_legacy_fallback_detects_fallback_kind():
    assert result_requires_legacy_fallback(AgentOrchestratorResult(kind="fallback_legacy")) is True
    assert result_requires_legacy_fallback(AgentOrchestratorResult(kind="chat_answer", message="你好")) is False


def test_voice_plan_from_agent_result_returns_none_for_legacy_fallback():
    result = AgentOrchestratorResult(kind="fallback_legacy", message="交回旧路径。")

    assert voice_plan_from_agent_result(result) is None


def test_voice_plan_from_agent_result_converts_chat_answer():
    result = AgentOrchestratorResult(kind="chat_answer", message="你好，我可以解释系统状态。")

    plan = voice_plan_from_agent_result(result)

    assert plan is not None
    assert plan.actions[0].action_type == "chat"
    assert plan.reason == "你好，我可以解释系统状态。"
    assert plan.source == "agent_orchestrator"


def test_compound_plan_adapter_payload_rehydrates_tool_result_data():
    payload = {
        "tool_result": {
            "data": {
                "kind": "compound_plan_draft",
                "plan_id": "compound:test",
                "raw_text": "先回零再等待",
                "created_at": 12.5,
                "steps": ["回零", "等待"],
                "step_results": [],
                "reason": "复合草案",
            }
        }
    }

    result = compound_plan_adapter_payload(payload)

    assert result.kind == "compound_plan_draft"
    assert result.plan_id == "compound:test"
    assert result.steps == ("回零", "等待")


def test_voice_plan_from_agent_result_converts_compound_tool_payload():
    result = AgentOrchestratorResult(
        kind="compound_plan_draft",
        message="已生成复合草案。",
        payload={
            "tool_result": {
                "data": {
                    "kind": "compound_plan_draft",
                    "plan_id": "compound:test",
                    "raw_text": "先回零再等待",
                    "created_at": 12.5,
                    "steps": ["回零", "等待"],
                    "step_results": [],
                    "reason": "复合草案",
                }
            }
        },
    )

    plan = voice_plan_from_agent_result(result)

    assert plan is not None
    assert plan.actions[0].action_type == "compound_plan"
    assert plan.flow_draft["agent_kind"] == "compound_plan_draft"
    assert plan.flow_draft["plan_id"] == "compound:test"
