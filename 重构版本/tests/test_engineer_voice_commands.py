from robot_modbus_lite.engineer_voice_commands import (
    ENGINEER_REQUIRED_BUTTON_LABELS,
    aliases_for_engineer_button,
    duplicate_engineer_voice_aliases,
    engineer_button_labels_with_voice_aliases,
    engineer_voice_capability_summary,
    engineer_voice_execution_policy,
    export_engineer_voice_command_markdown,
    export_engineer_voice_command_rows,
    match_engineer_voice_command,
    missing_required_engineer_button_labels,
)


def test_engineer_voice_command_table_covers_required_qt_buttons():
    assert missing_required_engineer_button_labels() == []
    assert ENGINEER_REQUIRED_BUTTON_LABELS <= engineer_button_labels_with_voice_aliases()


def test_engineer_voice_command_table_has_expected_aliases_for_key_buttons():
    assert "切到后台" in aliases_for_engineer_button("后台")
    assert "保存系统参数" in aliases_for_engineer_button("保存配置")
    assert "读取控制器限位" in aliases_for_engineer_button("读取控制器限位")
    assert "新增安全中间点" in aliases_for_engineer_button("新增中间点")
    assert "添加流程步骤" in aliases_for_engineer_button("添加步骤")
    assert "导出日志" in aliases_for_engineer_button("导出日志")


def test_engineer_voice_command_table_has_no_conflicting_aliases():
    assert duplicate_engineer_voice_aliases() == {}


def test_engineer_voice_command_table_classifies_confirm_and_danger_actions():
    save_config = match_engineer_voice_command("保存系统参数")
    delete_flow = match_engineer_voice_command("删除流程")
    estop = match_engineer_voice_command("急停 授权码 急停")

    assert save_config.danger_level == "confirm"
    assert delete_flow.danger_level == "danger"
    assert estop.danger_level == "emergency"


def test_engineer_voice_command_execution_policy_matches_qt_runtime_scope():
    assert engineer_voice_execution_policy(match_engineer_voice_command("切到后台")) == "direct"
    assert engineer_voice_execution_policy(match_engineer_voice_command("保存系统参数")) == "confirm"
    assert engineer_voice_execution_policy(match_engineer_voice_command("删除流程")) == "rejected"
    assert engineer_voice_execution_policy(match_engineer_voice_command("新增模板")) == "listed_only"


def test_engineer_voice_capability_summary_groups_by_execution_policy():
    summary = engineer_voice_capability_summary(limit_per_group=3)

    assert summary["direct"]["count"] > 0
    assert summary["confirm"]["count"] > 0
    assert summary["rejected"]["count"] > 0
    assert summary["listed_only"]["count"] > 0
    assert "后台" in summary["direct"]["examples"]
    assert "保存配置" in summary["confirm"]["examples"]
    assert "急停" in summary["rejected"]["examples"]
    assert "授权" in summary["listed_only"]["examples"]


def test_engineer_voice_command_matcher_resolves_common_commands():
    assert match_engineer_voice_command("切到系统参数").action == "show_system_params"
    assert match_engineer_voice_command("保存系统参数").action == "save_system_config"
    assert match_engineer_voice_command("打开流程管理").action == "show_flow_manage"
    assert match_engineer_voice_command("添加流程步骤").action == "add_flow_step"
    assert match_engineer_voice_command("清空日志").action == "clear_logs"


def test_engineer_voice_command_export_rows_are_reviewable():
    rows = export_engineer_voice_command_rows()

    assert rows[0] == {
        "section": "主导航",
        "button_label": "运行",
        "action": "show_run_page",
        "aliases": ["切到运行", "打开运行页", "运行页面"],
        "danger_level": "normal",
        "execution_policy": "direct",
        "execution_policy_label": "可直接语音执行",
    }
    assert any(row["button_label"] == "急停" and row["execution_policy"] == "rejected" for row in rows)
    assert any(row["button_label"] == "清空日志" and row["execution_policy"] == "rejected" for row in rows)
    assert any(row["button_label"] == "新增" and row["execution_policy"] == "listed_only" for row in rows)


def test_engineer_voice_command_export_markdown_contains_engineer_buttons():
    markdown = export_engineer_voice_command_markdown()

    assert "# 工程师页语音等价指令清单" in markdown
    assert "| 区域 | 按钮/入口 | 动作 | 语音说法 | 风险级别 | 执行策略 |" in markdown
    assert "保存系统参数、保存配置" in markdown
    assert "急停 授权码 急停" in markdown
    assert "清空日志" in markdown
    assert "需二次确认" in markdown
    assert "拒绝语音执行" in markdown
    assert "仅清单保留" in markdown
