from robot_modbus_lite.agent_runtime.local_tool_registry import LocalToolRegistry
from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore
from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent_runtime.tool_calling_agent import build_local_tool_specs
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.agent.confirmation import ConfirmationAgent
from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
from robot_modbus_lite.execution_plan_service import ExecutionPlanService
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionRegistry
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


def test_local_tool_registry_implements_every_exposed_tool_spec():
    registry = LocalToolRegistry()

    assert {spec.name for spec in build_local_tool_specs()} <= set(registry.tool_names)


def test_local_tool_registry_calls_command_parser():
    registry = LocalToolRegistry()

    result = registry.call("parse_command_params", text="X 一百，Y 0，Z 100，速度 50")

    assert result.ok is True
    assert result.data["params"]["target_x"] == 100.0
    assert result.data["params"]["spd_pct"] == 50.0


def test_local_tool_registry_calls_command_intent_parser():
    registry = LocalToolRegistry()

    result = registry.call("parse_command_intent", text="X 一百，Y 0，Z 100，速度 50")

    assert result.ok is True
    assert result.state == "command_intent_parsed"
    assert result.data["intent"] == "move_linear"
    assert result.data["func_id"] == 108
    assert "params" not in result.data


def test_local_tool_registry_calls_command_schema_and_boundary_tools():
    registry = LocalToolRegistry()

    schema = registry.call("lookup_command_schema", command_name="move_linear")
    missing = registry.call("validate_required_params", func_id=109, params={})
    bounds = registry.call(
        "check_param_bounds",
        params={"target_x": 120.0},
        bounds={"schema_version": "safety-bounds-v1", "x": (-100.0, 100.0)},
    )
    address = registry.call("resolve_command_address", name="absolute_motion_func")

    assert schema.ok is True
    assert schema.data["schema"]["func_id"] == 108
    assert missing.ok is False
    assert missing.state == "missing_params"
    assert bounds.ok is False
    assert bounds.state == "param_bounds_failed"
    assert address.data["value"] == 108


def test_local_tool_registry_calls_system_action_draft_tool():
    registry = LocalToolRegistry()

    result = registry.call("build_system_action_draft", text="继续")

    assert result.ok is True
    assert result.state == "system_action_draft_built"
    assert result.data["intent"] == "sys_resume"
    assert result.data["generates_command"] is False


def test_local_tool_registry_calls_command_draft_builder():
    registry = LocalToolRegistry(controller_snapshot_provider=_snapshot)

    result = registry.call("build_command_draft", text="移动到 X 一百，Y 0，Z 100，速度 50")

    assert result.ok is True
    assert result.state == "command_draft_built"
    assert result.data["draft"]["func_id"] == 108
    assert result.data["draft"]["params"]["target_x"] == 100.0
    assert result.data["draft"]["params"]["acc_pct"] == 20.0


def test_local_tool_registry_calls_atomic_template_tool():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))
    registry = LocalToolRegistry(atomic_memory_provider=lambda: memory)

    result = registry.call("apply_atomic_template", text="小正，移动到位置A")

    assert result.ok is True
    assert result.state == "atomic_template_applied"
    assert result.data["query_record"]["query_key"] == "atomic:position:A"
    assert result.data["generates_command"] is False


def test_local_tool_registry_calls_draft_to_query_record_tool():
    registry = LocalToolRegistry()

    result = registry.call(
        "draft_to_query_record",
        draft={
            "draft_id": "draft-1",
            "func_id": 109,
            "intent": "delay_blocking",
            "params": {"delay_sec": 2.0},
            "param_sources": {"delay_sec": "specified"},
            "raw_text": "等待2秒",
            "confidence": 0.95,
            "confirmed": True,
        },
    )

    assert result.ok is True
    assert result.state == "query_record_built"
    assert result.data["query_record"]["query_key"] == "agent:draft-1"


