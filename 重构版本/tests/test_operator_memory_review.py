from robot_modbus_lite.agent_tools.tool_result import ToolResult
from robot_modbus_lite.operator_memory_review import OperatorMemoryReviewCommands, memory_id_from_text, memory_review_view, memory_tool_result_text


def test_operator_memory_review_commands_routes_status_and_batch_approve():
    calls = []

    def call_tool(tool_name, **kwargs):
        calls.append((tool_name, kwargs))
        if tool_name == "query_memory_review":
            return ToolResult.success(
                state="memory_review_listed",
                message="找到 1 条经验记录。",
                data={"memories": [{"memory_id": "mem_1"}]},
            )
        return ToolResult.success(
            state="memory_approved",
            message="候选经验已审核通过并生效。",
            data={"memory": {"memory_id": kwargs["memory_id"], "status": "active"}},
        )

    commands = OperatorMemoryReviewCommands(call_tool)

    listed = commands.handle("查看生效经验")
    approved = commands.handle("批准全部待审核经验")

    assert listed.handled is True
    assert listed.category == "经验审核"
    assert calls[0] == ("query_memory_review", {"status": "active", "include_audit": True})
    assert approved.handled is True
    assert approved.category == "经验批量审核"
    assert calls[1] == ("query_memory_review", {"status": "candidate", "include_audit": False})
    assert calls[2] == ("approve_memory_candidate", {"memory_id": "mem_1", "reviewer": "operator-ui"})


def test_operator_memory_review_commands_support_kind_filter_and_batch_disable_rollback():
    calls = []

    def call_tool(tool_name, **kwargs):
        calls.append((tool_name, kwargs))
        if tool_name == "query_memory_review":
            return ToolResult.success(
                state="memory_review_listed",
                message="找到 2 条经验记录。",
                data={"memories": [{"memory_id": "mem_1"}, {"memory_id": "mem_2"}]},
            )
        if tool_name == "disable_memory":
            return ToolResult.success(
                state="memory_disabled",
                message="经验已停用。",
                data={"memory": {"memory_id": kwargs["memory_id"], "status": "disabled"}},
            )
        return ToolResult.success(
            state="memory_rolled_back",
            message="经验已回滚。",
            data={"memory": {"memory_id": kwargs["memory_id"], "status": "rolled_back"}},
        )

    commands = OperatorMemoryReviewCommands(call_tool)

    filtered = commands.handle("查看生效经验 asr_alias")
    disabled = commands.handle("停用全部生效经验")
    rolled_back = commands.handle("回滚全部生效经验")

    assert filtered.handled is True
    assert calls[0] == ("query_memory_review", {"status": "active", "kind": "asr_alias", "include_audit": True})
    assert disabled.handled is True
    assert disabled.category == "经验批量停用"
    assert calls[1] == ("query_memory_review", {"status": "active", "include_audit": False})
    assert calls[2] == ("disable_memory", {"memory_id": "mem_1", "reviewer": "operator-ui", "reason": "operator batch command"})
    assert calls[3] == ("disable_memory", {"memory_id": "mem_2", "reviewer": "operator-ui", "reason": "operator batch command"})
    assert rolled_back.handled is True
    assert rolled_back.category == "经验批量回滚"
    assert calls[4] == ("query_memory_review", {"status": "active", "include_audit": False})
    assert calls[5] == ("rollback_memory", {"memory_id": "mem_1", "reviewer": "operator-ui", "reason": "operator batch command"})
    assert calls[6] == ("rollback_memory", {"memory_id": "mem_2", "reviewer": "operator-ui", "reason": "operator batch command"})


def test_memory_review_format_helpers():
    result = ToolResult.success(
        state="memory_review_listed",
        message="找到 1 条经验记录。",
        data={"memories": [{"memory_id": "mem_1", "kind": "asr_alias", "key": "位置诶", "value": {"normalized": "位置A"}, "status": "active"}]},
    )

    assert memory_id_from_text("回滚经验 mem_1") == "mem_1"
    assert "位置诶" in memory_tool_result_text(result)


def test_memory_review_view_builds_rows_detail_and_filter_options():
    result = ToolResult.success(
        state="memory_review_listed",
        message="找到 2 条经验记录。",
        data={
            "memories": [
                {
                    "memory_id": "mem_1",
                    "kind": "asr_alias",
                    "key": "位置诶",
                    "value": {"normalized": "位置A"},
                    "status": "active",
                    "source": "vote",
                    "confidence": 0.8,
                    "audit_events": [
                        {"event": "candidate_created", "created_at": "2026-06-01T10:00:00"},
                        {"event": "memory_approved", "created_at": "2026-06-01T10:02:00"},
                    ],
                },
                {
                    "memory_id": "mem_2",
                    "kind": "flow_preference",
                    "key": "推荐流程",
                    "value": {"prefer": "先移动"},
                    "status": "candidate",
                },
            ]
        },
    )

    view = memory_review_view(result)

    assert view.ok is True
    assert view.rows[0].memory_id == "mem_1"
    assert view.rows[0].value_text == "{'normalized': '位置A'}"
    assert view.rows[0].audit_count == 2
    assert view.rows[0].last_event == "memory_approved"
    assert "mem_1" in view.detail_text
    assert "memory_approved" in view.detail_text
    assert view.status_options == ("candidate", "active", "disabled", "rolled_back")
    assert "asr_alias" in view.kind_options
