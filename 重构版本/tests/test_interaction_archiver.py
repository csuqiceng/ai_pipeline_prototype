import json

from robot_modbus_lite.dialog_logger import DialogLogger
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


def test_interaction_archive_writer_can_mirror_daily_dialog(tmp_path):
    writer = InteractionArchiveWriter(
        path=tmp_path / "session_interactions.jsonl",
        session_id="session-1",
        dialog_logger=DialogLogger(tmp_path / "dialog"),
    )

    writer.append_input_record(source="text", raw_text="小正，状态")

    files = list((tmp_path / "dialog").glob("dialog_*.jsonl"))
    assert len(files) == 1
    assert "小正，状态" in files[0].read_text(encoding="utf-8")


def test_interaction_archive_writer_writes_complete_dialogue_record(tmp_path):
    path = tmp_path / "session_interactions.jsonl"
    dialogue_path = tmp_path / "dialogue_session_session-1.jsonl"
    writer = InteractionArchiveWriter(
        path=path,
        dialogue_path=dialogue_path,
        session_id="session-1",
        clock=lambda: "2026-06-10T20:46:47.041",
    )

    record = writer.append_input_record(
        source="text",
        raw_text="小正，移动到位置A",
        normalized_text="小正，移动到位置A",
        input_event={
            "time": "20:46:46.996",
            "ts": "2026-06-10T20:46:46.996",
            "session_id": "session-1",
            "host": "10.168.3.21",
            "controller_mode": "real",
            "thread": "MainThread",
            "category": "自然语言",
            "action": "用户输入",
            "result": "收到",
            "detail": "小正，移动到位置A",
        },
    )
    writer.update_record(
        record.msg_id,
        {
            "execution": {"result": "skipped", "non_execution_result": "clarification"},
            "response": {"ack": "收到，正在处理。", "ack_delay_ms": 0, "final": "请明确位置A的坐标。", "final_delay_ms": 45},
            "_dialogue_response_event": {
                "time": "20:46:47.041",
                "ts": "2026-06-10T20:46:47.041",
                "session_id": "session-1",
                "host": "10.168.3.21",
                "controller_mode": "real",
                "thread": "MainThread",
                "category": "自然语言",
                "action": "澄清提示",
                "result": "提示",
                "detail": "请明确位置A的坐标。",
            },
        },
    )

    payload = json.loads(dialogue_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["msg_type"] == "dialogue_record"
    assert payload["session_id"] == "session-1"
    assert payload["seq"] == 1
    assert payload["host"] == "10.168.3.21"
    assert payload["category"] == "自然语言"
    assert payload["action"] == "澄清提示"
    assert payload["result"] == "提示"
    assert payload["detail"] == "请明确位置A的坐标。"
    assert payload["user"]["raw_text"] == "小正，移动到位置A"
    assert payload["user"]["normalized_text"] == "小正，移动到位置A"
    assert payload["assistant"]["final_text"] == "请明确位置A的坐标。"
    assert payload["response"]["final"] == "请明确位置A的坐标。"
