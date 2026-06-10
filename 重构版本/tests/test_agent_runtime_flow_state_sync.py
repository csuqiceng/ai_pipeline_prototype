from robot_modbus_lite.agent_runtime.flow_state_sync import sync_pending_flow_draft


def test_sync_pending_flow_draft_stores_dict_copy_and_updates_session_state():
    calls = []

    class Bridge:
        def set_flow_draft(self, draft, *, thread_id):
            calls.append(("set", draft, thread_id))
            return draft

        def clear_flow_draft(self, *, thread_id):
            calls.append(("clear", thread_id))

    draft = {"flow_name": "测试", "expanded_steps": []}

    stored = sync_pending_flow_draft(
        Bridge(),
        thread_id="session-1",
        draft=draft,
    )

    assert stored == draft
    assert stored is not draft
    assert calls == [("set", stored, "session-1")]


def test_sync_pending_flow_draft_clears_session_state_for_none():
    calls = []

    class Bridge:
        def set_flow_draft(self, draft, *, thread_id):
            calls.append(("set", draft, thread_id))

        def clear_flow_draft(self, *, thread_id):
            calls.append(("clear", thread_id))

    stored = sync_pending_flow_draft(
        Bridge(),
        thread_id="session-1",
        draft=None,
    )

    assert stored is None
    assert calls == [("clear", "session-1")]


def test_sync_pending_flow_draft_clears_session_state_for_non_dict_compat_value():
    calls = []
    marker = object()

    class Bridge:
        def set_flow_draft(self, draft, *, thread_id):
            calls.append(("set", draft, thread_id))

        def clear_flow_draft(self, *, thread_id):
            calls.append(("clear", thread_id))

    stored = sync_pending_flow_draft(
        Bridge(),
        thread_id="session-1",
        draft=marker,
    )

    assert stored is marker
    assert calls == [("clear", "session-1")]
