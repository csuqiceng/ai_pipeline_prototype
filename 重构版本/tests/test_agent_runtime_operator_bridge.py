import json

from robot_modbus_lite.agent_runtime.operator_bridge import OperatorAgentRuntimeBridge
from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent.confirmation import ConfirmationAgent, DraftStatus
from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
from robot_modbus_lite.execution_plan_service import ExecutionPlanService
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


def test_operator_bridge_caches_session_state_by_thread(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    first = bridge.session_state("session-1")
    second = bridge.session_state("session-1")
    other = bridge.session_state("session-2")

    assert first is second
    assert other.thread_id == "session-2"
    assert other is not first


def test_operator_bridge_applies_active_memory_and_updates_state(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    candidate = bridge.memory_store().create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    )
    bridge.memory_store().approve_memory(candidate["memory_id"], reviewer="engineer")

    normalized = bridge.apply_active_memory_to_text("移动到位置诶", thread_id="session-1")

    state = bridge.session_state("session-1")
    assert normalized == "移动到位置A"
    assert state.last_user_text == "移动到位置诶"
    assert state.last_normalized_text == "移动到位置A"
    assert state.applied_memories[0]["memory_id"] == candidate["memory_id"]


def test_operator_bridge_imports_json_seed_memory_from_runtime_data(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "nlp_standard_words.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "standard": "位置A",
                        "homophones": ["位置诶"],
                        "sichuan_variants": ["位置A点"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    store = bridge.memory_store()
    normalized = bridge.apply_active_memory_to_text("移动到位置诶", thread_id="session-1")

    imported = store.lookup_active(kind="text_alias", key="位置诶")
    assert imported
    assert imported[0]["value"]["normalized"] == "位置A"
    assert normalized == "移动到位置A"
    assert store.list_audit_events(memory_id=imported[0]["memory_id"])[-1]["event"] == "memory_applied"


def test_operator_bridge_runtime_uses_memory_backed_tool_registry(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    runtime = bridge.tool_calling_runtime()

    assert runtime is bridge.tool_calling_runtime()
    assert runtime.tool_registry.memory_store is bridge.memory_store()


def test_operator_bridge_passes_tool_decider_to_langgraph_runtime(tmp_path):
    def tool_decider(_payload):
        return {"tool_name": "explain_text", "args": {"text": "你好"}}

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=True,
        tool_decider=tool_decider,
    )

    result = bridge.handle_text(
        "移动到 X100",
        thread_id="session-1",
        legacy_fallback=lambda _text: AgentOrchestratorResult(kind="legacy", message="legacy"),
    )

    assert result.kind == "chat_answer"
    assert result.payload["tool_name"] == "explain_text"


def test_operator_bridge_runtime_passes_controller_snapshot_provider_to_command_tools(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, controller_snapshot_provider=_snapshot)

    runtime = bridge.tool_calling_runtime()
    result = runtime.tool_registry.call("build_command_draft", text="移动到 X 一百，Y 0，Z 100，速度 50")

    assert result.ok is True
    assert result.state == "command_draft_built"
    assert result.data["draft"]["params"]["target_x"] == 100.0
    assert result.data["draft"]["params"]["acc_pct"] == 20.0


def test_operator_bridge_runtime_passes_safety_precheck_dependencies_to_tools(tmp_path):
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {
                "valid": True,
                "status": "pass",
                "summary": f"{snapshot['motion']['running_state']}:{start_pose[0]}",
                "items": [],
            }

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        safety_review_agent_provider=lambda: ReviewAgent(),
        runtime_snapshot_provider=lambda: {"motion": {"running_state": "idle"}},
        start_pose_provider=lambda: (1, 2, 3, 4, 5, 6),
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

    result = bridge.tool_calling_runtime().tool_registry.call("run_safety_precheck", draft=draft)

    assert result.ok is True
    assert result.state == "safety_precheck_passed"
    assert result.message == "idle:1"


def test_operator_bridge_runtime_passes_confirmation_dependencies_to_tools(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    now = iter([10.0, 20.0])
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        confirmation_agent_provider=lambda: agent,
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

    created = bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    confirmed = bridge.tool_calling_runtime().tool_registry.call("confirm_pending_plan", draft_id="draft-1")

    assert created.state == "waiting_confirmation"
    assert confirmed.state == "confirmed"
    assert confirmed.data["query_record"]["func_num"] == 109


def test_operator_bridge_confirms_pending_plan_through_tool_registry(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    now = iter([10.0, 20.0])
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    result = bridge.confirm_pending_plan("draft-1", thread_id="session-1")

    state = bridge.session_state("session-1")
    assert result.ok is True
    assert result.state == "confirmed"
    assert result.data["query_record"]["func_num"] == 109
    assert state.mode == "idle"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}
    assert state.last_confirmed_execution["draft_id"] == "draft-1"


def test_operator_bridge_execution_failure_uses_last_confirmed_execution_context(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    bridge.confirm_pending_plan("draft-1", thread_id="session-1")
    result = bridge.record_execution_failure(
        thread_id="session-1",
        query_record={"query_key": "agent:draft-1", "func_num": 109},
        error="modbus write failed",
    )

    state = bridge.session_state("session-1")
    assert result.state == "execution_failed"
    assert state.mode == "execution_failed"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}
    assert state.last_failed_execution["execution_context"]["draft_id"] == "draft-1"
    assert state.last_failed_execution["query_record"]["func_num"] == 109


def test_operator_bridge_records_execution_failure_after_confirm_clears_reusable_state(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution({"draft_id": "draft-1", "intent": "delay_blocking"})
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    result = bridge.record_execution_failure(
        thread_id="session-1",
        query_record={"query_key": "agent:draft-1", "func_num": 109},
        error="controller write failed",
    )

    state = bridge.session_state("session-1")
    assert result.ok is False
    assert result.state == "execution_failed"
    assert state.mode == "execution_failed"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}
    assert state.last_tool_call["result"]["data"]["error"] == "controller write failed"


def test_operator_bridge_records_execution_failure_marks_active_compound_step_failed(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    bridge.set_session_state(
        SessionState(
            thread_id="session-1",
            mode="waiting_confirm",
            current_compound_plan={
                "plan_id": "compound:test",
                "steps": ["走到X100", "等待2秒"],
                "step_results": [
                    {"kind": "waiting_confirmation", "text": "走到X100"},
                    {"kind": "waiting_confirmation", "text": "等待2秒"},
                ],
                "active_step_index": 0,
                "active_step": "走到X100",
                "active_step_result": {"kind": "waiting_confirmation", "text": "走到X100"},
                "status": "waiting_step_confirm",
            },
            pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
            pending_execution={"draft_id": "draft-1", "intent": "cartesian_move"},
        )
    )

    result = bridge.record_execution_failure(
        thread_id="session-1",
        query_record={"query_key": "agent:draft-1", "func_num": 108},
        error="controller write failed",
    )

    state = bridge.session_state("session-1")
    assert result.state == "execution_failed"
    assert state.mode == "blocked"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}
    assert state.current_compound_plan["status"] == "failed"
    assert state.current_compound_plan["failed_step"] == {
        "index": 0,
        "step": "走到X100",
        "ok": False,
        "reason": "controller write failed",
    }
    assert state.last_tool_call["result"]["data"]["error"] == "controller write failed"


def test_operator_bridge_records_compound_step_result_through_langgraph(tmp_path):
    calls = []

    class GraphApp:
        def invoke(self, payload):
            calls.append(payload)
            state = SessionState.from_dict(payload["session_state"]).advance_compound_step(
                ok=payload["compound_step_result"]["ok"],
                reason=payload["compound_step_result"]["reason"],
            )
            return {
                "kind": "compound_step_result",
                "message": "第1步完成",
                "payload": {"session_state": state.to_dict()},
            }

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=True,
        langchain_graph_app=GraphApp(),
    )
    bridge.set_session_state(
        SessionState(
            thread_id="session-1",
            mode="waiting_confirm",
            current_compound_plan={
                "plan_id": "compound:test",
                "steps": ["走到X100", "等待2秒"],
                "step_results": [
                    {"kind": "waiting_confirmation", "text": "走到X100"},
                    {"kind": "waiting_confirmation", "text": "等待2秒"},
                ],
                "active_step_index": 0,
                "active_step": "走到X100",
                "active_step_result": {"kind": "waiting_confirmation", "text": "走到X100"},
                "status": "waiting_step_confirm",
            },
            pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
        )
    )

    result = bridge.record_compound_step_result(thread_id="session-1", ok=True, reason="第1步完成")

    state = bridge.session_state("session-1")
    assert calls[0]["compound_step_result"] == {"ok": True, "reason": "第1步完成"}
    assert result.kind == "compound_step_result"
    assert state.current_compound_plan["active_step_index"] == 1
    assert state.current_compound_plan["active_step"] == "等待2秒"
    assert state.pending_confirm == {}


def test_operator_bridge_records_compound_step_result_locally_when_graph_unavailable(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)
    bridge.set_session_state(
        SessionState(
            thread_id="session-1",
            mode="waiting_confirm",
            current_compound_plan={
                "plan_id": "compound:test",
                "steps": ["走到X100", "等待2秒"],
                "step_results": [
                    {"kind": "waiting_confirmation", "text": "走到X100"},
                    {"kind": "waiting_confirmation", "text": "等待2秒"},
                ],
                "active_step_index": 0,
                "active_step": "走到X100",
                "active_step_result": {"kind": "waiting_confirmation", "text": "走到X100"},
                "status": "waiting_step_confirm",
            },
            pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
        )
    )

    result = bridge.record_compound_step_result(thread_id="session-1", ok=False, reason="控制器报警")

    state = bridge.session_state("session-1")
    assert result.kind == "compound_step_result"
    assert state.mode == "blocked"
    assert state.current_compound_plan["status"] == "failed"
    assert state.current_compound_plan["failed_step"]["reason"] == "控制器报警"
    assert state.pending_confirm == {}


def test_operator_bridge_records_compound_step_result_locally_when_graph_raises(tmp_path):
    logs = []

    class GraphApp:
        def invoke(self, _payload):
            raise RuntimeError("graph unavailable")

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=True,
        langchain_graph_app=GraphApp(),
        log_func=lambda *args: logs.append(args),
    )
    bridge.set_session_state(
        SessionState(
            thread_id="session-1",
            mode="waiting_confirm",
            current_compound_plan={
                "plan_id": "compound:test",
                "steps": ["走到X100", "等待2秒"],
                "step_results": [
                    {"kind": "waiting_confirmation", "text": "走到X100"},
                    {"kind": "waiting_confirmation", "text": "等待2秒"},
                ],
                "active_step_index": 0,
                "active_step": "走到X100",
                "active_step_result": {"kind": "waiting_confirmation", "text": "走到X100"},
                "status": "waiting_step_confirm",
            },
            pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
        )
    )

    result = bridge.record_compound_step_result(thread_id="session-1", ok=False, reason="控制器报警")

    state = bridge.session_state("session-1")
    assert result.kind == "compound_step_result"
    assert result.payload["graph_error"] == "graph unavailable"
    assert logs[-1] == ("Agent", "复合指令步骤同步", "失败", "graph unavailable")
    assert state.mode == "blocked"
    assert state.current_compound_plan["status"] == "failed"
    assert state.current_compound_plan["failed_step"]["reason"] == "控制器报警"
    assert state.pending_confirm == {}


def test_operator_bridge_default_local_runtime_confirms_pending_plan_from_text(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    now = iter([10.0, 20.0])
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    result = bridge.handle_text("确认执行", thread_id="session-1", legacy_fallback=lambda text: None)

    state = bridge.session_state("session-1")
    assert result.kind == "confirm_result"
    assert result.payload["tool_result"]["state"] == "confirmed"
    assert state.mode == "idle"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}


def test_operator_bridge_default_local_runtime_cancels_pending_plan_from_text(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    result = bridge.handle_text("取消执行", thread_id="session-1", legacy_fallback=lambda text: None)

    state = bridge.session_state("session-1")
    assert result.kind == "confirm_cancelled"
    assert result.payload["tool_result"]["state"] == "cancelled"
    assert state.mode == "idle"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}


def test_operator_bridge_cancels_pending_plan_through_tool_registry(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    result = bridge.cancel_pending_plan("draft-1", thread_id="session-1")

    state = bridge.session_state("session-1")
    assert result.ok is True
    assert result.state == "cancelled"
    assert agent.get_status("draft-1") == DraftStatus.REJECTED
    assert state.mode == "idle"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}


def test_operator_bridge_cancel_pending_plan_clears_compound_execution_draft(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_flow_draft({"flow_name": "测试"})
        .with_compound_plan({"plan_id": "compound-1", "steps": ["走到X1000"]})
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    result = bridge.cancel_pending_plan("draft-1", thread_id="session-1")

    state = bridge.session_state("session-1")
    assert result.ok is True
    assert state.mode == "editing_flow"
    assert state.current_flow_draft["flow_name"] == "测试"
    assert state.current_compound_plan == {}
    assert state.pending_confirm == {}
    assert state.pending_execution == {}


def test_operator_bridge_records_feedback_and_learns_candidate(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    vote = bridge.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="把 位置诶 识别为 位置A",
    )
    learned = bridge.learn_memory_candidates_from_feedback()

    assert vote.state == "feedback_vote_recorded"
    assert learned.data["created_count"] == 1
    assert bridge.memory_store().list_memories(status="candidate", kind="asr_alias")[0]["key"] == "位置诶"


def test_operator_bridge_tracks_last_agent_result_for_followup_feedback(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)

    first = bridge.handle_text(
        "你好",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )
    state_after_first = bridge.session_state("session-1")

    second = bridge.handle_text(
        "这个回答没用",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )

    votes = bridge.memory_store().list_feedback_votes(interaction_id=state_after_first.last_interaction_id)
    assert first.kind == "chat_answer"
    assert state_after_first.last_interaction_id
    assert state_after_first.last_agent_result["kind"] == "chat_answer"
    assert second.kind == "feedback_vote_recorded"
    assert votes[0]["target_id"] == state_after_first.last_interaction_id
    assert votes[0]["vote"] == "down"


def test_operator_bridge_tracks_pending_confirm_lifecycle(tmp_path):
    class Plan:
        raw_text = "X100"
        source = "test"
        reason = "等待确认"

    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    payload = bridge.set_pending_confirm(
        Plan(),
        thread_id="session-1",
        expires_at=70.0,
    )

    state = bridge.session_state("session-1")
    assert state.mode == "waiting_confirm"
    assert state.pending_confirm == payload
    assert payload["plan_id"] == "X100"
    assert payload["expires_at"] == 70.0

    bridge.clear_pending_confirm(thread_id="session-1")

    cleared = bridge.session_state("session-1")
    assert cleared.mode == "idle"
    assert cleared.pending_confirm == {}


def test_operator_bridge_marks_pending_confirm_expired(tmp_path):
    class Plan:
        raw_text = "X100"
        source = "test"
        reason = "等待确认"

    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    bridge.set_pending_confirm(Plan(), thread_id="session-1", expires_at=70.0)

    bridge.expire_pending_confirm(thread_id="session-1")

    state = bridge.session_state("session-1")
    assert state.mode == "confirm_expired"
    assert state.pending_confirm == {}


def test_operator_bridge_expire_pending_confirm_marks_confirmation_agent_expired(tmp_path):
    agent = ConfirmationAgent(timeout_sec=60)
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        confirmation_agent_provider=lambda: agent,
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
    bridge.tool_calling_runtime().tool_registry.call("create_pending_confirm", draft=draft)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution(draft)
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    bridge.expire_pending_confirm(thread_id="session-1")

    state = bridge.session_state("session-1")
    assert agent.get_status("draft-1") == DraftStatus.EXPIRED
    assert state.mode == "confirm_expired"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}


def test_operator_bridge_tracks_flow_draft_lifecycle(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    draft = {"flow_name": "测试", "expanded_steps": []}

    stored = bridge.set_flow_draft(draft, thread_id="session-1")

    state = bridge.session_state("session-1")
    assert stored == draft
    assert stored is not draft
    assert state.mode == "editing_flow"
    assert state.current_flow_draft["flow_name"] == "测试"

    bridge.clear_flow_draft(thread_id="session-1")

    cleared = bridge.session_state("session-1")
    assert cleared.mode == "idle"
    assert cleared.current_flow_draft == {}


def test_operator_bridge_clear_flow_draft_preserves_waiting_confirm_mode(tmp_path):
    class Plan:
        raw_text = "X100"

    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    bridge.set_pending_confirm(Plan(), thread_id="session-1", expires_at=70.0)
    bridge.set_flow_draft({"flow_name": "测试"}, thread_id="session-1")

    bridge.clear_flow_draft(thread_id="session-1")

    state = bridge.session_state("session-1")
    assert state.mode == "waiting_confirm"
    assert state.pending_confirm
    assert state.current_flow_draft == {}


def test_operator_bridge_handle_text_uses_runtime_without_fallback(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    fallback_calls = []

    class Runtime:
        def handle(self, text, *, session_state):
            assert text == "你是谁"
            assert session_state.thread_id == "session-1"
            return AgentOrchestratorResult(kind="chat_answer", message="runtime answer")

    bridge._tool_calling_runtime = Runtime()

    result = bridge.handle_text(
        "你是谁",
        thread_id="session-1",
        legacy_fallback=lambda text: fallback_calls.append(text),
    )

    assert result.kind == "chat_answer"
    assert result.message == "runtime answer"
    assert fallback_calls == []


def test_operator_bridge_records_tool_result_from_runtime_payload(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    class Runtime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="chat_answer",
                message="runtime answer",
                payload={
                    "tool_name": "explain_text",
                    "tool_result": {
                        "ok": True,
                        "state": "chat_explained",
                        "message": "runtime answer",
                        "data": {},
                        "errors": [],
                    },
                },
            )

    bridge._tool_calling_runtime = Runtime()

    bridge.handle_text(
        "你是谁",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert state.last_tool_call["tool_name"] == "explain_text"
    assert state.last_tool_call["result"]["state"] == "chat_explained"


def test_operator_bridge_records_pending_confirm_tool_result_into_session_state(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    class Runtime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="confirm_plan",
                message="等待确认。",
                payload={
                    "tool_name": "create_pending_confirm",
                    "tool_result": {
                        "ok": True,
                        "state": "waiting_confirmation",
                        "message": "等待确认。",
                        "data": {
                            "draft_id": "draft-1",
                            "status": "waiting_confirmation",
                            "expires_at": 70.0,
                            "confirmation_text": "确认执行？",
                        },
                        "errors": [],
                    },
                },
            )

    bridge._tool_calling_runtime = Runtime()

    bridge.handle_text(
        "等待2秒",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert state.mode == "waiting_confirm"
    assert state.pending_confirm["draft_id"] == "draft-1"
    assert state.pending_confirm["expires_at"] == 70.0


def test_operator_bridge_clears_pending_confirm_after_confirm_or_cancel_tool_result(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    bridge.set_session_state(
        bridge.session_state("session-1")
        .with_pending_execution({"draft_id": "draft-1", "intent": "delay_blocking"})
        .with_pending_confirm({"draft_id": "draft-1", "expires_at": 70.0})
    )

    class Runtime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="confirm_plan",
                message="已确认。",
                payload={
                    "tool_name": "confirm_pending_plan",
                    "tool_result": {
                        "ok": True,
                        "state": "confirmed",
                        "message": "已确认。",
                        "data": {
                            "draft_id": "draft-1",
                            "query_record": {
                                "query_key": "agent:draft-1",
                                "func_num": 109,
                                "params": {"delay_sec": 2.0},
                                "description": "Agent draft",
                            },
                        },
                        "errors": [],
                    },
                },
            )

    bridge._tool_calling_runtime = Runtime()

    bridge.handle_text(
        "确认",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert state.mode == "idle"
    assert state.pending_confirm == {}
    assert state.pending_execution == {}


def test_operator_bridge_default_local_runtime_records_tool_result(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)

    result = bridge.handle_text(
        "你是谁",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert result.kind == "chat_answer"
    assert state.last_tool_call["tool_name"] == "explain_text"
    assert state.last_tool_call["result"]["state"] == "chat_explained"


def test_operator_bridge_default_local_runtime_records_compound_tool_result(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)

    result = bridge.handle_text(
        "走到X1000，然后等待2秒",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert result.kind == "compound_plan_draft"
    assert state.last_tool_call["tool_name"] == "plan_compound_command"
    assert state.last_tool_call["result"]["state"] == "compound_plan_draft"
    assert state.mode == "editing_flow"
    assert state.current_compound_plan["plan_id"]
    assert state.current_compound_plan["steps"] == ["走到X1000", "等待2秒"]


def test_operator_bridge_default_local_runtime_records_command_draft_into_pending_execution(tmp_path):
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        controller_snapshot_provider=_snapshot,
    )

    result = bridge.handle_text(
        "移动到 X 一百，Y 0，Z 100，速度 50",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert result.kind == "restricted_agent"
    assert state.mode == "waiting_confirm"
    assert state.current_intent == "move_linear"
    assert state.pending_execution["draft_id"]
    assert state.pending_execution["params"]["target_x"] == 100.0


def test_operator_bridge_default_local_runtime_creates_pending_confirm_when_dependencies_available(tmp_path):
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {"valid": True, "status": "pass", "summary": "L1通过。", "items": []}

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        controller_snapshot_provider=_snapshot,
        safety_review_agent_provider=lambda: ReviewAgent(),
        runtime_snapshot_provider=lambda: {"motion": {"running_state": "idle"}},
        start_pose_provider=lambda: (0, 0, 0, 0, 0, 0),
        confirmation_agent_provider=lambda: ConfirmationAgent(timeout_sec=60),
        clock=lambda: 10.0,
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )

    result = bridge.handle_text(
        "移动到 X 一百，Y 0，Z 100，速度 50",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert result.kind == "confirm_plan"
    assert state.mode == "waiting_confirm"
    assert state.pending_confirm["draft_id"]
    assert state.pending_confirm["confirmation_text"]
    assert state.pending_execution["params"]["target_x"] == 100.0
    assert state.pending_execution["precheck_result"]["valid"] is True


def test_operator_bridge_default_local_runtime_keeps_flow_name_clarification_state(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)

    first = bridge.handle_text(
        "你好，我先创建一个新的流程。",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )
    state_after_first = bridge.session_state("session-1")

    second = bridge.handle_text(
        "现在流程名字叫测试。",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )
    state_after_second = bridge.session_state("session-1")

    assert first.kind == "flow_draft"
    assert state_after_first.mode == "clarifying"
    assert state_after_first.current_intent == "create_flow"
    assert state_after_first.pending_missing_fields == ("flow_name",)
    assert second.kind == "flow_draft"
    assert state_after_second.mode == "editing_flow"
    assert state_after_second.current_flow_draft["flow_name"] == "测试"
    assert state_after_second.pending_missing_fields == ()


def test_operator_bridge_default_local_runtime_keeps_flow_step_clarification_state(tmp_path):
    service = ExecutionPlanService()
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        execution_plan_service_provider=lambda: service,
        controller_snapshot_provider=_snapshot,
    )

    bridge.handle_text("你好，我先创建一个新的流程。", thread_id="session-1", legacy_fallback=lambda text: None)
    bridge.handle_text("现在流程名字叫测试。", thread_id="session-1", legacy_fallback=lambda text: None)
    append_result = bridge.handle_text("添加第一步是移动到位置 A。", thread_id="session-1", legacy_fallback=lambda text: None)
    state_after_append = bridge.session_state("session-1")
    answer_result = bridge.handle_text(
        "我觉得坐标是 X 一百， Y 0， Z 100，速度 50。",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )
    state_after_answer = bridge.session_state("session-1")

    assert append_result.kind == "flow_draft"
    assert state_after_append.mode == "clarifying"
    assert state_after_append.pending_missing_fields == ("target_pose",)
    assert answer_result.kind == "flow_draft"
    assert state_after_answer.mode == "editing_flow"
    assert state_after_answer.pending_missing_fields == ()
    step = state_after_answer.current_flow_draft["expanded_steps"][0]
    assert step["params"]["target_x"] == 100.0
    assert step["params"]["spd_pct"] == 50.0


def test_operator_bridge_default_local_runtime_handles_spoken_flow_log_sequence(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    plan_service = ExecutionPlanService()
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        flow_service_provider=lambda: service,
        execution_plan_service_provider=lambda: plan_service,
        controller_snapshot_provider=_snapshot,
    )

    first = bridge.handle_text("我想创建流程", thread_id="session-1", legacy_fallback=lambda text: None)
    named = bridge.handle_text("流程名称叫测试", thread_id="session-1", legacy_fallback=lambda text: None)
    appended = bridge.handle_text("步骤一 移动到位置b", thread_id="session-1", legacy_fallback=lambda text: None)
    answered = bridge.handle_text(
        "位置B X475 Y0 Z545 RX0 RY0 RZ0，速度30%",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )
    edited = bridge.handle_text("那就改成20%", thread_id="session-1", legacy_fallback=lambda text: None)
    saved = bridge.handle_text("确认保存", thread_id="session-1", legacy_fallback=lambda text: None)

    assert first.kind == "flow_draft"
    assert first.message
    assert named.payload["tool_name"] == "set_flow_name"
    assert appended.payload["tool_name"] == "append_flow_step"
    assert appended.payload["tool_result"]["state"] == "flow_draft_needs_clarification"
    assert answered.payload["tool_name"] == "answer_flow_clarification"
    assert answered.payload["tool_result"]["state"] == "flow_draft_updated"
    step = answered.payload["tool_result"]["data"]["draft"]["expanded_steps"][0]
    assert step["params"]["target_x"] == 475.0
    assert step["params"]["target_z"] == 545.0
    assert step["params"]["spd_pct"] == 30.0
    assert edited.payload["tool_name"] == "edit_flow_draft_params"
    assert edited.payload["tool_result"]["state"] == "flow_draft_updated"
    edited_step = edited.payload["tool_result"]["data"]["draft"]["expanded_steps"][0]
    assert edited_step["params"]["spd_pct"] == 20.0
    assert saved.payload["tool_name"] == "save_flow_draft"
    assert saved.payload["tool_result"]["state"] == "flow_draft_saved"
    assert service.get_flow_entry("测试") is not None
    assert bridge.session_state("session-1").current_flow_draft == {}


def test_operator_bridge_default_local_runtime_saves_flow_and_clears_draft(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_available=False,
        flow_service_provider=lambda: service,
    )
    bridge.set_session_state(
        bridge.session_state("session-1").with_flow_draft(
            {
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
            }
        )
    )

    result = bridge.handle_text("保存流程", thread_id="session-1", legacy_fallback=lambda text: None)

    state = bridge.session_state("session-1")
    assert result.kind == "flow_draft"
    assert result.payload["tool_result"]["state"] == "flow_draft_saved"
    assert service.get_flow_entry("测试") is not None
    assert state.mode == "idle"
    assert state.current_flow_draft == {}


def test_operator_bridge_default_local_runtime_queries_current_flow_draft(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)
    bridge.set_session_state(
        bridge.session_state("session-1").with_flow_draft(
            {
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
            }
        )
    )

    result = bridge.handle_text("查看流程", thread_id="session-1", legacy_fallback=lambda text: None)

    state = bridge.session_state("session-1")
    assert result.kind == "flow_draft"
    assert result.payload["tool_name"] == "query_current_flow_draft"
    assert result.payload["tool_result"]["state"] == "flow_draft_loaded"
    assert state.mode == "editing_flow"
    assert state.current_flow_draft["flow_name"] == "测试"
    assert state.last_tool_call["tool_name"] == "query_current_flow_draft"


def test_operator_bridge_records_flow_draft_tool_result_into_session_state(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)

    class Runtime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="flow_draft_plan",
                message="请补充坐标。",
                payload={
                    "tool_name": "set_flow_draft",
                    "tool_result": {
                        "ok": False,
                        "state": "flow_draft_needs_clarification",
                        "message": "请补充坐标。",
                        "data": {
                            "flow_name": "测试流程",
                            "draft": {"flow_name": "测试流程", "expanded_steps": []},
                            "missing_fields": ["target_pose"],
                        },
                        "errors": [
                            {
                                "code": "FLOW_DRAFT_MISSING_PARAMS",
                                "message": "请补充坐标。",
                                "fields": ["target_pose"],
                            }
                        ],
                    },
                },
            )

    bridge._tool_calling_runtime = Runtime()

    bridge.handle_text(
        "创建测试流程",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert state.mode == "clarifying"
    assert state.current_flow_draft["flow_name"] == "测试流程"
    assert state.pending_missing_fields == ("target_pose",)


def test_operator_bridge_handle_text_fallback_uses_normalized_text(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path)
    candidate = bridge.memory_store().create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    )
    bridge.memory_store().approve_memory(candidate["memory_id"], reviewer="engineer")
    seen = []

    class Runtime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="fallback",
                payload={"fallback_required": True},
            )

    bridge._tool_calling_runtime = Runtime()

    result = bridge.handle_text(
        "移动到位置诶",
        thread_id="session-1",
        legacy_fallback=lambda text: seen.append(text) or AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    assert result.kind == "chat_answer"
    assert seen == ["移动到位置A"]
    assert bridge.session_state("session-1").last_normalized_text == "移动到位置A"


def test_operator_bridge_preserves_raw_and_normalized_text_in_runtime_payload(tmp_path):
    bridge = OperatorAgentRuntimeBridge(runtime_root=tmp_path, langchain_available=False)
    candidate = bridge.memory_store().create_candidate(
        kind="text_alias",
        key="为啥",
        value={"normalized": "为什么"},
    )
    bridge.memory_store().approve_memory(candidate["memory_id"], reviewer="engineer")

    result = bridge.handle_text(
        "为啥不能执行，建议怎么处理",
        thread_id="session-1",
        legacy_fallback=lambda text: None,
    )

    assert result.kind == "dashboard_query_action"
    assert result.payload["raw_text"] == "为啥不能执行，建议怎么处理"
    assert result.payload["normalized_text"] == "为什么不能执行，建议怎么处理"
    assert result.payload["applied_memories"][0]["memory_id"] == candidate["memory_id"]


def test_operator_bridge_can_use_injected_langchain_graph_app(tmp_path):
    class GraphApp:
        def invoke(self, payload):
            return {"kind": "chat_answer", "message": "graph answer"}

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_graph_app=GraphApp(),
        langchain_available=True,
    )

    result = bridge.handle_text(
        "你好",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    assert result.message == "graph answer"


def test_operator_bridge_persists_langgraph_session_state_payload(tmp_path):
    class GraphApp:
        def invoke(self, payload):
            state = dict(payload["session_state"])
            state["tool_call_history"] = {
                "call-1": {
                    "tool_name": "create_memory_candidate",
                    "result": {"ok": True, "state": "memory_candidate_created", "message": "ok", "data": {}, "errors": []},
                    "replay_count": 1,
                }
            }
            return {
                "kind": "memory_tool_result",
                "message": "ok",
                "payload": {"session_state": state},
            }

    bridge = OperatorAgentRuntimeBridge(
        runtime_root=tmp_path,
        langchain_graph_app=GraphApp(),
        langchain_available=True,
    )

    bridge.handle_text(
        "记住位置诶就是位置A",
        thread_id="session-1",
        legacy_fallback=lambda text: AgentOrchestratorResult(kind="chat_answer", message="legacy"),
    )

    state = bridge.session_state("session-1")
    assert state.tool_call_history["call-1"]["replay_count"] == 1
