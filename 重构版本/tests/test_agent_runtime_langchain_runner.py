from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent_runtime.langchain_runner import (
    LangChainRuntimeUnavailable,
    LangChainToolRunner,
    build_default_langgraph_app,
    build_langchain_tools,
    langchain_dependencies_available,
)
from robot_modbus_lite.agent_runtime.local_tool_registry import LocalToolRegistry
from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore
from robot_modbus_lite.agent_runtime.session_state import SessionState
from robot_modbus_lite.agent_runtime.tool_calling_agent import build_local_tool_specs


def test_langchain_dependencies_available_uses_injected_finder():
    def missing(_name):
        return None

    def present(_name):
        return object()

    assert langchain_dependencies_available(find_spec=missing) is False
    assert langchain_dependencies_available(find_spec=present) is True


def test_build_langchain_tools_raises_when_dependencies_missing():
    try:
        build_langchain_tools(LocalToolRegistry(), find_spec=lambda _name: None)
    except LangChainRuntimeUnavailable as exc:
        assert "LangChain/LangGraph" in str(exc)
    else:
        raise AssertionError("expected LangChainRuntimeUnavailable")


def test_langchain_tool_runner_invokes_graph_app_and_returns_result():
    calls = []

    class GraphApp:
        def invoke(self, payload):
            calls.append(payload)
            return {"kind": "chat_answer", "message": "graph answer", "payload": {"source": "graph"}}

    runner = LangChainToolRunner(LocalToolRegistry(), graph_app=GraphApp())

    result = runner("你好", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result == AgentOrchestratorResult(
        kind="chat_answer",
        message="graph answer",
        payload={"source": "graph"},
    )
    assert calls[0]["text"] == "你好"
    assert calls[0]["session_state"]["thread_id"] == "session-1"


def test_langchain_tool_runner_returns_unavailable_without_graph_app():
    runner = LangChainToolRunner(LocalToolRegistry(), graph_app=None)

    result = runner("你好", SessionState(thread_id="session-1"), build_local_tool_specs())

    assert result.kind == "tool_calling_unavailable"
    assert result.payload["fallback_required"] is True


def test_default_langgraph_app_uses_decider_tool_call_before_local_rules():
    decisions = []

    def tool_decider(payload):
        decisions.append(payload)
        return {"tool_name": "explain_text", "args": {"text": "你好"}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "移动到 X100",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "chat_answer"
    assert result["payload"]["tool_name"] == "explain_text"
    assert result["payload"]["tool_result"]["ok"] is True
    assert decisions[0]["text"] == "移动到 X100"
    assert decisions[0]["session_state"]["thread_id"] == "session-1"


def test_default_langgraph_app_exposes_multi_node_runtime_graph():
    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=lambda _payload: None)

    graph = app.get_graph()
    node_names = set(graph.nodes)

    assert {
        "check_pending_timeout",
        "expire_pending_state",
        "sync_compound_step_result",
        "decide_tool",
        "call_tool",
        "sync_flow_state",
        "sync_confirm_state",
        "sync_compound_state",
        "local_rules",
    } <= node_names


def test_default_langgraph_app_direct_flow_tool_updates_session_state():
    def tool_decider(_payload):
        return {
            "tool_call_id": "flow-start-1",
            "tool_name": "start_flow_draft",
            "args": {"flow_name": "测试"},
        }

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "创建流程测试",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "flow_draft"
    assert result["session_state"]["mode"] == "editing_flow"
    assert result["session_state"]["current_intent"] == "create_flow"
    assert result["session_state"]["current_flow_draft"] == {
        "flow_name": "测试",
        "expanded_steps": [],
    }


def test_default_langgraph_app_flow_clarification_failure_updates_session_state_without_fallback():
    def tool_decider(_payload):
        return {
            "tool_call_id": "flow-start-missing-name",
            "tool_name": "start_flow_draft",
            "args": {},
        }

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "创建一个新流程",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "flow_draft"
    assert result["payload"]["tool_name"] == "start_flow_draft"
    assert result["payload"]["tool_result"]["state"] == "flow_draft_needs_name"
    assert result["session_state"]["mode"] == "clarifying"
    assert result["session_state"]["pending_missing_fields"] == ["flow_name"]
    assert result["session_state"]["current_flow_draft"] == {
        "flow_name": "",
        "expanded_steps": [],
    }


def test_default_langgraph_app_flow_name_answer_uses_existing_draft_state():
    state = SessionState(thread_id="session-1").with_tool_result(
        tool_name="start_flow_draft",
        tool_result=type(
            "Result",
            (),
            {
                "state": "flow_draft_needs_name",
                "data": {"intent": "create_flow", "draft": {"flow_name": "", "expanded_steps": []}},
                "errors": [{"fields": ["flow_name"]}],
                "to_dict": lambda _self: {},
            },
        )(),
    ).with_flow_draft({"flow_name": "", "expanded_steps": []})

    def tool_decider(payload):
        return {
            "tool_call_id": "flow-set-name",
            "tool_name": "set_flow_name",
            "args": {
                "draft": payload["session_state"]["current_flow_draft"],
                "flow_name": "测试",
            },
        }

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "流程名字叫测试",
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "flow_draft"
    assert result["session_state"]["mode"] == "editing_flow"
    assert result["session_state"]["pending_missing_fields"] == []
    assert result["session_state"]["current_flow_draft"] == {
        "flow_name": "测试",
        "expanded_steps": [],
    }


def test_default_langgraph_app_local_rules_flow_clarification_updates_session_state():
    from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
    from robot_modbus_lite.execution_plan_service import ExecutionPlanService

    def snapshot():
        return ControllerSnapshot(
            current_pose={
                "target_x": 0.0,
                "target_y": 0.0,
                "target_z": 0.0,
                "target_rx": 0.0,
                "target_ry": 0.0,
                "target_rz": 0.0,
            },
            safety_params={"spd_pct": 30.0, "acc_pct": 20.0, "dec_pct": 20.0},
        )

    service = ExecutionPlanService()
    state = SessionState(
        thread_id="session-1",
        mode="clarifying",
        current_intent="create_flow",
        current_flow_draft={
            "flow_name": "测试",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动到位置A",
                    "func_id": 108,
                    "description": "移动到位置A",
                    "params": {"spd_pct": 50.0},
                }
            ],
        },
        pending_missing_fields=("target_pose",),
    )

    app = build_default_langgraph_app(
        LocalToolRegistry(
            execution_plan_service=service,
            controller_snapshot_provider=snapshot,
        ),
        tool_decider=lambda _payload: None,
    )

    result = app.invoke(
        {
            "text": "我觉得坐标是 X 一百， Y 0， Z 100，速度 50。",
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "flow_draft"
    assert result["payload"]["tool_name"] == "answer_flow_clarification"
    assert result["session_state"]["mode"] == "editing_flow"
    assert result["session_state"]["pending_missing_fields"] == []
    params = result["session_state"]["current_flow_draft"]["expanded_steps"][0]["params"]
    assert params["target_x"] == 100.0
    assert params["target_z"] == 100.0


def test_default_langgraph_app_cancel_flow_draft_clears_session_state():
    from robot_modbus_lite.execution_plan_service import ExecutionPlanService

    service = ExecutionPlanService()
    service.set_pending_flow_draft({"flow_name": "测试", "expanded_steps": []})
    state = SessionState(thread_id="session-1").with_flow_draft({"flow_name": "测试", "expanded_steps": []})

    def tool_decider(_payload):
        return {
            "tool_call_id": "flow-cancel",
            "tool_name": "cancel_flow_draft",
            "args": {},
        }

    app = build_default_langgraph_app(
        LocalToolRegistry(execution_plan_service=service),
        tool_decider=tool_decider,
    )

    result = app.invoke(
        {
            "text": "取消流程",
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "flow_draft"
    assert result["session_state"]["mode"] == "idle"
    assert result["session_state"]["current_flow_draft"] == {}


def test_default_langgraph_app_returns_tool_schema_failure_without_local_fallback():
    def tool_decider(_payload):
        return {"tool_name": "parse_command_params", "args": {}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "X100",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "command_tool_result"
    assert result["payload"]["tool_name"] == "parse_command_params"
    assert result["payload"]["tool_result"]["ok"] is False
    assert result["payload"]["tool_result"]["state"] == "tool_args_invalid"


def test_default_langgraph_app_compound_plan_updates_session_state():
    class Service:
        def parse(self, text):
            return {"kind": "waiting_confirmation", "text": text}

    def tool_decider(_payload):
        return {
            "tool_name": "plan_compound_command",
            "args": {"text": "走到X100，然后等待2秒"},
        }

    app = build_default_langgraph_app(LocalToolRegistry(restricted_service=Service()), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "走到X100，然后等待2秒",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "compound_plan_draft"
    assert result["session_state"]["mode"] == "editing_flow"
    assert result["session_state"]["current_compound_plan"]["steps"] == ["走到X100", "等待2秒"]
    assert result["session_state"]["current_compound_plan"]["step_results"] == [
        {"kind": "waiting_confirmation", "text": "走到X100"},
        {"kind": "waiting_confirmation", "text": "等待2秒"},
    ]


def test_default_langgraph_app_compound_plan_sets_active_step_for_confirm_loop():
    class Service:
        def parse(self, text):
            return {"kind": "waiting_confirmation", "text": text, "draft_id": f"draft:{text}"}

    def tool_decider(_payload):
        return {
            "tool_name": "plan_compound_command",
            "args": {"text": "走到X100，然后等待2秒"},
        }

    app = build_default_langgraph_app(LocalToolRegistry(restricted_service=Service()), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "走到X100，然后等待2秒",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    plan = result["session_state"]["current_compound_plan"]
    assert plan["active_step_index"] == 0
    assert plan["active_step"] == "走到X100"
    assert plan["active_step_result"] == {
        "kind": "waiting_confirmation",
        "text": "走到X100",
        "draft_id": "draft:走到X100",
    }
    assert plan["status"] == "waiting_step_confirm"


def test_default_langgraph_app_compound_step_result_advances_state_before_decider():
    calls = []
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_compound_plan={
            "plan_id": "compound:test",
            "steps": ["走到X100", "等待2秒"],
            "step_results": [
                {"kind": "waiting_confirmation", "text": "走到X100"},
                {"kind": "waiting_confirmation", "text": "等待2秒"},
            ],
            "active_step_index": 0,
            "active_step": "走到X100",
            "active_step_result": {"kind": "waiting_confirmation", "text": "走到X100"},
            "status": "waiting_step_confirm",
        },
        pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
    )

    def tool_decider(payload):
        calls.append(payload)
        return {"tool_name": "explain_text", "args": {"text": "不应该调用"}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "compound step completed",
            "compound_step_result": {"ok": True, "reason": "第1步完成"},
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert calls == []
    assert result["kind"] == "compound_step_result"
    assert result["session_state"]["mode"] == "editing_flow"
    assert result["session_state"]["pending_confirm"] == {}
    assert result["session_state"]["current_compound_plan"]["active_step_index"] == 1
    assert result["session_state"]["current_compound_plan"]["active_step"] == "等待2秒"


def test_default_langgraph_app_compound_step_result_failure_blocks_before_decider():
    calls = []
    state = SessionState(
        thread_id="session-1",
        mode="waiting_confirm",
        current_compound_plan={
            "plan_id": "compound:test",
            "steps": ["走到X100", "等待2秒"],
            "step_results": [
                {"kind": "waiting_confirmation", "text": "走到X100"},
                {"kind": "waiting_confirmation", "text": "等待2秒"},
            ],
            "active_step_index": 0,
            "active_step": "走到X100",
            "active_step_result": {"kind": "waiting_confirmation", "text": "走到X100"},
            "status": "waiting_step_confirm",
        },
        pending_confirm={"draft_id": "draft-1", "source": "compound_step"},
    )

    def tool_decider(payload):
        calls.append(payload)
        return {"tool_name": "explain_text", "args": {"text": "不应该调用"}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "compound step failed",
            "compound_step_result": {"ok": False, "reason": "控制器报警"},
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert calls == []
    assert result["kind"] == "compound_step_result"
    assert result["session_state"]["mode"] == "blocked"
    assert result["session_state"]["current_compound_plan"]["status"] == "failed"
    assert result["session_state"]["current_compound_plan"]["failed_step"]["reason"] == "控制器报警"


def test_default_langgraph_app_compound_failure_returns_structured_result_without_fallback():
    def tool_decider(_payload):
        return {"tool_name": "plan_compound_command", "args": {"text": "你好"}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "你好",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "compound_plan_draft"
    assert result["payload"]["tool_name"] == "plan_compound_command"
    assert result["payload"]["tool_result"]["ok"] is False
    assert result["payload"]["tool_result"]["state"] in {"not_compound", "unsupported_compound"}


def test_default_langgraph_app_create_pending_confirm_updates_session_state():
    from robot_modbus_lite.agent.confirmation import ConfirmationAgent

    def tool_decider(_payload):
        return {
            "tool_call_id": "confirm-create-1",
            "tool_name": "create_pending_confirm",
            "args": {
                "draft": {
                    "draft_id": "draft-1",
                    "func_id": 109,
                    "intent": "delay_blocking",
                    "params": {"delay_sec": 2.0},
                    "param_sources": {"delay_sec": "specified"},
                    "raw_text": "等待2秒",
                    "confidence": 0.95,
                }
            },
        }

    registry = LocalToolRegistry(
        confirmation_agent=ConfirmationAgent(timeout_sec=60),
        clock=lambda: 10.0,
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )
    app = build_default_langgraph_app(registry, tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "创建待确认",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "command_tool_result"
    assert result["session_state"]["mode"] == "waiting_confirm"
    assert result["session_state"]["pending_confirm"] == {
        "draft_id": "draft-1",
        "status": "waiting_confirmation",
        "expires_at": 70.0,
        "confirmation_text": result["payload"]["tool_result"]["data"]["confirmation_text"],
    }


def test_default_langgraph_app_expired_pending_confirm_short_circuits_before_decider():
    calls = []
    state = SessionState(thread_id="session-1").with_pending_confirm(
        {
            "draft_id": "draft-1",
            "status": "waiting_confirmation",
            "expires_at": 10.0,
        }
    )

    def tool_decider(payload):
        calls.append(payload)
        return {"tool_name": "confirm_pending_plan", "args": {"draft_id": "draft-1"}}

    registry = LocalToolRegistry(clock=lambda: 20.0)
    app = build_default_langgraph_app(registry, tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "确认执行",
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert calls == []
    assert result["kind"] == "confirm_result"
    assert result["message"] == "待确认计划已过期，请重新生成执行草案。"
    assert result["payload"]["tool_result"]["state"] == "confirm_expired"
    assert result["session_state"]["mode"] == "confirm_expired"
    assert result["session_state"]["pending_confirm"] == {}


def test_default_langgraph_app_cancel_pending_confirm_clears_session_state():
    from robot_modbus_lite.agent.confirmation import ConfirmationAgent

    agent = ConfirmationAgent(timeout_sec=60)
    registry = LocalToolRegistry(
        confirmation_agent=agent,
        clock=lambda: 10.0,
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )
    draft = {
        "draft_id": "draft-1",
        "func_id": 109,
        "intent": "delay_blocking",
        "params": {"delay_sec": 2.0},
        "param_sources": {"delay_sec": "specified"},
        "raw_text": "等待2秒",
        "confidence": 0.95,
    }
    created = registry.call("create_pending_confirm", draft=draft)
    state = SessionState(thread_id="session-1").with_pending_confirm(
        {
            "draft_id": "draft-1",
            "status": "waiting_confirmation",
            "expires_at": created.data["expires_at"],
        }
    )

    def tool_decider(_payload):
        return {
            "tool_call_id": "confirm-cancel-1",
            "tool_name": "cancel_pending_plan",
            "args": {"draft_id": "draft-1"},
        }

    app = build_default_langgraph_app(registry, tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "取消执行",
            "session_state": state.to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "confirm_result"
    assert result["session_state"]["mode"] == "idle"
    assert result["session_state"]["pending_confirm"] == {}


def test_default_langgraph_app_confirm_tool_failure_returns_structured_result_without_fallback():
    from robot_modbus_lite.agent.confirmation import ConfirmationAgent

    def tool_decider(_payload):
        return {
            "tool_call_id": "confirm-missing-1",
            "tool_name": "confirm_pending_plan",
            "args": {"draft_id": "missing-draft"},
        }

    registry = LocalToolRegistry(
        confirmation_agent=ConfirmationAgent(timeout_sec=60),
        clock=lambda: 10.0,
        status_signature_provider=lambda: "status-1",
        safety_signature_provider=lambda: "safety-1",
    )
    app = build_default_langgraph_app(registry, tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "确认执行",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "confirm_result"
    assert result["payload"]["tool_name"] == "confirm_pending_plan"
    assert result["payload"]["tool_result"]["ok"] is False
    assert result["payload"]["tool_result"]["state"] == "confirm_rejected"


def test_default_langgraph_app_falls_back_to_local_rules_for_invalid_decision():
    def tool_decider(_payload):
        return {"tool_name": "missing_tool", "args": {}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "你好",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "chat_answer"
    assert result["payload"]["tool_name"] == "explain_text"


def test_default_langgraph_app_classifies_command_intent_tool_result():
    def tool_decider(_payload):
        return {"tool_name": "parse_command_intent", "args": {"text": "急停"}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "急停",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "command_tool_result"
    assert result["payload"]["tool_name"] == "parse_command_intent"
    assert result["payload"]["tool_result"]["state"] == "command_intent_parsed"


def test_default_langgraph_app_classifies_command_schema_tool_result():
    def tool_decider(_payload):
        return {"tool_name": "lookup_command_schema", "args": {"command_name": "move_linear"}}

    app = build_default_langgraph_app(LocalToolRegistry(), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "移动命令需要哪些参数",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "command_tool_result"
    assert result["payload"]["tool_name"] == "lookup_command_schema"
    assert result["payload"]["tool_result"]["state"] == "command_schema_loaded"


def test_default_langgraph_app_classifies_atomic_template_tool_result():
    from robot_modbus_lite.atomic_memory import AtomicMemory

    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    def tool_decider(_payload):
        return {"tool_name": "apply_atomic_template", "args": {"text": "小正，移动到位置A"}}

    app = build_default_langgraph_app(LocalToolRegistry(atomic_memory_provider=lambda: memory), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "小正，移动到位置A",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "command_tool_result"
    assert result["payload"]["tool_name"] == "apply_atomic_template"
    assert result["payload"]["tool_result"]["state"] == "atomic_template_applied"
    assert result["payload"]["tool_result"]["data"]["generates_command"] is False


def test_default_langgraph_app_classifies_registered_flow_tool_result(tmp_path):
    from robot_modbus_lite.flow_registry import FlowEntry
    from robot_modbus_lite.service import RobotModbusService

    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.save_flow_entry(FlowEntry(name="点头", steps=[]))

    def tool_decider(_payload):
        return {"tool_name": "prepare_registered_flow_execution", "args": {"flow_name": "点头"}}

    app = build_default_langgraph_app(LocalToolRegistry(flow_service=service), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "执行点头",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "registered_flow_plan"
    assert result["payload"]["tool_name"] == "prepare_registered_flow_execution"
    assert result["payload"]["tool_result"]["state"] == "registered_flow_execution_draft"


def test_default_langgraph_app_allows_position_alias_tool_name_arg(tmp_path):
    from robot_modbus_lite.permission_service import PermissionService
    from robot_modbus_lite.position_registry import PositionRegistry

    position_registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))

    def tool_decider(_payload):
        return {
            "tool_call_id": "call-position-save",
            "tool_name": "save_position_alias",
            "args": {"name": "A", "pose": [1, 2, 3, 4, 5, 6]},
        }

    app = build_default_langgraph_app(
        LocalToolRegistry(position_registry_provider=lambda: position_registry),
        tool_decider=tool_decider,
    )

    result = app.invoke(
        {
            "text": "保存当前位置为位置A",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "memory_tool_result"
    assert result["payload"]["tool_name"] == "save_position_alias"
    assert result["payload"]["tool_result"]["state"] == "position_alias_saved"
    assert position_registry.get("A") is not None


def test_default_langgraph_app_classifies_memory_candidate_query(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})

    def tool_decider(_payload):
        return {"tool_name": "query_memory_candidates", "args": {"kind": "asr_alias"}}

    app = build_default_langgraph_app(LocalToolRegistry(memory_store=store), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "有哪些待审核经验",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "memory_tool_result"
    assert result["payload"]["tool_name"] == "query_memory_candidates"
    assert result["payload"]["tool_result"]["state"] == "memory_candidates_listed"


def test_default_langgraph_app_classifies_memory_applied_audit(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})
    store.approve_memory(candidate["memory_id"], reviewer="engineer")

    def tool_decider(_payload):
        return {"tool_name": "record_memory_applied", "args": {"memory_id": candidate["memory_id"]}}

    app = build_default_langgraph_app(LocalToolRegistry(memory_store=store), tool_decider=tool_decider)

    result = app.invoke(
        {
            "text": "移动到位置诶",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "memory_tool_result"
    assert result["payload"]["tool_name"] == "record_memory_applied"
    assert result["payload"]["tool_result"]["state"] == "memory_applied_recorded"


def test_default_langgraph_app_classifies_status_tool_result():
    snapshot = {
        "safety": {"long34": 1 << 25, "long36": 1, "long38": 0},
        "motion": {"current_func": 108},
        "hardware": {"axis_status": []},
    }

    def tool_decider(_payload):
        return {"tool_name": "get_alarm", "args": {}}

    app = build_default_langgraph_app(
        LocalToolRegistry(runtime_snapshot_provider=lambda: snapshot),
        tool_decider=tool_decider,
    )

    result = app.invoke(
        {
            "text": "现在报警是什么",
            "session_state": SessionState(thread_id="session-1").to_dict(),
            "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
        }
    )

    assert result["kind"] == "dashboard_query_action"
    assert result["payload"]["tool_name"] == "get_alarm"
    assert result["payload"]["tool_result"]["state"] == "alarm_loaded"


def test_default_langgraph_app_uses_tool_call_id_for_idempotent_decider_calls(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    def tool_decider(_payload):
        return {
            "tool_call_id": "call-1",
            "tool_name": "create_memory_candidate",
            "args": {
                "kind": "asr_alias",
                "key": "位置诶",
                "value": {"normalized": "位置A"},
            },
        }

    app = build_default_langgraph_app(LocalToolRegistry(memory_store=store), tool_decider=tool_decider)
    payload = {
        "text": "记住位置诶就是位置A",
        "session_state": SessionState(thread_id="session-1").to_dict(),
        "tool_specs": [spec.__dict__ for spec in build_local_tool_specs()],
    }

    first = app.invoke(payload)
    second = app.invoke({**payload, "session_state": first["session_state"]})

    assert first["payload"]["tool_result"] == second["payload"]["tool_result"]
    assert len(store.list_memories(status="candidate", kind="asr_alias")) == 1
    assert second["session_state"]["tool_call_history"]["call-1"]["replay_count"] == 1
