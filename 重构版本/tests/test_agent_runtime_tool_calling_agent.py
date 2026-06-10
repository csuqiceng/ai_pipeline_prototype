from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent_runtime.tool_calling_agent import (
    ToolCallingAgentRuntime,
    build_local_tool_specs,
)


def test_tool_calling_agent_reports_unavailable_without_langchain_runner():
    runtime = ToolCallingAgentRuntime(langchain_available=False)

    result = runtime.handle("你好", session_state=SessionState(thread_id="session-1"))

    assert result.kind == "tool_calling_unavailable"
    assert result.payload["fallback_required"] is True


def test_local_tool_specs_expose_command_intent_parser_before_param_parser():
    specs = build_local_tool_specs()
    names = [spec.name for spec in specs]

    assert "lookup_command_schema" in names
    assert "parse_command_intent" in names
    assert names.index("parse_command_intent") < names.index("parse_command_params")
    spec = specs[names.index("parse_command_intent")]
    assert spec.group == "command_tools"
    assert spec.side_effect is False
    assert specs[names.index("lookup_command_schema")].side_effect is False
    assert specs[names.index("validate_required_params")].side_effect is False
    assert specs[names.index("check_param_bounds")].side_effect is False
    assert specs[names.index("resolve_command_address")].side_effect is False
    assert specs[names.index("build_system_action_draft")].side_effect is False


def test_local_tool_specs_expose_atomic_template_tool_as_non_execution():
    specs = {spec.name: spec for spec in build_local_tool_specs()}

    assert specs["apply_atomic_template"].group == "command_tools"
    assert specs["apply_atomic_template"].side_effect is False


def test_local_tool_specs_expose_registered_flow_tools_without_execution():
    specs = {spec.name: spec for spec in build_local_tool_specs()}

    assert specs["query_registered_flow"].group == "flow_tools"
    assert specs["query_registered_flow"].side_effect is False
    assert specs["prepare_registered_flow_execution"].group == "flow_tools"
    assert specs["prepare_registered_flow_execution"].side_effect is False


def test_local_tool_specs_expose_flow_param_edit_tool():
    specs = {spec.name: spec for spec in build_local_tool_specs()}

    assert specs["edit_flow_draft_params"].group == "flow_tools"
    assert specs["edit_flow_draft_params"].side_effect is True
    assert "text" in specs["edit_flow_draft_params"].input_schema["properties"]
    assert "draft" in specs["edit_flow_draft_params"].input_schema["properties"]


def test_local_tool_specs_expose_axis_and_alarm_status_tools_as_read_only():
    specs = {spec.name: spec for spec in build_local_tool_specs()}

    assert specs["get_axis_status"].group == "status_tools"
    assert specs["get_axis_status"].side_effect is False
    assert specs["get_alarm"].group == "status_tools"
    assert specs["get_alarm"].side_effect is False
    assert specs["get_execution_progress"].group == "status_tools"
    assert specs["get_execution_progress"].side_effect is False


def test_local_tool_specs_expose_position_alias_memory_tools_as_side_effects():
    specs = {spec.name: spec for spec in build_local_tool_specs()}

    assert specs["query_memory_candidates"].group == "memory_tools"
    assert specs["query_memory_candidates"].side_effect is False
    assert specs["query_memory_review"].group == "memory_tools"
    assert specs["query_memory_review"].side_effect is False
    assert specs["approve_memory_candidate"].group == "memory_tools"
    assert specs["approve_memory_candidate"].side_effect is True
    assert specs["disable_memory"].group == "memory_tools"
    assert specs["disable_memory"].side_effect is True
    assert specs["record_memory_applied"].group == "memory_tools"
    assert specs["record_memory_applied"].side_effect is True
    assert specs["save_position_alias"].group == "memory_tools"
    assert specs["save_position_alias"].side_effect is True
    assert specs["delete_position_alias"].group == "memory_tools"
    assert specs["delete_position_alias"].side_effect is True


def test_tool_calling_agent_uses_injected_runner_when_available():
    calls = []

    def runner(text, session_state, tool_specs):
        calls.append((text, session_state.thread_id, tuple(spec.name for spec in tool_specs)))
        return AgentOrchestratorResult(kind="chat_answer", message="你好")

    runtime = ToolCallingAgentRuntime(langchain_available=True, runner=runner)

    result = runtime.handle("你好", session_state=SessionState(thread_id="session-1"))

    assert result.kind == "chat_answer"
    assert calls == [("你好", "session-1", tuple(spec.name for spec in build_local_tool_specs()))]


def test_local_tool_specs_expose_documented_tool_groups():
    specs = build_local_tool_specs()
    names = {spec.name for spec in specs}

    assert {
        "parse_command_params",
        "build_command_draft",
        "run_safety_precheck",
        "create_pending_confirm",
        "query_pending_confirm",
        "confirm_pending_plan",
        "cancel_pending_plan",
        "query_dashboard_section",
        "query_saved_position",
        "explain_text",
        "split_compound_command",
        "plan_compound_command",
        "set_flow_draft",
        "query_current_flow_draft",
        "edit_flow_draft_params",
        "cancel_flow_draft",
        "create_memory_candidate",
        "lookup_active_memory",
    } <= names


def test_local_tool_specs_expose_output_schema_for_every_tool():
    specs = build_local_tool_specs()
    missing = [spec.name for spec in specs if not getattr(spec, "output_schema", {})]

    assert missing == []
    sample = {spec.name: spec for spec in specs}["parse_command_params"]
    assert sample.output_schema["required"] == ["ok", "state"]
    assert "errors" in sample.output_schema["properties"]
