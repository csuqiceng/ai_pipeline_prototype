from robot_modbus_lite.dashboard_query_specs import (
    REQUIRED_DASHBOARD_QUERY_KEYS,
    dashboard_query_keys,
    export_dashboard_query_markdown,
    export_dashboard_query_rows,
    match_dashboard_query_spec,
    missing_dashboard_query_keys,
)


def test_dashboard_query_specs_cover_required_v21_boards():
    assert missing_dashboard_query_keys() == []
    assert REQUIRED_DASHBOARD_QUERY_KEYS <= dashboard_query_keys()


def test_dashboard_query_specs_match_common_questions():
    assert match_dashboard_query_spec("通讯正常吗").board_key == "communication_faults"
    assert match_dashboard_query_spec("现在能不能执行").board_key == "action_feasibility"
    assert match_dashboard_query_spec("当前位置安全吗").board_key == "safety_boundary"
    assert match_dashboard_query_spec("速度有没有超限").board_key == "motion_limits"
    assert match_dashboard_query_spec("流程预演到哪了").board_key == "process_preview"
    assert match_dashboard_query_spec("这个位置能到吗").board_key == "process_adaptation"
    assert match_dashboard_query_spec("现在设备状态").board_key == "device_status"
    assert match_dashboard_query_spec("现在下位机状态是什么").board_key == "device_status"


def test_dashboard_query_specs_export_rows_are_reviewable():
    rows = export_dashboard_query_rows()

    assert rows[0]["board_key"] == "device_status"
    assert rows[0]["board_name"] == "看板1 设备基础状态"
    assert "设备状态" in rows[0]["aliases"]
    assert "下位机状态" in rows[0]["aliases"]
    assert any(row["board_key"] == "communication_faults" for row in rows)


def test_dashboard_query_specs_export_markdown_contains_all_boards():
    markdown = export_dashboard_query_markdown()

    assert "| 看板 | board_key | 可问法 | 回答内容 |" in markdown
    assert "看板1 设备基础状态" in markdown
    assert "看板7 通讯+设备故障诊断" in markdown
    assert "通讯正常吗" in markdown
