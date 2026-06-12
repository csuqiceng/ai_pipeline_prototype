from robot_modbus_lite.agent_runtime.local_tool_registry import LocalToolRegistry
from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore
from robot_modbus_lite.agent_runtime.local_tool_runner import LocalToolCallingRunner
from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent_runtime.tool_calling_agent import build_local_tool_specs
from robot_modbus_lite.agent.confirmation import ConfirmationAgent
from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
from robot_modbus_lite.execution_plan_service import ExecutionPlanService
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.service import RobotModbusService


def _snapshot():
    return ControllerSnapshot(
        current_pose={
            "target_x": 0.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
        },
        safety_params={"spd_pct": 30.0, "acc_pct": 20.0, "dec_pct": 20.0},
    )


def test_local_tool_runner_answers_chat_through_tool_registry():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("你是谁", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "chat_answer"
    assert "机械手自然语言交互助手" in result.message
    assert result.payload["tool_name"] == "explain_text"
    assert result.payload["tool_result"]["state"] == "chat_explained"


def test_local_tool_runner_answers_capability_question_without_legacy_fallback():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("那你到底能做什么", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "chat_answer"
    assert result.payload["tool_name"] == "explain_text"
    assert result.payload["generates_command"] is False
    assert "机械手" in result.message


def test_local_tool_runner_answers_status_readiness_without_legacy_fallback():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("系统就绪了吗", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "dashboard_query_action"
    assert result.payload["tool_name"] == "query_dashboard_section"
    assert result.payload["generates_command"] is False
    assert "就绪" in result.message or "状态" in result.message


def test_local_tool_runner_answers_why_cannot_move_without_control_fallback():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("为什么不能走", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "dashboard_query_action"
    assert result.payload["tool_name"] == "query_dashboard_section"
    assert result.payload["generates_command"] is False
    assert "状态" in result.message or "安全" in result.message or "原因" in result.message


def test_local_tool_runner_returns_clarification_for_empty_or_junk_without_legacy_fallback():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    empty = runner("", SessionState(thread_id="session-1"), build_local_tool_specs())
    junk = runner("!!!@@@###", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert empty.kind == "clarification"
    assert junk.kind == "clarification"
    assert empty.payload["generates_command"] is False
    assert junk.payload["generates_command"] is False


def test_local_tool_runner_answers_command_catalog_from_local_data(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.table["move_a"] = QueryRecord(
        query_key="move_a",
        func_num=108,
        description="移动到位置A",
        params={"target_x": 100.0, "target_y": 0.0, "target_z": 100.0},
    )
    service.save_flow_entry(FlowEntry(name="点头", steps=[FlowStep(step_id=1, action="移动到位置A", func_id=108)]))
    runner = LocalToolCallingRunner(LocalToolRegistry(flow_service=service))

    result = runner("我有哪些命令", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "command_catalog"
    assert result.payload["tool_name"] == "query_command_catalog"
    assert result.payload["tool_result"]["state"] == "command_catalog_loaded"
    assert "当前共有 1 个流程" in result.message
    assert "移动到位置A" in result.message
    assert "二次原子函数能力" not in result.message


def test_local_tool_runner_routes_position_template_to_atomic_tool(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.table["位置A"] = QueryRecord(
        query_key="位置A",
        func_num=108,
        description="移动到位置A",
        keywords="A点 位置A",
        params={"target_x": 1000.0, "target_y": 0.0, "target_z": 800.0},
    )
    runner = LocalToolCallingRunner(LocalToolRegistry(flow_service=service))

    result = runner("小正，移动到位置a", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "atomic_template_action"
    assert result.payload["tool_name"] == "apply_atomic_template"
    assert result.payload["tool_result"]["state"] == "atomic_template_applied"
    assert result.payload["tool_result"]["data"]["query_record"]["query_key"] == "位置A"
    assert result.payload["generates_command"] is True


def test_local_tool_runner_answers_flow_context_question_after_clarification(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    plan_service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(flow_service=service, execution_plan_service=plan_service))
    specs = build_local_tool_specs()
    state = SessionState(thread_id="session-1")
    for text in (
        "你好，我先创建一个新的流程",
        "现在流程名字叫测试",
        "添加第一步是移动到位置 A",
        "我觉得坐标是 X 一百 Y0 Z100 速度 50",
    ):
        result = runner(text, state, specs)
        state = SessionState.from_dict(result.payload["session_state"])

    followup = runner("为什么也，为什么为什么又在哄呢", state, specs)

    assert followup.kind == "flow_draft"
    assert followup.payload["tool_name"] == "query_current_flow_draft"
    assert "测试" in followup.message
    assert "请补充明确的问题" not in followup.message


def test_local_tool_runner_acknowledges_flow_coordinates_after_clarification(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    plan_service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(flow_service=service, execution_plan_service=plan_service))
    specs = build_local_tool_specs()
    state = SessionState(thread_id="session-1")
    for text in (
        "你好，我先创建一个新的流程",
        "现在流程名字叫测试",
        "添加第一步是移动到位置 A",
        "我觉得坐标是 X 一百 Y0 Z100 速度 50",
    ):
        result = runner(text, state, specs)
        state = SessionState.from_dict(result.payload["session_state"])

    followup = runner("对呀，那肯定用我的坐标呀", state, specs)

    assert followup.kind == "flow_draft"
    assert followup.payload["tool_name"] == "query_current_flow_draft"
    assert "移动到位置A" in followup.message
    assert "请补充明确的问题" not in followup.message


def test_local_tool_runner_routes_dashboard_query_through_tool_registry():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("为什么不能执行，建议怎么处理", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "dashboard_query_action"
    assert result.payload["action_type"] == "query"
    assert result.payload["generates_command"] is False
    assert result.payload["tool_name"] == "query_dashboard_section"
    assert result.payload["tool_result"]["state"] == "dashboard_section_matched"


def test_local_tool_runner_routes_sequential_compound_to_tool_registry():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("走到X1000，然后等待2秒", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "compound_plan_draft"
    assert result.payload["generates_command"] is False
    assert result.payload["tool_name"] == "plan_compound_command"
    assert result.payload["tool_result"]["state"] == "compound_plan_draft"
    assert result.payload["tool_result"]["data"]["steps"] == ["走到X1000", "等待2秒"]


def test_local_tool_runner_starts_flow_creation_by_asking_for_name():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("你好，我先创建一个新的流程。", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["generates_command"] is False
    assert result.payload["tool_name"] == "start_flow_draft"
    assert result.payload["tool_result"]["state"] == "flow_draft_needs_name"
    assert result.payload["tool_result"]["data"]["missing_fields"] == ["flow_name"]
    assert "名称" in result.message


def test_local_tool_runner_creates_named_flow_and_inline_position_steps_in_one_turn():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))

    result = runner(
        "我想创建一个新的流程，流程的名字叫测试，步骤为先移动为位置a 然后移动到位置b",
        SessionState(thread_id="session-1"),
        build_local_tool_specs(),
    )

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "start_flow_draft"
    assert result.payload["draft"]["flow_name"] == "测试"
    steps = result.payload["draft"]["expanded_steps"]
    assert [step["action"] for step in steps] == ["移动到位置A", "移动到位置B"]
    assert result.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert result.payload["tool_result"]["data"]["missing_fields"] == ["target_pose"]


def test_local_tool_runner_sets_flow_name_from_clarifying_session_state():
    runner = LocalToolCallingRunner(LocalToolRegistry())
    state = SessionState(
        thread_id="session-1",
        mode="clarifying",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "", "expanded_steps": []},
        pending_missing_fields=("flow_name",),
    )

    result = runner("现在流程名字叫测试。", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["generates_command"] is False
    assert result.payload["tool_name"] == "set_flow_name"
    assert result.payload["tool_result"]["state"] == "flow_draft_updated"
    assert result.payload["tool_result"]["data"]["draft"]["flow_name"] == "测试"
    assert "测试" in result.message


def test_local_tool_runner_does_not_rename_flow_after_name_is_set_when_missing_field_is_stale():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="clarifying",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "测试", "expanded_steps": []},
        pending_missing_fields=("flow_name",),
    )

    result = runner("添加一个位置", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "append_flow_step"
    assert result.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert result.payload["draft"]["flow_name"] == "测试"
    assert result.payload["draft"]["expanded_steps"][0]["action"] == "移动到位置"


def test_local_tool_runner_appends_flow_step_to_current_flow_draft():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "测试", "expanded_steps": []},
    )

    result = runner("添加第一步是移动到位置 A。", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "append_flow_step"
    assert result.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert result.payload["tool_result"]["data"]["missing_fields"] == ["target_pose"]
    assert result.payload["draft"]["expanded_steps"][0]["action"] == "移动到位置A"


def test_local_tool_runner_appends_inline_delay_flow_step_without_clarification():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "测试", "expanded_steps": []},
    )

    result = runner("添加下一步等待2秒", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "append_flow_step"
    assert result.payload["tool_result"]["state"] == "flow_draft_updated"
    assert result.payload["draft"]["expanded_steps"][0]["params"]["delay_sec"] == 2.0


def test_local_tool_runner_appends_inline_io_flow_step_without_clarification():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "测试", "expanded_steps": []},
    )

    result = runner("添加下一步输出1打开", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "append_flow_step"
    assert result.payload["tool_result"]["state"] == "flow_draft_updated"
    assert result.payload["draft"]["expanded_steps"][0]["params"]["io_no"] == 1
    assert result.payload["draft"]["expanded_steps"][0]["params"]["io_action"] == 1


def test_local_tool_runner_appends_spoken_multi_step_flow_draft():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "测试", "expanded_steps": []},
    )

    result = runner("步骤一，移动到位置 A。步骤二，等待 2 秒。", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "append_flow_step"
    assert result.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert result.payload["tool_result"]["data"]["missing_fields"] == ["target_pose"]
    steps = result.payload["draft"]["expanded_steps"]
    assert len(steps) == 2
    assert steps[0]["action"] == "移动到位置A"
    assert steps[1]["params"]["delay_sec"] == 2.0


def test_local_tool_runner_answers_flow_step_clarification_before_control_route():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(
        LocalToolRegistry(
            execution_plan_service=service,
            controller_snapshot_provider=_snapshot,
        )
    )
    state = SessionState(
        thread_id="session-1",
        mode="clarifying",
        current_intent="create_flow",
        current_flow_draft={
            "flow_name": "测试",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动到位置A",
                    "func_id": 108,
                    "description": "移动到位置A",
                    "params": {"spd_pct": 50.0},
                }
            ],
        },
        pending_missing_fields=("target_pose",),
    )

    result = runner("我觉得坐标是 X 一百， Y 0， Z 100，速度 50。", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "answer_flow_clarification"
    assert result.payload["tool_result"]["state"] == "flow_draft_updated"
    assert result.payload["draft"]["expanded_steps"][0]["params"]["target_x"] == 100.0
    assert result.payload["draft"]["expanded_steps"][0]["params"]["spd_pct"] == 50.0


def test_local_tool_runner_continues_multi_step_flow_clarifications_across_turns():
    service = ExecutionPlanService()
    runner = LocalToolCallingRunner(
        LocalToolRegistry(
            execution_plan_service=service,
            controller_snapshot_provider=_snapshot,
        )
    )
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={"flow_name": "测试", "expanded_steps": []},
    )

    added = runner("步骤一，移动到位置 A。步骤二，等待。步骤三，输出。", state, build_local_tool_specs())
    state = SessionState.from_dict(added.payload["session_state"])
    assert added.payload["tool_result"]["data"]["missing_fields"] == ["target_pose"]

    pose = runner("X 一百，Y 0，Z 100，RX 0，RY 0，RZ 0，速度 50", state, build_local_tool_specs())
    state = SessionState.from_dict(pose.payload["session_state"])
    assert pose.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert pose.payload["tool_result"]["data"]["missing_fields"] == ["delay_sec"]

    delay = runner("两秒", state, build_local_tool_specs())
    state = SessionState.from_dict(delay.payload["session_state"])
    assert delay.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert delay.payload["tool_result"]["data"]["missing_fields"] == ["io_no"]

    io_no = runner("输出 1", state, build_local_tool_specs())
    state = SessionState.from_dict(io_no.payload["session_state"])
    assert io_no.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert io_no.payload["tool_result"]["data"]["missing_fields"] == ["io_action"]

    action = runner("打开", state, build_local_tool_specs())

    steps = action.payload["draft"]["expanded_steps"]
    assert action.payload["tool_result"]["state"] == "flow_draft_updated"
    assert action.payload["tool_result"]["data"]["missing_fields"] == []
    assert steps[0]["params"]["target_x"] == 100.0
    assert steps[1]["params"]["delay_sec"] == 2.0
    assert steps[2]["params"]["io_no"] == 1
    assert steps[2]["params"]["io_action"] == 1


def test_local_tool_runner_saves_current_flow_draft(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    runner = LocalToolCallingRunner(LocalToolRegistry(flow_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={
            "flow_name": "测试",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动到位置A",
                    "func_id": 108,
                    "description": "移动到位置A",
                    "params": {
                        "target_x": 100.0,
                        "target_y": 0.0,
                        "target_z": 100.0,
                        "target_rx": 0.0,
                        "target_ry": 0.0,
                        "target_rz": 0.0,
                    },
                }
            ],
        },
    )

    result = runner("保存流程", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "save_flow_draft"
    assert result.payload["tool_result"]["state"] == "flow_draft_saved"
    assert service.get_flow_entry("测试") is not None


def test_local_tool_runner_rejects_confirm_execution_without_pending_plan():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("确认执行", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "confirm_rejected"
    assert result.payload["tool_name"] == "query_pending_confirm"
    assert result.payload["tool_result"]["state"] == "confirm_not_found"
    assert result.payload["generates_command"] is False
    assert "没有" in result.message


def test_local_tool_runner_rejects_followup_execute_without_pending_context():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("我要执行我刚刚创建的命令", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "followup_rejected"
    assert result.payload["tool_name"] == "query_pending_confirm"
    assert result.payload["generates_command"] is False
    assert "当前没有待确认计划" in result.message


def test_local_tool_runner_confirms_pending_plan_through_tool_registry():
    agent = ConfirmationAgent(timeout_sec=60)
    now = iter([10.0, 20.0])
    registry = LocalToolRegistry(
        confirmation_agent=agent,
        clock=lambda: next(now),
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )
    draft = {
        "draft_id": "draft-1",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
        "param_sources": {"delay_sec": "specified"},
        "raw_text": "等待2秒",
        "confidence": 0.95,
    }
    registry.call("create_pending_confirm", draft=draft)
    runner = LocalToolCallingRunner(registry)
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        pending_confirm={"draft_id": "draft-1", "expires_at": 70.0},
        pending_execution=draft,
    )

    result = runner("确认执行", state, build_local_tool_specs())

    assert result.kind == "confirm_result"
    assert result.payload["tool_name"] == "confirm_pending_plan"
    assert result.payload["tool_result"]["state"] == "confirmed"
    assert result.payload["tool_result"]["data"]["query_record"]["func_num"] == 109
    assert result.payload["generates_command"] is False


def test_local_tool_runner_treats_positive_ack_as_confirm_when_pending_exists():
    agent = ConfirmationAgent(timeout_sec=60)
    now = iter([10.0, 20.0])
    registry = LocalToolRegistry(
        confirmation_agent=agent,
        clock=lambda: next(now),
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )
    draft = {
        "draft_id": "draft-1",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
        "param_sources": {"delay_sec": "specified"},
        "raw_text": "等待2秒",
        "confidence": 0.95,
    }
    registry.call("create_pending_confirm", draft=draft)
    runner = LocalToolCallingRunner(registry)
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        pending_confirm={"draft_id": "draft-1", "expires_at": 70.0},
        pending_execution=draft,
    )

    result = runner("好的", state, build_local_tool_specs())

    assert result.kind == "confirm_result"
    assert result.payload["tool_name"] == "confirm_pending_plan"
    assert result.payload["tool_result"]["state"] == "confirmed"


def test_local_tool_runner_rejects_cancel_without_pending_plan():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("取消执行", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "confirm_rejected"
    assert result.payload["tool_name"] == "query_pending_confirm"
    assert result.payload["tool_result"]["state"] == "confirm_not_found"
    assert result.payload["generates_command"] is False


def test_local_tool_runner_cancels_pending_plan_through_tool_registry():
    agent = ConfirmationAgent(timeout_sec=60)
    registry = LocalToolRegistry(
        confirmation_agent=agent,
        clock=lambda: 10.0,
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )
    draft = {
        "draft_id": "draft-1",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
        "param_sources": {"delay_sec": "specified"},
        "raw_text": "等待2秒",
        "confidence": 0.95,
    }
    registry.call("create_pending_confirm", draft=draft)
    runner = LocalToolCallingRunner(registry)
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        pending_confirm={"draft_id": "draft-1", "expires_at": 70.0},
        pending_execution=draft,
    )

    result = runner("取消执行", state, build_local_tool_specs())

    assert result.kind == "confirm_cancelled"
    assert result.payload["tool_name"] == "cancel_pending_plan"
    assert result.payload["tool_result"]["state"] == "cancelled"
    assert result.payload["generates_command"] is False


def test_local_tool_runner_records_user_feedback_vote(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    runner = LocalToolCallingRunner(LocalToolRegistry(memory_store=store))
    state = SessionState(
        thread_id="session-1",
        last_interaction_id="record-1",
        last_agent_result={"kind": "chat_answer", "target_id": "record-1"},
    )

    result = runner("这个回答没用", state, build_local_tool_specs())

    assert result.kind == "feedback_vote_recorded"
    assert result.payload["tool_name"] == "record_feedback_vote"
    assert result.payload["tool_result"]["state"] == "feedback_vote_recorded"
    assert result.payload["tool_result"]["data"]["vote"]["vote"] == "down"
    assert store.list_feedback_votes(interaction_id="record-1")[0]["note"] == "这个回答没用"


def test_local_tool_runner_applies_active_memory_before_routing_and_audits(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(
        kind="text_alias",
        key="为啥",
        value={"normalized": "为什么"},
        source="feedback:record-1",
    )
    store.approve_memory(candidate["memory_id"], reviewer="engineer")
    runner = LocalToolCallingRunner(LocalToolRegistry(memory_store=store))

    result = runner("为啥不能执行，建议怎么处理", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "dashboard_query_action"
    assert result.payload["raw_text"] == "为啥不能执行，建议怎么处理"
    assert result.payload["normalized_text"] == "为什么不能执行，建议怎么处理"
    assert result.payload["applied_memories"][0]["memory_id"] == candidate["memory_id"]
    assert store.list_audit_events(memory_id=candidate["memory_id"])[-1]["event"] == "memory_applied"


def test_local_tool_runner_queries_current_flow_draft_from_session_state():
    runner = LocalToolCallingRunner(LocalToolRegistry())
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft={
            "flow_name": "测试",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动到位置A",
                    "func_id": 108,
                    "description": "移动到位置A",
                    "params": {"target_x": 100.0},
                }
            ],
        },
    )

    result = runner("查看流程", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "query_current_flow_draft"
    assert result.payload["tool_result"]["state"] == "flow_draft_loaded"
    assert result.payload["draft"]["flow_name"] == "测试"
    assert result.payload["draft"]["expanded_steps"][0]["params"]["target_x"] == 100.0


def test_local_tool_runner_edits_current_flow_step_speed_from_context():
    service = ExecutionPlanService()
    draft = {
        "flow_name": "测试",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动到位置B",
                "func_id": 108,
                "description": "移动到位置B",
                "params": {
                    "target_x": 475.0,
                    "target_y": 0.0,
                    "target_z": 545.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 30.0,
                    "acc_pct": 30.0,
                    "dec_pct": 30.0,
                },
            }
        ],
    }
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft=draft,
    )

    result = runner("那就改成20%", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "edit_flow_draft_params"
    assert result.payload["tool_result"]["state"] == "flow_draft_updated"
    assert result.payload["draft"]["expanded_steps"][0]["params"]["spd_pct"] == 20.0
    assert result.payload["draft"]["expanded_steps"][0]["params"]["acc_pct"] == 30.0
    assert result.payload["draft"]["expanded_steps"][0]["params"]["dec_pct"] == 30.0
    assert "速度" in result.message


def test_local_tool_runner_edits_current_flow_step_acceleration_from_context():
    service = ExecutionPlanService()
    draft = {
        "flow_name": "测试",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动到位置B",
                "func_id": 108,
                "description": "移动到位置B",
                "params": {
                    "target_x": 475.0,
                    "target_y": 0.0,
                    "target_z": 545.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 30.0,
                    "acc_pct": 30.0,
                    "dec_pct": 30.0,
                },
            }
        ],
    }
    runner = LocalToolCallingRunner(LocalToolRegistry(execution_plan_service=service))
    state = SessionState(
        thread_id="session-1",
        mode="editing_flow",
        current_intent="create_flow",
        current_flow_draft=draft,
    )

    result = runner("我要加速度改成20%", state, build_local_tool_specs())

    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "edit_flow_draft_params"
    assert result.payload["draft"]["expanded_steps"][0]["params"]["spd_pct"] == 30.0
    assert result.payload["draft"]["expanded_steps"][0]["params"]["acc_pct"] == 20.0
    assert result.payload["draft"]["expanded_steps"][0]["params"]["dec_pct"] == 30.0


def test_local_tool_runner_routes_single_control_command_to_command_draft_tool():
    runner = LocalToolCallingRunner(LocalToolRegistry(controller_snapshot_provider=_snapshot))

    result = runner("小正，移动到 X 一百，Y 0，Z 100，速度 50", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "restricted_agent"
    assert result.payload.kind == "waiting_confirmation"
    assert result.payload.draft.func_id == 108
    assert result.payload.draft.params["target_x"] == 100.0
    assert result.payload.draft.params["acc_pct"] == 20.0
    assert result.payload.precheck_result == {}


def test_local_tool_runner_routes_lowercase_equal_cartesian_command_to_confirmation():
    runner = LocalToolCallingRunner(LocalToolRegistry(controller_snapshot_provider=_snapshot))

    result = runner("小正，移动到x=1000,y=0,z=1500", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "restricted_agent"
    assert result.payload.kind == "waiting_confirmation"
    assert result.payload.draft.func_id == 108
    assert result.payload.draft.params["target_x"] == 1000.0
    assert result.payload.draft.params["target_y"] == 0.0
    assert result.payload.draft.params["target_z"] == 1500.0
    assert result.payload.draft.params["target_rx"] == 0.0
    assert result.payload.draft.params["spd_pct"] == 30.0


def test_local_tool_runner_routes_height_increment_to_func108_confirmation():
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            assert draft.func_id == 108
            assert draft.intent == "move_linear"
            assert draft.params["target_z"] == 100.0
            assert draft.param_sources["target_z"] == "incremental"
            assert draft.params["target_x"] == 0.0
            assert draft.param_sources["target_x"] == "inherited"
            assert draft.params["spd_pct"] == 30.0
            assert draft.param_sources["spd_pct"] == "controller"
            return {"valid": True, "status": "pass", "summary": "L1通过。", "items": []}

    runner = LocalToolCallingRunner(
        LocalToolRegistry(
            controller_snapshot_provider=_snapshot,
            safety_review_agent=ReviewAgent(),
            runtime_snapshot_provider=lambda: {"motion": {"running_state": "idle"}},
            confirmation_agent=ConfirmationAgent(timeout_sec=60),
            clock=lambda: 10.0,
            status_signature_provider=lambda: "status-1",
            safety_signature_provider=lambda: "safety-1",
        )
    )

    result = runner("小正，高度升高100", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "confirm_plan"
    assert result.payload["tool_name"] == "create_pending_confirm"
    assert result.payload["tool_result"]["state"] == "waiting_confirmation"
    assert result.payload["draft"]["func_id"] == 108
    assert result.payload["draft"]["params"]["target_z"] == 100.0
    assert result.payload["draft"]["param_sources"]["target_z"] == "incremental"
    assert "确认" in result.payload["tool_result"]["data"]["confirmation_text"]


def test_local_tool_runner_routes_system_action_without_legacy_fallback():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("暂停", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "restricted_agent"
    assert result.payload.kind == "bypass"
    assert result.payload.intent == "sys_pause"
    assert result.payload.func_id == 104


def test_local_tool_runner_falls_back_when_control_tools_disabled():
    runner = LocalToolCallingRunner(LocalToolRegistry(control_tools_enabled=False))

    result = runner("暂停", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "tool_calling_unavailable"
    assert result.payload["fallback_required"] is True


def test_local_tool_runner_runs_command_precheck_and_pending_confirm_when_tools_are_available():
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {"valid": True, "status": "pass", "summary": "L1通过。", "items": []}

    runner = LocalToolCallingRunner(
        LocalToolRegistry(
            controller_snapshot_provider=_snapshot,
            safety_review_agent=ReviewAgent(),
            runtime_snapshot_provider=lambda: {"motion": {"running_state": "idle"}},
            start_pose_provider=lambda: (0, 0, 0, 0, 0, 0),
            confirmation_agent=ConfirmationAgent(timeout_sec=60),
            clock=lambda: 10.0,
            status_signature_provider=lambda: "status-1",
            safety_signature_provider=lambda: "safety-1",
        )
    )

    result = runner("小正，移动到 X 一百，Y 0，Z 100，速度 50", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "confirm_plan"
    assert result.payload["generates_command"] is False
    assert result.payload["tool_name"] == "create_pending_confirm"
    assert result.payload["tool_result"]["state"] == "waiting_confirmation"
    assert result.payload["draft"]["params"]["target_x"] == 100.0
    assert result.payload["precheck"]["valid"] is True


def test_local_tool_runner_returns_blocked_result_when_safety_precheck_fails():
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {
                "valid": False,
                "status": "fail",
                "summary": "L1预检未通过。",
                "blocking_level": "L1",
                "items": [],
            }

    runner = LocalToolCallingRunner(
        LocalToolRegistry(
            controller_snapshot_provider=_snapshot,
            safety_review_agent=ReviewAgent(),
            runtime_snapshot_provider=lambda: {"motion": {"running_state": "idle"}},
            confirmation_agent=ConfirmationAgent(timeout_sec=60),
            clock=lambda: 10.0,
            status_signature_provider=lambda: "status-1",
            safety_signature_provider=lambda: "safety-1",
        )
    )

    result = runner("小正，移动到 X 一百，Y 0，Z 100，速度 50", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "precheck_failed"
    assert result.payload["tool_name"] == "run_safety_precheck"
    assert result.payload["tool_result"]["state"] == "safety_precheck_failed"
    assert result.payload["generates_command"] is False


def test_local_tool_runner_falls_back_for_control_command():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("小正，走到 X100", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "tool_calling_unavailable"
    assert result.payload["fallback_required"] is True


def test_local_tool_runner_falls_back_for_memory_setting_command():
    runner = LocalToolCallingRunner(LocalToolRegistry())

    result = runner("小正，速度60%", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "tool_calling_unavailable"
    assert result.payload["fallback_required"] is True


def test_local_tool_runner_does_not_turn_memory_setting_into_command_draft_when_snapshot_exists():
    runner = LocalToolCallingRunner(LocalToolRegistry(controller_snapshot_provider=_snapshot))

    result = runner("小正，速度60%", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "tool_calling_unavailable"
    assert result.payload["fallback_required"] is True
