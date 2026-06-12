from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator, AgentOrchestratorResult
from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter


def test_orchestrator_returns_unknown_for_plain_chat_when_chat_agent_disabled():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=None)

    result = orchestrator.handle("你好")

    assert isinstance(result, AgentOrchestratorResult)
    assert result.kind == "fallback_legacy"
    assert result.message == "交回旧 NLP 路径。"
    assert result.payload["reason"] == "chat_agent_disabled_or_no_route"
    assert result.payload["understanding"]["intent"] == "unknown"


def test_orchestrator_routes_model_candidate_control_text_to_clarification():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=None)

    result = orchestrator.handle("往安全一点的位置挪一下")

    assert result.kind == "clarification"
    assert result.message == "请补充明确的坐标、方向或参数。"
    assert result.payload["needs_model"] is True
    assert result.payload["understanding"]["intent"] == "unknown"


def test_orchestrator_llm_fallback_rewrites_then_reroutes_through_rules():
    class FakeRestrictedService:
        def parse(self, text):
            self.text = text
            return "restricted-result"

    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            self.text = text
            self.intent = understanding.intent
            return {"kind": "candidate_text", "text": "向左移动200", "confidence": 0.8}

    service = FakeRestrictedService()
    fallback = FakeLlmFallbackAgent()
    orchestrator = AgentOrchestrator(
        restricted_service=service,
        chat_agent=None,
        llm_fallback_agent=fallback,
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("小正，往左边去一点")

    assert result.kind == "restricted_agent"
    assert result.payload == "restricted-result"
    assert service.text == "向左移动200"
    assert fallback.text == "小正，往左边去一点"
    assert fallback.intent == "unknown"


def test_orchestrator_llm_fallback_candidate_can_route_to_compound_plan():
    class FakeRestrictedService:
        def parse(self, text):
            return {"kind": "waiting_confirmation", "text": text}

    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {"kind": "candidate_text", "text": "走到X1000，然后等待2秒", "confidence": 0.8}

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=None,
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("先去安全位置等一下")

    assert result.kind == "compound_plan_draft"
    assert result.payload.raw_text == "走到X1000，然后等待2秒"
    assert result.payload.steps == ("走到X1000", "等待2秒")


def test_orchestrator_llm_fallback_clarification_is_not_executed():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"should not execute llm clarification: {text}")

    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {"kind": "clarification", "text": "请说明向哪个方向移动，以及移动多少毫米。"}

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=None,
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("往安全一点的位置挪一下")

    assert result.kind == "clarification"
    assert result.message == "请说明向哪个方向移动，以及移动多少毫米。"
    assert result.payload["llm_fallback"]["kind"] == "clarification"


def test_orchestrator_llm_fallback_returns_structured_context_intent_as_non_executable_answer():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"should not execute structured llm intent: {text}")

    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {
                "kind": "flow_append_step",
                "target_flow": "测试",
                "step_hint": "移动到位置A",
                "missing_fields": ["target_pose"],
                "suggested_reply": "我理解你要给测试流程追加一步，请补充位置A坐标。",
                "confidence": 0.88,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=None,
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("我想在测试流程后面添加一个移动到位置A")

    assert result.kind == "flow_append_step"
    assert "补充位置A坐标" in result.message
    assert result.payload["llm_context_intent"]["target_flow"] == "测试"

    plan = AgentPlanAdapter().to_voice_plan(result)
    assert plan.actions[0].action_type == "clarification"
    assert plan.reason == result.message


def test_orchestrator_llm_fallback_rejects_direct_execution_payloads():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"should not execute direct llm payload: {text}")

    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {"kind": "query_record", "func_id": 108, "params": {"target_x": 1000}}

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=None,
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("往安全一点的位置挪一下")

    assert result.kind == "clarification"
    assert "请补充明确" in result.message
    assert result.payload["llm_fallback_rejected"] is True


def test_orchestrator_blocks_supported_motion_without_wake_word_before_restricted_service():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"missing wake word command should not reach restricted service: {text}")

    orchestrator = AgentOrchestrator(restricted_service=FakeRestrictedService(), chat_agent=None)

    result = orchestrator.handle("走到 X1000 Z300")

    assert result.kind == "clarification"
    assert "缺少" in result.message
    assert "唤醒词" in result.message
    assert result.payload["reason"] == "missing_wake_word"


