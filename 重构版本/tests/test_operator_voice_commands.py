from robot_modbus_lite.operator_voice_commands import (
    OPERATOR_REQUIRED_BUTTON_LABELS,
    aliases_for_button,
    button_labels_with_voice_aliases,
    duplicate_voice_aliases,
    export_operator_voice_command_markdown,
    export_operator_voice_command_rows,
    match_operator_voice_command,
    missing_required_button_labels,
)


def test_operator_voice_command_table_covers_required_user_buttons():
    assert missing_required_button_labels() == []
    assert OPERATOR_REQUIRED_BUTTON_LABELS <= button_labels_with_voice_aliases()


def test_operator_voice_command_table_has_expected_aliases_for_key_buttons():
    assert "发送当前指令" in aliases_for_button("发送")
    assert "执行当前指令" in aliases_for_button("发送")
    assert "开始录音" in aliases_for_button("录音")
    assert "停止当前动作" in aliases_for_button("停止当前")
    assert "回到主界面" in aliases_for_button("主界面")
    assert "退出全屏" in aliases_for_button("退出全屏")
    assert "开启语音播报" in aliases_for_button("语音播报")
    assert "报警复位" in aliases_for_button("复位")


def test_operator_voice_command_table_has_no_duplicate_aliases():
    assert duplicate_voice_aliases() == {}


def test_operator_voice_command_matcher_resolves_common_ui_commands():
    assert match_operator_voice_command("请显示完整状态").action == "show_full_status"
    assert match_operator_voice_command("回到主界面").action == "go_home"
    assert match_operator_voice_command("进入全屏").action == "enter_fullscreen"
    assert match_operator_voice_command("退出全屏").action == "exit_fullscreen"
    assert match_operator_voice_command("暂停").action == "pause"
    assert match_operator_voice_command("继续").action == "resume"
    assert match_operator_voice_command("报警复位").action == "alarm_reset"
    assert match_operator_voice_command("停止当前动作").action == "stop_current"


def test_operator_voice_command_matcher_does_not_treat_emergency_template_as_direct_estop():
    assert match_operator_voice_command("急停 A1B2 急停") is None


def test_operator_voice_command_export_rows_are_reviewable():
    rows = export_operator_voice_command_rows()

    assert rows[0] == {
        "button_label": "发送",
        "action": "execute_text",
        "aliases": ["发送当前指令", "发送指令", "发送输入", "执行当前指令", "执行指令", "执行输入"],
        "requires_emergency_code": False,
    }
    assert any(row["button_label"] == "急停" and row["requires_emergency_code"] is True for row in rows)


def test_operator_voice_command_export_markdown_contains_all_aliases():
    markdown = export_operator_voice_command_markdown()

    assert "| 按钮/入口 | 动作 | 语音说法 | 备注 |" in markdown
    assert "发送当前指令、发送指令、发送输入、执行当前指令、执行指令、执行输入" in markdown
    assert "急停 授权码 急停" in markdown
    assert "需要三段式应急编码" in markdown
