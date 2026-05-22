from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


def test_voice_nlp_adapter_parses_cancel_current_action_as_system_cancel():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("小正，停止当前动作")

    assert plan.actions[0].action_type == "system"
    assert plan.actions[0].target == "sys_cancel"


def test_voice_nlp_adapter_rejects_production_command_without_wake_word():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("停止当前动作")

    assert plan.actions[0].action_type == "unknown"
    assert "小正" in plan.reason


def test_voice_nlp_adapter_accepts_wake_word_homophones():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("小郑，暂停")

    assert plan.actions[0].action_type == "system"
    assert plan.actions[0].target == "sys_pause"


def test_voice_nlp_adapter_marks_template_command_as_l3_production_execution():
    from robot_modbus_lite.models import QueryRecord

    adapter = VoiceNlpAdapter(
        table={
            "位置A": QueryRecord(
                query_key="位置A",
                func_num=108,
                params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0},
            )
        },
        flow_names=(),
    )

    plan = adapter.parse("小正，去位置A")

    assert plan.semantic_level == 3
    assert plan.semantic_label == "常规生产执行层"
    assert plan.response_deadline_ms == 2000
    assert plan.requires_precheck is True
    assert plan.requires_confirmation is True


def test_voice_nlp_adapter_marks_system_management_as_l4():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("小正，暂停")

    assert plan.semantic_level == 4
    assert plan.semantic_label == "系统管理层"
    assert plan.response_deadline_ms == 2000
    assert plan.requires_precheck is False


def test_voice_nlp_adapter_marks_coded_emergency_as_l5():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("急停 A1B2 急停")

    assert plan.semantic_level == 5
    assert plan.semantic_label == "应急安全层"
    assert plan.response_deadline_ms == 100
    assert plan.priority == "high"


def test_voice_nlp_adapter_marks_non_command_text_as_l1_consultation():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("你好")

    assert plan.semantic_level == 1
    assert plan.semantic_label == "闲聊咨询层"
    assert plan.response_deadline_ms == 1000
    assert plan.actions[0].action_type == "unknown"


def test_voice_nlp_adapter_marks_dashboard_query_as_l2_without_wake_word():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("通讯正常吗")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "communication_faults"
    assert plan.semantic_level == 2
    assert plan.semantic_label == "工艺查询层"
    assert plan.response_deadline_ms == 5000
    assert plan.requires_precheck is False


def test_voice_nlp_adapter_records_jieba_rule_tokens_when_tokenizer_is_available():
    adapter = VoiceNlpAdapter(
        table={},
        flow_names=(),
        tokenizer=lambda text: ("小正", "暂停") if "暂停" in text else (text,),
    )

    plan = adapter.parse("小正，暂停")

    assert plan.nlp_engine == "jieba_rule"
    assert plan.tokens == ("小正", "暂停")
    assert plan.to_preview_dict()["tokens"] == ["小正", "暂停"]
    assert plan.to_preview_dict()["engine"] == "jieba_rule"
