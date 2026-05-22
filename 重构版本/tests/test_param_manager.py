from robot_modbus_lite.param_manager import (
    PARAM_LAYER_LABELS,
    classify_system_config_field,
    export_param_layer_markdown,
    fields_by_layer,
    validate_param_patch,
)


def test_param_manager_classifies_core_system_config_fields():
    assert classify_system_config_field("x") == "readonly"
    assert classify_system_config_field("joint_limits") == "readonly"
    assert classify_system_config_field("safe_speed_max") == "optimizable"
    assert classify_system_config_field("safe_acc_max") == "optimizable"
    assert classify_system_config_field("l3_cumulative_error_limit_mm") == "optimizable"
    assert classify_system_config_field("emergency_codes") == "forbidden"
    assert classify_system_config_field("echo_retry_count") == "forbidden"
    assert classify_system_config_field("unknown_field") == "forbidden"


def test_param_manager_groups_fields_by_layer_for_review():
    grouped = fields_by_layer()

    assert "x" in grouped["readonly"]
    assert "safe_speed_max" in grouped["optimizable"]
    assert "emergency_codes" in grouped["forbidden"]
    assert PARAM_LAYER_LABELS["optimizable"] == "可优化区"


def test_param_manager_allows_ai_optimizer_to_write_only_optimizable_fields():
    allowed = validate_param_patch({"safe_speed_max": 60, "l3_min_step_delay_ms": 100}, actor="ai_optimizer")
    denied = validate_param_patch({"safe_speed_max": 60, "x": [-1, 1]}, actor="ai_optimizer")

    assert allowed.ok is True
    assert allowed.denied_fields == []
    assert denied.ok is False
    assert denied.denied_fields == ["x"]
    assert "只读区" in denied.message


def test_param_manager_allows_engineer_to_write_readonly_and_optimizable_but_not_forbidden():
    allowed = validate_param_patch({"x": [-1, 1], "safe_speed_max": 60}, actor="engineer")
    denied = validate_param_patch({"emergency_codes": ["A1B2", "B2C3"]}, actor="engineer")

    assert allowed.ok is True
    assert denied.ok is False
    assert denied.denied_fields == ["emergency_codes"]
    assert "禁写区" in denied.message


def test_param_manager_allows_system_actor_to_write_forbidden_fields():
    result = validate_param_patch({"emergency_codes": ["A1B2"], "echo_retry_count": 5}, actor="system")

    assert result.ok is True
    assert result.denied_fields == []


def test_param_manager_exports_reviewable_markdown():
    markdown = export_param_layer_markdown()

    assert "# 系统参数分层读写清单" in markdown
    assert "| 分层 | 字段 | AI优化器 | 工程师 | 系统内部 |" in markdown
    assert "safe_speed_max" in markdown
    assert "可写" in markdown
    assert "emergency_codes" in markdown
    assert "禁写区" in markdown