def test_local_tool_registry_calls_safety_precheck_tool():
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {"valid": True, "status": "pass", "summary": "L1通过。", "items": []}

    registry = LocalToolRegistry(
        safety_review_agent=ReviewAgent(),
        runtime_snapshot_provider=lambda: {"motion": {"running_state": "idle"}},
        start_pose_provider=lambda: (0, 0, 0, 0, 0, 0),
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

    result = registry.call("run_safety_precheck", draft=draft)

    assert result.ok is True
    assert result.state == "safety_precheck_passed"
    assert result.data["draft_id"] == "draft-1"


def test_local_tool_registry_safety_precheck_requires_configured_agent():
    registry = LocalToolRegistry()
    draft = {
        "draft_id": "draft-1",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
    }

    result = registry.call("run_safety_precheck", draft=draft)

    assert result.ok is False
    assert result.state == "safety_review_unavailable"
    assert result.errors[0]["code"] == "SAFETY_REVIEW_UNAVAILABLE"


def test_local_tool_registry_calls_confirmation_lifecycle_tools():
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

    created = registry.call("create_pending_confirm", draft=draft)
    queried = registry.call("query_pending_confirm", draft_id="draft-1")
    confirmed = registry.call("confirm_pending_plan", draft_id="draft-1")

    assert created.ok is True
    assert created.state == "waiting_confirmation"
    assert created.data["draft_id"] == "draft-1"
    assert queried.data["status"] == "waiting_confirmation"
    assert confirmed.ok is True
    assert confirmed.state == "confirmed"
    assert confirmed.data["query_record"]["func_num"] == 109


def test_local_tool_registry_calls_cancel_pending_plan_tool():
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
    cancelled = registry.call("cancel_pending_plan", draft_id="draft-1")

    assert cancelled.ok is True
    assert cancelled.state == "cancelled"


def test_local_tool_registry_calls_expire_pending_plan_tool():
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
    expired = registry.call("expire_pending_plan", draft_id="draft-1")
    queried = registry.call("query_pending_confirm", draft_id="draft-1")

    assert expired.ok is True
    assert expired.state == "expired"
    assert queried.data["status"] == "expired"


def test_local_tool_registry_confirmation_tools_require_configured_agent():
    registry = LocalToolRegistry()
    draft = {
        "draft_id": "draft-1",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
    }

    result = registry.call("create_pending_confirm", draft=draft)

    assert result.ok is False
    assert result.state == "confirmation_agent_unavailable"
    assert result.errors[0]["code"] == "CONFIRMATION_AGENT_UNAVAILABLE"


def test_local_tool_registry_calls_chat_tool():
    registry = LocalToolRegistry()

    result = registry.call("explain_text", text="你是谁")

    assert result.ok is True
    assert result.state == "chat_explained"


def test_local_tool_registry_calls_axis_and_alarm_status_tools():
    snapshot = {
        "safety": {"long34": 1 << 25, "long36": 1, "long38": 0},
        "motion": {"current_func": 108},
        "hardware": {"axis_status": [0, 1 << 3, 0, 0, 0, 0]},
    }
    registry = LocalToolRegistry(runtime_snapshot_provider=lambda: snapshot)

    axis = registry.call("get_axis_status", axis=2)
    alarm = registry.call("get_alarm")

    assert axis.ok is True
    assert axis.state == "axis_status_loaded"
    assert axis.data["axes"][0]["messages"][0]["code"] == "drive_alarm"
    assert alarm.ok is True
    assert alarm.state == "alarm_loaded"
    assert alarm.data["alarm"]["severity"] == "critical"


def test_local_tool_registry_calls_execution_progress_tool():
    registry = LocalToolRegistry(runtime_snapshot_provider=lambda: {"execution": {"progress": 80, "status": "running"}})

    result = registry.call("get_execution_progress")

    assert result.ok is True
    assert result.state == "execution_progress_loaded"
    assert result.data["progress"] == 80


def test_local_tool_registry_calls_compound_tool():
    registry = LocalToolRegistry()

    result = registry.call("split_compound_command", text="走到X1000，然后等待2秒")

    assert result.ok is True
    assert result.state == "compound_sequence"
    assert result.data["steps"] == ["走到X1000", "等待2秒"]


def test_local_tool_registry_compound_plan_uses_restricted_service():
    class Service:
        def __init__(self):
            self.calls = []

        def parse(self, text):
            self.calls.append(text)
            return {"kind": "waiting_confirmation", "text": text}

    service = Service()
    registry = LocalToolRegistry(restricted_service=service)

    result = registry.call("plan_compound_command", text="走到X1000，然后等待2秒")

    assert result.ok is True
    assert service.calls == ["走到X1000", "等待2秒"]
    assert result.data["step_results"] == [
        {"kind": "waiting_confirmation", "text": "走到X1000"},
        {"kind": "waiting_confirmation", "text": "等待2秒"},
    ]


def test_local_tool_registry_calls_flow_tools_with_execution_plan_service():
    service = ExecutionPlanService()
    registry = LocalToolRegistry(execution_plan_service=service)
    draft = {
        "flow_name": "测试流程",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动到位置A",
                "func_id": 108,
                "params": {"spd_pct": 50.0},
            }
        ],
    }

    result = registry.call("set_flow_draft", draft=draft)

    assert result.ok is False
    assert result.state == "flow_draft_needs_clarification"
    assert result.data["flow_name"] == "测试流程"
    assert result.data["missing_fields"] == ["target_pose"]
    assert service.pending_flow_draft()["flow_name"] == "测试流程"


