from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent_tools.tool_result import ToolResult


def test_session_state_defaults_to_idle_and_serializes():
    state = SessionState(thread_id="session-1")

    assert state.mode == "idle"
    assert state.to_dict()["thread_id"] == "session-1"
    assert state.to_dict()["pending_missing_fields"] == []
    assert state.to_dict()["pending_confirm"] == {}


def test_session_state_records_tool_result_and_missing_fields():
    state = SessionState(thread_id="session-1")
    result = ToolResult.failure(
        state="missing_params",
        message="缺少坐标。",
        code="MISSING_REQUIRED_PARAMS",
        fields=["target_x", "target_y"],
    )

    updated = state.with_tool_result(
        tool_name="parse_command_params",
        tool_result=result,
        user_text="走到位置A",
        normalized_text="走到位置A",
    )

    assert updated.mode == "clarifying"
    assert updated.pending_missing_fields == ("target_x", "target_y")
    assert updated.last_tool_call["tool_name"] == "parse_command_params"
    assert updated.last_tool_call["result"]["state"] == "missing_params"


def test_session_state_cancel_pending_confirm_returns_to_editing_flow():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_flow_draft={"flow_name": "测试"},
        pending_confirm={"confirm_id": "confirm-1"},
    )

    updated = state.cancel_pending_plan()

    assert updated.mode == "editing_flow"
    assert updated.pending_confirm == {}
    assert updated.pending_execution == {}
    assert updated.current_flow_draft == {"flow_name": "测试"}


def test_session_state_cancel_pending_plan_clears_compound_execution_draft():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_flow_draft={"flow_name": "测试"},
        current_compound_plan={"plan_id": "compound-1", "steps": ["走到X1000"]},
        pending_confirm={"confirm_id": "confirm-1"},
        pending_execution={"draft_id": "draft-1"},
    )

    updated = state.cancel_pending_plan()

    assert updated.mode == "editing_flow"
    assert updated.current_flow_draft == {"flow_name": "测试"}
    assert updated.current_compound_plan == {}
    assert updated.pending_confirm == {}
    assert updated.pending_execution == {}


def test_session_state_tracks_flow_draft_lifecycle():
    state = SessionState(thread_id="session-1")

    editing = state.with_flow_draft({"flow_name": "测试", "expanded_steps": []})

    assert editing.mode == "editing_flow"
    assert editing.current_flow_draft["flow_name"] == "测试"

    cleared = editing.with_flow_draft(None)

    assert cleared.mode == "idle"
    assert cleared.current_flow_draft == {}


def test_session_state_tracks_pending_confirm_lifecycle():
    state = SessionState(thread_id="session-1")

    waiting = state.with_pending_confirm({"plan_id": "plan-1", "expires_at": 70.0})

    assert waiting.mode == "waiting_confirm"
    assert waiting.pending_confirm["plan_id"] == "plan-1"

    cleared = waiting.with_pending_confirm(None)

    assert cleared.mode == "idle"
    assert cleared.pending_confirm == {}


def test_session_state_tracks_pending_execution_lifecycle():
    state = SessionState(thread_id="session-1")

    waiting = state.with_pending_execution(
        {
            "draft_id": "draft-1",
            "intent": "move_linear",
            "func_id": 108,
            "params": {"target_x": 100.0},
        }
    )

    assert waiting.mode == "waiting_confirm"
    assert waiting.current_intent == "move_linear"
    assert waiting.pending_execution["draft_id"] == "draft-1"

    cleared = waiting.cancel_pending_plan()

    assert cleared.mode == "idle"
    assert cleared.pending_execution == {}


def test_session_state_expire_pending_confirm_clears_confirmation():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        pending_confirm={"confirm_id": "confirm-1"},
    )

    updated = state.expire_pending_confirm()

    assert updated.mode == "confirm_expired"
    assert updated.pending_confirm == {}


def test_session_state_record_execution_failure_clears_pending_state_and_preserves_failure():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        pending_confirm={"draft_id": "draft-1"},
        pending_execution={"draft_id": "draft-1", "intent": "delay_blocking"},
    )

    updated = state.record_execution_failure(
        query_record={"query_key": "agent:draft-1", "func_num": 109},
        error="controller write failed",
    )

    assert updated.mode == "execution_failed"
    assert updated.pending_confirm == {}
    assert updated.pending_execution == {}
    assert updated.last_tool_call["tool_name"] == "controller_execution"
    assert updated.last_tool_call["result"]["state"] == "execution_failed"
    assert updated.last_tool_call["result"]["data"]["query_record"]["func_num"] == 109
    assert updated.last_failed_execution["error"] == "controller write failed"
    assert updated.last_failed_execution["execution_context"]["draft_id"] == "draft-1"


def test_session_state_moves_pending_execution_to_confirmed_context_before_failure():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        pending_confirm={"draft_id": "draft-1"},
        pending_execution={"draft_id": "draft-1", "intent": "delay_blocking", "params": {"delay_sec": 2.0}},
    )

    confirmed = state.mark_pending_execution_confirmed()
    failed = confirmed.record_execution_failure(
        query_record={"query_key": "agent:draft-1", "func_num": 109},
        error="modbus write failed",
    )

    assert confirmed.pending_confirm == {}
    assert confirmed.pending_execution == {}
    assert confirmed.last_confirmed_execution["draft_id"] == "draft-1"
    assert failed.mode == "execution_failed"
    assert failed.last_failed_execution["execution_context"]["draft_id"] == "draft-1"
    assert failed.last_failed_execution["query_record"]["func_num"] == 109


