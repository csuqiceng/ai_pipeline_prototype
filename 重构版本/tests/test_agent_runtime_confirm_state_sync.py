from robot_modbus_lite.agent_runtime.confirm_state_sync import sync_pending_confirm_plan


class Bridge:
    def __init__(self):
        self.calls = []

    def set_pending_confirm(self, plan, *, thread_id, expires_at):
        self.calls.append(("set", plan, thread_id, expires_at))
        return {"plan": plan, "thread_id": thread_id, "expires_at": expires_at}

    def clear_pending_confirm(self, *, thread_id):
        self.calls.append(("clear", thread_id))
        return None


def test_sync_pending_confirm_plan_sets_plan_and_deadline():
    bridge = Bridge()
    plan = object()

    result = sync_pending_confirm_plan(
        bridge,
        thread_id="session-1",
        plan=plan,
        now_seconds=lambda: 100.0,
        timeout_seconds=lambda: 30.0,
    )

    assert result.plan is plan
    assert result.deadline_sec == 130.0
    assert bridge.calls == [("set", plan, "session-1", 130.0)]


def test_sync_pending_confirm_plan_clears_plan_and_deadline():
    bridge = Bridge()

    result = sync_pending_confirm_plan(
        bridge,
        thread_id="session-1",
        plan=None,
        now_seconds=lambda: 100.0,
        timeout_seconds=lambda: 30.0,
    )

    assert result.plan is None
    assert result.deadline_sec == 0.0
    assert bridge.calls == [("clear", "session-1")]


def test_sync_pending_confirm_plan_uses_operator_thread_fallback():
    bridge = Bridge()
    plan = object()

    result = sync_pending_confirm_plan(
        bridge,
        thread_id="",
        plan=plan,
        now_seconds=lambda: 1.0,
        timeout_seconds=lambda: 2.0,
    )

    assert result.deadline_sec == 3.0
    assert bridge.calls == [("set", plan, "operator-ui", 3.0)]