def test_local_tool_registry_calls_save_flow_draft_tool(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    registry = LocalToolRegistry(flow_service=service)
    draft = {
        "flow_name": "测试流程",
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
    }

    result = registry.call("save_flow_draft", draft=draft)

    assert result.ok is True
    assert result.state == "flow_draft_saved"
    assert service.get_flow_entry("测试流程") is not None


def test_local_tool_registry_calls_registered_flow_tools(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.save_flow_entry(
        FlowEntry(
            name="点头",
            steps=[
                FlowStep(
                    step_id=1,
                    action="移动到位置A",
                    func_id=108,
                    params={"target_x": 100.0},
                    description="移动到位置A",
                )
            ],
        )
    )
    registry = LocalToolRegistry(flow_service=service)

    queried = registry.call("query_registered_flow", flow_name="点头")
    prepared = registry.call("prepare_registered_flow_execution", flow_name="点头")

    assert queried.ok is True
    assert queried.state == "registered_flow_loaded"
    assert prepared.ok is True
    assert prepared.state == "registered_flow_execution_draft"
    assert prepared.data["requires_execution_gate"] is True
    assert prepared.data["generates_command"] is False


def test_local_tool_registry_calls_memory_tools(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    registry = LocalToolRegistry(memory_store=store)

    candidate = registry.call(
        "create_memory_candidate",
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="test",
    )
    registry.call("approve_memory_candidate", memory_id=candidate.data["memory"]["memory_id"], reviewer="engineer")
    lookup = registry.call("lookup_active_memory", kind="asr_alias", key="位置诶")
    candidates = registry.call("query_memory_candidates", kind="asr_alias")
    review = registry.call("query_memory_review", status="active", kind="asr_alias")
    rollback = registry.call(
        "rollback_memory",
        memory_id=candidate.data["memory"]["memory_id"],
        reviewer="engineer",
        reason="用户点踩",
    )
    vote = registry.call(
        "record_feedback_vote",
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="up",
    )

    assert lookup.data["memories"][0]["value"]["normalized"] == "位置A"
    assert candidates.ok is True
    assert candidates.state == "memory_candidates_listed"
    assert len(candidates.data["memories"]) == 0
    assert review.ok is True
    assert review.state == "memory_review_listed"
    assert review.data["count"] == 1
    assert review.data["memories"][0]["audit_events"][0]["event"] == "candidate_created"
    assert rollback.ok is True
    assert rollback.state == "memory_rolled_back"
    assert registry.call("lookup_active_memory", kind="asr_alias", key="位置诶").data["memories"] == []
    assert vote.state == "feedback_vote_recorded"


def test_local_tool_registry_queries_memory_candidates(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    registry = LocalToolRegistry(memory_store=store)
    registry.call(
        "create_memory_candidate",
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    )

    result = registry.call("query_memory_candidates", kind="asr_alias")

    assert result.ok is True
    assert result.state == "memory_candidates_listed"
    assert result.data["memories"][0]["key"] == "位置诶"


def test_local_tool_registry_calls_position_alias_memory_tools(tmp_path):
    position_registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))
    registry = LocalToolRegistry(position_registry_provider=lambda: position_registry)

    saved = registry.call("save_position_alias", name="A", pose=(1, 2, 3, 4, 5, 6))
    deleted = registry.call("delete_position_alias", name="A")

    assert saved.ok is True
    assert saved.state == "position_alias_saved"
    assert saved.data["generates_command"] is False
    assert deleted.ok is True
    assert deleted.state == "position_alias_deleted"
    assert position_registry.get("A") is None


def test_local_tool_registry_save_position_alias_uses_current_pose_provider_when_pose_missing(tmp_path):
    position_registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))
    registry = LocalToolRegistry(
        position_registry_provider=lambda: position_registry,
        start_pose_provider=lambda: (10, 20, 30, 0, 90, 0),
    )

    result = registry.call("save_position_alias", name="A")

    assert result.ok is True
    assert result.state == "position_alias_saved"
    assert position_registry.get("A").pose == (10.0, 20.0, 30.0, 0.0, 90.0, 0.0)


def test_local_tool_registry_unknown_tool_fails():
    registry = LocalToolRegistry()

    result = registry.call("missing_tool", text="你好")

    assert result.ok is False
    assert result.state == "tool_not_found"


def test_local_tool_registry_replays_same_tool_call_id_without_duplicate_side_effect(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    registry = LocalToolRegistry(memory_store=store)
    state = SessionState(thread_id="session-1")

    first, state = registry.call_idempotent(
        "create_memory_candidate",
        session_state=state,
        tool_call_id="call-1",
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    )
    second, state = registry.call_idempotent(
        "create_memory_candidate",
        session_state=state,
        tool_call_id="call-1",
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    )

    assert first.to_dict() == second.to_dict()
    assert len(store.list_memories(status="candidate", kind="asr_alias")) == 1
    assert state.tool_call_history["call-1"]["replay_count"] == 1