def test_orchestrator_blocks_llm_candidate_without_original_wake_word():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"missing wake word llm candidate should not execute: {text}")

    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {"kind": "candidate_text", "text": "小正，X100", "confidence": 0.8}

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=None,
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("去X100")

    assert result.kind == "clarification"
    assert "缺少" in result.message
    assert "唤醒词" in result.message
    assert result.payload["reason"] == "missing_wake_word"


def test_orchestrator_routes_short_vertical_step_to_restricted_agent():
    class FakeRestrictedService:
        def parse(self, text):
            self.text = text
            return "restricted-result"

    service = FakeRestrictedService()
    orchestrator = AgentOrchestrator(restricted_service=service, chat_agent=None)

    result = orchestrator.handle("小正，Z上升50")

    assert result.kind == "restricted_agent"
    assert result.payload == "restricted-result"
    assert service.text == "小正，Z上升50"


def test_orchestrator_still_routes_forward_step_to_restricted_service():
    class FakeRestrictedService:
        def parse(self, text):
            self.text = text
            return "restricted-result"

    service = FakeRestrictedService()
    orchestrator = AgentOrchestrator(restricted_service=service, chat_agent=None)

    result = orchestrator.handle("小正，X前进50")

    assert result.kind == "restricted_agent"
    assert result.payload == "restricted-result"
    assert service.text == "小正，X前进50"


def test_orchestrator_does_not_route_joint_jog_to_restricted_service():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"joint jog should not be routed: {text}")

    orchestrator = AgentOrchestrator(restricted_service=FakeRestrictedService(), chat_agent=None)

    result = orchestrator.handle("小正，J1转到45度")

    assert result.kind == "fallback_legacy"
    assert result.payload["understanding"]["intent"] == "unknown"


def test_orchestrator_routes_l2_question_to_chat_agent():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())

    result = orchestrator.handle("这个L2是什么")

    assert result.kind == "chat_answer"
    assert "运动规划预演" in result.message


def test_orchestrator_routes_confirmation_explanation_to_chat_agent():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())

    result = orchestrator.handle("为什么要确认")

    assert result.kind == "chat_answer"
    assert "核对" in result.message


def test_orchestrator_prefers_enabled_llm_context_for_unknown_before_local_chat():
    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {
                "kind": "chat_answer",
                "suggested_reply": "DeepSeek结合上下文回答。",
                "confidence": 0.9,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("你是谁")

    assert result.kind == "chat_answer"
    assert result.message == "DeepSeek结合上下文回答。"


def test_orchestrator_falls_back_to_local_chat_when_early_llm_rejected():
    class FakeLlmFallbackAgent:
        def apply(self, text, understanding):
            return {"kind": "rejected", "reason": "invalid_json"}

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        llm_fallback_agent=FakeLlmFallbackAgent(),
        llm_fallback_enabled=True,
    )

    result = orchestrator.handle("你是谁")

    assert result.kind == "chat_answer"
    assert "机械手自然语言交互助手" in result.message


def test_orchestrator_routes_atomic_capability_question_to_chat_agent():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())

    result = orchestrator.handle("支持哪些原子命令")

    assert result.kind == "chat_answer"
    assert "二次原子函数能力" in result.message


def test_orchestrator_routes_position_query_to_position_agent_before_chat():
    class FakePositionQueryAgent:
        def answer(self, text):
            return {
                "kind": "position_query_answer",
                "text": "位置A坐标：X=350.0 Y=200.0 Z=500.0 RX=0.0 RY=90.0 RZ=0.0。",
                "generates_command": False,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        position_query_agent=FakePositionQueryAgent(),
    )

    result = orchestrator.handle("位置A坐标是多少")

    assert result.kind == "position_query_answer"
    assert "X=350.0" in result.message


def test_orchestrator_routes_memory_setting_before_chat():
    class FakeMemorySettingAgent:
        def apply(self, text):
            return {
                "kind": "memory_setting_answer",
                "text": "已更新原子函数记忆参数：速度=60.0%。",
                "generates_command": False,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        memory_setting_agent=FakeMemorySettingAgent(),
    )

    result = orchestrator.handle("小正，速度60%")

    assert result.kind == "memory_setting_answer"
    assert "速度=60.0%" in result.message


