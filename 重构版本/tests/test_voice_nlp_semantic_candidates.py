from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


def test_jieba_tokens_promote_dashboard_query_intent():
    adapter = VoiceNlpAdapter(
        table={},
        flow_names=(),
        tokenizer=lambda text: ("查询", "安全", "范围"),
    )

    plan = adapter.parse("查一下安全范围")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "safety_boundary"
    assert plan.semantic_level == 2
    assert plan.tokens == ("查询", "安全", "范围")
    assert plan.nlp_engine == "jieba_rule"


def test_tokens_do_not_override_emergency_fast_path():
    adapter = VoiceNlpAdapter(
        table={},
        flow_names=(),
        tokenizer=lambda text: ("急停", "ABC123", "急停", "查询"),
    )

    plan = adapter.parse("急停 ABC123 急停")

    assert plan.semantic_level == 5
    assert plan.actions[0].action_type == "system"
    assert plan.actions[0].target == "sys_estop"
