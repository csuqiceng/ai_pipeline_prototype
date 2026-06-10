from robot_modbus_lite.agent_tools.tool_result import ToolResult


def test_tool_result_success_serializes_standard_shape():
    result = ToolResult.success(
        state="flow_draft_updated",
        message="已添加第1步。",
        data={"flow_name": "测试"},
    )

    assert result.ok is True
    assert result.to_dict() == {
        "ok": True,
        "state": "flow_draft_updated",
        "message": "已添加第1步。",
        "data": {"flow_name": "测试"},
        "errors": [],
    }


def test_tool_result_failure_normalizes_error_message():
    result = ToolResult.failure(
        state="missing_params",
        message="缺少坐标。",
        code="MISSING_REQUIRED_PARAMS",
        fields=["target_x"],
    )

    assert result.ok is False
    assert result.errors == [
        {
            "code": "MISSING_REQUIRED_PARAMS",
            "message": "缺少坐标。",
            "fields": ["target_x"],
        }
    ]