def test_session_state_tracks_tool_call_id_idempotently():
    state = SessionState(thread_id="session-1")
    result = ToolResult.success(
        state="memory_candidate_created",
        message="已创建候选经验。",
        data={"memory_id": "mem-1"},
    )

    recorded = state.with_idempotent_tool_call(
        tool_call_id="call-1",
        tool_name="create_memory_candidate",
        tool_result=result,
    )

    assert recorded.get_idempotent_tool_result("call-1") == result.to_dict()
    assert recorded.get_idempotent_tool_result("missing") is None


def test_session_state_tracks_compound_plan_lifecycle():
    state = SessionState(thread_id="session-1")

    editing = state.with_compound_plan({"plan_id": "compound:test", "steps": ["走到X1000", "等待2秒"]})

    assert editing.mode == "editing_flow"
    assert editing.current_compound_plan["plan_id"] == "compound:test"

    cleared = editing.with_compound_plan(None)

    assert cleared.mode == "idle"
    assert cleared.current_compound_plan == {}


def test_session_state_advances_compound_step_to_next_step():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_compound_plan={
            "plan_id": "compound:test",
            "steps": ["走到X1000", "等待2秒"],
            "step_results": [
                {"kind": "waiting_confirmation", "text": "走到X1000"},
                {"kind": "waiting_confirmation", "text": "等待2秒"},
            ],
            "active_step_index": 0,
            "active_step": "走到X1000",
            "active_step_result": {"kind": "waiting_confirmation", "text": "走到X1000"},
            "status": "waiting_step_confirm",
        },
        pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
    )

    advanced = state.advance_compound_step(ok=True, reason="第1步完成")

    assert advanced.mode == "editing_flow"
    assert advanced.pending_confirm == {}
    assert advanced.current_compound_plan["status"] == "waiting_step_confirm"
    assert advanced.current_compound_plan["active_step_index"] == 1
    assert advanced.current_compound_plan["active_step"] == "等待2秒"
    assert advanced.current_compound_plan["completed_steps"] == [
        {"index": 0, "step": "走到X1000", "ok": True, "reason": "第1步完成"}
    ]


def test_session_state_completes_compound_plan_on_final_step():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_compound_plan={
            "plan_id": "compound:test",
            "steps": ["走到X1000"],
            "step_results": [{"kind": "waiting_confirmation", "text": "走到X1000"}],
            "active_step_index": 0,
            "active_step": "走到X1000",
            "active_step_result": {"kind": "waiting_confirmation", "text": "走到X1000"},
            "status": "waiting_step_confirm",
        },
        pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
    )

    completed = state.advance_compound_step(ok=True, reason="第1步完成")

    assert completed.mode == "idle"
    assert completed.pending_confirm == {}
    assert completed.current_compound_plan["status"] == "completed"
    assert completed.current_compound_plan["active_step_index"] is None
    assert completed.current_compound_plan["completed_steps"] == [
        {"index": 0, "step": "走到X1000", "ok": True, "reason": "第1步完成"}
    ]


def test_session_state_marks_compound_step_failed_without_advancing():
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_compound_plan={
            "plan_id": "compound:test",
            "steps": ["走到X1000", "等待2秒"],
            "step_results": [
                {"kind": "waiting_confirmation", "text": "走到X1000"},
                {"kind": "waiting_confirmation", "text": "等待2秒"},
            ],
            "active_step_index": 0,
            "active_step": "走到X1000",
            "active_step_result": {"kind": "waiting_confirmation", "text": "走到X1000"},
            "status": "waiting_step_confirm",
        },
        pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
    )

    failed = state.advance_compound_step(ok=False, reason="控制器报警")

    assert failed.mode == "blocked"
    assert failed.pending_confirm == {}
    assert failed.current_compound_plan["status"] == "failed"
    assert failed.current_compound_plan["active_step_index"] == 0
    assert failed.current_compound_plan["failed_step"] == {
        "index": 0,
        "step": "走到X1000",
        "ok": False,
        "reason": "控制器报警",
    }


def test_session_state_from_dict_restores_runtime_context():
    state = SessionState.from_dict(
        {
            "thread_id": "session-1",
            "mode": "waiting_confirm",
            "current_intent": "move",
            "current_flow_draft": {"flow_name": "测试"},
            "current_compound_plan": {"plan_id": "compound-1"},
            "pending_missing_fields": ["target_x"],
            "pending_confirm": {"draft_id": "draft-1"},
            "pending_execution": {"draft_id": "draft-1"},
            "last_tool_call": {"tool_name": "create_pending_confirm"},
            "tool_call_history": {"call-1": {"tool_name": "x"}},
            "last_interaction_id": "interaction-1",
            "last_agent_result": {"kind": "chat_answer"},
            "last_user_text": "位置诶",
            "last_normalized_text": "位置A",
            "applied_memories": [{"memory_id": "mem-1"}],
        }
    )

    assert state.thread_id == "session-1"
    assert state.mode == "waiting_confirm"
    assert state.current_compound_plan["plan_id"] == "compound-1"
    assert state.pending_missing_fields == ("target_x",)
    assert state.pending_confirm["draft_id"] == "draft-1"
    assert state.tool_call_history["call-1"]["tool_name"] == "x"
    assert state.applied_memories[0]["memory_id"] == "mem-1"
