import json

from robot_modbus_lite.interaction_archiver import InteractionArchiveWriter
from robot_modbus_lite.json_schema import validate_interaction_record


def test_interaction_archive_writer_appends_type_b_jsonl_record(tmp_path):
    path = tmp_path / "session_interactions.jsonl"
    writer = InteractionArchiveWriter(path=path, session_id="session-1", clock=lambda: "2026-05-20T10:30:00.000+08:00")

    record = writer.append_input_record(
        source="text",
        raw_text="移动到安全点",
        device_snapshot={"system_status": {"ready": True, "alarm": False, "estop": False}},
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    assert record.msg_id == payload["msg_id"]
    assert payload["msg_type"] == "interaction_record"
    assert payload["session_id"] == "session-1"
    assert payload["input"]["raw_text"] == "移动到安全点"
    assert payload["nlp_result"]["intent"] == "pending"
    assert validate_interaction_record(payload) is None


def test_interaction_archive_writer_uses_distinct_record_ids(tmp_path):
    writer = InteractionArchiveWriter(path=tmp_path / "session_interactions.jsonl", session_id="session-1")

    first = writer.append_input_record(source="text", raw_text="A")
    second = writer.append_input_record(source="text", raw_text="B")

    assert first.msg_id != second.msg_id


def test_interaction_archive_writer_updates_nlp_result_in_existing_record(tmp_path):
    path = tmp_path / "session_interactions.jsonl"
    writer = InteractionArchiveWriter(path=path, session_id="session-1")
    record = writer.append_input_record(source="text", raw_text="移动")

    updated = writer.update_nlp_result(
        record.msg_id,
        {
            "semantic_level": 3,
            "intent": "command",
            "func_id": 108,
            "params": {"target_x": 1.0},
            "confidence": 0.8,
            "engine": "rule",
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["msg_id"] == record.msg_id
    assert payload["nlp_result"]["func_id"] == 108
    assert payload["nlp_result"]["params"]["target_x"] == 1.0
    assert validate_interaction_record(payload) is None
