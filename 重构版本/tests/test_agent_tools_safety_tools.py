from robot_modbus_lite.agent.confirmation import ConfirmationAgent
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent_tools.safety_tools import (
    cancel_pending_plan,
    confirm_pending_plan,
    create_pending_confirm,
    expire_pending_plan,
    query_pending_confirm,
    run_safety_precheck,
)


def _draft():
    return CommandDraft(
        draft_id="draft-1",
        func_id=109,
        intent="delay_blocking",
        params={"delay_sec": 2.0},
        param_sources={"delay_sec": "specified"},
        raw_text="等待2秒",
        confidence=0.95,
        precheck_result={"valid": True, "summary": "L1通过。"},
    )


def test_create_and_query_pending_confirm():
    agent = ConfirmationAgent(timeout_sec=60)

    created = create_pending_confirm(
        agent,
        _draft(),
        now=10.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )
    queried = query_pending_confirm(agent, "draft-1")

    assert created.ok is True
    assert created.state == "waiting_confirmation"
    assert created.data["draft_id"] == "draft-1"
    assert "确认" in created.data["confirmation_text"]
    assert queried.ok is True
    assert queried.data["status"] == "waiting_confirmation"


def test_create_pending_confirm_is_idempotent_for_existing_waiting_draft():
    agent = ConfirmationAgent(timeout_sec=60)

    first = create_pending_confirm(
        agent,
        _draft(),
        now=10.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )
    second = create_pending_confirm(
        agent,
        _draft(),
        now=20.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )

    assert second.ok is True
    assert second.state == "waiting_confirmation"
    assert second.data["draft_id"] == first.data["draft_id"]
    assert second.data["expires_at"] == first.data["expires_at"]


def test_confirm_pending_plan_consumes_waiting_session():
    agent = ConfirmationAgent(timeout_sec=60)
    create_pending_confirm(agent, _draft(), now=10.0, status_signature="status-1", safety_signature="safety-1")

    confirmed = confirm_pending_plan(
        agent,
        "draft-1",
        now=20.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )
    repeated = confirm_pending_plan(
        agent,
        "draft-1",
        now=21.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )

    assert confirmed.ok is True
    assert confirmed.state == "confirmed"
    assert confirmed.data["query_record"]["func_num"] == 109
    assert repeated.ok is False
    assert repeated.state == "confirm_rejected"


def test_create_pending_confirm_rejects_finished_draft_id():
    agent = ConfirmationAgent(timeout_sec=60)
    create_pending_confirm(agent, _draft(), now=10.0, status_signature="status-1", safety_signature="safety-1")
    confirm_pending_plan(
        agent,
        "draft-1",
        now=20.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )

    recreated = create_pending_confirm(
        agent,
        _draft(),
        now=30.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )

    assert recreated.ok is False
    assert recreated.state == "confirm_lifecycle_closed"


def test_cancel_pending_plan_rejects_session():
    agent = ConfirmationAgent(timeout_sec=60)
    create_pending_confirm(agent, _draft(), now=10.0, status_signature="status-1", safety_signature="safety-1")

    cancelled = cancel_pending_plan(agent, "draft-1")
    queried = query_pending_confirm(agent, "draft-1")

    assert cancelled.ok is True
    assert cancelled.state == "cancelled"
    assert queried.ok is True
    assert queried.data["status"] == "rejected"


def test_expire_pending_plan_marks_waiting_session_expired():
    agent = ConfirmationAgent(timeout_sec=60)
    create_pending_confirm(agent, _draft(), now=10.0, status_signature="status-1", safety_signature="safety-1")

    expired = expire_pending_plan(agent, "draft-1")
    queried = query_pending_confirm(agent, "draft-1")
    repeated_confirm = confirm_pending_plan(
        agent,
        "draft-1",
        now=20.0,
        status_signature="status-1",
        safety_signature="safety-1",
    )

    assert expired.ok is True
    assert expired.state == "expired"
    assert queried.ok is True
    assert queried.data["status"] == "expired"
    assert repeated_confirm.ok is False
    assert repeated_confirm.state == "confirm_rejected"


def test_run_safety_precheck_reuses_safety_review_agent_for_safe_draft():
    class ReviewAgent:
        def __init__(self):
            self.calls = []

        def review(self, draft, *, snapshot, start_pose=None):
            self.calls.append((draft.draft_id, snapshot, start_pose))
            return {
                "valid": True,
                "status": "pass",
                "summary": "L1通过。",
                "items": [],
                "robot_safety": {"safe": True, "position_ok": True, "ik_ok": True, "pose_ok": True},
            }

    agent = ReviewAgent()

    result = run_safety_precheck(
        agent,
        _draft(),
        snapshot={"motion": {"running_state": "idle"}},
        start_pose=(0, 0, 0, 0, 0, 0),
    )

    assert result.ok is True
    assert result.state == "safety_precheck_passed"
    assert result.data["draft_id"] == "draft-1"
    assert result.data["precheck"]["valid"] is True
    assert result.data["robot_safety"]["ik_ok"] is True
    assert agent.calls == [("draft-1", {"motion": {"running_state": "idle"}}, (0, 0, 0, 0, 0, 0))]


def test_run_safety_precheck_returns_failure_for_blocking_review():
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {
                "valid": False,
                "status": "fail",
                "blocking_level": "L1",
                "summary": "L1预检未通过。",
                "items": [{"id": "target_x_range", "status": "fail"}],
            }

    result = run_safety_precheck(ReviewAgent(), _draft(), snapshot={})

    assert result.ok is False
    assert result.state == "safety_precheck_failed"
    assert result.errors[0]["code"] == "SAFETY_PRECHECK_FAILED"
    assert result.data["precheck"]["blocking_level"] == "L1"


def test_run_safety_precheck_accepts_draft_dict_payload():
    class ReviewAgent:
        def review(self, draft, *, snapshot, start_pose=None):
            return {"valid": True, "status": "pass", "summary": "L1通过。", "items": []}

    draft_payload = {
        "draft_id": "draft-dict",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
        "param_sources": {"delay_sec": "specified"},
        "raw_text": "等待2秒",
        "confidence": 0.95,
    }

    result = run_safety_precheck(ReviewAgent(), draft_payload, snapshot={})

    assert result.ok is True
    assert result.data["draft_id"] == "draft-dict"
