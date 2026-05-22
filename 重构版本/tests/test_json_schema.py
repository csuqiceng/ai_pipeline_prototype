from robot_modbus_lite.dashboard import DashboardSnapshot
from robot_modbus_lite.json_schema import (
    CommandIntent,
    DeviceSnapshot,
    InteractionRecord,
    validate_command_intent,
    validate_device_snapshot,
    validate_interaction_record,
)


def test_command_intent_exports_v21_type_a_shape():
    intent = CommandIntent(
        msg_id="intent-1",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
        raw_text="J1转到30度",
        semantic_level=3,
        intent="command",
        func_id=106,
        confidence=0.95,
        params={"axis_no": 0, "pos_val": 30.0},
        fuzzy={"pos": 0, "spd": 0, "acc": 0, "dec": 0},
        emergency_code=None,
        is_emergency=False,
        priority="normal",
    )

    data = intent.to_dict()

    assert data["msg_type"] == "command_intent"
    assert data["semantic_level"] == 3
    assert data["params"]["pos_val"] == 30.0
    assert validate_command_intent(data) is None


def test_command_intent_validation_reports_missing_required_field():
    assert validate_command_intent({"msg_type": "command_intent"}) == "command_intent 缺少字段: msg_id"


def test_interaction_record_exports_v21_type_b_shape():
    record = InteractionRecord(
        msg_id="interaction-1",
        session_id="session-1",
        timestamp_start="2026-05-20T10:30:00.000+08:00",
        timestamp_end="2026-05-20T10:30:01.500+08:00",
        duration_ms=1500,
        input={"source": "text", "raw_text": "J1转到30度", "asr_confidence": None},
        nlp_result={"semantic_level": 3, "intent": "command", "func_id": 106, "params": {}, "confidence": 0.95},
        safety_check={"pc_precheck": "pass", "warnings": []},
        execution={"result": "success", "exec_duration_ms": 1200},
        response={"ack": "收到", "ack_delay_ms": 12, "final": "完成", "final_delay_ms": 1215},
        device_snapshot={"system_status": {"ready": True, "alarm": False, "estop": False}},
    )

    data = record.to_dict()

    assert data["msg_type"] == "interaction_record"
    assert data["duration_ms"] == 1500
    assert data["response"]["final"] == "完成"
    assert validate_interaction_record(data) is None


def test_device_snapshot_exports_v21_type_c_shape_from_dashboard_snapshot():
    dashboard = DashboardSnapshot(
        ts="2026-05-20T10:30:00.050+08:00",
        position={"joints": (1, 2, 3, 4, 5, 6), "x": 10, "y": 20, "z": 30, "r": 40},
        safety={"estop": False, "paused": False, "alarm_active": True, "alarm_code": "ERR_1"},
        motion={"current_func": "FUNC108", "speed": "50%"},
        connection={"realtime_feedback": "online"},
    )

    snapshot = DeviceSnapshot.from_dashboard_snapshot(dashboard, dashboard_type="status", refresh_ms=500).to_dict()

    assert snapshot["msg_type"] == "device_snapshot"
    assert snapshot["dashboard_type"] == "status"
    assert snapshot["refresh_ms"] == 500
    assert snapshot["data"]["dpos_j"] == [1, 2, 3, 4, 5, 6]
    assert snapshot["data"]["alarm"] is True
    assert validate_device_snapshot(snapshot) is None


def test_device_snapshot_defaults_to_dashboard_refresh_interval():
    dashboard = DashboardSnapshot(
        ts="2026-05-20T10:30:00.050+08:00",
        refresh_ms=50,
        position={},
        safety={},
        motion={},
        connection={},
    )

    snapshot = DeviceSnapshot.from_dashboard_snapshot(dashboard).to_dict()

    assert snapshot["refresh_ms"] == 50


def test_device_snapshot_validation_reports_wrong_msg_type():
    assert validate_device_snapshot({"msg_type": "command_intent"}) == "device_snapshot.msg_type 必须为 device_snapshot"
