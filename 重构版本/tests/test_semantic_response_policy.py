from robot_modbus_lite.semantic_response_policy import policy_for_level, policy_for_plan
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


def test_policy_for_l3_requires_precheck_and_confirmation():
    policy = policy_for_level(3)

    assert policy.semantic_label == "常规生产执行层"
    assert policy.ack_limit_ms == 50
    assert policy.result_deadline_ms == 2000
    assert policy.progress_interval_ms == 1000
    assert policy.requires_precheck is True
    assert policy.requires_confirmation is True
    assert policy.emergency_fast_path is False


def test_policy_for_l5_uses_emergency_fast_path():
    policy = policy_for_level(5)

    assert policy.semantic_label == "应急安全层"
    assert policy.ack_limit_ms == 30
    assert policy.result_deadline_ms == 100
    assert policy.progress_interval_ms == 0
    assert policy.requires_precheck is False
    assert policy.requires_confirmation is False
    assert policy.emergency_fast_path is True
    assert policy.priority == "high"


def test_policy_for_plan_uses_plan_semantic_level():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("system", "sys_cancel", "rule", "取消当前任务", "测试"),),
        source="rule",
        raw_text="取消当前任务",
        reason="测试",
        semantic_level=4,
        semantic_label="系统管理层",
    )

    policy = policy_for_plan(plan)

    assert policy.semantic_level == 4
    assert policy.requires_precheck is False
    assert policy.result_deadline_ms == 2000
