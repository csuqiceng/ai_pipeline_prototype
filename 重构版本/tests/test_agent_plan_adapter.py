from robot_modbus_lite.agent.compound import CompoundPlanResult
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter
from robot_modbus_lite.agent.service import RestrictedAgentResult
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


def test_agent_policy_mapping_requires_confirmation_for_motion():
    policy = AgentPlanAdapter().policy_for_agent_result("move_linear")

    assert policy.semantic_level == 3
    assert policy.requires_precheck is True
    assert policy.requires_confirmation is True
    assert policy.emergency_fast_path is False


def test_agent_policy_mapping_fast_paths_estop():
    policy = AgentPlanAdapter().policy_for_agent_result("sys_estop")

    assert policy.semantic_level == 5
    assert policy.requires_precheck is False
    assert policy.requires_confirmation is False
    assert policy.emergency_fast_path is True


def test_agent_policy_mapping_query_is_read_only():
    policy = AgentPlanAdapter().policy_for_agent_result("alarm_query")

    assert policy.semantic_level == 2
    assert policy.requires_precheck is False
    assert policy.requires_confirmation is False
    assert policy.emergency_fast_path is False


def test_plan_adapter_converts_chat_answer():
    result = AgentOrchestratorResult(kind="chat_answer", message="L2是运动规划预演。")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "chat"
    assert plan.actions[0].reason == "L2是运动规划预演。"
    assert plan.requires_confirmation is False
    assert plan.source == "agent_orchestrator"


