from robot_modbus_lite.agent_runtime.archive_finalizer import (
    build_non_execution_detail,
    build_non_execution_nlp_payload,
    finalize_non_execution_nlp,
    should_finalize_pending_nlp,
)


def test_should_finalize_pending_nlp_accepts_empty_or_pending_payload():
    assert should_finalize_pending_nlp({}) is True
    assert should_finalize_pending_nlp({"engine": "pending", "intent": "chat"}) is True
    assert should_finalize_pending_nlp({"engine": "local", "intent": "pending"}) is True
    assert should_finalize_pending_nlp({"engine": "local", "intent": "chat"}) is False


def test_build_non_execution_nlp_payload_maps_chat_intents():
    payload = build_non_execution_nlp_payload("streaming_chat")

    assert payload["engine"] == "streaming_chat"
    assert payload["intent"] == "chat"
    assert payload["action_type"] == "chat"
    assert payload["confidence"] == 1.0


def test_build_non_execution_nlp_payload_preserves_non_chat_label():
    payload = build_non_execution_nlp_payload("flow_draft")

    assert payload["engine"] == "flow_draft"
    assert payload["intent"] == "flow_draft"
    assert payload["action_type"] == "non_execution"


def test_build_non_execution_detail_marks_skipped_side_effects():
    assert build_non_execution_detail("clarification") == {
        "modbus_write": {},
        "non_execution_result": "clarification",
    }


def test_finalize_non_execution_nlp_updates_only_pending_records():
    calls = []

    class Writer:
        def update_nlp_result(self, msg_id, payload):
            calls.append((msg_id, payload))

    updated = finalize_non_execution_nlp(
        Writer(),
        msg_id="interaction-1",
        result="chat",
        current_nlp={"engine": "pending", "intent": "pending"},
    )
    skipped = finalize_non_execution_nlp(
        Writer(),
        msg_id="interaction-2",
        result="chat",
        current_nlp={"engine": "agent", "intent": "chat"},
    )

    assert updated is True
    assert skipped is False
    assert calls[0][0] == "interaction-1"
    assert calls[0][1]["engine"] == "chat"