def test_orchestrator_routes_position_memory_before_chat():
    class FakePositionMemoryAgent:
        def apply(self, text):
            return {
                "kind": "position_memory_action",
                "action_type": "memory",
                "target": "position_save:A",
                "text": "请求保存当前位置为位置A。",
                "generates_robot_command": False,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        position_memory_agent=FakePositionMemoryAgent(),
    )

    result = orchestrator.handle("小正，保存当前位置为位置A")

    assert result.kind == "position_memory_action"
    assert result.payload["target"] == "position_save:A"


def test_orchestrator_routes_atomic_template_before_chat():
    class FakeAtomicTemplateAgent:
        def apply(self, text):
            return {
                "kind": "atomic_template_action",
                "action_type": "atomic_template",
                "target": "atomic:rest_pose",
                "text": "移动到默认休息姿态",
                "record": object(),
                "requires_confirmation": True,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        atomic_template_agent=FakeAtomicTemplateAgent(),
    )

    result = orchestrator.handle("小正，去休息")

    assert result.kind == "atomic_template_action"
    assert result.payload["target"] == "atomic:rest_pose"


def test_orchestrator_routes_dashboard_query_before_chat():
    class FakeDashboardQueryAgent:
        def answer(self, text):
            return {
                "kind": "dashboard_query_action",
                "action_type": "query",
                "target": "communication_faults",
                "text": "命中看板7 通讯+设备故障诊断。",
                "generates_command": False,
            }

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        dashboard_query_agent=FakeDashboardQueryAgent(),
    )

    result = orchestrator.handle("通讯正常吗")

    assert result.kind == "dashboard_query_action"
    assert result.payload["target"] == "communication_faults"


def test_orchestrator_routes_flow_draft_before_chat():
    class FakeFlowDraftAgent:
        def apply(self, text):
            return {"kind": "flow_draft_plan", "text": "已生成流程草案。", "plan": object()}

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        flow_draft_agent=FakeFlowDraftAgent(),
    )

    result = orchestrator.handle("小正，创建流程")

    assert result.kind == "flow_draft_plan"
    assert result.payload["plan"] is not None


def test_orchestrator_routes_registered_flow_before_chat():
    class FakeRegisteredFlowAgent:
        def apply(self, text):
            return {"kind": "registered_flow_plan", "text": "命中流程规则", "plan": object()}

    orchestrator = AgentOrchestrator(
        restricted_service=None,
        chat_agent=ChatExplanationAgent(),
        registered_flow_agent=FakeRegisteredFlowAgent(),
    )

    result = orchestrator.handle("执行打招呼")

    assert result.kind == "registered_flow_plan"
    assert result.payload["plan"] is not None


def test_orchestrator_routes_identity_question_to_chat_agent():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())

    result = orchestrator.handle("你是谁")

    assert result.kind == "chat_answer"
    assert "机械手自然语言交互助手" in result.message


def test_orchestrator_routes_actionable_compound_to_compound_plan():
    class FakeRestrictedService:
        def parse(self, text):
            return {"kind": "waiting_confirmation", "text": text}

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=ChatExplanationAgent(),
    )

    result = orchestrator.handle("走到X1000，然后等待2秒")

    assert result.kind == "compound_plan_draft"
    assert result.payload.steps == ("走到X1000", "等待2秒")
    assert result.payload.step_results == (
        {"kind": "waiting_confirmation", "text": "走到X1000"},
        {"kind": "waiting_confirmation", "text": "等待2秒"},
    )


def test_orchestrator_does_not_send_unsupported_compound_to_restricted_service():
    class FakeRestrictedService:
        def parse(self, text):
            raise AssertionError(f"should not parse unsupported compound: {text}")

    orchestrator = AgentOrchestrator(
        restricted_service=FakeRestrictedService(),
        chat_agent=ChatExplanationAgent(),
    )

    result = orchestrator.handle("小正，如果没有报警就走到X1000")

    assert result.kind == "unsupported_compound"
