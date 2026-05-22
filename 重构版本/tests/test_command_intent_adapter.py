from robot_modbus_lite.command_intent_adapter import command_intent_from_plan
from robot_modbus_lite.json_schema import validate_command_intent
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


def test_command_intent_from_template_plan_uses_query_record_params():
    table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "spd_pct": 20.0},
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    intent = command_intent_from_plan(
        plan,
        table=table,
        msg_id="intent-1",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["msg_type"] == "command_intent"
    assert intent["intent"] == "command"
    assert intent["semantic_level"] == 3
    assert intent["func_id"] == 108
    assert intent["params"]["target_x"] == 1.0
    assert intent["priority"] == "normal"
    assert validate_command_intent(intent) is None


def test_command_intent_from_atomic_template_plan_uses_atomic_record_params():
    record = QueryRecord(
        query_key="atomic:virtual:8:1:3",
        func_num=107,
        params={"axis_no": 8, "pos_val": 3.0, "spd_pct": 50.0, "fuzzy_pos": 1},
    )
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", record.query_key, "atomic_rule", "上升3毫米", "原子动作"),),
        source="atomic_rule",
        raw_text="小正，上升3毫米",
        reason="原子动作",
        semantic_level=3,
        semantic_label="常规生产执行层",
        atomic_records={record.query_key: record},
    )

    intent = command_intent_from_plan(
        plan,
        table={},
        msg_id="intent-atomic",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["intent"] == "command"
    assert intent["semantic_level"] == 3
    assert intent["func_id"] == 107
    assert intent["params"]["axis_no"] == 8
    assert intent["params"]["pos_val"] == 3.0
    assert intent["fuzzy"]["pos"] == 1
    assert validate_command_intent(intent) is None


def test_command_intent_from_estop_system_plan_marks_emergency():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("system", "sys_estop", "rule", "急停 A1B2 急停", "测试"),),
        source="rule",
        raw_text="急停 A1B2 急停",
        reason="测试",
    )

    intent = command_intent_from_plan(
        plan,
        table={},
        msg_id="intent-2",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["semantic_level"] == 5
    assert intent["func_id"] == 104
    assert intent["params"] == {"action_key": "sys_estop"}
    assert intent["is_emergency"] is True
    assert intent["priority"] == "high"
    assert validate_command_intent(intent) is None


def test_command_intent_from_cancel_system_plan_is_normal_priority_func104():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("system", "sys_cancel", "rule", "取消当前任务", "测试"),),
        source="rule",
        raw_text="取消当前任务",
        reason="测试",
    )

    intent = command_intent_from_plan(
        plan,
        table={},
        msg_id="intent-cancel",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["func_id"] == 104
    assert intent["semantic_level"] == 4
    assert intent["params"] == {"action_key": "sys_cancel"}
    assert intent["is_emergency"] is False
    assert intent["priority"] == "normal"
    assert validate_command_intent(intent) is None


def test_command_intent_from_unknown_plan_keeps_unknown_intent():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("unknown", None, "rule", "不知道", "未识别"),),
        source="rule",
        raw_text="不知道",
        reason="未识别",
    )

    intent = command_intent_from_plan(
        plan,
        table={},
        msg_id="intent-3",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["intent"] == "unknown"
    assert intent["semantic_level"] == 0
    assert intent["func_id"] is None
    assert intent["confidence"] == 0.0


def test_command_intent_prefers_plan_semantic_level_when_present():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("unknown", None, "rule", "你好", "闲聊"),),
        source="rule",
        raw_text="你好",
        reason="闲聊",
        semantic_level=1,
        semantic_label="闲聊咨询层",
    )

    intent = command_intent_from_plan(
        plan,
        table={},
        msg_id="intent-chat",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["semantic_level"] == 1
    assert intent["intent"] == "chat"
    assert intent["confidence"] == 0.5


def test_command_intent_from_query_plan_marks_l2_query():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "communication_faults", "rule", "通讯正常吗", "命中看板查询"),),
        source="rule",
        raw_text="通讯正常吗",
        reason="命中看板查询",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    intent = command_intent_from_plan(
        plan,
        table={},
        msg_id="intent-query",
        timestamp="2026-05-20T10:30:00.000+08:00",
        source="text",
    ).to_dict()

    assert intent["semantic_level"] == 2
    assert intent["intent"] == "query"
    assert intent["func_id"] is None
    assert intent["params"] == {"board_key": "communication_faults"}
    assert intent["confidence"] == 0.75
    assert validate_command_intent(intent) is None
