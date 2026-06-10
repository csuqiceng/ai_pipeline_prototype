from robot_modbus_lite.agent_tools.compound_tools import plan_compound_command, split_compound_command


def test_split_compound_command_wraps_existing_coordinator():
    result = split_compound_command("走到X1000，然后等待2秒，再IO1开")

    assert result.ok is True
    assert result.state == "compound_sequence"
    assert result.data["steps"] == ["走到X1000", "等待2秒", "IO1开"]
    assert result.data["generates_command"] is False


def test_split_compound_command_rejects_unsupported_parallel_command():
    result = split_compound_command("同时走到X1000并且IO1开")

    assert result.ok is False
    assert result.state == "unsupported_compound"
    assert result.errors[0]["code"] == "UNSUPPORTED_COMPOUND"
    assert result.data["generates_command"] is False


def test_plan_compound_command_returns_draft_without_execution():
    result = plan_compound_command(
        "走到X1000，然后等待2秒",
        clock=lambda: 100.0,
        id_factory=lambda: "compound:test",
    )

    assert result.ok is True
    assert result.state == "compound_plan_draft"
    assert result.data["plan_id"] == "compound:test"
    assert result.data["steps"] == ["走到X1000", "等待2秒"]
    assert result.data["created_at"] == 100.0
    assert result.data["generates_command"] is False