def test_plan_adapter_converts_tool_flow_draft_result():
    result = AgentOrchestratorResult(
        kind="flow_draft",
        message="请问新流程的名称是什么？",
        payload={
            "raw_text": "创建流程",
            "tool_name": "start_flow_draft",
            "tool_result": {
                "ok": False,
                "state": "flow_draft_needs_name",
                "message": "请问新流程的名称是什么？",
                "data": {
                    "intent": "create_flow",
                    "draft": {"flow_name": "", "expanded_steps": []},
                    "missing_fields": ["flow_name"],
                },
                "errors": [],
            },
            "draft": {"flow_name": "", "expanded_steps": []},
            "generates_command": False,
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "flow_draft"
    assert plan.actions[0].reason == "请问新流程的名称是什么？"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.flow_draft["agent_kind"] == "flow_draft"
    assert plan.flow_draft["missing_fields"] == ["flow_name"]


def test_plan_adapter_converts_position_query_answer():
    result = type(
        "Result",
        (),
        {
            "kind": "position_query_answer",
            "message": "位置A坐标：X=350.0 Y=200.0 Z=500.0 RX=0.0 RY=90.0 RZ=0.0。没有触发机械手动作。",
            "payload": {},
        },
    )()

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "chat"
    assert "位置A坐标" in plan.actions[0].reason
    assert plan.requires_confirmation is False
    assert plan.source == "agent_orchestrator"


def test_plan_adapter_converts_memory_setting_answer():
    result = AgentOrchestratorResult(kind="memory_setting_answer", message="已更新原子函数记忆参数：速度=60.0%。")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "chat"
    assert "速度=60.0%" in plan.actions[0].reason
    assert plan.requires_confirmation is False
    assert plan.source == "agent_orchestrator"


def test_plan_adapter_converts_position_memory_action():
    result = AgentOrchestratorResult(
        kind="position_memory_action",
        message="请求保存当前位置为位置A。",
        payload={
            "action_type": "memory",
            "target": "position_save:A",
            "raw_text": "小正，保存当前位置为位置A",
            "text": "请求保存当前位置为位置A。",
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "memory"
    assert plan.actions[0].target == "position_save:A"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.requires_precheck is False


def test_plan_adapter_converts_atomic_template_action_with_record():
    record = QueryRecord(
        query_key="atomic:rest_pose",
        func_num=108,
        params={
            "target_x": 900.0,
            "target_y": 0.0,
            "target_z": 1000.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "stop_cmd": 0,
            "fuzzy_pos": 0,
            "fuzzy_spd": 1,
            "fuzzy_acc": 1,
            "fuzzy_dec": 1,
            "move_type": 0,
        },
    )
    result = AgentOrchestratorResult(
        kind="atomic_template_action",
        message="移动到默认休息姿态",
        payload={
            "action_type": "atomic_template",
            "target": record.query_key,
            "raw_text": "小正，去休息",
            "text": "移动到默认休息姿态",
            "record": record,
            "requires_confirmation": True,
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.actions[0].target == "atomic:rest_pose"
    assert plan.atomic_records["atomic:rest_pose"] is record
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True


def test_plan_adapter_converts_dashboard_query_action():
    result = AgentOrchestratorResult(
        kind="dashboard_query_action",
        message="命中看板7 通讯+设备故障诊断。",
        payload={
            "action_type": "query",
            "target": "communication_faults",
            "raw_text": "通讯正常吗",
            "text": "命中看板7 通讯+设备故障诊断。",
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "communication_faults"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.requires_precheck is False


def test_plan_adapter_passes_through_flow_draft_plan():
    flow_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "打招呼", "flow_draft", "小正，创建流程", "已生成流程草案。"),),
        source="flow_draft",
        raw_text="小正，创建流程",
        reason="已生成流程草案。",
        flow_draft={"flow_name": "打招呼"},
    )
    result = AgentOrchestratorResult(
        kind="flow_draft_plan",
        message="已生成流程草案。",
        payload={"plan": flow_plan},
    )

    assert AgentPlanAdapter().to_voice_plan(result) is flow_plan


def test_plan_adapter_passes_through_registered_flow_plan():
    flow_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "打招呼", "rule", "执行打招呼", "命中流程规则"),),
        source="rule",
        raw_text="执行打招呼",
        reason="命中流程规则",
    )
    result = AgentOrchestratorResult(
        kind="registered_flow_plan",
        message="命中流程规则",
        payload={"plan": flow_plan},
    )

    assert AgentPlanAdapter().to_voice_plan(result) is flow_plan


def test_plan_adapter_converts_compound_plan_draft_to_nonexecutable_summary():
    result = CompoundPlanResult(
        kind="compound_plan_draft",
        plan_id="compound:test",
        raw_text="走到X1000，然后等待2秒",
        created_at=100.0,
        steps=("走到X1000", "等待2秒"),
        step_results=({"kind": "waiting_confirmation"}, {"kind": "waiting_confirmation"}),
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "compound_plan"
    assert plan.actions[0].target == "compound:test"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.requires_precheck is False
    assert plan.flow_draft["agent_kind"] == "compound_plan_draft"
    assert plan.flow_draft["steps"] == ("走到X1000", "等待2秒")
    assert plan.flow_draft["step_machine"]["status"] == "waiting_step_confirmation"
    assert plan.flow_draft["step_machine"]["current_index"] == 0
    assert plan.flow_draft["step_machine"]["current_step_text"] == "走到X1000"


def test_plan_adapter_converts_compound_waiting_steps_to_flow_draft_steps():
    move = RestrictedAgentResult(
        kind="waiting_confirmation",
        intent="move_linear",
        func_id=108,
        message="等待操作者确认。",
        draft=_draft(),
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="确认移动",
    )
    delay_draft = CommandDraft(
        draft_id="delay1",
        func_id=109,
        intent="delay_blocking",
        params={"delay_sec": 2.0},
        param_sources={"delay_sec": "specified"},
        raw_text="等待2秒",
        confidence=0.95,
        precheck_result={"valid": True, "summary": "L1通过。"},
    )
    delay = RestrictedAgentResult(
        kind="waiting_confirmation",
        intent="delay_blocking",
        func_id=109,
        message="等待操作者确认。",
        draft=delay_draft,
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="确认延时",
    )
    result = CompoundPlanResult(
        kind="compound_plan_draft",
        plan_id="compound:test",
        raw_text="走到X1000，然后等待2秒",
        created_at=100.0,
        steps=("走到X1000", "等待2秒"),
        step_results=(move, delay),
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "compound_plan"
    assert plan.reason == "已生成可执行复合指令草案：2 步，等待确认执行。"
    assert plan.actions[0].reason == "已生成可执行复合指令草案：2 步，等待确认执行。"
    assert plan.flow_draft["safe_to_execute"] is True
    assert plan.flow_draft["flow_name"] == "agent_compound_test"
    assert [step["func_id"] for step in plan.flow_draft["expanded_steps"]] == [108, 109]
    assert plan.flow_draft["expanded_steps"][0]["params"]["target_x"] == 1000.0
    assert plan.flow_draft["expanded_steps"][1]["params"]["delay_sec"] == 2.0


def test_agent_policy_mapping_auxiliary_commands_require_confirmation():
    for intent in ("delay_blocking", "delay_parallel", "io"):
        policy = AgentPlanAdapter().policy_for_agent_result(intent)

        assert policy.semantic_level == 3
        assert policy.requires_confirmation is True
        assert policy.emergency_fast_path is False


def test_adapter_converts_waiting_confirmation_without_query_record():
    draft = _draft()
    result = RestrictedAgentResult(
        kind="waiting_confirmation",
        intent="move_linear",
        func_id=108,
        message="等待操作者确认。",
        draft=draft,
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="【复述确认】Func108 直线插补",
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_draft"
    assert plan.actions[0].target == "draft1"
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True
    assert plan.atomic_records == {}
    assert plan.flow_draft["agent_kind"] == "waiting_confirmation"
    assert plan.flow_draft["draft_id"] == "draft1"
    assert plan.flow_draft["confirmation_text"] == "【复述确认】Func108 直线插补"


def test_adapter_converts_tool_confirm_plan_to_agent_draft_confirmation():
    draft = {
        "draft_id": "draft-tool",
        "func_id": 108,
        "intent": "move_linear",
        "params": {"target_x": 100.0, "target_y": 0.0, "target_z": 100.0},
        "param_sources": {"target_x": "specified"},
        "raw_text": "移动到X100",
        "confidence": 0.9,
        "precheck_result": {"valid": True, "summary": "L1通过。"},
    }
    result = AgentOrchestratorResult(
        kind="confirm_plan",
        message="已创建待确认计划。",
        payload={
            "draft": draft,
            "precheck": {"valid": True, "summary": "L1通过。"},
            "tool_result": {
                "data": {
                    "draft_id": "draft-tool",
                    "confirmation_text": "【复述确认】Func108 直线插补",
                    "expires_at": 70.0,
                }
            },
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_draft"
    assert plan.actions[0].target == "draft-tool"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True
    assert plan.flow_draft["agent_kind"] == "waiting_confirmation"
    assert plan.flow_draft["draft_id"] == "draft-tool"
    assert plan.flow_draft["confirmation_text"] == "【复述确认】Func108 直线插补"
    assert plan.flow_draft["precheck_result"]["valid"] is True


def test_adapter_converts_tool_confirm_result_to_nonexecuting_ack():
    result = AgentOrchestratorResult(
        kind="confirm_result",
        message="确认已通过，已生成执行记录。",
        payload={
            "raw_text": "确认执行",
            "tool_name": "confirm_pending_plan",
            "tool_result": {
                "ok": True,
                "state": "confirmed",
                "message": "确认已通过，已生成执行记录。",
                "data": {
                    "draft_id": "draft-1",
                    "query_record": {
                        "query_key": "agent:draft-1",
                        "func_num": 109,
                        "params": {"delay_sec": 2.0},
                        "description": "等待2秒",
                    },
                },
                "errors": [],
            },
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_confirmed"
    assert plan.actions[0].target == "draft-1"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.requires_precheck is False
    assert plan.flow_draft["agent_kind"] == "confirmed"
    assert plan.flow_draft["query_record"]["func_num"] == 109


def test_adapter_converts_confirm_rejected_to_clarification():
    result = AgentOrchestratorResult(
        kind="confirm_rejected",
        message="当前没有待确认计划，不能确认执行。",
        payload={
            "raw_text": "确认执行",
            "tool_name": "query_pending_confirm",
            "tool_result": {
                "ok": False,
                "state": "confirm_not_found",
                "message": "当前没有待确认计划，不能确认执行。",
                "data": {},
                "errors": [{"code": "CONFIRM_NOT_FOUND", "message": "当前没有待确认计划，不能确认执行。"}],
            },
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "clarification"
    assert plan.source == "agent_orchestrator"
    assert plan.reason == "当前没有待确认计划，不能确认执行。"
    assert plan.requires_confirmation is False
    assert plan.flow_draft["agent_kind"] == "confirm_rejected"


def test_adapter_converts_confirm_cancelled_to_nonexecuting_ack():
    result = AgentOrchestratorResult(
        kind="confirm_cancelled",
        message="已取消待确认计划。",
        payload={
            "raw_text": "取消执行",
            "tool_name": "cancel_pending_plan",
            "tool_result": {
                "ok": True,
                "state": "cancelled",
                "message": "已取消待确认计划。",
                "data": {"draft_id": "draft-1"},
                "errors": [],
            },
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_cancelled"
    assert plan.actions[0].target == "draft-1"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.flow_draft["agent_kind"] == "cancelled"


def test_adapter_converts_feedback_vote_recorded_to_chat_ack():
    result = AgentOrchestratorResult(
        kind="feedback_vote_recorded",
        message="用户反馈已记录。",
        payload={
            "raw_text": "这个回答没用",
            "tool_name": "record_feedback_vote",
            "tool_result": {
                "ok": True,
                "state": "feedback_vote_recorded",
                "message": "用户反馈已记录。",
                "data": {"vote": {"vote_id": "vote-1", "vote": "down"}},
                "errors": [],
            },
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "chat"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.flow_draft["agent_kind"] == "feedback_vote_recorded"


def test_adapter_converts_func112_waiting_confirmation_to_executable_agent_draft():
    draft = CommandDraft(
        draft_id="path1",
        func_id=112,
        intent="continuous_path",
        params={
            "target_x": 1000.0,
            "target_y": 20.0,
            "target_z": 300.0,
            "target_rx": 1.0,
            "target_ry": 2.0,
            "target_rz": 3.0,
            "spd_pct": 40.0,
            "acc_pct": 45.0,
            "dec_pct": 50.0,
            "stop_cmd": 0,
            "fuzzy_pos": 0,
            "fuzzy_spd": 0,
            "fuzzy_acc": 0,
            "fuzzy_dec": 0,
            "move_type": 0,
            "position_increment": 0,
        },
        param_sources={},
        raw_text="规划路径走到 X1000 Z300",
        confidence=0.9,
    )
    result = RestrictedAgentResult(
        kind="waiting_confirmation",
        intent="continuous_path",
        func_id=112,
        message="等待操作者确认。",
        draft=draft,
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="【复述确认】Func112 连续路径运动",
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_draft"
    assert plan.actions[0].target == "path1"
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True
    assert plan.flow_draft["agent_kind"] == "waiting_confirmation"
    assert plan.flow_draft["func_id"] == 112
    assert plan.flow_draft["confirmation_text"] == "【复述确认】Func112 连续路径运动"


def test_adapter_converts_clarification_to_nonexecutable_unknown():
    result = RestrictedAgentResult(kind="clarification", intent="unknown", message="请补充明确的坐标。")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "unknown"
    assert plan.requires_confirmation is False
    assert plan.reason == "请补充明确的坐标。"


def test_adapter_converts_orchestrator_clarification_to_clarification_action():
    result = AgentOrchestratorResult(
        kind="clarification",
        message="请补充明确的坐标、方向或参数。",
        payload={"understanding": {"raw_text": "往安全一点的位置挪一下"}},
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "clarification"
    assert plan.actions[0].source == "agent_orchestrator"
    assert plan.raw_text == "往安全一点的位置挪一下"
    assert plan.reason == "请补充明确的坐标、方向或参数。"
    assert plan.requires_confirmation is False
    assert plan.requires_precheck is False


def test_adapter_converts_blocked_to_nonexecutable_unknown():
    result = RestrictedAgentResult(kind="blocked", intent="move_linear", func_id=108, message="当前设备运动中")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "unknown"
    assert plan.source == "restricted_agent"
    assert plan.reason == "当前设备运动中"


def test_adapter_converts_precheck_failed_with_draft_metadata():
    draft = _draft()
    result = RestrictedAgentResult(
        kind="precheck_failed",
        intent="move_linear",
        func_id=108,
        message="L1预检未通过。",
        draft=draft,
        precheck_result={"valid": False, "summary": "L1预检未通过。"},
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_blocked"
    assert plan.requires_confirmation is False
    assert plan.flow_draft["draft_id"] == "draft1"
    assert plan.flow_draft["precheck_result"]["valid"] is False


def test_adapter_converts_tool_precheck_failed_to_agent_blocked_plan():
    result = AgentOrchestratorResult(
        kind="precheck_failed",
        message="L1预检未通过。",
        payload={
            "draft": {
                "draft_id": "draft-tool",
                "func_id": 108,
                "intent": "move_linear",
                "params": {"target_x": 100.0},
                "param_sources": {"target_x": "specified"},
                "raw_text": "移动到X100",
                "confidence": 0.9,
            },
            "precheck": {"valid": False, "summary": "L1预检未通过。"},
        },
    )

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "agent_blocked"
    assert plan.actions[0].target == "draft-tool"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False
    assert plan.flow_draft["agent_kind"] == "precheck_failed"
    assert plan.flow_draft["precheck_result"]["valid"] is False


def test_adapter_converts_emergency_bypass_to_system_action():
    result = RestrictedAgentResult(kind="bypass", intent="sys_estop", func_id=104, message="规则旁路")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "system"
    assert plan.actions[0].target == "sys_estop"
    assert plan.requires_confirmation is False
    assert plan.priority == "high"


def test_adapter_converts_alarm_query_to_query_action():
    result = RestrictedAgentResult(kind="bypass", intent="alarm_query", message="规则旁路")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "alarm_query"
    assert plan.requires_confirmation is False


def test_adapter_converts_status_query_to_query_action():
    result = RestrictedAgentResult(kind="bypass", intent="status_query", message="规则旁路")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "status_query"
    assert plan.requires_confirmation is False


def _draft() -> CommandDraft:
    params = {
        "target_x": 1000.0,
        "target_y": 20.0,
        "target_z": 300.0,
        "target_rx": 1.0,
        "target_ry": 2.0,
        "target_rz": 3.0,
        "spd_pct": 60.0,
        "acc_pct": 45.0,
        "dec_pct": 50.0,
        "stop_cmd": 0,
        "fuzzy_pos": 0,
        "fuzzy_spd": 0,
        "fuzzy_acc": 0,
        "fuzzy_dec": 0,
        "move_type": 0,
    }
    return CommandDraft(
        draft_id="draft1",
        func_id=108,
        intent="move_linear",
        params=params,
        param_sources={key: "specified" for key in params},
        raw_text="走到 X1000",
        confidence=0.9,
    )
