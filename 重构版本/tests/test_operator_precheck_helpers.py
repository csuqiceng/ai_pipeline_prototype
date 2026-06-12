import json
import time
from types import SimpleNamespace
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStackedWidget

from robot_modbus_lite.avoidance_config import AvoidanceConfig, SafePoint
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.broadcast_queue import BroadcastQueue
from robot_modbus_lite.clarification_state import PendingClarification
from robot_modbus_lite.dashboard import DashboardCache
from robot_modbus_lite.emergency_channel import EmergencyDecision
from robot_modbus_lite.execution_plan import ExecutionPlanStatus
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.models import FlowDefinition, QueryRecord
from robot_modbus_lite.kinematics_engine import InverseKinematicsResult
from robot_modbus_lite.flow_management_mixin import FlowManagementMixin
from robot_modbus_lite.nlp_mixin import NlpMixin
from robot_modbus_lite.operator_ui_mixin import OperatorSceneState, OperatorUiMixin
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionRegistry
from robot_modbus_lite.response_builder import ResponseBuilder, ResponseMessage
from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.speech_broadcast import CallableSpeechSink, Pyttsx3SpeechSink, WindowsSapiSpeechSink
from robot_modbus_lite.system_config import AxisRangeConfig
from robot_modbus_lite.agent_tools.tool_result import ToolResult
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


class DummyOperator(OperatorUiMixin):
    pass


class DummyNlpMemory(NlpMixin):
    pass


class DummyFlowManager(FlowManagementMixin):
    pass


def log_args(entry):
    if (
        isinstance(entry, tuple)
        and len(entry) == 2
        and isinstance(entry[0], tuple)
        and isinstance(entry[1], dict)
    ):
        return entry[0]
    return entry


def progress_bar():
    state = {"range": None, "value": None, "format": None}
    return SimpleNamespace(
        state=state,
        setRange=lambda low, high: state.update(range=(low, high)),
        setValue=lambda value: state.update(value=value),
        setFormat=lambda text: state.update(format=text),
    )


def flow_draft_payload():
    return {
        "flow_name": "打招呼",
        "positions": [
            {"name": "home", "pose": [1475.0, 0.0, 1545.0, 0.0, 0.0, 0.0]},
        ],
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动",
                "func_id": 108,
                "position_name": "home",
                "description": "移动到home",
                "params": {
                    "target_x": 1475.0,
                    "target_y": 0.0,
                    "target_z": 1545.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 50.0,
                    "acc_pct": 50.0,
                    "dec_pct": 50.0,
                    "move_type": 0,
                },
            },
            {
                "step_id": 2,
                "action": "Ry正转",
                "func_id": 107,
                "description": "小臂上下点头:Ry正转",
                "params": {
                    "axis_no": 10,
                    "pos_val": 15.0,
                    "spd_pct": 50.0,
                    "acc_pct": 50.0,
                    "dec_pct": 50.0,
                },
            },
            {
                "step_id": 3,
                "action": "Ry反转",
                "func_id": 107,
                "description": "小臂上下点头:Ry反转",
                "params": {
                    "axis_no": 10,
                    "pos_val": -15.0,
                    "spd_pct": 50.0,
                    "acc_pct": 50.0,
                    "dec_pct": 50.0,
                },
            },
        ],
    }


def make_flow_draft_operator(tmp_path):
    dummy = DummyOperator()
    dummy.runtime_root = tmp_path
    dummy.json_path = tmp_path / "data" / "query_table.json"
    dummy.table = {}
    dummy.service = RobotModbusService(
        dummy.json_path,
        flows_path=tmp_path / "data" / "flows.json",
        table=dummy.table,
    )
    dummy._authenticated_role = "engineer"
    dummy._append_log = lambda *args, **kwargs: None
    dummy._show_info = lambda *args, **kwargs: None
    dummy._show_warning = lambda *args, **kwargs: None
    dummy._position_registry = lambda: PositionRegistry(
        tmp_path / "data" / "position_registry.json",
        permission=PermissionService("engineer"),
    )
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda *args, **kwargs: None
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "nlp_sequence_running", busy)
    return dummy


def test_operator_save_flow_draft_persists_positions_templates_and_flow(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)

    ok, message, flow_name = dummy._operator_save_flow_draft(flow_draft_payload())

    assert ok is True
    assert flow_name == "打招呼"
    assert "已保存" in message
    assert len(dummy.table) == 3
    assert all(key.startswith("flowdraft:打招呼:") for key in dummy.table)
    assert dummy.service.get_flow("打招呼").steps == tuple(dummy.table.keys())
    assert dummy.service.get_flow_entry("打招呼") is not None
    assert dummy._position_registry().get("home").pose == (1475.0, 0.0, 1545.0, 0.0, 0.0, 0.0)
    assert dummy.json_path.exists()


def test_operator_pending_clarification_answer_updates_flow_draft(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.session_id = "flow-session"
    draft = flow_draft_payload()
    draft["expanded_steps"][0]["params"] = {"spd_pct": 50.0}
    dummy._operator_set_pending_flow_draft(draft)
    service = dummy._operator_execution_plan_service()
    service.set_pending_flow_draft(draft)
    service.pending_plan = service.pending_plan.transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    service.add_clarifications(
        [
            PendingClarification.new(
                service.pending_plan.plan_id,
                1,
                "target_pose",
                "目标坐标是多少？",
                ("pose",),
                now=10.0,
            )
        ]
    )

    handled = dummy._handle_operator_ui_command("900,0,1000,0,0,0")

    assert handled is True
    updated = dummy._operator_pending_flow_draft
    params = updated["expanded_steps"][0]["params"]
    runtime_params = dummy._operator_session_state().current_flow_draft["expanded_steps"][0]["params"]
    assert params["target_x"] == 900.0
    assert params["target_z"] == 1000.0
    assert runtime_params["target_x"] == 900.0
    assert runtime_params["target_z"] == 1000.0
    assert updated["needs_precheck"] is True
    assert service.current_clarification() is None


def test_operator_pending_clarification_answer_archives_non_execution_result(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    draft = flow_draft_payload()
    draft["expanded_steps"][0]["params"] = {"spd_pct": 50.0}
    dummy._operator_set_pending_flow_draft(draft)
    service = dummy._operator_execution_plan_service()
    service.set_pending_flow_draft(draft)
    service.pending_plan = service.pending_plan.transition_to(ExecutionPlanStatus.NEED_CLARIFICATION)
    service.add_clarifications(
        [
            PendingClarification.new(
                service.pending_plan.plan_id,
                1,
                "target_pose",
                "目标坐标是多少？",
                ("pose",),
                now=10.0,
            )
        ]
    )
    archived = []
    dummy._archive_non_execution_result = lambda *, result, final_text: archived.append((result, final_text)) or True

    handled = dummy._operator_handle_pending_clarification_answer("900,0,1000,0,0,0")

    assert handled is True
    assert archived
    assert archived[0][0] == "clarification_answer"
    assert "当前流程草案已更新" in archived[0][1]


def make_context_operator(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.chat_messages = []
    dummy.logs = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: dummy.chat_messages.append((role, text, kwargs))
    dummy._append_log = lambda *args, **kwargs: dummy.logs.append(args)
    dummy._operator_prepare_plan_prechecks = lambda plan: setattr(dummy, "prepared_plan", plan)
    dummy._operator_publish_ai_answer_for_speech = lambda text: setattr(dummy, "spoken_answer", text)
    return dummy


def test_operator_pending_confirm_can_modify_acceleration_only(tmp_path):
    dummy = make_context_operator(tmp_path)
    record = QueryRecord(
        query_key="move",
        func_num=108,
        params={
            "target_x": 100.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "spd_pct": 60.0,
            "acc_pct": 60.0,
            "dec_pct": 60.0,
        },
        description="移动",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
        atomic_records={"move": record},
    )

    handled = dummy._handle_operator_ui_command("我想把加速度改为 50%。")

    assert handled is True
    assert record.params["spd_pct"] == 60.0
    assert record.params["acc_pct"] == 50.0
    assert record.params["dec_pct"] == 60.0
    assert "加速度" in dummy.chat_messages[-1][1]


def test_operator_pending_confirm_answers_current_motion_params(tmp_path):
    dummy = make_context_operator(tmp_path)
    record = QueryRecord(
        query_key="move",
        func_num=108,
        params={"target_x": 100.0, "target_y": 0.0, "target_z": 0.0, "spd_pct": 60.0, "acc_pct": 50.0, "dec_pct": 40.0},
        description="移动",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
        atomic_records={"move": record},
    )

    handled = dummy._handle_operator_ui_command("现在的运动参数是哪些？")

    assert handled is True
    answer = dummy.chat_messages[-1][1]
    assert "当前待确认计划" in answer
    assert "target_x=100" in answer
    assert "spd_pct=60" in answer
    assert "acc_pct=50" in answer


def test_operator_saved_flow_append_request_creates_edit_context_and_followup_updates_step(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.table["move_a"] = QueryRecord(
        query_key="move_a",
        func_num=108,
        params={
            "target_x": 10.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "move_type": 0,
        },
        description="移动到A",
    )
    dummy.service.save_flow(FlowDefinition(name="测试", steps=("move_a",), step_delay_ms=500))
    dummy.current_flow_name = "测试"

    handled = dummy._handle_operator_ui_command("我想在这个测试流程后面添加一个移动到位置 a。")

    assert handled is True
    draft = dummy._operator_pending_flow_draft
    assert draft["flow_name"] == "测试"
    assert len(draft["expanded_steps"]) == 2
    assert dummy._operator_execution_plan_service().current_clarification() is not None

    handled = dummy._handle_operator_ui_command("X100 Y0 Z0 RX0 RY0 RZ0")

    assert handled is True
    updated = dummy._operator_pending_flow_draft
    params = updated["expanded_steps"][1]["params"]
    assert params["target_x"] == 100.0
    assert params["target_z"] == 0.0


def test_operator_llm_flow_append_intent_enters_saved_flow_edit_context(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.table["move_a"] = QueryRecord(
        query_key="move_a",
        func_num=108,
        params={
            "target_x": 10.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "move_type": 0,
        },
        description="移动到A",
    )
    dummy.service.save_flow(FlowDefinition(name="测试", steps=("move_a",), step_delay_ms=500))
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "添加位置A", "我理解你要追加一步。"),),
        source="agent_orchestrator",
        raw_text="添加位置A",
        reason="我理解你要追加一步。",
        flow_draft={
            "llm_context_intent": {
                "kind": "flow_append_step",
                "target_flow": "测试",
                "step_hint": "移动到位置A",
                "missing_fields": ["target_pose"],
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    draft = dummy._operator_pending_flow_draft
    assert draft["flow_name"] == "测试"
    assert len(draft["expanded_steps"]) == 2
    assert dummy._operator_execution_plan_service().current_clarification() is not None


def test_operator_llm_confirm_modify_intent_updates_pending_confirmation(tmp_path):
    dummy = make_context_operator(tmp_path)
    record = QueryRecord(
        query_key="move",
        func_num=108,
        params={"target_x": 100.0, "spd_pct": 60.0, "acc_pct": 60.0, "dec_pct": 60.0},
        description="移动",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
        atomic_records={"move": record},
    )
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "加速度调低", "已识别为修改待确认参数。"),),
        source="agent_orchestrator",
        raw_text="加速度调低",
        reason="已识别为修改待确认参数。",
        flow_draft={
            "llm_context_intent": {
                "kind": "confirm_modify",
                "field": "acc_pct",
                "value_text": "50%",
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    assert record.params["spd_pct"] == 60.0
    assert record.params["acc_pct"] == 50.0
    assert record.params["dec_pct"] == 60.0
    assert "加速度" in dummy.chat_messages[-1][1]


def test_operator_llm_flow_query_intent_shows_saved_flow(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.table["move_a"] = QueryRecord(
        query_key="move_a",
        func_num=108,
        params={"target_x": 10.0, "target_y": 0.0, "target_z": 0.0, "spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
        description="移动到A",
    )
    dummy.service.save_flow(FlowDefinition(name="测试", steps=("move_a",), step_delay_ms=500))
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "看测试流程", "已识别为流程查询。"),),
        source="agent_orchestrator",
        raw_text="看测试流程",
        reason="已识别为流程查询。",
        flow_draft={"llm_context_intent": {"kind": "flow_query", "target_flow": "测试"}},
    )

    dummy._execute_nlp_plan(plan)

    answer = dummy.chat_messages[-1][1]
    assert "流程 测试" in answer
    assert "移动到A" in answer


def test_operator_flow_list_query_lists_all_saved_flows(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.service.save_flow(FlowDefinition(name="点头", steps=(), step_delay_ms=500))
    dummy.service.save_flow(FlowDefinition(name="打招呼", steps=(), step_delay_ms=500))

    handled = dummy._handle_operator_ui_command("现在有哪些流程")

    assert handled is True
    answer = dummy.chat_messages[-1][1]
    assert "当前共有 2 个流程" in answer
    assert "点头" in answer
    assert "打招呼" in answer
    assert "查看" in answer
    assert "执行" in answer


def test_operator_flow_list_query_accepts_those_flows_wording(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.service.save_flow(FlowDefinition(name="点头", steps=(), step_delay_ms=500))

    handled = dummy._handle_operator_ui_command("我有那些流程")

    assert handled is True
    assert "当前共有 1 个流程" in dummy.chat_messages[-1][1]
    assert "点头" in dummy.chat_messages[-1][1]


def test_operator_flow_count_query_uses_flow_list_not_dashboard_status(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.service.save_flow(FlowDefinition(name="点头", steps=(), step_delay_ms=500))
    dummy._operator_handle_dashboard_query = lambda text: (_ for _ in ()).throw(AssertionError("should not route to dashboard"))

    handled = dummy._handle_operator_ui_command("我要你看看总共有多少个流程")

    assert handled is True
    assert "当前共有 1 个流程" in dummy.chat_messages[-1][1]


def test_operator_llm_flow_list_intent_lists_all_saved_flows(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.service.save_flow(FlowDefinition(name="点头", steps=(), step_delay_ms=500))
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "有哪些流程", "已识别为流程列表查询。"),),
        source="agent_orchestrator",
        raw_text="有哪些流程",
        reason="已识别为流程列表查询。",
        flow_draft={"llm_context_intent": {"kind": "flow_list"}},
    )

    dummy._execute_nlp_plan(plan)

    assert "当前共有 1 个流程" in dummy.chat_messages[-1][1]
    assert "点头" in dummy.chat_messages[-1][1]


def test_operator_llm_flow_create_intent_starts_empty_flow_draft(tmp_path):
    dummy = make_context_operator(tmp_path)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "创建测试流程", "已识别为创建流程。"),),
        source="agent_orchestrator",
        raw_text="创建测试流程",
        reason="已识别为创建流程。",
        flow_draft={
            "llm_context_intent": {
                "kind": "flow_create",
                "flow_name": "测试流程",
                "suggested_reply": "已开始创建测试流程，请继续添加步骤。",
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    draft = dummy._operator_pending_flow_draft
    assert draft["flow_name"] == "测试流程"
    assert draft["expanded_steps"] == []
    answer = dummy.chat_messages[-1][1]
    assert "怎么添加步骤" in answer
    assert "例如" in answer
    assert "保存并执行" in answer


def test_operator_clarification_plan_is_published_to_speech(tmp_path):
    dummy = make_context_operator(tmp_path)
    spoken = []
    dummy._operator_publish_ai_answer_for_speech = lambda text: spoken.append(text)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "创建流程", "请问新流程叫什么名字？"),),
        source="agent_orchestrator",
        raw_text="创建流程",
        reason="请问新流程叫什么名字？",
        semantic_level=1,
    )

    handled = dummy._operator_handle_flow_draft_plan(plan)

    assert handled is True
    assert spoken == ["请问新流程叫什么名字？"]


def test_operator_text_new_flow_starts_local_flow_draft_without_engineer_fallback(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_handle_engineer_voice_command_spec = (
        lambda text: (_ for _ in ()).throw(AssertionError("should not route to engineer voice command"))
    )

    handled = dummy._handle_operator_ui_command("新建一个流程")

    assert handled is True
    draft = dummy._operator_pending_flow_draft
    assert draft["flow_name"] == "未命名流程"
    assert draft["expanded_steps"] == []
    answer = dummy.chat_messages[-1][1]
    assert "已开始创建流程草案" in answer
    assert "怎么添加步骤" in answer
    assert "移动到位置A" in answer


def test_operator_add_flow_phrase_starts_local_flow_draft(tmp_path):
    dummy = make_context_operator(tmp_path)

    handled = dummy._handle_operator_ui_command("我想添加流程")

    assert handled is True
    assert dummy._operator_pending_flow_draft["flow_name"] == "未命名流程"
    assert dummy._operator_pending_flow_creation_followup is True
    assert "已开始创建流程草案" in dummy.chat_messages[-1][1]


def test_operator_pending_flow_draft_can_rename_from_followup(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "未命名流程",
        "expanded_steps": [],
        "positions": [],
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command("新流程叫测试")

    assert handled is True
    assert dummy._operator_pending_flow_draft["flow_name"] == "测试"
    assert "流程名称已改为“测试”" in dummy.chat_messages[-1][1]


def test_operator_pending_flow_draft_appends_missing_position_step(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [],
        "positions": [],
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command("移动到位置a")

    assert handled is True
    step = dummy._operator_pending_flow_draft["expanded_steps"][0]
    assert step["description"] == "移动到位置a"
    assert step["func_id"] == 108
    assert step["target_label"] == "A"
    assert "请补充位置A的坐标" in dummy.chat_messages[-1][1]


def test_operator_pending_flow_draft_coordinate_answer_updates_missing_position_not_dashboard(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_handle_dashboard_query = (
        lambda text: (_ for _ in ()).throw(AssertionError("should not route to dashboard"))
    )
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "move_position",
                "func_id": 108,
                "target_label": "A",
                "description": "移动到位置A",
                "params": {
                    "spd_pct": 50.0,
                    "acc_pct": 50.0,
                    "dec_pct": 50.0,
                    "move_type": 0,
                },
            }
        ],
        "positions": [],
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command("那位置a x=475，y=0，z=545，rx=0，ry=0，rz=0；速度=30%")

    assert handled is True
    step = dummy._operator_pending_flow_draft["expanded_steps"][0]
    assert step["params"]["target_x"] == 475.0
    assert step["params"]["target_z"] == 545.0
    assert step["params"]["target_rz"] == 0.0
    assert step["params"]["spd_pct"] == 30.0
    assert step["params"]["acc_pct"] == 30.0
    assert step["params"]["dec_pct"] == 30.0
    assert "已补齐位置A参数" in dummy.chat_messages[-1][1]


def test_operator_pending_flow_draft_coordinate_answer_archives_non_execution_result(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "move_position",
                "func_id": 108,
                "target_label": "B",
                "description": "移动到位置B",
                "params": {"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0, "move_type": 0},
            }
        ],
        "positions": [],
        "needs_precheck": True,
    }
    archived = []
    dummy._archive_non_execution_result = lambda *, result, final_text: archived.append((result, final_text)) or True

    handled = dummy._operator_handle_pending_flow_draft_edit("位置B X475 Y0 Z545 RX0 RY0 RZ0，速度30%")

    assert handled is True
    assert archived
    assert archived[0][0] == "flow_draft_edit"
    assert "已补齐位置B参数" in archived[0][1]


def test_operator_pending_flow_draft_appends_spoken_multi_step_with_inline_position(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [],
        "positions": [
            {"name": "home", "pose": [1475.0, 0.0, 1545.0, 0.0, 0.0, 0.0]},
        ],
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command(
        "步骤一，移动到位置 a x 200 Y0 Z700 速度 30% 速度二，等待 2 秒。步骤三，移动到 home。"
    )

    assert handled is True
    steps = dummy._operator_pending_flow_draft["expanded_steps"]
    assert len(steps) == 3
    assert steps[0]["func_id"] == 108
    assert steps[0]["target_label"] == "A"
    assert steps[0]["params"]["target_x"] == 200.0
    assert steps[0]["params"]["target_y"] == 0.0
    assert steps[0]["params"]["target_z"] == 700.0
    assert steps[0]["params"]["spd_pct"] == 30.0
    assert steps[1]["func_id"] == 109
    assert steps[1]["params"]["delay_sec"] == 2.0
    assert steps[2]["func_id"] == 108
    assert steps[2]["target_label"] == "home"


def test_operator_pending_flow_draft_splits_then_move_steps(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [],
        "positions": [
            {"name": "home", "pose": [1475.0, 0.0, 1545.0, 0.0, 0.0, 0.0]},
        ],
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command("第一步 移动到位置a 然后移动到home")

    assert handled is True
    steps = dummy._operator_pending_flow_draft["expanded_steps"]
    assert len(steps) == 2
    assert steps[0]["func_id"] == 108
    assert steps[0]["target_label"] == "A"
    assert "然后" not in steps[0]["description"]
    assert steps[1]["func_id"] == 108
    assert steps[1]["target_label"] == "home"


def test_operator_pending_flow_draft_uses_query_table_positions_when_registry_missing(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 45.0,
                "acc_pct": 45.0,
                "dec_pct": 45.0,
                "move_type": 0,
            },
        ),
        "位置B": QueryRecord(
            query_key="位置B",
            func_num=108,
            description="移动到位置B",
            keywords="B点 位置B",
            params={
                "target_x": 600.0,
                "target_y": 0.0,
                "target_z": 1000.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "acc_pct": 50.0,
                "dec_pct": 50.0,
                "move_type": 0,
            },
        ),
    }
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [],
        "positions": dummy._operator_position_registry_draft_items(),
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command("第一步 移动到位置a 然后移动到位置b")

    assert handled is True
    steps = dummy._operator_pending_flow_draft["expanded_steps"]
    assert len(steps) == 2
    assert steps[0]["target_label"] == "A"
    assert steps[0]["params"]["target_x"] == 1000.0
    assert steps[0]["params"]["target_z"] == 800.0
    assert steps[0]["params"]["target_ry"] == 90.0
    assert steps[0]["params"]["spd_pct"] == 45.0
    assert steps[1]["target_label"] == "B"
    assert steps[1]["params"]["target_x"] == 600.0
    assert steps[1]["params"]["target_z"] == 1000.0


def test_operator_position_draft_items_prefer_registry_and_mark_query_table_conflict(tmp_path):
    dummy = make_context_operator(tmp_path)
    ok, message = dummy._position_registry().set_position("home", (1475, 0, 1545, 0, 0, 0), created_by="operator")
    assert ok, message
    dummy.table = {
        "home": QueryRecord(
            query_key="home",
            func_num=108,
            description="移动到home",
            keywords="home 回home",
            params={
                "target_x": 1400.0,
                "target_y": 0.0,
                "target_z": 1270.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "move_type": 0,
            },
        )
    }

    items = dummy._operator_position_registry_draft_items()

    home = next(item for item in items if item["name"].lower() == "home")
    assert home["pose"] == [1475.0, 0.0, 1545.0, 0.0, 0.0, 0.0]
    assert home["source"] == "position_registry"
    assert home["conflict_sources"] == ["query_table"]


def test_operator_pending_flow_draft_save_short_command_saves_current_draft(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    archived = []
    dummy._archive_non_execution_result = lambda *, result, final_text: archived.append((result, final_text)) or True

    handled = dummy._handle_operator_ui_command("保存")

    assert handled is True
    assert dummy._operator_pending_flow_draft is None
    assert dummy.service.get_flow("打招呼") is not None
    assert archived
    assert archived[-1][0] == "flow_draft_saved"


def test_operator_context_query_answers_position_from_pending_flow_draft(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "move_position",
                "func_id": 108,
                "target_label": "A",
                "description": "移动到位置A",
                "params": {
                    "target_x": 200.0,
                    "target_y": 0.0,
                    "target_z": 700.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 30.0,
                    "acc_pct": 30.0,
                    "dec_pct": 30.0,
                    "move_type": 0,
                },
            }
        ],
        "positions": [],
        "needs_precheck": True,
    }

    handled = dummy._handle_operator_ui_command("我的位置a的数据在哪里")

    assert handled is True
    answer = dummy.chat_messages[-1][1]
    assert "当前流程草案中的位置A" in answer
    assert "x=200" in answer
    assert "z=700" in answer


def test_operator_flow_and_command_query_answers_both(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.service.save_flow(FlowDefinition(name="点头", steps=(), step_delay_ms=500))

    handled = dummy._handle_operator_ui_command("现在有哪些命令和流程")

    assert handled is True
    answer = dummy.chat_messages[-1][1]
    assert "当前共有 1 个流程" in answer
    assert "可用命令示例" in answer
    assert "小正，执行点头流程" in answer


def test_operator_engineer_new_flow_command_returns_to_agent_route(tmp_path):
    from robot_modbus_lite.engineer_voice_commands import EngineerVoiceCommandSpec

    dummy = make_context_operator(tmp_path)
    spec = EngineerVoiceCommandSpec("流程管理", "新增流程", "new_flow", ("新增流程", "新建流程"))

    handled = dummy._operator_execute_engineer_voice_command_spec(spec, raw_text="新建流程")

    assert handled is False
    assert dummy.chat_messages == []
    assert not any("未接入" in str(entry) for entry in dummy.logs)


def test_operator_llm_flow_modify_step_intent_updates_pending_flow_draft(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "第二步加速度30", "已识别为修改流程步骤。"),),
        source="agent_orchestrator",
        raw_text="第二步加速度30",
        reason="已识别为修改流程步骤。",
        flow_draft={
            "llm_context_intent": {
                "kind": "flow_modify_step",
                "step_index": 2,
                "field": "acc_pct",
                "value_text": "30%",
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    params = dummy._operator_pending_flow_draft["expanded_steps"][1]["params"]
    assert params["spd_pct"] == 50.0
    assert params["acc_pct"] == 30.0
    assert params["dec_pct"] == 50.0


def test_operator_llm_dashboard_query_intent_uses_local_dashboard_query(tmp_path):
    dummy = make_context_operator(tmp_path)
    called = []
    dummy._operator_handle_dashboard_query = lambda text: called.append(text) or True
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "现在状态怎么样", "已识别为状态查询。"),),
        source="agent_orchestrator",
        raw_text="现在状态怎么样",
        reason="已识别为状态查询。",
        flow_draft={"llm_context_intent": {"kind": "dashboard_query", "query_text": "现在状态怎么样"}},
    )

    dummy._execute_nlp_plan(plan)

    assert called == ["现在状态怎么样"]


def test_operator_llm_suggestion_intent_is_answer_only(tmp_path):
    dummy = make_context_operator(tmp_path)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "建议怎么做", "已识别为建议。"),),
        source="agent_orchestrator",
        raw_text="建议怎么做",
        reason="已识别为建议。",
        flow_draft={
            "llm_context_intent": {
                "kind": "suggestion",
                "suggested_reply": "建议先暂停当前流程，再修改第二步参数。",
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "_operator_pending_flow_draft", None) is None
    assert "先暂停当前流程" in dummy.chat_messages[-1][1]


def test_operator_llm_command_candidate_reenters_local_confirmation_path(tmp_path):
    dummy = make_context_operator(tmp_path)
    candidate_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "restricted_agent", "小正，X100", "等待确认"),),
        source="restricted_agent",
        raw_text="小正，X100",
        reason="等待确认",
        requires_confirmation=True,
        atomic_records={
            "move": QueryRecord(
                query_key="move",
                func_num=108,
                params={"target_x": 100.0, "spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
                description="移动",
            )
        },
    )
    dummy._operator_try_agent_orchestrator_plan = lambda text: candidate_plan if text == "小正，X100" else None
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "小正，去X100", "已识别为候选运动指令。"),),
        source="agent_orchestrator",
        raw_text="小正，去X100",
        reason="已识别为候选运动指令。",
        flow_draft={
            "llm_context_intent": {
                "kind": "command_candidate",
                "candidate_text": "小正，X100",
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    assert dummy._operator_pending_confirm_plan is candidate_plan
    assert "确认执行" in dummy.chat_messages[-1][1]


def test_operator_llm_command_candidate_without_original_wake_word_is_rejected(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy._operator_try_agent_orchestrator_plan = lambda text: (_ for _ in ()).throw(AssertionError("should not parse candidate"))
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "去X100", "已识别为候选运动指令。"),),
        source="agent_orchestrator",
        raw_text="去X100",
        reason="已识别为候选运动指令。",
        flow_draft={
            "llm_context_intent": {
                "kind": "command_candidate",
                "candidate_text": "小正，X100",
            }
        },
    )

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "_operator_pending_confirm_plan", None) is None
    assert "缺少“小正或小兵”唤醒词" in dummy.chat_messages[-1][1]


def test_operator_pending_flow_draft_can_edit_step_acceleration_only(tmp_path):
    dummy = make_context_operator(tmp_path)
    draft = flow_draft_payload()
    dummy._operator_pending_flow_draft = draft

    handled = dummy._handle_operator_ui_command("把第二步加速度改成30%")

    assert handled is True
    params = dummy._operator_pending_flow_draft["expanded_steps"][1]["params"]
    assert params["spd_pct"] == 50.0
    assert params["acc_pct"] == 30.0
    assert params["dec_pct"] == 50.0


def test_operator_pending_flow_draft_can_delete_last_step(tmp_path):
    dummy = make_context_operator(tmp_path)
    draft = flow_draft_payload()
    dummy._operator_pending_flow_draft = draft

    handled = dummy._handle_operator_ui_command("删除最后一步")

    assert handled is True
    updated = dummy._operator_pending_flow_draft
    assert len(updated["expanded_steps"]) == 2
    assert updated["expanded_steps"][-1]["description"] == "小臂上下点头:Ry正转"


def test_operator_running_pause_without_wake_word_uses_local_system_action(tmp_path):
    dummy = make_context_operator(tmp_path)
    dummy.flow_running = True
    dummy.actions = []
    dummy._handle_system_action = lambda action: dummy.actions.append(action)

    handled = dummy._handle_operator_ui_command("暂停。")

    assert handled is True
    assert dummy.actions == ["sys_pause"]
    assert "暂停" in dummy.chat_messages[-1][1]


def test_operator_save_flow_draft_allows_operator_to_create_flow_draft(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._authenticated_role = "operator"
    dummy._position_registry = lambda: PositionRegistry(
        tmp_path / "data" / "position_registry.json",
        permission=PermissionService("operator"),
    )

    ok, message, flow_name = dummy._operator_save_flow_draft(flow_draft_payload())

    assert ok is True
    assert flow_name == "打招呼"
    assert "已保存" in message
    assert "打招呼" in dummy.service.list_flow_names()
    assert dummy._position_registry().get("home") is not None


def test_operator_execute_text_clears_input_after_send():
    dummy = DummyOperator()
    events = []
    dummy._operator_submit_nlp_text = lambda text, **kwargs: events.append((text, kwargs)) or True
    dummy._operator_scene_override = "execute"
    dummy.nlp_input_edit = SimpleNamespace(
        value="小正，查询状态",
        clear=lambda: setattr(dummy.nlp_input_edit, "value", ""),
        toPlainText=lambda: dummy.nlp_input_edit.value,
    )
    dummy.operator_command_edit = SimpleNamespace(
        value="小正，查询状态",
        clear=lambda: setattr(dummy.operator_command_edit, "value", ""),
        text=lambda: dummy.operator_command_edit.value,
    )

    dummy._operator_execute_text()

    assert events == [("小正，查询状态", {"input_mode": "text", "add_user_message": True})]
    assert dummy.operator_command_edit.value == ""
    assert dummy.nlp_input_edit.value == ""


def test_operator_submit_nlp_text_runs_same_execute_path_for_text_input():
    dummy = DummyOperator()
    calls = []
    dummy.nlp_input_edit = SimpleNamespace(
        value="",
        setPlainText=lambda text: setattr(dummy.nlp_input_edit, "value", text),
        clear=lambda: setattr(dummy.nlp_input_edit, "value", ""),
        toPlainText=lambda: dummy.nlp_input_edit.value,
    )
    dummy.operator_voice_label = SimpleNamespace(setText=lambda text: calls.append(("label", text)))
    dummy._operator_interrupt_current_speech_for_user_input = lambda: calls.append(("interrupt", ""))
    dummy._operator_add_chat_message = lambda role, text: calls.append(("chat", role, text))
    dummy._operator_archive_text_input = lambda text: calls.append(("archive", text))
    dummy._execute_nlp_text = lambda: calls.append(("execute", dummy.nlp_input_edit.toPlainText()))

    ok = dummy._operator_submit_nlp_text("你好", input_mode="text", add_user_message=True)

    assert ok is True
    assert ("interrupt", "") in calls
    assert ("chat", "user", "你好") in calls
    assert ("archive", "你好") in calls
    assert ("execute", "你好") in calls
    assert dummy.nlp_input_edit.toPlainText() == ""


def test_operator_refresh_dialog_labels_does_not_repopulate_cleared_input_after_send():
    dummy = DummyOperator()
    dummy._operator_chat_rendered = True
    dummy.status_label = SimpleNamespace(text=lambda: "系统在线")
    dummy.operator_response_label = SimpleNamespace(setText=lambda _text: None)
    dummy.operator_voice_label = SimpleNamespace(setText=lambda _text: None)
    dummy.operator_chat_scroll = SimpleNamespace()
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "")
    dummy.operator_command_edit = SimpleNamespace(
        value="",
        hasFocus=lambda: False,
        text=lambda: dummy.operator_command_edit.value,
        setText=lambda text: setattr(dummy.operator_command_edit, "value", text),
    )

    dummy._refresh_operator_dialog_labels()

    assert dummy.operator_command_edit.value == ""


def test_operator_pending_flow_draft_confirm_save_uses_pending_draft(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()

    handled = dummy._operator_handle_pending_flow_draft_command("确认保存")

    assert handled is True
    assert dummy._operator_pending_flow_draft is None
    assert "打招呼" in dummy.service.list_flow_names()
    assert "已保存流程草案" in dummy.status_text


def test_operator_pending_flow_draft_confirm_save_archives_non_execution_result(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    archived = []
    dummy._archive_non_execution_result = lambda *, result, final_text: archived.append((result, final_text)) or True

    handled = dummy._operator_handle_pending_flow_draft_command("确认保存")

    assert handled is True
    assert archived == [("flow_draft_saved", "已保存流程草案：打招呼。")]


def test_operator_pending_flow_draft_save_and_execute_starts_saved_flow(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    started = []
    dummy._start_flow = lambda on_done=None: started.append(dummy.current_flow_name) or (on_done and on_done(True))

    handled = dummy._operator_handle_pending_flow_draft_command("保存并执行")

    assert handled is True
    assert started == ["打招呼"]
    assert dummy.current_flow_name == "打招呼"


def test_operator_empty_pending_flow_draft_save_failure_clears_stale_draft(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [],
        "positions": {"home": [1475, 0, 1545, 0, 0, 0]},
        "needs_precheck": True,
    }
    warnings = []
    chats = []
    dummy._show_warning = lambda title, text: warnings.append((title, text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))

    handled = dummy._operator_handle_pending_flow_draft_command("保存并执行")

    assert handled is True
    assert dummy._operator_pending_flow_draft is None
    assert "没有可保存的展开步骤" in dummy.status_text
    assert warnings == []
    assert chats == [("assistant", dummy.status_text)]


def test_operator_pending_flow_draft_short_execute_starts_saved_flow(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    started = []
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._start_flow = lambda on_done=None: started.append(dummy.current_flow_name) or (on_done and on_done(True))

    handled = dummy._operator_handle_pending_flow_draft_command("执行")

    assert handled is True
    assert started == ["打招呼"]
    assert dummy.current_flow_name == "打招呼"
    assert "开始执行" in chats[-1][1]


def test_operator_pending_flow_draft_reprecheck_prepares_temporary_flow(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    prepared = []
    dummy._operator_prepare_plan_prechecks = lambda plan: prepared.append(plan)

    handled = dummy._handle_operator_ui_command("重新预检")

    assert handled is True
    assert dummy._operator_pending_flow_draft is not None
    assert prepared
    plan = prepared[-1]
    assert plan.actions[0].action_type == "flow"
    assert str(plan.actions[0].target).startswith("__draft_precheck_")
    assert plan.actions[0].target in dummy.service.flows
    assert dummy.json_path.exists() is False
    assert "重新预检" in dummy.status_text


def test_operator_pending_flow_draft_query_previews_current_draft(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    logs = []
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    handled = dummy._handle_operator_ui_command("我想看下是什么样的流程")

    assert handled is True
    assert chats[-1][0] == "assistant"
    answer = chats[-1][1]
    assert "当前待确认流程草案" in answer
    assert "打招呼" in answer
    assert "尚未保存/执行" in answer
    assert "home" in answer
    assert "移动到home" in answer
    assert "A到B" not in answer
    assert dummy.status_text == DummyOperator._operator_footer_status_text(answer)
    assert logs[-1][1] == "流程草案查询"


def test_operator_pending_flow_draft_query_archives_non_execution_result(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    archived = []
    dummy._archive_non_execution_result = lambda *, result, final_text: archived.append((result, final_text)) or True

    handled = dummy._operator_handle_pending_flow_draft_query("查看流程")

    assert handled is True
    assert archived
    assert archived[0][0] == "flow_draft_query"
    assert "当前待确认流程草案" in archived[0][1]


def test_operator_pending_flow_status_text_summarizes_pending_draft():
    text = DummyOperator._operator_pending_flow_status_text(flow_draft_payload())

    assert "待确认流程草案" in text
    assert "流程名：打招呼" in text
    assert "步骤数：3" in text
    assert "确认保存" in text
    assert "保存并执行" in text
    assert "完整步骤和参数已显示在对话中" in text


def test_operator_sidebars_use_ui_scale():
    app = QApplication.instance() or QApplication([])
    dummy = DummyOperator()
    dummy._ui_scale_factor = 0.8
    dummy._scaled = lambda value: max(1, int(round(float(value) * dummy._ui_scale_factor)))
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.table = {}
    dummy.service = SimpleNamespace()
    dummy.operator_scene_stack = QStackedWidget()
    left = dummy._build_operator_status_bar()
    right = dummy._build_operator_right_sidebar()
    assert left.minimumWidth() == 214
    assert left.maximumWidth() == 214
    assert right.minimumWidth() == 269
    assert right.maximumWidth() == 269
    assert hasattr(dummy, "operator_memory_review_table")
    assert hasattr(dummy, "operator_memory_review_detail")
    assert hasattr(dummy, "operator_memory_status_filter")
    assert hasattr(dummy, "operator_memory_kind_filter")
    assert dummy.operator_memory_review_table.columnCount() == 7
    assert dummy.operator_memory_status_filter.count() >= 4
    assert dummy.operator_memory_kind_filter.count() >= 5
    app.processEvents()


def test_operator_context_query_answers_pending_flow_draft_parameters(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("草案的参数是什么")

    assert handled is True
    answer = chats[-1][1]
    assert "当前待确认流程草案" in answer
    assert "target_x=1475" in answer
    assert "target_z=1545" in answer
    assert "spd_pct=50" in answer


def test_operator_pending_flow_draft_edit_updates_step_speed(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("第 2 步速度改成 30%")

    assert handled is True
    assert dummy._operator_pending_flow_draft["expanded_steps"][1]["params"]["spd_pct"] == 30.0
    assert dummy._operator_pending_flow_draft["expanded_steps"][1]["params"]["acc_pct"] == 30.0
    assert dummy._operator_pending_flow_draft["expanded_steps"][1]["params"]["dec_pct"] == 30.0
    assert "已更新" in chats[-1][1]


def test_operator_pending_flow_draft_edit_deletes_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("删除第 3 步")

    assert handled is True
    steps = dummy._operator_pending_flow_draft["expanded_steps"]
    assert len(steps) == 2
    assert [step["step_id"] for step in steps] == [1, 2]
    assert steps[-1]["description"] == "小臂上下点头:Ry正转"


def test_operator_pending_flow_draft_edit_updates_delay_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    draft = flow_draft_payload()
    draft["expanded_steps"].append(
        {
            "step_id": 4,
            "action": "延时",
            "func_id": 109,
            "description": "延时1秒",
            "params": {"delay_sec": 1.0},
        }
    )
    dummy._operator_pending_flow_draft = draft
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("第 4 步延时改成 5 秒")

    assert handled is True
    assert dummy._operator_pending_flow_draft["expanded_steps"][3]["params"]["delay_sec"] == 5.0


def test_operator_pending_flow_draft_edit_updates_all_speed(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("整体速度改成 20%")

    assert handled is True
    for step in dummy._operator_pending_flow_draft["expanded_steps"]:
        assert step["params"]["spd_pct"] == 20.0
        assert step["params"]["acc_pct"] == 20.0
        assert step["params"]["dec_pct"] == 20.0


def test_operator_pending_flow_draft_edit_undo_reverts_last_change(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("整体速度改成 20%") is True
    handled = dummy._handle_operator_ui_command("撤销上一次修改")

    assert handled is True
    assert dummy._operator_pending_flow_draft["expanded_steps"][0]["params"]["spd_pct"] == 50.0


def test_operator_pending_flow_draft_edit_marks_draft_as_needing_precheck(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("整体速度改成 20%") is True
    handled = dummy._handle_operator_ui_command("看看流程草案")

    assert handled is True
    assert dummy._operator_pending_flow_draft["needs_precheck"] is True
    assert "需要重新预检" in chats[-1][1]


def test_operator_pending_flow_draft_edit_appends_home_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("最后加一步回 home")

    assert handled is True
    step = dummy._operator_pending_flow_draft["expanded_steps"][-1]
    assert step["step_id"] == 4
    assert step["func_id"] == 108
    assert step["position_name"] == "home"
    assert step["params"]["target_x"] == 1475.0
    assert step["params"]["target_z"] == 1545.0


def test_operator_pending_flow_draft_edit_appends_rest_zero_pose(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("最后加一步回 0 位休息姿态")

    assert handled is True
    step = dummy._operator_pending_flow_draft["expanded_steps"][-1]
    assert step["step_id"] == 4
    assert step["func_id"] == 108
    assert step["position_name"] == "休息姿态"
    assert step["params"]["target_x"] == 900.0
    assert step["params"]["target_y"] == 0.0
    assert step["params"]["target_z"] == 1000.0
    assert step["params"]["target_rx"] == 0.0
    assert step["params"]["target_ry"] == 0.0
    assert step["params"]["target_rz"] == 0.0


def test_operator_context_query_answers_position_pose_from_registry(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    registry = dummy._position_registry()
    ok, message = registry.set_position("home", (1475, 0, 1545, 0, 0, 0), created_by="operator")
    assert ok, message
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("home位置的参数是什么")

    assert handled is True
    answer = chats[-1][1]
    assert "home" in answer
    assert "x=1475" in answer
    assert "y=0" in answer
    assert "z=1545" in answer
    assert "rx=0" in answer


def test_operator_context_query_does_not_intercept_flow_creation_request_with_home_pose(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    registry = dummy._position_registry()
    ok, message = registry.set_position("home", (1475, 0, 1545, 0, 0, 0), created_by="operator")
    assert ok, message
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command(
        "你直接编写一下，小正，打个招呼的小流程，让机械手先回到home位再小臂上下点头15度，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0"
    )

    assert handled is False
    assert chats == []


def test_operator_pending_flow_draft_followup_then_previews_current_draft(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy._operator_pending_flow_draft = flow_draft_payload()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("然后呢")

    assert handled is True
    assert chats
    answer = chats[-1][1]
    assert "当前待确认流程草案：打招呼" in answer
    assert "A到B" not in answer
    assert "确认保存" in answer


def test_operator_context_query_uses_single_known_position_for_coordinate_followup(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    registry = dummy._position_registry()
    ok, message = registry.set_position("home", (1475, 0, 1545, 0, 0, 0), created_by="operator")
    assert ok, message
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("具体的x y 信息是什么")

    assert handled is True
    answer = chats[-1][1]
    assert "位置 home" in answer
    assert "x=1475" in answer
    assert "y=0" in answer


def test_operator_position_context_does_not_default_generic_format_question_to_single_home(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    registry = dummy._position_registry()
    ok, message = registry.set_position("home", (1475, 0, 1545, 0, 0, 0), created_by="operator")
    assert ok, message

    answer = dummy._operator_position_context_answer("位置的坐标要什么样的")

    assert answer == ""


def test_operator_context_query_answers_position_params_from_query_table():
    dummy = DummyOperator()
    chats = []
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "move_type": 0,
            },
        )
    }
    dummy._position_registry = lambda: SimpleNamespace(list_all=lambda: [])
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("我问的位置A")

    assert handled is True
    answer = chats[-1][1]
    assert "位置 位置A 的参数" in answer
    assert "x=1000" in answer
    assert "z=800" in answer
    assert "ry=90" in answer


def test_operator_context_query_prefers_query_table_named_position_over_single_registry_fallback(tmp_path):
    dummy = make_context_operator(tmp_path)
    registry = dummy._position_registry()
    ok, message = registry.set_position("home", (1475, 0, 1545, 0, 0, 0), created_by="operator")
    assert ok, message
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "move_type": 0,
            },
        )
    }

    handled = dummy._handle_operator_ui_command("位置A坐标是多少")

    assert handled is True
    answer = dummy.chat_messages[-1][1]
    assert "位置 位置A 的参数" in answer
    assert "x=1000" in answer
    assert "z=800" in answer
    assert "位置 home" not in answer


def test_operator_context_query_answers_command_contains_position_from_query_table():
    dummy = DummyOperator()
    chats = []
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "move_type": 0,
            },
        )
    }
    dummy._position_registry = lambda: SimpleNamespace(list_all=lambda: [])
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("我的命令里面没有位置a吗")

    assert handled is True
    answer = chats[-1][1]
    assert "位置 位置A 的参数" in answer
    assert "x=1000" in answer
    assert "z=800" in answer


def test_operator_context_query_answers_current_device_status_locally():
    dummy = DummyOperator()
    chats = []
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "msg_type": "device_snapshot",
        "data": {
            "system_state": "空闲",
            "func_id_current": "空闲",
            "ready": True,
            "estop": False,
            "pause": False,
            "alarm": False,
            "alarm_code": "ERR_000",
            "ecat_ok": True,
            "dpos_c": ["0", "0", "0"],
        },
    }
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("现在是什么状态")

    assert handled is True
    answer = chats[-1][1]
    assert "当前下位机状态：空闲" in answer
    assert "当前函数：空闲" in answer
    assert "就绪：是" in answer
    assert "报警：无" in answer
    assert "当前位置：X=0，Y=0，Z=0" in answer


def test_operator_context_query_answers_axis_position_question_locally():
    dummy = DummyOperator()
    chats = []
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "msg_type": "device_snapshot",
        "data": {
            "system_state": "空闲",
            "func_id_current": "Func108",
            "ready": True,
            "estop": False,
            "pause": False,
            "alarm": False,
            "alarm_code": "ERR_000",
            "ecat_ok": True,
            "dpos_j": [1, 2, 3, 4, 5, 6],
            "dpos_c": [100, 200, 300, 0, 45, 0],
        },
    }
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("各轴位置多少")

    assert handled is True
    answer = chats[-1][1]
    assert "关节位置：J1=1，J2=2，J3=3，J4=4，J5=5，J6=6" in answer
    assert "当前位置：X=100，Y=200，Z=300" in answer


def test_operator_context_query_answers_registered_flow_information():
    dummy = DummyOperator()
    chats = []
    flow = FlowEntry(
        name="打个招呼的小",
        description="打招呼演示",
        steps=[
            FlowStep(
                step_id=1,
                action="移动到home",
                func_id=108,
                position_name="home",
                params={"target_x": 1475, "target_y": 0, "target_z": 1545, "spd_pct": 50},
            ),
            FlowStep(
                step_id=2,
                action="小臂上下点头",
                func_id=107,
                params={"axis_no": 10, "pos_val": 15, "repeat": 3},
            ),
        ],
        confirmed=True,
        version=2,
    )
    dummy.service = SimpleNamespace(
        flow_registry=SimpleNamespace(list_all=lambda: [flow]),
        get_flow_entry=lambda name: flow if name == "打个招呼的小" else None,
        flows={},
    )
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("我想看看打个招呼的小的信息")

    assert handled is True
    answer = chats[-1][1]
    assert "流程 打个招呼的小" in answer
    assert "共 2 步" in answer
    assert "01  Func108  移动到home" in answer
    assert "Func108" in answer
    assert "02  Func107  小臂上下点头" in answer
    assert "axis_no=10" in answer


def test_operator_context_query_does_not_show_registered_flow_details_for_negative_reference():
    dummy = DummyOperator()
    chats = []
    flow = FlowEntry(
        name="点头",
        description="",
        steps=[FlowStep(step_id=1, action="小臂上下点头", func_id=107, params={"axis_no": 10})],
        confirmed=True,
    )
    dummy.service = SimpleNamespace(
        flow_registry=SimpleNamespace(list_all=lambda: [flow]),
        get_flow_entry=lambda name: flow if name == "点头" else None,
        flows={},
    )
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._operator_handle_context_query("不是点头流程")

    assert handled is False
    assert chats == []


def test_operator_context_query_archives_registered_flow_information():
    dummy = DummyOperator()
    flow = FlowEntry(
        name="测试",
        description="",
        steps=[
            FlowStep(
                step_id=1,
                action="移动到位置B",
                func_id=108,
                params={"target_x": 475, "target_z": 545, "spd_pct": 20},
            ),
        ],
        confirmed=True,
    )
    dummy.service = SimpleNamespace(
        flow_registry=SimpleNamespace(list_all=lambda: [flow]),
        get_flow_entry=lambda name: flow if name == "测试" else None,
        flows={},
    )
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_publish_ai_answer_for_speech = lambda _text: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None
    archived = []
    dummy._archive_non_execution_result = lambda *, result, final_text: archived.append((result, final_text)) or True

    handled = dummy._operator_handle_context_query("查看下测试")

    assert handled is True
    assert archived
    assert archived[0][0] == "context_query"
    assert "流程 测试" in archived[0][1]


def test_operator_context_query_prefers_pending_flow_draft_for_named_test_flow(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.current_flow_name = "打个招呼的小"
    dummy._operator_pending_flow_draft = {
        "flow_name": "测试",
        "expanded_steps": [
            {
                "step_id": 1,
                "description": "移动到位置A",
                "func_id": 108,
                "params": {"target_x": 100.0},
            },
        ],
    }

    answer = dummy._operator_context_answer("上面的那个测试流程怎么样呢")

    assert "当前待确认流程草案：测试" in answer
    assert "打个招呼的小" not in answer


def test_operator_context_query_does_not_intercept_wake_flow_execution_command():
    dummy = DummyOperator()
    chats = []
    flow = FlowDefinition(name="点头", steps=("step_a",), step_delay_ms=300)
    dummy.service = SimpleNamespace(flow_registry=None, flows={"点头": flow})
    dummy.table = {
        "step_a": QueryRecord(
            query_key="step_a",
            func_num=108,
            description="移动到位置A",
            keywords="点头 第1步",
            params={},
        )
    }
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("小正，执行点头流程")

    assert handled is False
    assert chats == []


def test_operator_context_query_warns_when_registered_flow_execution_lacks_wake_word():
    dummy = DummyOperator()
    chats = []
    spoken = []
    logs = []
    flow = FlowDefinition(name="点头", steps=("step_a",), step_delay_ms=300)
    dummy.service = SimpleNamespace(flow_registry=None, flows={"点头": flow})
    dummy.table = {
        "step_a": QueryRecord(
            query_key="step_a",
            func_num=108,
            description="移动到位置A",
            keywords="点头 第1步",
            params={},
        )
    }
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_publish_ai_answer_for_speech = lambda text: spoken.append(text)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("执行点头流程")

    assert handled is True
    assert len(chats) == 1
    assert "生产执行指令缺少“小正或小兵”唤醒词" in chats[0][1]
    assert "小正，执行点头流程" in chats[0][1]
    assert "小兵，执行点头流程" in chats[0][1]
    assert "步骤流" not in chats[0][1]
    assert spoken == [chats[0][1]]
    assert any(entry[0:3] == ("自然语言", "缺少唤醒词", "提示") for entry in logs)


def test_operator_context_query_warns_when_flow_execution_asr_says_zhixing_without_wake_word():
    dummy = DummyOperator()
    chats = []
    spoken = []
    logs = []
    flow = FlowDefinition(name="点头", steps=("step_a",), step_delay_ms=300)
    dummy.service = SimpleNamespace(flow_registry=None, flows={"点头": flow})
    dummy.table = {
        "step_a": QueryRecord(
            query_key="step_a",
            func_num=108,
            description="移动到位置A",
            keywords="点头 第1步",
            params={},
        )
    }
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_publish_ai_answer_for_speech = lambda text: spoken.append(text)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("我想直行点头，流程。")

    assert handled is True
    assert "生产执行指令缺少“小正或小兵”唤醒词" in chats[0][1]
    assert "步骤流" not in chats[0][1]
    assert spoken == [chats[0][1]]
    assert any(entry[0:3] == ("自然语言", "缺少唤醒词", "提示") for entry in logs)


def test_operator_plain_registered_flow_execution_uses_local_flow_agent(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "A到B", "rule", "运行流程 A 到 B", "命中流程规则"),),
        source="rule",
        raw_text="运行流程 A 到 B",
        reason="命中流程规则",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=True,
        requires_confirmation=True,
    )
    dummy._operator_agent_registered_flow_parse = lambda text: plan

    result = dummy._operator_try_agent_orchestrator_plan("运行流程 A 到 B")

    assert result.actions[0].action_type == "flow"
    assert result.actions[0].target == "A到B"
    assert result.requires_confirmation is True


def test_operator_context_query_resolves_saved_flow_query_keys_to_records():
    dummy = DummyOperator()
    chats = []
    flow = FlowDefinition(
        name="home点头流程",
        steps=("flowdraft:home点头流程:01", "flowdraft:home点头流程:02"),
        step_delay_ms=300,
    )
    dummy.service = SimpleNamespace(flow_registry=None, flows={"home点头流程": flow})
    dummy.table = {
        "flowdraft:home点头流程:01": QueryRecord(
            query_key="flowdraft:home点头流程:01",
            func_num=108,
            description="移动到home",
            keywords="home点头流程 第1步",
            params={"target_x": 1475, "target_y": 0, "target_z": 1545, "spd_pct": 50},
        ),
        "flowdraft:home点头流程:02": QueryRecord(
            query_key="flowdraft:home点头流程:02",
            func_num=107,
            description="小臂上下点头:Ry正转",
            keywords="home点头流程 第2步",
            params={"axis_no": 10, "pos_val": 15, "spd_pct": 50},
        ),
    }
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("我想看看home点头流程的信息")

    assert handled is True
    answer = chats[-1][1]
    assert "流程 home点头流程" in answer
    assert "01  Func108  移动到home" in answer
    assert "目标  X target_x=1475" in answer
    assert "02  Func107  小臂上下点头:Ry正转" in answer
    assert "动作  axis_no=10  pos_val=15" in answer
    assert "flowdraft:home点头流程:01" not in answer


def test_operator_context_query_queues_ai_answer_for_speech():
    dummy = DummyOperator()
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "msg_type": "device_snapshot",
        "data": {
            "system_state": "空闲",
            "func_id_current": "空闲",
            "ready": True,
            "estop": False,
            "pause": False,
            "alarm": False,
            "alarm_code": "ERR_000",
            "ecat_ok": True,
            "dpos_c": ["0", "0", "0"],
        },
    }
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("现在是什么状态")

    assert handled is True
    pending = dummy.operator_broadcast_queue.messages_since(0)
    assert pending[-1].context_id == "chat:ai_answer"
    assert "当前下位机状态：空闲" in pending[-1].text


def test_operator_context_query_answers_last_execution_result(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path / "logs"
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_execution_result = OperatorUiMixin._operator_archive_execution_result.__get__(
        dummy,
        DummyOperator,
    )
    dummy._operator_archive_text_input("保存并执行")
    dummy._operator_archive_execution_result(result="success", final_text="流程完成：共完成 1 步")
    dummy._operator_archive_text_input("执行结果是什么")
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None

    handled = dummy._handle_operator_ui_command("执行结果是什么")

    assert handled is True
    answer = chats[-1][1]
    assert "最近一次执行结果" in answer
    assert "保存并执行" in answer
    assert "成功" in answer
    assert "流程完成：共完成 1 步" in answer
    assert "未实际执行任何动作" not in answer


def test_operator_deepseek_runtime_context_includes_recent_dialogue(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_chat_messages = [
        ("assistant", "系统在线"),
        ("user", "小正，创建打招呼流程"),
        ("assistant", "已生成流程草案：打招呼。"),
        ("user", "然后呢"),
        ("assistant", "当前待确认流程草案：打招呼，共 7 步。"),
    ]
    dummy._operator_pending_flow_draft = flow_draft_payload()

    context = dummy._operator_deepseek_runtime_context()

    assert "最近对话：" in context
    assert "用户：小正，创建打招呼流程" in context
    assert "AI：已生成流程草案：打招呼。" in context
    assert "用户：然后呢" in context
    assert "当前待确认流程草案：打招呼" in context


def test_operator_deepseek_runtime_context_includes_pending_confirmation(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_scene_state = OperatorSceneState("confirm")
    dummy._operator_chat_messages = [("user", "X100。"), ("assistant", "等待确认执行。")]
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "agent:draft", "restricted_agent", "X100", "等待确认"),),
        source="restricted_agent",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
        flow_draft={
            "agent_kind": "waiting_confirmation",
            "func_id": 108,
            "confirmation_text": "【复述确认】Func108 直线插补\nX=100.0mm\n速度=50.0%",
            "params": {"target_x": 100.0, "spd_pct": 50.0},
        },
    )

    context = dummy._operator_deepseek_runtime_context()

    assert "当前页面：confirm" in context
    assert "待确认指令：Func108" in context
    assert "X=100.0mm" in context
    assert "用户：X100。" in context


def test_operator_deepseek_runtime_context_includes_last_execution_state_after(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path / "logs"
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "system_state": "空闲",
        "func_id_current": "Func108",
        "ready": True,
        "estop": False,
        "pause": False,
        "alarm": False,
        "alarm_code": "ERR_000",
        "dpos_c": [900.0, 0.0, 1000.0],
    }
    dummy._operator_archive_execution_result = OperatorUiMixin._operator_archive_execution_result.__get__(
        dummy,
        DummyOperator,
    )
    dummy._operator_archive_text_input("休息")
    dummy._operator_archive_execution_result(
        result="success",
        final_text="执行完成：移动到默认休息姿态。",
        execution_detail={
            "state_after": {
                "data": {
                    "system_state": "空闲",
                    "func_id_current": "Func108",
                    "ready": True,
                    "estop": False,
                    "pause": False,
                    "alarm": False,
                    "alarm_code": "ERR_000",
                    "dpos_c": [900.0, 0.0, 1000.0],
                }
            }
        },
    )
    dummy._operator_archive_text_input("刚才执行后状态是什么")

    context = dummy._operator_deepseek_runtime_context()

    assert "最近一次执行后状态：" in context
    assert "结果=成功" in context
    assert "反馈=执行完成：移动到默认休息姿态。" in context
    assert "system_state=空闲" in context
    assert "func_id_current=Func108" in context
    assert "位置=(900, 0, 1000)" in context
    assert "报警=无" in context


def test_operator_refresh_dashboard_cache_updates_v21_snapshot_without_full_view():
    dummy = DummyOperator()
    dummy.operator_dashboard_cache = DashboardCache()
    dummy.robot_x = "1"
    dummy.robot_y = "2"
    dummy.robot_z = "3"
    dummy.robot_r = "4"
    dummy.robot_joints = (1, 2, 3, 4, 5, 6)
    dummy.robot_speed = "20%"
    dummy.alarm_code = "0"
    dummy.alarm_text = "正常"
    dummy.estop_active = False
    dummy.pause_active = False
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.current_func_text = "空闲"
    dummy.motion_percent = "-"
    dummy.result = "0"
    dummy.io_status = "0"
    dummy.servo_enable = "1"
    dummy.claw_enable = "1"
    dummy.claw_brake = "0"
    dummy.monitor_label = SimpleNamespace(text=lambda: "实时监控运行中")

    snapshot = dummy._operator_refresh_dashboard_cache()

    assert snapshot.refresh_ms == 50
    assert snapshot.boards["device_status"]["dpos_j"] == (1, 2, 3, 4, 5, 6)
    assert snapshot.boards["communication_faults"]["ecat_ok"] is True


def test_operator_make_dashboard_cache_uses_configured_refresh_and_stale_threshold():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        operator_dashboard_refresh_ms=80,
        dashboard_stale_after_ms=2500,
    )

    cache = dummy._operator_make_dashboard_cache()

    assert cache.refresh_ms == 80
    assert cache.stale_after_ms == 2500


def test_operator_recent_events_hides_chat_only_nlp_logs():
    dummy = DummyOperator()
    dummy.logs = [
        {
            "time": "21:49:44.404",
            "category": "自然语言",
            "action": "闲聊咨询",
            "result": "成功",
            "detail": "当前系统已加载模板...",
        },
        {
            "time": "21:49:44.333",
            "category": "自然语言",
            "action": "DeepSeek问答",
            "result": "成功",
            "detail": "当前系统已加载模板...",
        },
        {
            "time": "21:49:43.000",
            "category": "自然语言",
            "action": "流程草案",
            "result": "提示",
            "detail": "已生成流程草案",
        },
        {
            "time": "21:49:42.000",
            "category": "用户页面",
            "action": "场景切换",
            "result": "成功",
            "detail": "execute -> idle",
        },
    ]

    events = dummy._operator_recent_event_entries(limit=10)

    actions = [entry["action"] for entry in events]
    assert actions == ["流程草案", "场景切换"]


def test_operator_refresh_alarm_monitor_keeps_independent_50ms_sample():
    dummy = DummyOperator()
    dummy.alarm_code = "ERR_8"
    dummy.alarm_text = "速度超限"
    dummy.six_long38 = 8
    dummy.estop_active = False
    dummy.pause_active = False
    dummy.monitor_label = SimpleNamespace(text=lambda: "实时监控运行中")

    sample = dummy._operator_refresh_alarm_monitor()

    assert sample.interval_ms == 50
    assert dummy._operator_last_alarm_detection["codes"] == ["OVER_SPEED"]


def test_operator_dashboard_change_broadcasts_alarm_transition_once():
    dummy = DummyOperator()
    dummy.operator_dashboard_cache = DashboardCache()
    dummy._operator_dashboard_broadcast_state = {
        "alarm": False,
        "estop": False,
        "pause": False,
        "ecat_ok": True,
        "alarm_code": "0",
    }
    messages = []
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": True, "estop": False, "pause": False, "alarm_code": "ERR_9"},
                "communication_faults": {"ecat_ok": True},
            },
            safety={"alarm_text": "驱动器报警"},
        )
    )
    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": True, "estop": False, "pause": False, "alarm_code": "ERR_9"},
                "communication_faults": {"ecat_ok": True},
            },
            safety={"alarm_text": "驱动器报警"},
        )
    )

    assert len(messages) == 1
    assert messages[0].priority == "high"
    assert "ERR_9" in messages[0].text


def test_operator_dashboard_change_broadcasts_alarm_and_comm_recovery():
    dummy = DummyOperator()
    messages = []
    dummy._operator_dashboard_broadcast_state = {
        "alarm": True,
        "estop": False,
        "pause": False,
        "ecat_ok": False,
        "alarm_code": "ERR_9",
    }
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": False, "estop": False, "pause": False, "alarm_code": "0"},
                "communication_faults": {"ecat_ok": True},
            },
            safety={"alarm_text": "系统正常"},
        )
    )

    assert [message.text for message in messages] == [
        "报警状态已解除，当前无报警。",
        "通讯状态已恢复，实时反馈在线。",
    ]
    assert all(message.priority == "normal" for message in messages)


def test_operator_dashboard_change_broadcasts_estop_and_pause_recovery():
    dummy = DummyOperator()
    messages = []
    dummy._operator_dashboard_broadcast_state = {
        "alarm": False,
        "estop": True,
        "pause": True,
        "ecat_ok": True,
        "alarm_code": "0",
    }
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": False, "estop": False, "pause": False, "alarm_code": "0"},
                "communication_faults": {"ecat_ok": True},
            },
            safety={},
        )
    )

    assert [message.text for message in messages] == [
        "急停状态已解除，请确认现场安全后再继续。",
        "系统已退出暂停状态。",
    ]


def test_operator_dashboard_change_broadcasts_motion_completion_when_channel_becomes_idle():
    dummy = DummyOperator()
    messages = []
    dummy._operator_dashboard_broadcast_state = {
        "alarm": False,
        "estop": False,
        "pause": False,
        "ecat_ok": True,
        "alarm_code": "0",
        "channel_idle": False,
        "current_func": "FUNC108",
        "motion_percent": "80%",
    }
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {
                    "alarm": False,
                    "estop": False,
                    "pause": False,
                    "alarm_code": "0",
                    "dpos_j": (1, 2, 3, 4, 5, 6),
                    "dpos_c": ("10", "20", "30"),
                },
                "action_feasibility": {
                    "channel_idle": True,
                    "current_func": "FUNC108",
                    "result": "0",
                },
                "motion_limits": {"motion_percent": "100%"},
                "communication_faults": {"ecat_ok": True},
            },
            safety={},
        )
    )

    assert [message.text for message in messages] == [
        "动作执行完成，控制器通道已空闲，函数 FUNC108，结果 0，当前位置 10 / 20 / 30。"
    ]
    assert messages[0].kind == "result"


def test_operator_dashboard_change_broadcasts_realtime_feedback_stale():
    dummy = DummyOperator()
    messages = []
    dummy._operator_dashboard_broadcast_state = {
        "alarm": False,
        "estop": False,
        "pause": False,
        "ecat_ok": True,
        "feedback_fresh": True,
        "alarm_code": "0",
    }
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": False, "estop": False, "pause": False, "alarm_code": "0"},
                "communication_faults": {
                    "ecat_ok": False,
                    "realtime_feedback": "stale",
                    "feedback_fresh": False,
                    "feedback_age_ms": 1350,
                },
            },
            safety={},
        )
    )

    assert [message.text for message in messages] == ["实时反馈数据已过期 1350ms，请确认控制器轮询和通讯状态。"]
    assert messages[0].priority == "high"


def test_operator_dashboard_change_broadcasts_axis_status_abnormal():
    dummy = DummyOperator()
    messages = []
    dummy._operator_dashboard_broadcast_state = {
        "alarm": False,
        "estop": False,
        "pause": False,
        "ecat_ok": True,
        "axis_status_abnormal": False,
        "axis_status": (0, 0, 0, 0, 0, 0),
        "alarm_code": "0",
    }
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": False, "estop": False, "pause": False, "alarm_code": "0"},
                "motion_limits": {"axis_status": (0, 0, 7, 0, 0, 0)},
                "communication_faults": {"ecat_ok": True},
            },
            safety={},
        )
    )

    assert [message.text for message in messages] == ["轴状态异常，当前轴状态 0 / 0 / 7 / 0 / 0 / 0。"]
    assert messages[0].priority == "high"


def test_operator_dashboard_change_does_not_broadcast_initial_idle_motion_state():
    dummy = DummyOperator()
    messages = []
    dummy._operator_dashboard_broadcast_state = None
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_dashboard_change_broadcasts(
        SimpleNamespace(
            boards={
                "device_status": {"alarm": False, "estop": False, "pause": False, "alarm_code": "0"},
                "action_feasibility": {"channel_idle": True, "current_func": "空闲", "result": "0"},
                "motion_limits": {"motion_percent": "0%"},
                "communication_faults": {"ecat_ok": True},
            },
            safety={},
        )
    )

    assert messages == []


def test_operator_ui_command_answers_dashboard_query_without_nlp():
    dummy = DummyOperator()
    published = []
    statuses = []
    logs = []
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "communication_faults": {
                "ecat_ok": True,
                "controller": "online",
                "realtime_feedback": "online",
                "io_status": "0",
            }
        }
    }
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._set_workspace_mode = lambda _mode: None
    dummy._operator_scene_override = None

    handled = dummy._handle_operator_ui_command("通讯正常吗")

    assert handled is True
    assert published[-1].kind == "result"
    assert "通讯正常" in published[-1].text
    assert statuses[-1] == published[-1].text
    assert dummy._operator_scene_override == "query"


def test_operator_ui_dashboard_query_queues_ai_answer_for_speech():
    dummy = DummyOperator()
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "communication_faults": {
                "ecat_ok": True,
                "controller": "online",
                "realtime_feedback": "online",
                "io_status": "0",
            }
        }
    }
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._set_workspace_mode = lambda _mode: None
    dummy._operator_scene_override = None

    handled = dummy._handle_operator_ui_command("通讯正常吗")

    assert handled is True
    pending = dummy.operator_broadcast_queue.messages_since(0)
    assert pending[-1].context_id == "chat:ai_answer"
    assert "通讯正常" in pending[-1].text


def test_operator_answer_query_plan_answers_l2_query_without_confirm():
    dummy = DummyOperator()
    messages = []
    chats = []
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "communication_faults": {
                "ecat_ok": True,
                "controller": "online",
                "realtime_feedback": "online",
                "io_status": "0",
            }
        }
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_confirm_plan = object()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "communication_faults", "rule", "通讯正常吗", "命中看板查询"),),
        source="rule",
        raw_text="通讯正常吗",
        reason="命中看板查询",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert dummy._operator_pending_confirm_plan is None
    assert dummy._operator_scene_override == "query"
    assert messages[-1].kind == "result"
    assert "通讯正常" in messages[-1].text


def test_operator_answer_query_plan_answers_atomic_capability_query():
    dummy = DummyOperator()
    messages = []
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_confirm_plan = object()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "atomic_capabilities", "rule", "支持哪些原子命令", "命中原子能力查询"),),
        source="rule",
        raw_text="支持哪些原子命令",
        reason="命中原子能力查询",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert dummy._operator_pending_confirm_plan is None
    assert messages[-1].kind == "result"
    assert "二次原子函数能力" in messages[-1].text
    assert "保护性拒绝" in messages[-1].text


def test_operator_answer_query_plan_uses_agent_alarm_explanation_for_long38_radius():
    dummy = DummyOperator()
    messages = []
    archived = []
    dummy.axis_ranges = AxisRangeConfig(
        x=(-3000.0, 3000.0),
        y=(-3000.0, 3000.0),
        z=(0.0, 3000.0),
        safe_r_min=200.0,
        safe_r_max=1800.0,
        safe_z_min=0.0,
        safe_z_max=2500.0,
    )
    dummy.six_long34 = 1 << 28
    dummy.six_long36 = 0
    dummy.six_long38 = 1 << 0
    dummy.current_func_num = 108
    dummy.axis_status = (0, 0, 0, 0, 0, 0)
    dummy.current_r = 1900.0
    dummy.robot_z = "800"
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "alarm_query", "restricted_agent", "当前报警是什么", "规则旁路"),),
        source="restricted_agent",
        raw_text="当前报警是什么",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert messages[-1].context_id == "agent:alarm_query"
    assert "手臂伸太远了" in messages[-1].text
    assert "超出100.0mm" in messages[-1].text
    assert archived[-1]["result"] == "answered"


def test_operator_answer_query_plan_passes_hardware_state_to_agent_alarm_explanation():
    dummy = DummyOperator()
    messages = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.six_long34 = 1 << 28
    dummy.six_long36 = 0
    dummy.six_long38 = 0
    dummy.current_func_num = 108
    dummy.axis_status = ()
    dummy.servo_enable = "0"
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "hardware": {"servo_enable": "0"},
        "boards": {},
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "alarm_query", "restricted_agent", "当前报警是什么", "规则旁路"),),
        source="restricted_agent",
        raw_text="当前报警是什么",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert "伺服未使能" in messages[-1].text


def test_operator_answer_query_plan_uses_agent_status_explanation():
    dummy = DummyOperator()
    messages = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.six_long34 = 1 << 28
    dummy.six_long36 = 0
    dummy.six_long38 = 0
    dummy.current_func_num = 108
    dummy.axis_status = ()
    dummy.servo_enable = "0"
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "hardware": {"servo_enable": "0"},
        "boards": {},
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "status_query", "restricted_agent", "为什么不能动", "规则旁路"),),
        source="restricted_agent",
        raw_text="为什么不能动",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert messages[-1].context_id == "agent:status_query"
    assert "伺服未使能" in messages[-1].text


def test_operator_answer_completion_query_uses_execution_monitor_snapshot():
    dummy = DummyOperator()
    messages = []
    archived = []
    dummy._operator_last_execution_monitor_snapshot = {
        "status": "completed",
        "query_key": "move_a",
        "func_id": 108,
        "result_code": "0",
        "detail": "动作执行完成",
        "updated_at": 10.0,
        "feedback": [0.0, 1000.0, 200.0, 800.0, 0.0, 45.0, 0.0],
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "status_query", "restricted_agent", "执行完成了吗", "规则旁路"),),
        source="restricted_agent",
        raw_text="执行完成了吗",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert messages[-1].context_id == "agent:execution_monitor"
    assert "动作执行完成：move_a" in messages[-1].text
    assert "当前位置 1000.0 / 200.0 / 800.0" in messages[-1].text
    assert archived[-1]["result"] == "answered"


def test_operator_answer_completion_query_reports_timeout_for_running_snapshot():
    dummy = DummyOperator()
    messages = []
    dummy._operator_now_seconds = lambda: 45.0
    dummy._operator_last_execution_monitor_snapshot = {
        "status": "running",
        "query_key": "move_a",
        "func_id": 108,
        "started_at": 10.0,
        "updated_at": 10.0,
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "status_query", "restricted_agent", "执行完成了吗", "规则旁路"),),
        source="restricted_agent",
        raw_text="执行完成了吗",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    assert dummy._operator_answer_query_plan(plan) is True

    assert messages[-1].context_id == "agent:execution_monitor"
    assert "可能超时" in messages[-1].text


def test_operator_answer_query_plan_passes_axis_alarm_flags_to_agent():
    dummy = DummyOperator()
    messages = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.six_long34 = (1 << 28) | (1 << 24)
    dummy.six_long36 = 0
    dummy.six_long38 = 0
    dummy.current_func_num = 108
    dummy.axis_status = ()
    dummy.axis_alarm_flags = (0, 1, 0, 0, 0, 1)
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "hardware": {"servo_enable": "1", "axis_alarm_flags": (0, 1, 0, 0, 0, 1)},
        "boards": {},
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "status_query", "restricted_agent", "为什么不能动", "规则旁路"),),
        source="restricted_agent",
        raw_text="为什么不能动",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert "J2" in messages[-1].text
    assert "J6" in messages[-1].text
    assert "AXISSTATUS" in messages[-1].text


def test_operator_answer_query_plan_passes_any_axis_moving_to_agent():
    dummy = DummyOperator()
    messages = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.six_long34 = 1 << 28
    dummy.six_long36 = 0
    dummy.six_long38 = 0
    dummy.current_func_num = 108
    dummy.axis_status = ()
    dummy.motion_percent = "运动中"
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "hardware": {"servo_enable": "1", "any_axis_moving": 1},
        "boards": {},
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "status_query", "restricted_agent", "为什么不能动", "规则旁路"),),
        source="restricted_agent",
        raw_text="为什么不能动",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert "存在轴正在运动" in messages[-1].text
    assert "BIT(252" in messages[-1].text or "BIT(252-255" in messages[-1].text


def test_operator_answer_query_plan_passes_axis_enabled_to_agent():
    dummy = DummyOperator()
    messages = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.six_long34 = 1 << 28
    dummy.six_long36 = 0
    dummy.six_long38 = 0
    dummy.current_func_num = 108
    dummy.axis_status = ()
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "hardware": {"servo_enable": "1", "axis_enabled": [1, 0, 1, 1, 1, 1]},
        "boards": {},
    }
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_set_pending_confirm_plan = lambda pending: setattr(dummy, "_operator_pending_confirm_plan", pending)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "status_query", "restricted_agent", "为什么不能动", "规则旁路"),),
        source="restricted_agent",
        raw_text="为什么不能动",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    assert "J2轴未使能" in messages[-1].text
    assert "BIT(190-195" in messages[-1].text


def test_operator_answer_query_plan_queues_ai_answer_for_speech():
    dummy = DummyOperator()
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "communication_faults": {
                "ecat_ok": True,
                "controller": "online",
                "realtime_feedback": "online",
                "io_status": "0",
            }
        }
    }
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_confirm_plan = object()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "communication_faults", "rule", "通讯正常吗", "命中看板查询"),),
        source="rule",
        raw_text="通讯正常吗",
        reason="命中看板查询",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    handled = dummy._operator_answer_query_plan(plan)

    assert handled is True
    pending = dummy.operator_broadcast_queue.messages_since(0)
    assert pending[-1].context_id == "chat:ai_answer"
    assert "通讯正常" in pending[-1].text


def test_operator_semantic_policy_requires_confirm_for_l3_but_not_l4_or_l5():
    l3_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=True,
        requires_confirmation=True,
    )
    l4_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("system", "sys_pause", "rule", "暂停", "测试"),),
        source="rule",
        raw_text="暂停",
        reason="测试",
        semantic_level=4,
        semantic_label="系统管理层",
        requires_precheck=False,
        requires_confirmation=False,
    )
    l5_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("system", "sys_estop", "rule", "急停 A1B2 急停", "测试"),),
        source="rule",
        raw_text="急停 A1B2 急停",
        reason="测试",
        semantic_level=5,
        semantic_label="应急安全层",
        priority="high",
    )

    assert DummyOperator._operator_plan_requires_precheck(l3_plan) is True
    assert DummyOperator._operator_plan_requires_confirmation(l3_plan) is True
    assert DummyOperator._operator_plan_requires_precheck(l4_plan) is False
    assert DummyOperator._operator_plan_requires_confirmation(l4_plan) is False
    assert DummyOperator._operator_plan_requires_precheck(l5_plan) is False
    assert DummyOperator._operator_plan_requires_confirmation(l5_plan) is False


def test_operator_query_plan_is_not_executable():
    query_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "communication_faults", "rule", "通讯正常吗", "命中看板查询"),),
        source="rule",
        raw_text="通讯正常吗",
        reason="命中看板查询",
        semantic_level=2,
        semantic_label="工艺查询层",
    )

    assert DummyOperator._operator_plan_is_executable(query_plan) is False
    assert DummyOperator._operator_plan_requires_confirmation(query_plan) is False


def test_operator_atomic_template_uses_plan_confirmation_policy():
    auto_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "atomic:io:1:1", "atomic_rule", "IO1开", "IO"),),
        source="atomic_rule",
        raw_text="小正，IO1开",
        reason="IO",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=True,
        requires_confirmation=False,
    )
    confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "atomic:virtual:8:1:3", "atomic_rule", "上升3毫米", "运动"),),
        source="atomic_rule",
        raw_text="小正，上升3毫米",
        reason="运动",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=True,
        requires_confirmation=True,
    )

    assert DummyOperator._operator_plan_is_executable(auto_plan) is True
    assert DummyOperator._operator_plan_requires_precheck(auto_plan) is True
    assert DummyOperator._operator_plan_requires_confirmation(auto_plan) is False
    assert DummyOperator._operator_plan_requires_confirmation(confirm_plan) is True


def test_operator_execute_nlp_plan_waits_when_plan_requires_confirmation():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=True,
        requires_confirmation=True,
    )
    status_messages = []
    chats = []
    logs = []
    dummy._operator_pending_confirm_plan = plan
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert "等待安全确认" in status_messages[-1]
    assert "确认执行" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "等待确认", "提示")


def test_operator_execute_nlp_plan_sets_agent_draft_pending_before_sequence():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待操作者确认。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=False,
        requires_confirmation=True,
        flow_draft={
            "agent_kind": "waiting_confirmation",
            "draft_id": "draft1",
            "confirmation_text": "【复述确认】Func108 直线插补\nX=1000mm（指定）",
        },
    )
    status_messages = []
    chats = []
    logs = []
    dummy._operator_pending_confirm_plan = None
    dummy._operator_pending_confirm_deadline_sec = 0
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert dummy._operator_pending_confirm_plan is plan
    assert dummy._operator_scene_override == "confirm"
    assert "等待安全确认" in status_messages[-1]
    assert "Func108" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "等待确认", "提示")


def test_operator_agent_draft_plan_is_confirmable_without_old_precheck():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待操作者确认。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=True,
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )

    assert DummyOperator._operator_plan_is_executable(plan) is True
    assert DummyOperator._operator_plan_requires_precheck(plan) is False
    assert DummyOperator._operator_plan_requires_confirmation(plan) is True


def test_operator_confirm_detail_uses_agent_confirmation_text():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待操作者确认。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={
            "agent_kind": "waiting_confirmation",
            "draft_id": "draft1",
            "confirmation_text": "【复述确认】Func108 直线插补\nX=1000mm（指定）",
        },
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 0

    text = dummy._operator_confirm_detail_text()

    assert "【复述确认】Func108 直线插补" in text
    assert "X=1000mm（指定）" in text


def test_operator_confirm_detail_html_groups_agent_parameters():
    dummy = DummyOperator()
    dummy._operator_now_seconds = lambda: 10.0
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待操作者确认。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={
            "agent_kind": "waiting_confirmation",
            "draft_id": "draft1",
            "func_id": 108,
            "confirmation_text": "【复述确认】Func108 直线插补\nX=1000mm（指定）",
            "params": {
                "target_x": 1000.0,
                "target_y": 200.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 45.0,
                "target_rz": 0.0,
                "spd_pct": 60.0,
                "acc_pct": 50.0,
                "dec_pct": 50.0,
            },
            "param_sources": {
                "target_x": "specified",
                "target_y": "specified",
                "target_z": "specified",
                "target_rx": "inherited",
                "target_ry": "specified",
                "target_rz": "inherited",
                "spd_pct": "specified",
                "acc_pct": "controller",
                "dec_pct": "controller",
            },
            "precheck_result": {"valid": True, "summary": "L1安全检查通过。"},
        },
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 56.0

    html_text = dummy._operator_confirm_detail_html()

    assert "Func108 直线插补" in html_text
    assert "目标位置" in html_text
    assert "姿态" in html_text
    assert "运动参数" in html_text
    assert "安全预检" in html_text
    assert "1000.0 mm" in html_text
    assert "继承安全参数" in html_text
    assert "确认有效期：剩余 46 秒。" in html_text
    assert "当前模式：等待确认" in html_text
    assert "可以说：确认执行、取消指令、速度改为50%、加速度改为50%、现在的运动参数是哪些" in html_text
    assert "confirm-bottom-spacer" in html_text


def test_operator_set_confirm_detail_html_preserves_scroll_for_same_draft(monkeypatch):
    dummy = DummyOperator()
    calls = []

    class Bar:
        def __init__(self):
            self._value = 120
            self.maximum_value = 400

        def value(self):
            return self._value

        def maximum(self):
            return self.maximum_value

        def minimum(self):
            return 0

        def setValue(self, value):
            calls.append(value)
            self._value = value

    class Browser:
        def __init__(self):
            self.bar = Bar()
            self.html = ""

        def verticalScrollBar(self):
            return self.bar

        def setHtml(self, value):
            self.html = value

    monkeypatch.setattr("robot_modbus_lite.operator_ui_mixin.QTimer.singleShot", lambda _ms, callback: callback())
    dummy.operator_confirm_detail = Browser()
    dummy._operator_confirm_html_signature = "draft1"
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待操作者确认。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )

    dummy._operator_set_confirm_detail_html("<html>updated</html>")

    assert dummy.operator_confirm_detail.html == "<html>updated</html>"
    assert calls[-1] == 120


def test_operator_confirm_agent_draft_converts_to_atomic_plan_for_existing_execution():
    dummy = DummyOperator()
    record = QueryRecord(
        query_key="agent:draft1",
        func_num=108,
        params={
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
        },
        description="Agent confirmed draft",
    )
    service = SimpleNamespace(confirm=lambda draft_id: record)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待操作者确认。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    executed = []
    status_messages = []
    chats = []
    logs = []
    dummy._restricted_agent_service = service
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = None
    dummy._operator_last_motion_plan_result = None
    dummy._operator_last_process_precheck_result = None
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._execute_nlp_plan = lambda converted_plan: executed.append(converted_plan)

    dummy._operator_confirm_execute()

    assert getattr(dummy, "_operator_pending_confirm_plan") is None
    assert getattr(dummy, "execute_busy") is True
    assert "确认收到" in status_messages[-1]
    converted = executed[-1]
    assert converted.actions[0].action_type == "atomic_template"
    assert converted.actions[0].target == "agent:draft1"
    assert converted.atomic_records["agent:draft1"] is record
    assert converted.requires_confirmation is False


def test_operator_confirm_agent_draft_uses_bridge_for_tool_chain_plan():
    dummy = DummyOperator()
    record = {
        "query_key": "agent:draft1",
        "func_num": 109,
        "params": {"delay_sec": 2.0},
        "description": "Agent confirmed draft",
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "agent_orchestrator", "等待2秒", "等待操作者确认。"),),
        source="agent_orchestrator",
        raw_text="等待2秒",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    confirmed = []
    executed = []
    status_messages = []
    chats = []
    logs = []

    class Bridge:
        def confirm_pending_plan(self, draft_id, *, thread_id):
            confirmed.append((draft_id, thread_id))
            from robot_modbus_lite.agent_tools.tool_result import ToolResult

            return ToolResult.success(
                state="confirmed",
                message="确认已通过。",
                data={"draft_id": draft_id, "query_record": record},
            )

        def clear_pending_confirm(self, *, thread_id):
            return None

    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_session_thread_id = lambda: "session-1"
    dummy._restricted_agent_service = SimpleNamespace(confirm=lambda draft_id: (_ for _ in ()).throw(AssertionError("legacy confirm should not run")))
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = None
    dummy._operator_last_motion_plan_result = None
    dummy._operator_last_process_precheck_result = None
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._execute_nlp_plan = lambda converted_plan: executed.append(converted_plan)

    dummy._operator_confirm_execute()

    assert confirmed == [("draft1", "session-1")]
    assert getattr(dummy, "_operator_pending_confirm_plan") is None
    assert getattr(dummy, "execute_busy") is True
    converted = executed[-1]
    assert converted.actions[0].action_type == "atomic_template"
    assert converted.actions[0].target == "agent:draft1"
    assert converted.requires_confirmation is False


def test_operator_confirm_agent_draft_records_runtime_failure_when_execution_raises():
    dummy = DummyOperator()
    record = {
        "query_key": "agent:draft1",
        "func_num": 109,
        "params": {"delay_sec": 2.0},
        "description": "Agent confirmed draft",
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "agent_orchestrator", "等待2秒", "等待操作者确认。"),),
        source="agent_orchestrator",
        raw_text="等待2秒",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    failures = []
    archived = []
    chats = []
    logs = []

    class Bridge:
        def confirm_pending_plan(self, draft_id, *, thread_id):
            return ToolResult.success(
                state="confirmed",
                message="确认已通过。",
                data={"draft_id": draft_id, "query_record": record},
            )

        def record_execution_failure(self, *, thread_id, query_record, error):
            failures.append((thread_id, query_record, error))
            return ToolResult.failure(
                state="execution_failed",
                message=f"执行失败：{error}",
                code="EXECUTION_FAILED",
                data={"query_record": query_record},
            )

        def clear_pending_confirm(self, *, thread_id):
            return None

    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_session_thread_id = lambda: "session-1"
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = None
    dummy._operator_last_motion_plan_result = None
    dummy._operator_last_process_precheck_result = None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    def failing_execute(_converted_plan):
        raise RuntimeError("modbus write failed")

    dummy._execute_nlp_plan = failing_execute

    dummy._operator_confirm_execute()

    assert failures == [("session-1", record, "modbus write failed")]
    assert getattr(dummy, "_operator_pending_confirm_plan") is None
    assert getattr(dummy, "execute_busy") is False
    assert "执行失败" in dummy.last_status
    assert archived[-1]["result"] == "failure"
    assert "modbus write failed" in archived[-1]["final_text"]
    assert log_args(logs[-1])[0:3] == ("用户页面", "Agent执行", "失败")


def test_operator_confirm_agent_draft_survives_runtime_failure_record_exception():
    dummy = DummyOperator()
    record = {
        "query_key": "agent:draft1",
        "func_num": 109,
        "params": {"delay_sec": 2.0},
        "description": "Agent confirmed draft",
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "agent_orchestrator", "等待2秒", "等待操作者确认。"),),
        source="agent_orchestrator",
        raw_text="等待2秒",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    archived = []
    chats = []
    logs = []

    class Bridge:
        def confirm_pending_plan(self, draft_id, *, thread_id):
            return ToolResult.success(
                state="confirmed",
                message="确认已通过。",
                data={"draft_id": draft_id, "query_record": record},
            )

        def record_execution_failure(self, *, thread_id, query_record, error):
            raise RuntimeError("runtime sqlite locked")

        def clear_pending_confirm(self, *, thread_id):
            return None

    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_session_thread_id = lambda: "session-1"
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = None
    dummy._operator_last_motion_plan_result = None
    dummy._operator_last_process_precheck_result = None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    def failing_execute(_converted_plan):
        raise RuntimeError("modbus write failed")

    dummy._execute_nlp_plan = failing_execute

    dummy._operator_confirm_execute()

    assert getattr(dummy, "execute_busy") is False
    assert "modbus write failed" in dummy.last_status
    assert "runtime failure record failed: runtime sqlite locked" in dummy.last_status
    assert archived[-1]["result"] == "failure"
    assert "runtime failure record failed: runtime sqlite locked" in archived[-1]["final_text"]
    assert chats[-1][2]["kind"] == "warn"
    assert log_args(logs[-1])[0:3] == ("用户页面", "Agent执行", "失败")


def test_operator_agent_async_execution_failure_log_records_runtime_failure():
    dummy = DummyOperator()
    record = QueryRecord(
        query_key="agent:draft1",
        func_num=109,
        params={"delay_sec": 2.0},
        description="Agent confirmed draft",
    )
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent:draft1", "restricted_agent", "等待2秒", "已确认"),),
        source="restricted_agent",
        raw_text="等待2秒",
        reason="Agent 草稿已确认，转入现有执行链路。",
        requires_confirmation=False,
        atomic_records={"agent:draft1": record},
    )
    failures = []
    archived = []

    class Bridge:
        def record_execution_failure(self, *, thread_id, query_record, error):
            failures.append((thread_id, query_record, error))
            return ToolResult.failure(
                state="execution_failed",
                message=f"执行失败：{error}",
                code="EXECUTION_FAILED",
                data={"query_record": query_record},
            )

    dummy.session_id = "session-1"
    dummy._nlp_current_plan = plan
    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs) or True

    entry = {
        "category": "自然语言",
        "action": "动作序列终止",
        "result": "失败",
        "detail": "停止于第 1 步",
    }

    handled = dummy._operator_archive_execution_from_log(entry, "执行失败：停止于第 1 步")

    assert handled is True
    assert failures == [
        (
            "session-1",
            {
                "query_key": "agent:draft1",
                "func_num": 109,
                "params": {"delay_sec": 2.0},
                "keywords": "",
                "description": "Agent confirmed draft",
                "safety_level": 5,
            },
            "停止于第 1 步",
        )
    ]
    assert archived[-1]["result"] == "failure"


def test_operator_agent_async_execution_failure_log_mentions_runtime_record_exception():
    dummy = DummyOperator()
    record = QueryRecord(
        query_key="agent:draft1",
        func_num=109,
        params={"delay_sec": 2.0},
        description="Agent confirmed draft",
    )
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent:draft1", "restricted_agent", "等待2秒", "已确认"),),
        source="restricted_agent",
        raw_text="等待2秒",
        reason="Agent 草稿已确认，转入现有执行链路。",
        requires_confirmation=False,
        atomic_records={"agent:draft1": record},
    )
    archived = []

    class Bridge:
        def record_execution_failure(self, *, thread_id, query_record, error):
            raise RuntimeError("runtime sqlite locked")

    dummy.session_id = "session-1"
    dummy._nlp_current_plan = plan
    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs) or True

    entry = {
        "category": "自然语言",
        "action": "动作序列终止",
        "result": "失败",
        "detail": "停止于第 1 步",
    }

    handled = dummy._operator_archive_execution_from_log(entry, "执行失败：停止于第 1 步")

    assert handled is True
    assert archived[-1]["result"] == "failure"
    assert "runtime failure record failed: runtime sqlite locked" in archived[-1]["final_text"]


def test_operator_confirm_agent_draft_blocks_when_execution_gate_rejects():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "agent_orchestrator", "等待2秒", "等待操作者确认。"),),
        source="agent_orchestrator",
        raw_text="等待2秒",
        reason="等待操作者确认。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    confirmed = []
    executed = []
    status_messages = []
    chats = []
    archived = []
    logs = []

    class Bridge:
        def confirm_pending_plan(self, draft_id, *, thread_id):
            confirmed.append((draft_id, thread_id))
            return ToolResult.success(state="confirmed", message="不应确认。", data={})

        def clear_pending_confirm(self, *, thread_id):
            return None

    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_session_thread_id = lambda: "session-1"
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_confirm_execution_gate_result = lambda _plan: ToolResult.failure(
        state="permission_denied",
        message="当前权限不允许执行该动作。",
        code="PERMISSION_DENIED",
    )
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append

    dummy._operator_confirm_execute()

    assert confirmed == []
    assert executed == []
    assert status_messages[-1] == "执行门禁未通过，已拒绝执行。"
    assert "当前权限不允许执行该动作" in chats[-1][1]
    assert archived[-1]["result"] == "blocked"
    assert log_args(logs[-1])[0:3] == ("执行门禁", "确认执行", "拒绝")


def test_operator_restricted_agent_gate_is_conservative():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    assert dummy._operator_should_try_restricted_agent("走到 X1000 Z300") is True
    assert dummy._operator_should_try_restricted_agent("当前报警是什么") is True
    assert dummy._operator_should_try_restricted_agent("为什么不能动") is True
    assert dummy._operator_should_try_restricted_agent("运动完成了吗") is True
    assert dummy._operator_should_try_restricted_agent("急停") is True
    assert dummy._operator_should_try_restricted_agent("等待2秒") is True
    assert dummy._operator_should_try_restricted_agent("IO1开") is True
    assert dummy._operator_should_try_restricted_agent("向左移动200") is True
    assert dummy._operator_should_try_restricted_agent("上升3毫米") is False
    assert dummy._operator_should_try_restricted_agent("小正，上升3毫米") is True
    assert dummy._operator_should_try_restricted_agent("小正，左移5毫米") is True
    assert dummy._operator_should_try_restricted_agent("小正，J1转到45度") is True
    assert dummy._operator_should_try_restricted_agent("小正，RY反转15度") is True
    assert dummy._operator_should_try_restricted_agent("J2关节软限位是多少") is False


def test_operator_restricted_agent_gate_is_enabled_by_default():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))

    assert dummy._operator_should_try_restricted_agent("走到 X1000 Z300") is True


def test_operator_restricted_agent_gate_can_be_disabled():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=False)

    assert dummy._operator_should_try_restricted_agent("走到 X1000 Z300") is False


def test_operator_restricted_agent_moving_state_ignores_stale_current_func_when_idle():
    dummy = DummyOperator()
    dummy.motion_percent = "空闲"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.current_func_text = "Func108"
    dummy.nlp_sequence_running = False
    dummy.flow_running = False

    assert dummy._operator_restricted_agent_is_moving() is False


def test_operator_restricted_agent_moving_state_blocks_active_runtime():
    dummy = DummyOperator()
    dummy.motion_percent = "空闲"
    dummy.busy = "运行中"
    dummy.run_state = "空闲"
    dummy.current_func_text = "空闲"
    dummy.nlp_sequence_running = False
    dummy.flow_running = False

    assert dummy._operator_restricted_agent_is_moving() is True


def test_operator_restricted_agent_moving_state_ignores_agent_background_parse_busy():
    dummy = DummyOperator()
    dummy.motion_percent = "空闲"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.current_func_text = "Func108"
    dummy.nlp_sequence_running = True
    dummy._operator_agent_parse_running = True
    dummy.flow_running = False

    assert dummy._operator_restricted_agent_is_moving() is False
    snapshot = dummy._operator_controller_snapshot_provider()
    assert snapshot.is_moving is False
    assert "nlp_sequence_running=True" not in snapshot.moving_reasons


def test_operator_try_restricted_agent_plan_uses_injected_service_and_adapter():
    dummy = DummyOperator()
    result = SimpleNamespace(
        kind="bypass",
        intent="alarm_query",
        func_id=None,
        message="规则旁路",
        understanding=None,
        draft=None,
        precheck_result=None,
        confirmation_text="",
        query_record=None,
    )
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._restricted_agent_service = SimpleNamespace(parse=lambda text: result)

    plan = dummy._operator_try_restricted_agent_plan("当前报警是什么")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "alarm_query"
    assert plan.source == "restricted_agent"


def test_operator_try_agent_orchestrator_returns_chat_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("L2是什么")

    assert plan is not None
    assert plan.actions[0].action_type == "chat"
    assert "运动规划预演" in plan.reason
    assert plan.source == "agent_orchestrator"


def test_operator_try_agent_orchestrator_returns_atomic_capability_chat_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("支持哪些原子命令")

    assert plan is not None
    assert plan.actions[0].action_type == "chat"
    assert "二次原子函数能力" in plan.reason
    assert plan.source == "agent_orchestrator"


def test_operator_try_agent_orchestrator_returns_identity_chat_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("你是谁")

    assert plan is not None
    assert plan.actions[0].action_type == "chat"
    assert "机械手自然语言交互助手" in plan.reason
    assert plan.source == "agent_orchestrator"


def test_operator_try_agent_orchestrator_returns_position_query_chat_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._position_registry = lambda: SimpleNamespace(
        get=lambda name: SimpleNamespace(pose=(350.0, 200.0, 500.0, 0.0, 90.0, 0.0)) if name == "A" else None
    )

    plan = dummy._operator_try_agent_orchestrator_plan("位置A坐标是多少")

    assert plan is not None
    assert plan.actions[0].action_type == "chat"
    assert "位置A坐标" in plan.reason
    assert "没有触发机械手动作" in plan.reason
    assert plan.source == "agent_orchestrator"


def test_operator_try_agent_orchestrator_returns_dashboard_query_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("通讯正常吗")

    assert plan is not None
    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "communication_faults"
    assert plan.source == "agent_orchestrator"


def test_operator_try_agent_orchestrator_returns_flow_draft_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    flow_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "打招呼", "flow_draft", "小正，创建流程", "已生成流程草案。"),),
        source="flow_draft",
        raw_text="小正，创建流程",
        reason="已生成流程草案。",
        flow_draft={"flow_name": "打招呼", "expanded_steps": [{"step_id": 1}]},
    )
    dummy._operator_agent_flow_draft_parse = lambda text: flow_plan

    plan = dummy._operator_try_agent_orchestrator_plan("小正，创建流程")

    assert plan is flow_plan


def test_operator_try_agent_orchestrator_returns_registered_flow_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    flow_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "打招呼", "rule", "执行打招呼", "命中流程规则"),),
        source="rule",
        raw_text="执行打招呼",
        reason="命中流程规则",
    )
    dummy._operator_agent_registered_flow_parse = lambda text: flow_plan

    plan = dummy._operator_try_agent_orchestrator_plan("执行打招呼")

    assert plan is flow_plan


def test_operator_try_agent_orchestrator_returns_memory_setting_chat_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    saved = []
    dummy._save_atomic_memory = lambda: saved.append(True)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，速度60%")

    assert plan is not None
    assert plan.actions[0].action_type == "chat"
    assert "速度=60.0%" in plan.reason
    assert dummy._atomic_memory.current_speed == 60.0
    assert saved == [True]


def test_operator_try_agent_orchestrator_returns_position_save_memory_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，保存当前位置为位置A")

    assert plan is not None
    assert plan.actions[0].action_type == "memory"
    assert plan.actions[0].target == "position_save:A"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is False


def test_operator_try_agent_orchestrator_returns_position_move_atomic_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy._atomic_memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    plan = dummy._operator_try_agent_orchestrator_plan("小正，移动到位置A")

    assert plan is not None
    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.func_num == 108
    assert record.params["target_x"] == 350.0
    assert record.params["target_z"] == 500.0
    assert plan.requires_confirmation is True


def test_operator_try_agent_orchestrator_prefers_query_table_position_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "acc_pct": 60.0,
                "dec_pct": 60.0,
                "move_type": 0,
            },
        )
    }

    plan = dummy._operator_try_agent_orchestrator_plan("小正，移动到位置a")

    assert plan is not None
    assert plan.actions[0].action_type == "atomic_template"
    assert plan.actions[0].target == "位置A"
    assert "请明确位置a的坐标" not in plan.reason
    record = plan.atomic_records[plan.actions[0].target]
    assert record.params["target_x"] == 1000.0
    assert record.params["target_z"] == 800.0
    assert plan.requires_confirmation is True


def test_operator_try_agent_orchestrator_blocks_position_move_without_wake_word():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
            },
        )
    }

    plan = dummy._operator_try_agent_orchestrator_plan("移动到位置a")

    assert plan is not None
    assert plan.actions[0].action_type == "clarification"
    assert "缺少" in plan.reason
    assert "唤醒词" in plan.reason
    assert plan.requires_confirmation is False
    assert not plan.atomic_records


def test_operator_execute_nlp_text_uses_query_table_position_template():
    dummy = DummyOperator()
    executed = []
    parsed = []
    dummy.nlp_input_edit = SimpleNamespace(
        toPlainText=lambda: "小正，移动到位置a",
        setPlainText=lambda text: None,
    )
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy.table = {
        "位置A": QueryRecord(
            query_key="位置A",
            func_num=108,
            description="移动到位置A",
            keywords="A点 位置A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "acc_pct": 60.0,
                "dec_pct": 60.0,
                "move_type": 0,
            },
        )
    }
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_set_pending_confirm_plan = lambda plan: None
    dummy._operator_maybe_begin_agent_processing_response = lambda text: False
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._set_nlp_result_plan = lambda plan: parsed.append(plan)
    dummy._execute_nlp_plan = lambda plan: executed.append(plan)

    dummy._execute_nlp_text()

    assert parsed
    assert executed == parsed
    plan = executed[0]
    assert plan.actions[0].action_type == "atomic_template"
    assert plan.actions[0].target == "位置A"
    assert "请明确位置a的坐标" not in plan.reason
    assert plan.atomic_records["位置A"].params["target_x"] == 1000.0


def test_operator_try_agent_orchestrator_returns_rest_pose_atomic_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory(default_rest_pose=(900.0, 0.0, 1000.0, 0.0, 0.0, 0.0))

    plan = dummy._operator_try_agent_orchestrator_plan("小正，去休息")

    assert plan is not None
    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.query_key == "atomic:rest_pose"
    assert record.params["target_x"] == 900.0


def test_operator_try_agent_orchestrator_repeats_last_atomic_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    last_record = QueryRecord(
        query_key="atomic:virtual:8:1:3",
        func_num=108,
        description="原子函数：Func108相对位移/姿态",
        params={
            "target_x": 0.0,
            "target_y": 0.0,
            "target_z": 3.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "fuzzy_pos": 1,
            "position_increment": 1,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
        },
    )
    dummy._atomic_memory.remember_record(last_record)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，再走一次")

    assert plan is not None
    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.query_key == "atomic:repeat:atomic:virtual:8:1:3"
    assert record.func_num == 108
    assert record.params["target_z"] == 3.0


def test_operator_try_agent_orchestrator_continues_last_direction_atomic_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy._atomic_memory.record_direction(func_num=108, axis_no=6, direction=1, step=3.0)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，继续")

    assert plan is not None
    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.func_num == 108
    assert record.params["target_y"] == 3.0


def test_operator_try_agent_orchestrator_does_not_treat_update_question_as_resume():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy._atomic_memory.record_direction(func_num=107, axis_no=6, direction=1, step=3.0)

    plan = dummy._operator_try_agent_orchestrator_plan("为什么不更新")

    if plan is not None:
        assert all(getattr(action, "target", "") != "sys_resume" for action in tuple(plan.actions or ()))
        assert all(getattr(action, "action_type", "") != "atomic_template" for action in tuple(plan.actions or ()))


def test_operator_try_agent_orchestrator_returns_back_history_atomic_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._atomic_memory = AtomicMemory()
    dummy._atomic_memory.push_position((100.0, 200.0, 300.0, 0.0, 45.0, 0.0))
    before_stack = list(dummy._atomic_memory.position_stack)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，返回上一步")

    assert plan is not None
    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.query_key == "atomic:history:back"
    assert record.func_num == 108
    assert record.params["target_x"] == 100.0
    assert dummy._atomic_memory.position_stack == before_stack


def test_nlp_apply_memory_action_deletes_position_from_memory_and_registry():
    dummy = DummyNlpMemory()
    dummy._atomic_memory = AtomicMemory()
    dummy._atomic_memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))
    removed = []
    logs = []
    dummy._position_registry = lambda: SimpleNamespace(
        remove=lambda name: (removed.append(name), (True, f"位置'{name}'已删除"))[1]
    )
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    ok = dummy._nlp_apply_memory_action(
        VoiceNlpAction("memory", "position_delete:A", "agent_orchestrator", "小正，删除位置A", "请求删除位置A。")
    )

    assert ok is True
    assert removed == ["A"]
    assert dummy._atomic_memory.get_position("A") is None
    assert log_args(logs[-1])[0:3] == ("自然语言", "删除位置", "成功")


def test_operator_try_agent_orchestrator_routes_joint_limit_query_to_dashboard_query():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("J2关节软限位是多少")

    assert plan is not None
    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "safety_boundary"


def test_operator_try_agent_orchestrator_falls_back_for_unknown_legacy_text():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("打开旧版帮助页面")

    assert plan is None


def test_operator_try_agent_orchestrator_logs_fallback_audit_payload():
    dummy = DummyOperator()
    logs = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    plan = dummy._operator_try_agent_orchestrator_plan("打开旧版帮助页面")

    assert plan is None
    assert log_args(logs[-1])[0:3] == ("Agent", "统一Agent交回旧路径", "提示")
    assert "reason=chat_agent_disabled_or_no_route" in log_args(logs[-1])[3]
    assert "intent=unknown" in log_args(logs[-1])[3]
    assert "needs_model=False" in log_args(logs[-1])[3]


def test_operator_try_agent_orchestrator_returns_clarification_for_ambiguous_control_text():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)

    plan = dummy._operator_try_agent_orchestrator_plan("往安全一点的位置挪一下")

    assert plan is not None
    assert plan.actions[0].action_type == "clarification"
    assert plan.source == "agent_orchestrator"
    assert "请补充明确" in plan.reason


def test_operator_try_agent_orchestrator_uses_deepseek_fallback_when_enabled():
    class FakeRestrictedService:
        def parse(self, text):
            self.text = text
            return SimpleNamespace(
                kind="blocked",
                intent="move_linear",
                func_id=108,
                message="测试阻断",
                understanding=None,
                draft=None,
                precheck_result=None,
                confirmation_text="",
                query_record=None,
            )

    class FakeDeepSeekClient:
        def generate_chat(self, prompt, system_prompt=None):
            self.prompt = prompt
            self.system_prompt = system_prompt
            return '{"kind":"candidate_text","text":"向左移动200","confidence":0.8}'

    service = FakeRestrictedService()
    client = FakeDeepSeekClient()
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._restricted_agent_service = service
    dummy._deepseek_client = client
    dummy.nlp_use_deepseek_check = SimpleNamespace(isChecked=lambda: True)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，往左边去一点")

    assert plan is not None
    assert service.text == "向左移动200"
    assert "不要输出 MODBUS" in client.system_prompt


def test_operator_try_agent_orchestrator_rejects_joint_jog_agent_draft():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    result = SimpleNamespace(
        kind="waiting_confirmation",
        intent="joint_jog",
        func_id=106,
        message="等待操作者确认。",
        understanding=None,
        draft=SimpleNamespace(
            draft_id="draft-j1",
            func_id=106,
            intent="joint_jog",
            params={
                "axis_no": 0,
                "pos_val": 45.0,
                "spd_pct": 40.0,
                "acc_pct": 45.0,
                "dec_pct": 50.0,
                "fuzzy_pos": 0,
                "fuzzy_spd": 1,
                "fuzzy_acc": 1,
                "fuzzy_dec": 1,
                "stop_cmd": 0,
            },
            param_sources={},
            raw_text="小正，J1转到45度",
            confidence=0.95,
            precheck_result={"valid": True, "summary": "L1通过。"},
        ),
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="【复述确认】Func106 关节轴点动",
        query_record=None,
    )
    dummy._restricted_agent_service = SimpleNamespace(parse=lambda text: result)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，J1转到45度")

    assert plan is None


def test_operator_try_agent_orchestrator_uses_tool_chain_confirm_plan_for_single_command():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1000, 1000),
        y=(-1000, 1000),
        z=(0, 1000),
        safe_speed_max=50.0,
        safe_acc_max=50.0,
        safe_dec_max=50.0,
        safe_r_min=0.0,
        safe_r_max=1000.0,
        safe_z_min=0.0,
        safe_z_max=1000.0,
        restricted_agent_enabled=True,
    )
    dummy.robot_x = "0.0"
    dummy.robot_y = "0.0"
    dummy.robot_z = "100.0"
    dummy.robot_r = "0.0 / 0.0 / 0.0"
    dummy._operator_restricted_agent_is_moving = lambda: False
    dummy._operator_dashboard_snapshot_dict = lambda refresh=False: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }
    dummy._append_log = lambda *args: None

    plan = dummy._operator_try_agent_orchestrator_plan("小正，移动到 X 一百，Y 0，Z 100，速度 50")

    assert plan is not None
    assert plan.actions[0].action_type == "agent_draft"
    assert plan.source == "agent_orchestrator"
    assert plan.requires_confirmation is True
    assert plan.flow_draft["agent_kind"] == "waiting_confirmation"
    assert plan.flow_draft["params"]["target_x"] == 100.0
    assert plan.flow_draft["precheck_result"]["valid"] is True


def test_operator_try_agent_orchestrator_does_not_depend_on_old_whitelist_for_supported_actions():
    from robot_modbus_lite.agent.command_understanding import CommandUnderstandingResult
    from robot_modbus_lite.agent.service import RestrictedAgentResult

    class FakeRestrictedService:
        def __init__(self):
            self.calls = []

        def parse(self, text):
            self.calls.append(text)
            return RestrictedAgentResult(
                kind="bypass",
                intent="sys_estop",
                message="应急停止。",
                understanding=CommandUnderstandingResult(raw_text=text, intent="sys_estop", func_id=104, confidence=1.0),
            )

    dummy = DummyOperator()
    service = FakeRestrictedService()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._operator_should_try_restricted_agent = lambda text: False
    dummy._operator_restricted_agent_service = lambda: service

    plan = dummy._operator_try_agent_orchestrator_plan("急停")

    assert plan is not None
    assert service.calls == []
    assert plan.actions[0].action_type == "system"
    assert plan.actions[0].target == "sys_estop"


def test_operator_try_agent_orchestrator_respects_restricted_agent_disabled():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=False)
    dummy._operator_restricted_agent_service = lambda: (_ for _ in ()).throw(AssertionError("service should not be created"))

    plan = dummy._operator_try_agent_orchestrator_plan("急停")

    assert plan is None


def test_operator_try_agent_orchestrator_routes_virtual_linear_jog_to_agent_draft():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    result = SimpleNamespace(
        kind="waiting_confirmation",
        intent="move_linear",
        func_id=108,
        message="等待操作者确认。",
        understanding=None,
        draft=SimpleNamespace(
            draft_id="draft-up",
            func_id=108,
            intent="move_linear",
            params={
                "target_x": 0.0,
                "target_y": 0.0,
                "target_z": 3.0,
                "target_rx": 0.0,
                "target_ry": 0.0,
                "target_rz": 0.0,
                "spd_pct": 40.0,
                "acc_pct": 45.0,
                "dec_pct": 50.0,
                "fuzzy_pos": 1,
                "fuzzy_spd": 0,
                "fuzzy_acc": 0,
                "fuzzy_dec": 0,
                "stop_cmd": 0,
                "position_increment": 1,
                "move_type": 0,
            },
            param_sources={},
            raw_text="小正，上升3毫米",
            confidence=0.95,
            precheck_result={"valid": True, "summary": "L1通过。"},
        ),
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="【复述确认】Func108 笛卡尔运动",
        query_record=None,
    )
    dummy._restricted_agent_service = SimpleNamespace(parse=lambda text: result)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，上升3毫米")

    assert plan is not None
    assert plan.actions[0].action_type == "agent_draft"
    assert plan.source == "restricted_agent"
    assert plan.flow_draft["func_id"] == 108


def test_operator_try_agent_orchestrator_routes_continuous_path_to_agent_draft():
    from robot_modbus_lite.agent.command_understanding import CommandUnderstandingResult
    from robot_modbus_lite.agent.drafts import CommandDraft
    from robot_modbus_lite.agent.service import RestrictedAgentResult

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
        raw_text="小正，规划路径走到 X1000 Z300",
        confidence=0.9,
    )
    result = RestrictedAgentResult(
        kind="waiting_confirmation",
        intent="continuous_path",
        func_id=112,
        message="等待操作者确认。",
        understanding=CommandUnderstandingResult(
                raw_text="小正，规划路径走到 X1000 Z300",
            intent="continuous_path",
            func_id=112,
            confidence=0.9,
        ),
        draft=draft,
        precheck_result={"valid": True, "summary": "L1通过。"},
        confirmation_text="【复述确认】Func112 连续路径运动",
    )
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._restricted_agent_service = SimpleNamespace(parse=lambda text: result)

    plan = dummy._operator_try_agent_orchestrator_plan("小正，规划路径走到 X1000 Z300")

    assert plan is not None
    assert plan.actions[0].action_type == "agent_draft"
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True
    assert plan.flow_draft["func_id"] == 112
    assert plan.flow_draft["safe_to_execute"] is False


def test_operator_try_agent_orchestrator_compound_plans_each_step_with_restricted_service(tmp_path):
    class FakeRestrictedService:
        def __init__(self):
            self.calls = []

        def parse(self, text):
            self.calls.append(text)
            return {"kind": "waiting_confirmation", "text": text}

    dummy = DummyOperator()
    dummy.runtime_root = tmp_path
    service = FakeRestrictedService()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._operator_restricted_agent_service = lambda: service

    plan = dummy._operator_try_agent_orchestrator_plan("走到X1000，然后等待2秒")

    assert plan is not None
    assert plan.actions[0].action_type == "compound_plan"
    assert service.calls == ["走到X1000", "等待2秒"]
    assert len(plan.flow_draft["step_results"]) == 2


def test_operator_parse_nlp_text_uses_restricted_agent_for_supported_text():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("query", "alarm_query", "restricted_agent", "当前报警是什么", "规则旁路"),),
        source="restricted_agent",
        raw_text="当前报警是什么",
        reason="规则旁路",
        semantic_level=2,
        semantic_label="工艺查询层",
    )
    calls = []
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "当前报警是什么")
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_try_agent_orchestrator_plan = lambda text: plan
    dummy._set_nlp_parse_busy = lambda busy: calls.append(("busy", busy))
    dummy._set_nlp_result_plan = lambda parsed: calls.append(("plan", parsed))
    dummy.status_label = SimpleNamespace(setText=lambda text: calls.append(("status", text)))
    dummy._append_log = lambda *args, **kwargs: calls.append(("log", args))

    dummy._parse_nlp_text()

    assert ("busy", True) in calls
    assert ("busy", False) in calls
    assert ("plan", plan) in calls
    assert any(item[0] == "status" and "解析完成" in item[1] for item in calls)


def test_operator_execute_nlp_text_uses_restricted_agent_for_supported_text():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "restricted_agent", "走到 X1000", "等待确认"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="等待确认",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    calls = []
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "走到 X1000", setPlainText=lambda text: None)
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), restricted_agent_enabled=True)
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_set_pending_confirm_plan = lambda pending: calls.append(("pending", pending))
    dummy._operator_try_agent_orchestrator_plan = lambda text: plan
    dummy._set_nlp_execute_busy = lambda busy: calls.append(("busy", busy))
    dummy._set_nlp_result_plan = lambda parsed: calls.append(("plan", parsed))
    dummy._execute_nlp_plan = lambda parsed: calls.append(("execute", parsed))

    dummy._execute_nlp_text()

    assert ("pending", None) in calls
    assert ("busy", True) in calls
    assert ("plan", plan) in calls
    assert ("execute", plan) in calls


def test_operator_restricted_agent_end_to_end_to_existing_execution_plan():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-100.0, 1200.0),
        y=(-200.0, 200.0),
        z=(0.0, 500.0),
        safe_r_min=0.0,
        safe_r_max=1200.0,
        safe_z_min=0.0,
        safe_z_max=500.0,
        safe_speed_max=80.0,
        safe_acc_max=70.0,
        safe_dec_max=60.0,
        restricted_agent_enabled=True,
    )
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "走到 X1000 Z300 速度60%", setPlainText=lambda text: None)
    dummy.robot_x = "10"
    dummy.robot_y = "20"
    dummy.robot_z = "30"
    dummy.robot_r = "1 / 2 / 3"
    dummy.busy = "空闲"
    dummy.motion_percent = "空闲"
    dummy.current_func_text = "空闲"
    dummy.alarm_code = "ERR_000"
    dummy.estop_active = False
    dummy.pause_active = False
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_dashboard_snapshot_dict = lambda refresh=True: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 300.0, "z": 100.0}},
    }
    status_messages = []
    chats = []
    executed = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = lambda converted_plan: executed.append(converted_plan)

    plan = dummy._operator_try_restricted_agent_plan("走到 X1000 Z300 速度60%")
    dummy._operator_set_pending_confirm_plan(plan)

    assert plan.actions[0].action_type == "agent_draft"
    assert dummy._operator_pending_confirm_plan is plan
    assert "Func108" in dummy._operator_confirm_detail_text()

    dummy._operator_confirm_execute()

    converted = executed[-1]
    assert converted.actions[0].action_type == "atomic_template"
    record = converted.atomic_records[converted.actions[0].target]
    assert record.func_num == 108
    assert record.params["target_x"] == 1000.0
    assert record.params["target_y"] == 20.0
    assert record.params["target_z"] == 300.0
    assert converted.requires_confirmation is False


def test_operator_restricted_agent_accepts_full_pose_inside_rz_boundary():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-3000.0, 3000.0),
        y=(-3000.0, 3000.0),
        z=(0.0, 3000.0),
        safe_r_min=200.0,
        safe_r_max=1800.0,
        safe_z_min=0.0,
        safe_z_max=2500.0,
        safe_speed_max=150.0,
        safe_acc_max=150.0,
        safe_dec_max=150.0,
        restricted_agent_enabled=True,
    )
    dummy.robot_x = "10"
    dummy.robot_y = "20"
    dummy.robot_z = "30"
    dummy.robot_r = "1 / 2 / 3"
    dummy.busy = "空闲"
    dummy.motion_percent = "空闲"
    dummy.current_func_text = "空闲"
    dummy.alarm_code = "ERR_000"
    dummy.estop_active = False
    dummy.pause_active = False
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_dashboard_snapshot_dict = lambda refresh=True: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 300.0, "z": 100.0}},
    }

    plan = dummy._operator_try_restricted_agent_plan(
        "让机械手走到X1000, Y200, Z800, RX0, RY45, RZ0, 速度60%, 加速度50%, 减速度50%"
    )

    assert plan.actions[0].action_type == "agent_draft"
    assert plan.flow_draft["precheck_result"]["valid"] is True


def test_operator_restricted_agent_runs_l2_motion_plan_when_engine_configured():
    class FakeEngine:
        def inverse(self, pose, fstatus: int):
            if fstatus == 0:
                return InverseKinematicsResult(True, (10.0, 20.0, 30.0, 40.0, 0.0, 0.0), fstatus)
            return InverseKinematicsResult(False, (), fstatus, "no solution")

    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-3000.0, 3000.0),
        y=(-3000.0, 3000.0),
        z=(0.0, 3000.0),
        safe_r_min=200.0,
        safe_r_max=1800.0,
        safe_z_min=0.0,
        safe_z_max=2500.0,
        safe_speed_max=150.0,
        safe_acc_max=150.0,
        safe_dec_max=150.0,
        restricted_agent_enabled=True,
    )
    dummy.operator_kinematics_engine = FakeEngine()
    dummy.robot_x = "10"
    dummy.robot_y = "20"
    dummy.robot_z = "650"
    dummy.robot_r = "1 / 2 / 3"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.motion_percent = "空闲"
    dummy.current_func_text = "空闲"
    dummy.alarm_code = "ERR_000"
    dummy.estop_active = False
    dummy.pause_active = False
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_publish_l2_progress = lambda event: None
    dummy._operator_dashboard_snapshot_dict = lambda refresh=True: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 300.0, "z": 650.0}},
    }

    plan = dummy._operator_try_restricted_agent_plan(
        "让机械手走到X1000, Y200, Z800, RX0, RY45, RZ0, 速度60%, 加速度50%, 减速度50%"
    )

    precheck = plan.flow_draft["precheck_result"]
    assert precheck["valid"] is True
    assert precheck["l2"]["status"] == "pass"
    assert precheck["selected_fstatus"] == 0


def test_operator_restricted_agent_blocks_when_l2_motion_plan_fails():
    class SingularEngine:
        def inverse(self, pose, fstatus: int):
            return InverseKinematicsResult(True, (10.0, 20.0, 30.0, 1.0, 0.0, 0.0), fstatus)

    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-3000.0, 3000.0),
        y=(-3000.0, 3000.0),
        z=(0.0, 3000.0),
        safe_r_min=200.0,
        safe_r_max=1800.0,
        safe_z_min=0.0,
        safe_z_max=2500.0,
        safe_speed_max=150.0,
        safe_acc_max=150.0,
        safe_dec_max=150.0,
        restricted_agent_enabled=True,
    )
    dummy.operator_kinematics_engine = SingularEngine()
    dummy.robot_x = "10"
    dummy.robot_y = "20"
    dummy.robot_z = "650"
    dummy.robot_r = "1 / 2 / 3"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.motion_percent = "空闲"
    dummy.current_func_text = "空闲"
    dummy.alarm_code = "ERR_000"
    dummy.estop_active = False
    dummy.pause_active = False
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_publish_l2_progress = lambda event: None
    dummy._operator_dashboard_snapshot_dict = lambda refresh=True: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 300.0, "z": 650.0}},
    }

    plan = dummy._operator_try_restricted_agent_plan(
        "让机械手走到X1000, Y200, Z800, RX0, RY45, RZ0, 速度60%, 加速度50%, 减速度50%"
    )

    assert plan.actions[0].action_type == "agent_blocked"
    assert plan.flow_draft["precheck_result"]["blocking_level"] == "L2"
    assert plan.flow_draft["precheck_result"]["l2"]["status"] == "fail"


def test_operator_restricted_agent_blocks_pose_angle_after_l2_passes():
    class FakeEngine:
        def inverse(self, pose, fstatus: int):
            return InverseKinematicsResult(True, (10.0, 20.0, 30.0, 40.0, 0.0, 0.0), fstatus)

    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-3000.0, 3000.0),
        y=(-3000.0, 3000.0),
        z=(0.0, 3000.0),
        safe_r_min=200.0,
        safe_r_max=1800.0,
        safe_z_min=0.0,
        safe_z_max=2500.0,
        safe_speed_max=150.0,
        safe_acc_max=150.0,
        safe_dec_max=150.0,
        restricted_agent_enabled=True,
    )
    dummy.operator_kinematics_engine = FakeEngine()
    dummy.robot_x = "10"
    dummy.robot_y = "20"
    dummy.robot_z = "650"
    dummy.robot_r = "1 / 2 / 3"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.motion_percent = "空闲"
    dummy.current_func_text = "空闲"
    dummy.alarm_code = "ERR_000"
    dummy.estop_active = False
    dummy.pause_active = False
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_publish_l2_progress = lambda event: None
    dummy._operator_dashboard_snapshot_dict = lambda refresh=True: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 300.0, "z": 650.0}},
    }

    plan = dummy._operator_try_restricted_agent_plan(
        "让机械手走到X1000, Y200, Z800, RX0, RY120, RZ0, 速度60%, 加速度50%, 减速度50%"
    )

    precheck = plan.flow_draft["precheck_result"]
    assert plan.actions[0].action_type == "agent_blocked"
    assert precheck["blocking_level"] == "POSE"
    assert precheck["pose_angles"]["status"] == "fail"


def test_operator_execute_nlp_plan_handles_agent_blocked_without_running_sequence():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_blocked", "draft1", "restricted_agent", "走到 X1000", "L1预检未通过。"),),
        source="restricted_agent",
        raw_text="走到 X1000",
        reason="L1预检未通过。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        requires_precheck=False,
        requires_confirmation=False,
        flow_draft={
            "agent_kind": "precheck_failed",
            "draft_id": "draft1",
            "precheck_result": {
                "valid": False,
                "items": [
                    {"id": "target_r_range", "status": "fail", "message": "目标 R=1900.0mm 超出安全范围。"}
                ],
                "suggestion": "请处理失败项后再执行计划。",
            },
        },
    )
    status_messages = []
    chats = []
    logs = []
    archived = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_publish_ai_answer_for_speech = lambda text: None
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert "目标 R=1900.0mm 超出安全范围" in status_messages[-1]
    assert "目标 R=1900.0mm 超出安全范围" in chats[-1][1]
    assert archived[-1]["result"] == "blocked"
    assert log_args(logs[-1])[0:3] == ("Agent", "安全预检阻断", "阻断")


def test_operator_execute_nlp_plan_handles_clarification_without_running_sequence():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", "gesture_mapping:小臂上下点头", "flow_draft", "原话", "需要补充动作映射：小臂上下点头"),),
        source="flow_draft",
        raw_text="原话",
        reason="需要补充动作映射：小臂上下点头",
        semantic_level=1,
        semantic_label="澄清确认层",
    )
    status_messages = []
    info_messages = []
    logs = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._show_info = lambda title, text: info_messages.append((title, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_add_chat_message = lambda *args, **kwargs: None

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert not getattr(dummy, "nlp_sequence_running", False)
    assert "需要补充动作映射" in status_messages[-1]
    assert info_messages == []
    assert log_args(logs[-1])[0:3] == ("自然语言", "澄清提示", "提示")


def test_operator_execute_nlp_plan_handles_flow_draft_without_running_sequence():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "打招呼", "flow_draft", "原话", "已生成流程草案：打招呼，共7步，等待确认保存/执行。"),),
        source="flow_draft",
        raw_text="原话",
        reason="已生成流程草案：打招呼，共7步，等待确认保存/执行。",
        semantic_level=3,
        semantic_label="流程草案编排层",
        requires_confirmation=True,
        flow_draft={"flow_name": "打招呼", "expanded_steps": [{"step_id": 1}, {"step_id": 2}]},
    )
    status_messages = []
    info_messages = []
    logs = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._show_info = lambda title, text: info_messages.append((title, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert not getattr(dummy, "nlp_sequence_running", False)
    assert "流程草案" in status_messages[-1]
    assert info_messages == []
    assert "2 步" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("自然语言", "流程草案", "提示")


def test_operator_flow_draft_initial_response_includes_step_preview():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "打招呼", "flow_draft", "原话", "已生成流程草案：打招呼，共3步，等待确认保存/执行。"),),
        source="flow_draft",
        raw_text="原话",
        reason="已生成流程草案：打招呼，共3步，等待确认保存/执行。",
        semantic_level=3,
        semantic_label="流程草案编排层",
        requires_confirmation=True,
        flow_draft={
            "flow_name": "打招呼",
            "positions": [{"name": "home", "pose": [1475, 0, 1545, 0, 0, 0]}],
            "expanded_steps": [
                {
                    "step_id": 1,
                    "description": "移动到home",
                    "func_id": 108,
                    "position_name": "home",
                    "params": {
                        "target_x": 1475.0,
                        "target_y": 0.0,
                        "target_z": 1545.0,
                        "target_rx": 0.0,
                        "target_ry": 0.0,
                        "target_rz": 0.0,
                        "spd_pct": 50.0,
                        "acc_pct": 50.0,
                        "dec_pct": 50.0,
                        "move_type": 0,
                        "stop_cmd": 0,
                        "fuzzy_pos": 0,
                    },
                },
                {
                    "step_id": 2,
                    "description": "小臂上下点头:Ry正转",
                    "func_id": 107,
                    "params": {"axis_no": 10, "pos_val": 15.0, "spd_pct": 50.0, "fuzzy_pos": 0},
                },
                {
                    "step_id": 3,
                    "description": "小臂上下点头:Ry反转",
                    "func_id": 107,
                    "params": {"axis_no": 10, "pos_val": -15.0, "spd_pct": 50.0, "fuzzy_pos": 0},
                },
            ],
        },
    )
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=lambda text: None)
    dummy._append_log = lambda *args, **kwargs: None
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))

    dummy._execute_nlp_plan(plan)

    response = chats[-1][1]
    assert "步骤流：" in response
    assert "01  Func108  移动到home" in response
    assert "Func108" in response
    assert "目标  X target_x=1475  Y target_y=0  Z target_z=1545" in response
    assert "运动  速度 spd_pct=50%  加速度 acc_pct=50%  减速度 dec_pct=50%  move_type=0" in response
    assert "标志  stop_cmd=0  fuzzy_pos=0" in response
    assert "02  Func107  小臂上下点头:Ry正转" in response
    assert "动作  axis_no=10  pos_val=15" in response
    assert "03  Func107  小臂上下点头:Ry反转" in response
    assert "pos_val=-15" in response


def test_operator_pending_flow_draft_query_ignores_new_draft_detail_answer():
    dummy = DummyOperator()
    dummy._operator_pending_flow_draft = {
        "flow_name": "打个招呼的小",
        "expanded_steps": [{"step_id": 1, "description": "移动到home", "func_id": 108, "params": {}}],
    }
    chats = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))

    handled = dummy._operator_handle_pending_flow_draft_query(
        "草案名称为抓取流程，然后步骤为从 a 位置移动到位置 b，再移动位置 c。"
    )

    assert handled is False
    assert chats == []


def test_operator_flow_creation_followup_adds_wake_word_for_detail_answer():
    dummy = DummyOperator()
    dummy._operator_pending_flow_creation_followup = True

    prepared = dummy._operator_prepare_pending_flow_creation_followup_text(
        "这个流程为，先移动到位置 a，然后移动到位置 b，最后移动到位置 c。"
    )

    assert prepared.startswith("小正，创建流程，")
    assert "先移动到位置 a" in prepared
    assert dummy._operator_pending_flow_creation_followup is False


def test_operator_flow_creation_followup_does_not_rewrite_unrelated_chat():
    dummy = DummyOperator()
    dummy._operator_pending_flow_creation_followup = True

    prepared = dummy._operator_prepare_pending_flow_creation_followup_text("你好")

    assert prepared == "你好"
    assert dummy._operator_pending_flow_creation_followup is True


def test_operator_flow_draft_with_missing_target_prompts_for_clarification():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "缺目标流程", "flow_draft", "原话", "已生成流程草案。"),),
        source="flow_draft",
        raw_text="原话",
        reason="已生成流程草案。",
        semantic_level=3,
        semantic_label="流程草案编排层",
        requires_confirmation=True,
        flow_draft={
            "flow_name": "缺目标流程",
            "expanded_steps": [{"step_id": 1, "action": "移动", "func_id": 108, "params": {"spd_pct": 50.0}}],
        },
    )
    status_messages = []
    logs = []
    chats = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))

    dummy._execute_nlp_plan(plan)

    current = dummy._operator_execution_plan_service().current_clarification()
    assert current is not None
    assert current.missing_field == "target_pose"
    assert dummy._operator_pending_flow_draft["needs_precheck"] is True
    assert "请补充" in chats[-1][1]
    assert "目标坐标" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("自然语言", "流程草案追问", "提示")


def test_operator_flow_draft_move_type_default_is_shown_to_user():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow_draft", "缺运动方式流程", "flow_draft", "原话", "已生成流程草案。"),),
        source="flow_draft",
        raw_text="原话",
        reason="已生成流程草案。",
        semantic_level=3,
        semantic_label="流程草案编排层",
        requires_confirmation=True,
        flow_draft={
            "flow_name": "缺运动方式流程",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动",
                    "func_id": 108,
                    "params": {
                        "target_x": 1.0,
                        "target_y": 2.0,
                        "target_z": 3.0,
                        "target_rx": 4.0,
                        "target_ry": 5.0,
                        "target_rz": 6.0,
                    },
                }
            ],
        },
    )
    status_messages = []
    logs = []
    chats = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))

    dummy._execute_nlp_plan(plan)

    assert dummy._operator_pending_flow_draft["expanded_steps"][0]["params"]["move_type"] == 0
    assert "默认使用直线插补" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("自然语言", "流程草案", "提示")


def test_operator_execute_nlp_plan_handles_chat_unknown_without_failure():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("unknown", None, "rule", "我不知道", "闲聊或咨询，未触发控制动作"),),
        source="rule",
        raw_text="我不知道",
        reason="闲聊或咨询，未触发控制动作",
        semantic_level=1,
        semantic_label="闲聊咨询层",
    )
    status_messages = []
    info_messages = []
    logs = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._show_info = lambda title, text: info_messages.append((title, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert not getattr(dummy, "nlp_sequence_running", False)
    assert "没有触发机械手动作" in status_messages[-1]
    assert info_messages == []
    assert log_args(logs[-1])[0:3] == ("自然语言", "闲聊咨询", "成功")


def test_operator_execute_nlp_plan_handles_compound_plan_without_running_sequence():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("compound_plan", "compound:test", "agent_orchestrator", "走到X1000，然后等待2秒", "已生成复合指令草案：2 步，等待确认。"),),
        source="agent_orchestrator",
        raw_text="走到X1000，然后等待2秒",
        reason="已生成复合指令草案：2 步，当前仅展示，不自动执行。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        flow_draft={
            "agent_kind": "compound_plan_draft",
            "plan_id": "compound:test",
            "steps": ("走到X1000", "等待2秒"),
            "step_machine": {
                "status": "waiting_step_confirmation",
                "current_index": 0,
                "current_step_text": "走到X1000",
                "steps": (
                    {"index": 0, "text": "走到X1000", "status": "waiting_confirmation"},
                    {"index": 1, "text": "等待2秒", "status": "pending"},
                ),
            },
        },
    )
    status_messages = []
    logs = []
    chats = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert not getattr(dummy, "nlp_sequence_running", False)
    assert "复合指令草案" in status_messages[-1]
    assert "当前等待确认第 1/2 步：走到X1000" in chats[-1][1]
    assert "当前不会自动执行" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("Agent", "复合指令草案", "提示")


def test_operator_execute_nlp_plan_stores_executable_compound_as_pending_flow_draft():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("compound_plan", "compound:test", "agent_orchestrator", "走到X1000，然后等待2秒", "已生成复合指令草案：2 步。"),),
        source="agent_orchestrator",
        raw_text="走到X1000，然后等待2秒",
        reason="已生成复合指令草案：2 步。",
        semantic_level=3,
        semantic_label="常规生产执行层",
        flow_draft={
            "agent_kind": "compound_plan_draft",
            "plan_id": "compound:test",
            "flow_name": "agent_compound_test",
            "steps": ("走到X1000", "等待2秒"),
            "expanded_steps": [
                {"step_id": 1, "action": "move_linear", "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}},
                {"step_id": 2, "action": "delay_blocking", "func_id": 109, "description": "等待2秒", "params": {"delay_sec": 2.0}},
            ],
            "safe_to_execute": True,
            "step_machine": {
                "status": "waiting_step_confirmation",
                "current_index": 0,
                "current_step_text": "走到X1000",
                "steps": (
                    {"index": 0, "text": "走到X1000", "status": "waiting_confirmation"},
                    {"index": 1, "text": "等待2秒", "status": "pending"},
                ),
            },
        },
    )
    status_messages = []
    chats = []
    logs = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    assert dummy._operator_pending_flow_draft["flow_name"] == "agent_compound_test"
    assert dummy._operator_pending_flow_draft["expanded_steps"][1]["func_id"] == 109
    assert "可说“确认执行”" in chats[-1][1]
    assert "当前不会自动执行" not in chats[-1][1]


def test_operator_pending_executable_compound_confirm_prepares_first_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    status_messages = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "move_linear",
                "func_id": 108,
                "description": "走到X1000",
                "params": {
                    "target_x": 1000.0,
                    "target_y": 200.0,
                    "target_z": 800.0,
                    "target_rx": 0.0,
                    "target_ry": 45.0,
                    "target_rz": 0.0,
                    "spd_pct": 60.0,
                    "acc_pct": 50.0,
                    "dec_pct": 50.0,
                    "stop_cmd": 0,
                    "fuzzy_pos": 0,
                    "fuzzy_spd": 0,
                    "fuzzy_acc": 0,
                    "fuzzy_dec": 0,
                    "move_type": 0,
                },
            },
            {"step_id": 2, "action": "delay_blocking", "func_id": 109, "description": "等待2秒", "params": {"delay_sec": 2.0}},
        ],
        "step_machine": {
            "status": "waiting_step_confirmation",
            "current_index": 0,
            "current_step_text": "走到X1000",
            "steps": [
                {"index": 0, "text": "走到X1000", "status": "waiting_confirmation"},
                {"index": 1, "text": "等待2秒", "status": "pending"},
            ],
        },
    }

    handled = dummy._operator_handle_pending_flow_draft_command("确认执行")

    assert handled is True
    assert dummy._operator_pending_confirm_plan.actions[0].action_type == "atomic_template"
    assert dummy._operator_pending_confirm_plan.actions[0].target == "agent_compound_test_step_1"
    assert "第 1/2 步" in chats[-1][1]


def test_operator_confirm_executable_compound_prepares_current_step_confirmation(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    status_messages = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [
            {"step_id": 1, "action": "move_linear", "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}},
            {"step_id": 2, "action": "delay_blocking", "func_id": 109, "description": "等待2秒", "params": {"delay_sec": 2.0}},
        ],
        "step_machine": {
            "status": "waiting_step_confirmation",
            "current_index": 0,
            "current_step_text": "走到X1000",
            "steps": (
                {"index": 0, "text": "走到X1000", "status": "waiting_confirmation"},
                {"index": 1, "text": "等待2秒", "status": "pending"},
            ),
        },
    }

    dummy._operator_confirm_execute()

    assert dummy._operator_pending_confirm_plan is not None
    assert dummy._operator_pending_confirm_plan.actions[0].action_type == "atomic_template"
    assert dummy._operator_pending_confirm_plan.actions[0].target == "agent_compound_test_step_1"
    assert "agent_compound_test_step_1" in dummy._operator_pending_confirm_plan.atomic_records
    assert dummy._operator_pending_flow_draft["step_machine"]["steps"][0]["status"] == "waiting_confirmation"
    assert "第 1/2 步" in chats[-1][1]
    assert "走到X1000" in chats[-1][1]


def test_operator_compound_step_success_advances_to_next_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    status_messages = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [
            {"step_id": 1, "action": "move_linear", "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}},
            {"step_id": 2, "action": "delay_blocking", "func_id": 109, "description": "等待2秒", "params": {"delay_sec": 2.0}},
        ],
        "step_machine": {
            "status": "step_confirmed",
            "current_index": 0,
            "current_step_text": "走到X1000",
            "steps": [
                {"index": 0, "text": "走到X1000", "status": "confirmed"},
                {"index": 1, "text": "等待2秒", "status": "pending"},
            ],
        },
    }

    handled = dummy._operator_update_compound_step_result(ok=True, reason="第1步完成")

    machine = dummy._operator_pending_flow_draft["step_machine"]
    assert handled is True
    assert machine["status"] == "waiting_step_confirmation"
    assert machine["current_index"] == 1
    assert machine["steps"][0]["status"] == "completed"
    assert machine["steps"][1]["status"] == "waiting_confirmation"
    assert "第 2/2 步" in chats[-1][1]
    assert "等待2秒" in chats[-1][1]


def test_operator_compound_step_success_records_runtime_event(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.session_id = "compound-session"
    calls = []
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._refresh_operator_view = lambda: None

    class Bridge:
        def record_compound_step_result(self, *, thread_id, ok, reason):
            calls.append({"thread_id": thread_id, "ok": ok, "reason": reason})
            return None

    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [
            {"step_id": 1, "action": "move_linear", "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}},
            {"step_id": 2, "action": "delay_blocking", "func_id": 109, "description": "等待2秒", "params": {"delay_sec": 2.0}},
        ],
        "step_machine": {
            "status": "step_confirmed",
            "current_index": 0,
            "current_step_text": "走到X1000",
            "steps": [
                {"index": 0, "text": "走到X1000", "status": "confirmed"},
                {"index": 1, "text": "等待2秒", "status": "pending"},
            ],
        },
    }

    handled = dummy._operator_update_compound_step_result(ok=True, reason="第1步完成")

    assert handled is True
    assert calls == [{"thread_id": "compound-session", "ok": True, "reason": "第1步完成"}]


def test_operator_compound_step_failure_stops_at_current_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.session_id = "compound-session"
    chats = []
    status_messages = []
    archived = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [
            {"step_id": 1, "action": "move_linear", "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}},
            {"step_id": 2, "action": "delay_blocking", "func_id": 109, "description": "等待2秒", "params": {"delay_sec": 2.0}},
        ],
        "step_machine": {
            "status": "step_confirmed",
            "current_index": 0,
            "current_step_text": "走到X1000",
            "steps": [
                {"index": 0, "text": "走到X1000", "status": "confirmed"},
                {"index": 1, "text": "等待2秒", "status": "pending"},
            ],
        },
    }
    state = dummy._operator_session_state()
    dummy._operator_set_session_state(
        state.with_compound_plan({"plan_id": "compound:test", "steps": ["走到X1000", "等待2秒"]}).with_pending_confirm(
            {"plan_id": "compound:test:step1", "source": "compound_step"}
        )
    )

    handled = dummy._operator_update_compound_step_result(ok=False, reason="控制器报警")

    machine = dummy._operator_pending_flow_draft["step_machine"]
    runtime_state = dummy._operator_session_state()
    assert handled is True
    assert machine["status"] == "failed"
    assert machine["current_index"] == 0
    assert machine["steps"][0]["status"] == "failed"
    assert machine["steps"][1]["status"] == "pending"
    assert runtime_state.current_compound_plan == {}
    assert runtime_state.pending_confirm == {}
    assert runtime_state.mode == "editing_flow"
    assert "停止在第 1/2 步" in chats[-1][1]
    assert "控制器报警" in chats[-1][1]
    assert archived[-1]["result"] == "compound_step_failed"


def test_operator_compound_final_step_success_marks_plan_completed(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    status_messages = []
    archived = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [{"step_id": 1, "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}}],
        "step_machine": {
            "status": "step_confirmed",
            "current_index": 0,
            "current_step_text": "走到X1000",
            "steps": [{"index": 0, "text": "走到X1000", "status": "confirmed"}],
        },
    }

    handled = dummy._operator_update_compound_step_result(ok=True, reason="第1步完成")

    machine = dummy._operator_pending_flow_draft["step_machine"]
    assert handled is True
    assert machine["status"] == "completed"
    assert machine["steps"][0]["status"] == "completed"
    assert "复合指令执行完成：共完成 1 步" in chats[-1][1]
    assert archived[-1]["result"] == "compound_completed"


def test_operator_confirm_compound_step_marks_step_confirmed_before_execution(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    executed = []
    dummy.status_label = SimpleNamespace(setText=lambda text: None)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._set_nlp_execute_busy = lambda busy: None
    dummy._execute_nlp_plan = lambda plan: executed.append(plan)
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "plan_id": "compound:test",
        "flow_name": "agent_compound_test",
        "safe_to_execute": True,
        "expanded_steps": [{"step_id": 1, "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0}}],
        "step_machine": {
            "status": "waiting_step_confirmation",
            "current_index": 0,
            "steps": [{"index": 0, "text": "走到X1000", "status": "waiting_confirmation"}],
        },
    }
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent_compound_test_step_1", "compound_step", "", "第1步"),),
        source="compound_step",
        raw_text="",
        reason="第1步",
        requires_confirmation=True,
        atomic_records={
            "agent_compound_test_step_1": QueryRecord(
                query_key="agent_compound_test_step_1",
                func_num=108,
                params={"target_x": 1000.0},
            )
        },
        flow_draft={"agent_kind": "compound_step_confirmation", "compound_step_index": 0},
    )

    dummy._operator_confirm_execute()

    assert executed
    assert dummy._operator_pending_flow_draft["step_machine"]["status"] == "step_confirmed"
    assert dummy._operator_pending_flow_draft["step_machine"]["steps"][0]["status"] == "confirmed"


def test_operator_compound_step_result_log_updates_step_machine(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    calls = []
    dummy._operator_update_compound_step_result = lambda *, ok, reason="": calls.append((ok, reason)) or True
    dummy._operator_archive_execution_from_log = lambda entry, text: None
    dummy._operator_publish_response = lambda message: None
    dummy._operator_route_voice_recognition_from_log = lambda entry: False
    dummy._operator_note_flow_completion_response = lambda entry: None
    dummy.operator_response_builder = ResponseBuilder()

    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列第1步成功",
            "result": "成功",
            "detail": "atomic_template | agent_compound_test_step_1 | compound_step",
        }
    )
    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列第1步失败",
            "result": "失败",
            "detail": "atomic_template | agent_compound_test_step_1 | compound_step",
        }
    )

    assert calls == [(True, "atomic_template | agent_compound_test_step_1 | compound_step"), (False, "atomic_template | agent_compound_test_step_1 | compound_step")]


def test_operator_cancel_compound_step_confirmation_clears_compound_plan(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy.session_id = "compound-session"
    chats = []
    status_messages = []
    archived = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._refresh_operator_view = lambda: None
    dummy._operator_pending_flow_draft = {"agent_kind": "compound_plan_draft", "plan_id": "compound:test"}
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent_compound_test_step_1", "compound_step", "", "第1步"),),
        source="compound_step",
        raw_text="",
        reason="第1步",
        requires_confirmation=True,
        flow_draft={"agent_kind": "compound_step_confirmation", "compound_step_index": 0},
    )
    state = dummy._operator_session_state()
    dummy._operator_set_session_state(
        state.with_compound_plan({"plan_id": "compound:test", "steps": ["走到X1000"]}).with_pending_confirm(
            {"plan_id": "compound:test:step1", "source": "compound_step"}
        )
    )

    dummy._operator_cancel_confirm()

    runtime_state = dummy._operator_session_state()
    assert dummy._operator_pending_flow_draft is None
    assert dummy._operator_pending_confirm_plan is None
    assert runtime_state.current_compound_plan == {}
    assert runtime_state.pending_confirm == {}
    assert runtime_state.mode == "idle"
    assert "已取消复合指令" in chats[-1][1]
    assert archived[-1]["result"] == "compound_cancelled"


def test_operator_confirm_stage_modify_compound_step_updates_draft_params(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy.status_label = SimpleNamespace(setText=lambda text: None)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    record = QueryRecord(
        query_key="agent_compound_test_step_1",
        func_num=108,
        params={"target_x": 1000.0, "spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent_compound_test_step_1", "compound_step", "", "第1步"),),
        source="compound_step",
        raw_text="",
        reason="第1步",
        requires_confirmation=True,
        atomic_records={"agent_compound_test_step_1": record},
        flow_draft={"agent_kind": "compound_step_confirmation", "compound_step_index": 0},
    )
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "expanded_steps": [
            {"step_id": 1, "func_id": 108, "description": "走到X1000", "params": {"target_x": 1000.0, "spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0}}
        ],
    }
    dummy._operator_prepare_plan_prechecks = lambda plan: None

    handled = dummy._operator_handle_pending_confirm_modify("速度改为30%")

    assert handled is True
    assert record.params["spd_pct"] == 30.0
    assert dummy._operator_pending_flow_draft["expanded_steps"][0]["params"]["spd_pct"] == 30.0
    assert dummy._operator_pending_flow_draft["expanded_steps"][0]["params"]["acc_pct"] == 30.0
    assert "速度调整为30%" in chats[-1][1]


def test_operator_confirm_stage_modify_single_motion_updates_pending_record(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_prepare_plan_prechecks = lambda plan: setattr(dummy, "prechecked", True)
    record = QueryRecord(
        query_key="agent_single_move",
        func_num=108,
        params={
            "target_x": 1000.0,
            "target_y": 200.0,
            "target_z": 800.0,
            "target_rx": 0.0,
            "target_ry": 45.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
        },
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent_single_move", "agent", "", "单条待确认动作"),),
        source="restricted_agent",
        raw_text="让机械手走到X1000 Y200 Z800",
        reason="单条待确认动作",
        requires_confirmation=True,
        atomic_records={"agent_single_move": record},
        flow_draft={
            "agent_kind": "single_command_confirmation",
            "func_id": 108,
            "params": dict(record.params),
        },
    )

    handled = dummy._operator_handle_pending_confirm_modify("速度改为30%")

    assert handled is True
    assert record.params["spd_pct"] == 30.0
    assert record.params["acc_pct"] == 30.0
    assert record.params["dec_pct"] == 30.0
    assert getattr(dummy, "prechecked", False) is True
    assert getattr(dummy, "refreshed", False) is True
    assert "速度调整为30%" in chats[-1][1]
    assert logs[-1][1] == "确认阶段修改参数"


def test_operator_confirm_stage_modify_pose_updates_pending_motion_record(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_prepare_plan_prechecks = lambda plan: setattr(dummy, "prechecked", True)
    record = QueryRecord(
        query_key="agent_single_move",
        func_num=108,
        params={
            "target_x": 1000.0,
            "target_y": 200.0,
            "target_z": 800.0,
            "target_rx": 0.0,
            "target_ry": 45.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
        },
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent_single_move", "agent", "", "单条待确认动作"),),
        source="restricted_agent",
        raw_text="让机械手走到X1000 Y200 Z800",
        reason="单条待确认动作",
        requires_confirmation=True,
        atomic_records={"agent_single_move": record},
        flow_draft={
            "agent_kind": "single_command_confirmation",
            "func_id": 108,
            "params": dict(record.params),
        },
    )

    handled = dummy._operator_handle_pending_confirm_modify("X改为1200，RY改为30")

    assert handled is True
    assert record.params["target_x"] == 1200.0
    assert record.params["target_ry"] == 30.0
    assert getattr(dummy, "prechecked", False) is True
    assert "目标参数调整" in chats[-1][1]


def test_operator_confirm_stage_speed_modify_on_delay_step_does_not_route_to_dashboard(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_handle_dashboard_query = lambda text: (_ for _ in ()).throw(AssertionError("should not route to dashboard"))
    record = QueryRecord(
        query_key="delay_2s",
        func_num=109,
        params={"delay_sec": 2.0},
        description="等待2秒",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "delay_2s", "compound_step", "", "第2步"),),
        source="compound_step",
        raw_text="",
        reason="第2步",
        requires_confirmation=True,
        atomic_records={"delay_2s": record},
        flow_draft={"agent_kind": "compound_step_confirmation", "compound_step_index": 1},
    )

    handled = dummy._handle_operator_ui_command("速度改为30%")

    assert handled is True
    assert record.params == {"delay_sec": 2.0}
    assert "当前待确认步骤不包含速度参数" in chats[-1][1]
    assert getattr(dummy, "refreshed", False) is True
    assert logs[-1][1] == "确认阶段修改参数"


def test_operator_confirm_stage_modify_delay_updates_delay_record(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_prepare_plan_prechecks = lambda plan: setattr(dummy, "prechecked", True)
    record = QueryRecord(
        query_key="delay_2s",
        func_num=109,
        params={"delay_sec": 2.0},
        description="等待2秒",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "delay_2s", "compound_step", "", "第2步"),),
        source="compound_step",
        raw_text="",
        reason="第2步",
        requires_confirmation=True,
        atomic_records={"delay_2s": record},
    )

    handled = dummy._operator_handle_pending_confirm_modify("延时改为500毫秒")

    assert handled is True
    assert record.params["delay_sec"] == 0.5
    assert getattr(dummy, "prechecked", False) is True
    assert "延时调整为0.5秒" in chats[-1][1]


def test_operator_confirm_stage_modify_io_updates_io_record(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_prepare_plan_prechecks = lambda plan: setattr(dummy, "prechecked", True)
    record = QueryRecord(
        query_key="io_1_off",
        func_num=120,
        params={"io_no": 1, "io_action": 0},
        description="输出1关闭",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "io_1_off", "compound_step", "", "第3步"),),
        source="compound_step",
        raw_text="",
        reason="第3步",
        requires_confirmation=True,
        atomic_records={"io_1_off": record},
    )

    handled = dummy._operator_handle_pending_confirm_modify("改成输出2打开")

    assert handled is True
    assert record.params["io_no"] == 2.0
    assert record.params["io_action"] == 1.0
    assert getattr(dummy, "prechecked", False) is True
    assert "IO2 调整为打开" in chats[-1][1]


def test_operator_confirm_stage_incomplete_func_replacement_asks_for_full_action(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_handle_dashboard_query = lambda text: (_ for _ in ()).throw(AssertionError("should not route to dashboard"))
    record = QueryRecord(
        query_key="delay_2s",
        func_num=109,
        params={"delay_sec": 2.0},
        description="等待2秒",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "delay_2s", "compound_step", "", "第2步"),),
        source="compound_step",
        raw_text="",
        reason="第2步",
        requires_confirmation=True,
        atomic_records={"delay_2s": record},
        flow_draft={"agent_kind": "compound_step_confirmation", "compound_step_index": 1},
    )

    handled = dummy._handle_operator_ui_command("改成Func108")

    assert handled is True
    assert record.func_num == 109
    assert "不能只改 Func 号" in chats[-1][1]
    assert "移动到 X100 Y0 Z800" in chats[-1][1]
    assert getattr(dummy, "refreshed", False) is True


def test_operator_confirm_stage_complete_replacement_updates_compound_step(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    chats = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: setattr(dummy, "refreshed", True)
    dummy._operator_prepare_plan_prechecks = lambda plan: setattr(dummy, "prechecked_plan", plan)
    dummy._operator_pending_flow_draft = {
        "agent_kind": "compound_plan_draft",
        "flow_name": "测试",
        "raw_text": "测试流程",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "delay_blocking",
                "func_id": 109,
                "description": "等待2秒",
                "params": {"delay_sec": 2.0},
            }
        ],
    }
    delay_record = QueryRecord(
        query_key="测试_step_1",
        func_num=109,
        params={"delay_sec": 2.0},
        description="等待2秒",
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "测试_step_1", "compound_step", "", "第1步"),),
        source="compound_step",
        raw_text="",
        reason="第1步",
        requires_confirmation=True,
        atomic_records={"测试_step_1": delay_record},
        flow_draft={"agent_kind": "compound_step_confirmation", "compound_step_index": 0},
    )
    replacement_record = QueryRecord(
        query_key="agent_single_move",
        func_num=108,
        params={
            "target_x": 100.0,
            "target_y": 0.0,
            "target_z": 800.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "move_type": 0,
        },
        description="移动到X100 Y0 Z800",
    )
    replacement_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "agent_single_move", "agent", "", "替换步骤"),),
        source="restricted_agent",
        raw_text="把这一步改成移动到X100 Y0 Z800，速度50%",
        reason="替换步骤",
        requires_confirmation=True,
        atomic_records={"agent_single_move": replacement_record},
    )
    dummy._operator_try_agent_orchestrator_plan = lambda text: replacement_plan

    handled = dummy._handle_operator_ui_command("把这一步改成移动到X100 Y0 Z800，速度50%")

    assert handled is True
    step = dummy._operator_pending_flow_draft["expanded_steps"][0]
    assert step["func_id"] == 108
    assert step["params"]["target_x"] == 100.0
    assert step["params"]["spd_pct"] == 50.0
    assert step["description"] == "移动到X100 Y0 Z800"
    pending_records = dummy._operator_pending_confirm_plan.atomic_records
    pending_record = next(iter(pending_records.values()))
    assert pending_record.func_num == 108
    assert pending_record.params["target_z"] == 800.0
    assert getattr(dummy, "prechecked_plan", None) is dummy._operator_pending_confirm_plan
    assert "已将当前待确认步骤替换为 Func108" in chats[-1][1]


def test_operator_compound_step_machine_text_shows_blocked_reason():
    text = DummyOperator._operator_compound_step_machine_text(
        {
            "step_machine": {
                "status": "blocked",
                "current_index": 0,
                "current_step_text": "走到X1000",
                "reason": "L1预检未通过。",
                "steps": ({"index": 0, "text": "走到X1000", "status": "blocked"},),
            }
        }
    )

    assert text == "L1预检未通过。"


def test_operator_execute_nlp_plan_handles_deepseek_chat_answer_without_popup():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "deepseek_chat", "你是谁", "我是机械手自然语言交互助手。"),),
        source="deepseek_chat",
        raw_text="你是谁",
        reason="我是机械手自然语言交互助手。",
        semantic_level=1,
        semantic_label="闲聊咨询层",
    )
    status_messages = []
    info_messages = []
    logs = []
    chats = []
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._show_info = lambda title, text: info_messages.append((title, text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    assert getattr(dummy, "execute_busy") is False
    assert status_messages[-1] == "我是机械手自然语言交互助手。"
    assert chats[-1] == ("assistant", "我是机械手自然语言交互助手。")
    assert info_messages == []
    assert log_args(logs[-1])[0:3] == ("自然语言", "闲聊咨询", "成功")


def test_operator_execute_nlp_plan_queues_deepseek_chat_answer_for_speech():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "deepseek_chat", "你是谁", "我是机械手自然语言交互助手。"),),
        source="deepseek_chat",
        raw_text="你是谁",
        reason="我是机械手自然语言交互助手。",
        semantic_level=1,
        semantic_label="闲聊咨询层",
    )
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._show_info = lambda *args, **kwargs: None
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_plan(plan)

    pending = dummy.operator_broadcast_queue.messages_since(0)
    assert pending[-1].text == "我是机械手自然语言交互助手。"
    assert pending[-1].context_id == "chat:ai_answer"


def test_operator_nonexecutable_plan_text_uses_chat_answer_directly():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "deepseek_chat", "你好", "你好，我可以帮你了解系统功能。"),),
        source="deepseek_chat",
        raw_text="你好",
        reason="你好，我可以帮你了解系统功能。",
        semantic_level=1,
        semantic_label="闲聊咨询层",
    )

    text = dummy._operator_nonexecutable_plan_chat_text(plan)

    assert text == "你好，我可以帮你了解系统功能。"


def test_operator_streaming_chat_response_updates_single_ai_bubble():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    dummy._operator_append_streaming_chat_response("我是")
    dummy._operator_append_streaming_chat_response("问答助手。")
    while dummy._operator_streaming_chat_pending_chars:
        dummy._operator_flush_streaming_chat_char()
    dummy._operator_finish_streaming_chat_response("我是问答助手。")

    assert dummy._operator_chat_messages == [("assistant", "我是问答助手。")]
    assert all(len(snapshot) == 1 for snapshot in rendered)


def test_operator_streaming_chat_begins_with_thinking_hint():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()

    assert dummy._operator_chat_messages == [("assistant", "")]
    assert dummy._operator_chat_thinking_steps[-1] == ["正在思考", "识别为普通问答", "检索本地资料"]
    assert dummy._operator_chat_thinking_meta[-1]["active"] is True


def test_operator_streaming_chat_completion_archives_non_execution_result(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy._operator_archive_text_input("你好")

    dummy._operator_begin_streaming_chat_response()
    dummy._operator_complete_streaming_chat_response("你好，我可以解释系统状态。")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "skipped"
    assert payload["execution"]["non_execution_result"] == "streaming_chat"
    assert payload["response"]["final"] == "你好，我可以解释系统状态。"


def test_operator_maybe_begin_streaming_chat_starts_immediately_for_deepseek_chat():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    started = dummy._operator_maybe_begin_streaming_chat_for_text("你好", use_deepseek=True)

    assert started is True
    assert dummy._operator_chat_messages == [("assistant", "")]
    assert dummy._operator_chat_thinking_meta[-1]["active"] is True


def test_operator_maybe_begin_streaming_chat_rejects_control_intent():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    started = dummy._operator_maybe_begin_streaming_chat_for_text("小正，走到 X100", use_deepseek=True)

    assert started is False
    assert getattr(dummy, "_operator_streaming_chat_active", False) is False
    assert dummy._operator_chat_messages == []


def test_operator_maybe_begin_streaming_chat_rejects_flow_and_confirm_intents():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    for text in ("创建流程", "添加步骤移动到位置A", "确认执行", "保存流程"):
        started = dummy._operator_maybe_begin_streaming_chat_for_text(text, use_deepseek=True)

        assert started is False
        assert getattr(dummy, "_operator_streaming_chat_active", False) is False
        assert dummy._operator_chat_messages == []


def test_operator_agent_orchestrator_is_reused_per_ui_session(monkeypatch):
    created = []

    class CountingOrchestrator:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr("robot_modbus_lite.agent.orchestrator.AgentOrchestrator", CountingOrchestrator)

    dummy = DummyOperator()
    dummy._append_log = lambda *args, **kwargs: None

    first = dummy._operator_agent_orchestrator()
    second = dummy._operator_agent_orchestrator()

    assert first is second
    assert len(created) == 1


def test_operator_tool_calling_runtime_has_memory_backed_registry(tmp_path):
    dummy = DummyOperator()
    dummy.runtime_root = tmp_path

    runtime = dummy._operator_tool_calling_agent_runtime()

    assert runtime.tool_registry.memory_store is dummy._operator_agent_memory_store()
    assert "lookup_active_memory" in runtime.tool_registry.tool_names


def test_operator_agent_runtime_bridge_uses_deepseek_tool_decider_when_enabled(tmp_path):
    class FakeDeepSeekClient:
        def parse_json(self, prompt, **_kwargs):
            assert "available_tools" in prompt
            return {"tool_name": "explain_text", "args": {"text": "你好"}}

    dummy = DummyOperator()
    dummy.runtime_root = tmp_path
    dummy._deepseek_client = FakeDeepSeekClient()
    dummy.nlp_use_deepseek_check = SimpleNamespace(isChecked=lambda: True)

    result = dummy._operator_agent_runtime_bridge().handle_text(
        "移动到 X100",
        thread_id="operator-ui",
        legacy_fallback=lambda _text: VoiceNlpPlan(),
    )

    assert result.kind == "chat_answer"
    assert result.payload["tool_name"] == "explain_text"


def test_operator_agent_runtime_bridge_cache_key_tracks_deepseek_client(tmp_path):
    dummy = DummyOperator()
    dummy.runtime_root = tmp_path
    dummy.nlp_use_deepseek_check = SimpleNamespace(isChecked=lambda: True)
    dummy._deepseek_client = object()

    first = dummy._operator_agent_runtime_bridge()
    dummy._deepseek_client = object()
    second = dummy._operator_agent_runtime_bridge()

    assert second is not first


def test_operator_try_agent_runtime_precedes_legacy_orchestrator(monkeypatch):
    from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult

    class FakeRuntime:
        def handle(self, text, *, session_state):
            assert text == "你好"
            assert session_state.thread_id == "operator-ui"
            return AgentOrchestratorResult(kind="chat_answer", message="runtime answer")

    def fail_legacy(*args, **kwargs):
        raise AssertionError("legacy orchestrator should not be constructed")

    monkeypatch.setattr("robot_modbus_lite.agent.orchestrator.AgentOrchestrator", fail_legacy)

    dummy = DummyOperator()
    dummy._operator_tool_calling_agent_runtime = lambda: FakeRuntime()

    plan = dummy._operator_try_agent_orchestrator_plan("你好")

    assert plan.source == "agent_orchestrator"
    assert plan.reason == "runtime answer"


def test_operator_try_agent_runtime_falls_back_when_unavailable(monkeypatch):
    from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult

    class FakeRuntime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="LangChain 不可用。",
                payload={"fallback_required": True},
            )

    class FakeLegacyOrchestrator:
        def __init__(self, **kwargs):
            pass

        def handle(self, text):
            return AgentOrchestratorResult(kind="chat_answer", message="legacy answer")

    monkeypatch.setattr("robot_modbus_lite.agent.orchestrator.AgentOrchestrator", FakeLegacyOrchestrator)

    dummy = DummyOperator()
    dummy._operator_tool_calling_agent_runtime = lambda: FakeRuntime()

    plan = dummy._operator_try_agent_orchestrator_plan("你好")

    assert plan.source == "agent_orchestrator"
    assert plan.reason == "legacy answer"


def test_operator_try_agent_applies_active_memory_before_legacy_orchestrator(tmp_path):
    from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="vote",
    )
    store.approve_memory(candidate["memory_id"], reviewer="engineer")
    seen = []

    class FakeRuntime:
        def handle(self, text, *, session_state):
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="LangChain 不可用。",
                payload={"fallback_required": True},
            )

    class FakeLegacy:
        def handle(self, text):
            seen.append(text)
            return AgentOrchestratorResult(kind="chat_answer", message="legacy answer")

    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._operator_agent_memory_store = lambda: store
    dummy._operator_tool_calling_agent_runtime = lambda: FakeRuntime()
    dummy._operator_agent_orchestrator = lambda: FakeLegacy()

    plan = dummy._operator_try_agent_orchestrator_plan("移动到位置诶")

    state = dummy._operator_session_state()
    assert plan.reason == "legacy answer"
    assert seen == ["移动到位置A"]
    assert state.last_user_text == "移动到位置诶"
    assert state.last_normalized_text == "移动到位置A"
    assert state.applied_memories[0]["memory_id"] == candidate["memory_id"]
    assert store.list_audit_events(memory_id=candidate["memory_id"])[-1]["event"] == "memory_applied"


def test_operator_record_feedback_vote_writes_sqlite_memory_store(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store
    dummy._operator_current_interaction_record_id = "record-1"

    result = dummy._operator_record_feedback_vote(vote="down", note="坐标没有按我说的")

    votes = store.list_feedback_votes(interaction_id="record-1")
    assert result.ok is True
    assert result.state == "feedback_vote_recorded"
    assert votes[0]["vote"] == "down"
    assert votes[0]["note"] == "坐标没有按我说的"


def test_operator_record_feedback_vote_uses_last_archived_interaction_id(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_agent_memory_store = lambda: store
    record = dummy._operator_archive_text_input("你好")

    result = dummy._operator_record_feedback_vote(vote="down", note="回答没用")

    votes = store.list_feedback_votes(interaction_id=record["msg_id"])
    assert result.ok is True
    assert result.state == "feedback_vote_recorded"
    assert votes[0]["target_id"] == record["msg_id"]
    assert votes[0]["vote"] == "down"


def test_operator_record_feedback_vote_without_interaction_id_fails(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store

    result = dummy._operator_record_feedback_vote(vote="up")

    assert result.ok is False
    assert result.state == "feedback_vote_missing_target"
    assert store.list_feedback_votes() == []


def test_operator_learn_memory_candidates_from_feedback_creates_candidates(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="把 位置诶 识别为 位置A",
    )
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store

    result = dummy._operator_learn_memory_candidates_from_feedback()

    assert result.ok is True
    assert result.state == "memory_candidates_learned"
    assert result.data["created_count"] == 1
    assert store.list_memories(status="candidate", kind="asr_alias")[0]["key"] == "位置诶"


def test_operator_learn_memory_candidates_from_feedback_skips_forbidden_candidates(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="跳过确认=低风险命令直接执行",
    )
    store.record_feedback_vote(
        interaction_id="record-2",
        target_type="interaction",
        target_id="record-2",
        vote="down",
        note="夫位=复位",
    )
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store

    result = dummy._operator_learn_memory_candidates_from_feedback()

    assert result.ok is True
    assert result.state == "memory_candidates_learned"
    assert result.data["created_count"] == 1
    assert result.data["skipped_count"] == 1
    candidates = store.list_memories(status="candidate", kind="asr_alias")
    assert len(candidates) == 1
    assert candidates[0]["key"] == "夫位"


def test_operator_memory_review_command_lists_pending_candidates(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="vote",
    )
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    chats = []
    logs = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("查看待审核经验")

    assert handled is True
    assert candidate["memory_id"] in dummy.last_status
    assert "位置诶" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "经验审核", "成功")


def test_operator_memory_review_command_approves_and_rolls_back_memory(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="vote",
    )
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    chats = []
    logs = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    approved = dummy._handle_operator_ui_command(f"批准经验 {candidate['memory_id']}")
    rolled_back = dummy._handle_operator_ui_command(f"回滚经验 {candidate['memory_id']}")

    assert approved is True
    assert rolled_back is True
    assert store.list_memories(status="active") == []
    assert store.list_memories(status="rolled_back")[0]["memory_id"] == candidate["memory_id"]
    assert "已回滚" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "经验回滚", "成功")


def test_operator_memory_review_command_lists_active_memories(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    active = store.create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="vote",
    )
    candidate = store.create_candidate(
        kind="asr_alias",
        key="位置比",
        value={"normalized": "位置B"},
        source="vote",
    )
    store.approve_memory(active["memory_id"], reviewer="engineer")
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    chats = []
    logs = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("查看生效经验")

    assert handled is True
    assert active["memory_id"] in dummy.last_status
    assert candidate["memory_id"] not in dummy.last_status
    assert "active" in chats[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "经验审核", "成功")


def test_operator_memory_review_command_batch_approves_candidates(tmp_path):
    from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore

    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    first = store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"}, source="vote")
    second = store.create_candidate(kind="asr_alias", key="位置比", value={"normalized": "位置B"}, source="vote")
    dummy = DummyOperator()
    dummy._operator_agent_memory_store = lambda: store
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    chats = []
    logs = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    handled = dummy._handle_operator_ui_command("批准全部待审核经验")

    assert handled is True
    active_ids = {item["memory_id"] for item in store.list_memories(status="active")}
    assert active_ids == {first["memory_id"], second["memory_id"]}
    assert "已批准 2 条候选经验" in dummy.last_status
    assert log_args(logs[-1])[0:3] == ("用户页面", "经验批量审核", "成功")


def test_operator_memory_review_presenter_updates_optional_table_detail_and_filters():
    class FakeTable:
        def __init__(self):
            self.row_count = 0
            self.column_count = 0
            self.headers = []
            self.items = {}

        def setRowCount(self, count):
            self.row_count = count

        def setColumnCount(self, count):
            self.column_count = count

        def setHorizontalHeaderLabels(self, labels):
            self.headers = list(labels)

        def setItem(self, row, column, item):
            self.items[(row, column)] = item.text() if hasattr(item, "text") else str(item)

    class FakeText:
        def __init__(self):
            self.text = ""

        def setPlainText(self, text):
            self.text = text

    class FakeCombo:
        def __init__(self):
            self.items = []

        def clear(self):
            self.items.clear()

        def addItems(self, items):
            self.items.extend(list(items))

    dummy = DummyOperator()
    dummy.operator_memory_review_table = FakeTable()
    dummy.operator_memory_review_detail = FakeText()
    dummy.operator_memory_status_filter = FakeCombo()
    dummy.operator_memory_kind_filter = FakeCombo()
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda *args, **kwargs: None
    result = ToolResult.success(
        state="memory_review_listed",
        message="找到 1 条经验记录。",
        data={
            "memories": [
                {
                    "memory_id": "mem_1",
                    "kind": "asr_alias",
                    "key": "位置诶",
                    "value": {"normalized": "位置A"},
                    "status": "active",
                    "audit_events": [{"event": "memory_approved", "created_at": "2026-06-01"}],
                }
            ]
        },
    )

    dummy._operator_present_memory_tool_result(result, category="经验审核")

    assert dummy.operator_memory_review_table.row_count == 1
    assert dummy.operator_memory_review_table.headers == ["ID", "类型", "键", "值", "状态", "来源", "审计"]
    assert dummy.operator_memory_review_table.items[(0, 0)] == "mem_1"
    assert dummy.operator_memory_review_table.items[(0, 1)] == "asr_alias"
    assert "memory_approved" in dummy.operator_memory_review_detail.text
    assert "active" in dummy.operator_memory_status_filter.items
    assert "asr_alias" in dummy.operator_memory_kind_filter.items


def test_operator_memory_review_filters_trigger_query_and_present_result():
    class FakeCombo:
        def __init__(self, text):
            self._text = text

        def currentText(self):
            return self._text

    calls = []
    presented = []
    dummy = DummyOperator()
    dummy.operator_memory_status_filter = FakeCombo("active")
    dummy.operator_memory_kind_filter = FakeCombo("asr_alias")
    dummy._operator_call_memory_tool = lambda tool_name, **kwargs: calls.append((tool_name, kwargs)) or ToolResult.success(
        state="memory_review_listed",
        message="找到 0 条经验记录。",
        data={"memories": []},
    )
    dummy._operator_present_memory_tool_result = lambda result, *, category: presented.append((result.state, category))

    dummy._operator_refresh_memory_review_from_filters()

    assert calls == [("query_memory_review", {"status": "active", "kind": "asr_alias", "include_audit": True})]
    assert presented == [("memory_review_listed", "经验审核")]


def test_operator_memory_review_row_selection_updates_detail_text():
    dummy = DummyOperator()
    result = ToolResult.success(
        state="memory_review_listed",
        message="找到 2 条经验记录。",
        data={
            "memories": [
                {"memory_id": "mem_1", "kind": "asr_alias", "key": "位置诶", "value": {"normalized": "位置A"}, "status": "active"},
                {
                    "memory_id": "mem_2",
                    "kind": "flow_preference",
                    "key": "推荐流程",
                    "value": {"prefer": "先移动"},
                    "status": "candidate",
                    "audit_events": [{"event": "candidate_created", "created_at": "2026-06-02"}],
                },
            ]
        },
    )
    dummy._operator_update_memory_review_view(result)
    captured = []
    dummy.operator_memory_review_detail = SimpleNamespace(setPlainText=lambda text: captured.append(text))

    dummy._operator_on_memory_review_row_selected(1, 0, 0, 0)

    assert "mem_2" in captured[-1]
    assert "推荐流程" in captured[-1]
    assert "candidate_created" in captured[-1]


def test_operator_memory_review_selected_row_actions_call_tools_and_refresh():
    dummy = DummyOperator()
    result = ToolResult.success(
        state="memory_review_listed",
        message="找到 2 条经验记录。",
        data={
            "memories": [
                {"memory_id": "mem_1", "kind": "asr_alias", "key": "位置诶", "value": {"normalized": "位置A"}, "status": "active"},
                {"memory_id": "mem_2", "kind": "asr_alias", "key": "位置比", "value": {"normalized": "位置B"}, "status": "candidate"},
            ]
        },
    )
    dummy._operator_update_memory_review_view(result)
    calls = []
    presented = []
    dummy._operator_selected_memory_review_row = 1
    dummy._operator_call_memory_tool = lambda tool_name, **kwargs: calls.append((tool_name, kwargs)) or ToolResult.success(
        state=f"{tool_name}_ok",
        message="ok",
        data={"memory": {"memory_id": kwargs["memory_id"], "status": "changed"}},
    )
    dummy._operator_present_memory_tool_result = lambda result, *, category: presented.append((result.state, category))

    dummy._operator_approve_selected_memory()
    dummy._operator_disable_selected_memory()
    dummy._operator_rollback_selected_memory()

    assert calls == [
        ("approve_memory_candidate", {"memory_id": "mem_2", "reviewer": "operator-ui"}),
        ("disable_memory", {"memory_id": "mem_2", "reviewer": "operator-ui", "reason": "operator panel"}),
        ("rollback_memory", {"memory_id": "mem_2", "reviewer": "operator-ui", "reason": "operator panel"}),
    ]
    assert presented == [
        ("approve_memory_candidate_ok", "经验审核"),
        ("disable_memory_ok", "经验停用"),
        ("rollback_memory_ok", "经验回滚"),
    ]


def test_operator_execute_text_shows_agent_processing_hint_before_sync_parse():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy._operator_process_pending_ui_events = lambda: setattr(dummy, "processed_ui_events", True)
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "我想在流程后面加一步", setPlainText=lambda _text: None)
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_set_pending_confirm_plan = lambda plan: None
    dummy._operator_agent_llm_fallback_enabled = lambda: True
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._set_nlp_result_plan = lambda plan: setattr(dummy, "nlp_result_plan", plan)
    dummy._execute_nlp_plan = lambda plan: setattr(dummy, "executed_plan", plan)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("clarification", None, "agent_orchestrator", "我想在流程后面加一步", "请补充步骤。"),),
        source="agent_orchestrator",
        raw_text="我想在流程后面加一步",
        reason="请补充步骤。",
    )

    def try_plan(text):
        assert dummy._operator_streaming_chat_active is True
        assert dummy._operator_chat_messages == [("assistant", "")]
        assert dummy._operator_chat_thinking_steps[-1] == ["正在理解上下文", "读取当前对话和流程状态", "等待 AI 上下文解释"]
        assert getattr(dummy, "processed_ui_events", False) is True
        return plan

    dummy._operator_try_agent_orchestrator_plan = try_plan

    dummy._execute_nlp_text()

    assert dummy.executed_plan is plan


def test_operator_execute_text_schedules_agent_parse_in_background_when_deepseek_enabled():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy._operator_process_pending_ui_events = lambda: setattr(dummy, "processed_ui_events", True)
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "小正，帮我理解这个流程", setPlainText=lambda _text: None)
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_set_pending_confirm_plan = lambda plan: None
    dummy._operator_agent_llm_fallback_enabled = lambda: True
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._set_nlp_result_plan = lambda plan: setattr(dummy, "nlp_result_plan", plan)
    dummy._execute_nlp_plan = lambda plan: setattr(dummy, "executed_plan", plan)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "agent_orchestrator", "小正，帮我理解这个流程", "这是流程说明。"),),
        source="agent_orchestrator",
        raw_text="小正，帮我理解这个流程",
        reason="这是流程说明。",
    )
    background = {}

    def run_in_background(work_fn, done_fn):
        background["work_fn"] = work_fn
        background["done_fn"] = done_fn

    dummy._run_in_background = run_in_background
    dummy._operator_try_agent_orchestrator_plan = lambda text: plan

    dummy._execute_nlp_text()

    assert "work_fn" in background
    assert getattr(dummy, "executed_plan", None) is None
    assert dummy._operator_streaming_chat_active is True
    assert dummy._operator_chat_messages == [("assistant", "")]

    result = background["work_fn"]()
    background["done_fn"](result)

    assert dummy.executed_plan is plan
    assert dummy.nlp_result_plan is plan


def test_operator_agent_background_fallback_restores_text_before_legacy_execute():
    class DummyOperatorWithNlp(OperatorUiMixin, NlpMixin):
        pass

    dummy = DummyOperatorWithNlp()
    state = {"text": ""}
    dummy.nlp_input_edit = SimpleNamespace(
        toPlainText=lambda: state["text"],
        setPlainText=lambda text: state.update(text=text),
    )
    dummy.nlp_parse_running = False
    dummy.nlp_sequence_running = False
    dummy.flow_running = False
    dummy.nlp_use_deepseek_check = SimpleNamespace(isChecked=lambda: False)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._set_nlp_result_plan = lambda plan: setattr(dummy, "nlp_result_plan", plan)
    dummy._execute_nlp_plan = lambda plan: setattr(dummy, "executed_plan", plan)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._append_log = lambda *args, **kwargs: None
    dummy._show_warning = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应弹输入为空"))
    dummy._show_info = lambda *args, **kwargs: None
    dummy._show_critical = lambda *args, **kwargs: None
    dummy._operator_maybe_begin_streaming_chat_for_text = lambda text, use_deepseek: False
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "legacy", "测试", "测试回答"),),
        source="legacy",
        raw_text="测试",
        reason="测试回答",
        semantic_level=1,
    )

    class Adapter:
        def parse(self, text, **kwargs):
            assert text == "测试"
            return plan

    dummy._build_voice_nlp_adapter = lambda: Adapter()
    dummy._run_in_background = lambda work_fn, done_fn: done_fn(work_fn())

    dummy._operator_apply_agent_plan_background_result(None, mode="execute", fallback_text="测试")

    assert dummy.executed_plan is plan


def test_operator_parse_text_schedules_agent_parse_in_background_when_deepseek_enabled():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy._operator_process_pending_ui_events = lambda: None
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "这个流程是什么", setPlainText=lambda _text: None)
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False
    dummy._operator_agent_llm_fallback_enabled = lambda: True
    dummy._set_nlp_parse_busy = lambda busy: setattr(dummy, "parse_busy", busy)
    dummy._set_nlp_result_plan = lambda plan: setattr(dummy, "nlp_result_plan", plan)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._append_log = lambda *args, **kwargs: None
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "agent_orchestrator", "这个流程是什么", "流程说明"),),
        source="agent_orchestrator",
        raw_text="这个流程是什么",
        reason="流程说明",
    )
    background = {}
    dummy._run_in_background = lambda work_fn, done_fn: background.update(work_fn=work_fn, done_fn=done_fn)
    dummy._operator_try_agent_orchestrator_plan = lambda text: plan

    dummy._parse_nlp_text()

    assert "work_fn" in background
    assert getattr(dummy, "nlp_result_plan", None) is None
    background["done_fn"](background["work_fn"]())

    assert dummy.nlp_result_plan is plan
    assert dummy.parse_busy is False
    assert "解析完成" in dummy.status_text


def test_operator_agent_processing_hint_is_replaced_by_final_answer():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_agent_processing_response("测试输入")
    dummy._operator_finish_streaming_chat_response("已进入流程编辑，请补充坐标。")

    assert dummy._operator_chat_messages == [("assistant", "已进入流程编辑，请补充坐标。")]
    assert dummy._operator_chat_thinking_meta[-1]["active"] is False
    assert dummy._operator_chat_thinking_steps[-1] == ["识别上下文意图", "本地安全策略复核", "生成可执行前提示，未直接控制机械手"]


def test_operator_streaming_chat_callback_reuses_existing_thinking_bubble():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy._operator_begin_streaming_chat_response()

    callback = dummy._operator_streaming_chat_delta_callback()
    callback("你好")

    assert len(dummy._operator_chat_messages) == 1
    assert dummy._operator_streaming_chat_pending_chars == ["你", "好"]


def test_operator_streaming_chat_callback_reuses_bubble_when_callback_created_before_begin():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    callback = dummy._operator_streaming_chat_delta_callback()
    dummy._operator_begin_streaming_chat_response()
    callback("你好")

    assert len(dummy._operator_chat_messages) == 1
    assert dummy._operator_streaming_chat_pending_chars == ["你", "好"]


def test_operator_cancel_streaming_chat_response_removes_pending_thinking_bubble():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy._operator_begin_streaming_chat_response()

    dummy._operator_cancel_streaming_chat_response()

    assert dummy._operator_chat_messages == []
    assert dummy._operator_chat_thinking_steps == []
    assert dummy._operator_chat_thinking_meta == []
    assert dummy._operator_streaming_chat_active is False


def test_operator_streaming_chat_finish_keeps_collapsed_process_summary():
    dummy = DummyOperator()
    now = [100.0]
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    now[0] = 104.2
    dummy._operator_finish_streaming_chat_response("我是问答助手。")

    assert dummy._operator_chat_messages == [("assistant", "我是问答助手。")]
    assert dummy._operator_chat_thinking_steps == [
        ["识别为普通问答", "基于本地资料整理回答", "AI 生成回答，未触发机械手动作"]
    ]
    assert dummy._operator_chat_thinking_meta[-1] == {"active": False, "elapsed_sec": 4}


def test_operator_ai_chat_row_contains_collapsible_process_summary():
    app = QApplication.instance() or QApplication([])
    dummy = DummyOperator()

    row = dummy._build_operator_chat_row(
        "assistant",
        "我是问答助手。",
        thinking_steps=["识别为普通问答", "基于本地资料整理回答", "AI 生成回答，未触发机械手动作"],
        thinking_meta={"active": False, "elapsed_sec": 4},
    )

    toggle = row.findChild(QPushButton, "operatorThinkingToggle")
    detail = row.findChild(QLabel, "operatorThinkingDetail")

    assert toggle is not None
    assert toggle.text() == "已思考（用时 4 秒）"
    assert detail is not None
    assert detail.isVisible() is False
    row.close()
    app.processEvents()


def test_operator_ai_chat_row_shows_active_thinking_expanded():
    app = QApplication.instance() or QApplication([])
    dummy = DummyOperator()

    row = dummy._build_operator_chat_row(
        "assistant",
        "",
        thinking_steps=["正在思考", "识别为普通问答", "检索本地资料"],
        thinking_meta={"active": True},
    )

    toggle = row.findChild(QPushButton, "operatorThinkingToggle")
    detail = row.findChild(QLabel, "operatorThinkingDetail")
    answer = row.findChild(QLabel, "operatorChatText")

    assert toggle is not None
    assert toggle.text() == "正在思考..."
    assert toggle.isChecked() is True
    assert detail is not None
    assert detail.isHidden() is False
    assert answer is not None
    assert answer.isHidden() is True
    row.close()
    app.processEvents()


def test_operator_formats_flow_steps_for_chat_display_only():
    raw = (
        "好的，已根据您的描述，添加点头动作。新的流程草案共 **4 步**： "
        "1. 移动到home（Func108） "
        "2. 小臂上下点头:Ry正转（Func107） "
        "3. 小臂上下点头:Ry反转（Func107） "
        "4. 小臂上下点头:Ry正转（Func107） "
        "请问是否需要确认执行或继续调整？"
    )

    formatted = DummyOperator._operator_chat_display_text("assistant", raw)

    assert "新的流程草案共 4 步" in formatted
    assert "步骤 1\n移动到home（Func108）" in formatted
    assert "步骤 4\n小臂上下点头:Ry正转（Func107）" in formatted
    assert "请问是否需要确认执行或继续调整？" in formatted
    assert "**" not in formatted


def test_operator_chat_row_uses_formatted_display_without_mutating_message():
    app = QApplication.instance() or QApplication([])
    dummy = DummyOperator()
    raw = "新的流程草案共 **2 步**： 1. 移动到home（Func108） 2. 点头（Func107） 请确认。"

    row = dummy._build_operator_chat_row("assistant", raw)

    answer = row.findChild(QLabel, "operatorChatText")
    assert answer is not None
    assert answer.text() != raw
    assert "步骤 1\n移动到home（Func108）" in answer.text()
    row.close()
    app.processEvents()


def test_operator_streaming_chat_delta_render_is_throttled():
    dummy = DummyOperator()
    now = [10.0]
    rendered = []
    dummy._operator_chat_messages = []
    dummy._operator_now_seconds = lambda: now[0]
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    rendered.clear()
    dummy._operator_append_streaming_chat_response("你")
    dummy._operator_append_streaming_chat_response("好")
    dummy._operator_append_streaming_chat_response("。")

    assert dummy._operator_chat_messages == [("assistant", "")]
    assert len(rendered) <= 1


def test_operator_streaming_chat_typewriter_interval_is_batched_for_smooth_typing():
    interval = DummyOperator._operator_streaming_chat_typewriter_interval_seconds()

    assert 0.018 <= interval <= 0.035


def test_operator_streaming_chat_flushes_batched_characters():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    dummy._operator_append_streaming_chat_response("你好世界")
    dummy._operator_flush_streaming_chat_char()

    assert dummy._operator_chat_messages == [("assistant", "你好世界")]
    assert dummy._operator_streaming_chat_pending_chars == []


def test_operator_streaming_chat_render_interval_is_responsive():
    assert DummyOperator._operator_streaming_chat_render_interval_seconds() <= 0.08


def test_operator_streaming_chat_flushes_one_character_at_a_time():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    dummy._operator_append_streaming_chat_response("你好")
    dummy._operator_flush_streaming_chat_char()

    assert dummy._operator_chat_messages == [("assistant", "你好")]
    assert dummy._operator_streaming_chat_pending_chars == []


def test_operator_streaming_chat_flush_updates_existing_label_without_full_render():
    dummy = DummyOperator()
    rendered = []
    label = SimpleNamespace(text="", setText=lambda text: setattr(label, "text", text), setVisible=lambda _visible: None)
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    rendered.clear()
    dummy._operator_streaming_chat_content_label = label
    dummy._operator_append_streaming_chat_response("ab")
    dummy._operator_flush_streaming_chat_char()

    assert label.text == "ab"
    assert rendered == []


def test_operator_streaming_chat_flush_keeps_existing_message_when_label_missing():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    rendered.clear()
    dummy._operator_streaming_chat_content_label = None
    dummy._operator_append_streaming_chat_response("ab")
    dummy._operator_flush_streaming_chat_char()

    assert dummy._operator_chat_messages == [("assistant", "ab")]
    assert rendered == []


def test_operator_streaming_chat_finish_waits_for_pending_typewriter_chars():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    dummy._operator_append_streaming_chat_response("你好")
    dummy._operator_finish_streaming_chat_response("你好")

    assert dummy._operator_streaming_chat_active is True
    assert dummy._operator_chat_messages == [("assistant", "")]
    dummy._operator_flush_streaming_chat_char()
    assert dummy._operator_chat_messages == [("assistant", "你好")]
    assert dummy._operator_streaming_chat_active is False
    assert dummy._operator_chat_thinking_meta[-1]["active"] is False


def test_operator_late_streaming_delta_reuses_final_answer_bubble():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_finish_streaming_chat_response("我是问答助手。")
    dummy._operator_begin_streaming_chat_response()
    dummy._operator_append_streaming_chat_response("我是")
    dummy._operator_append_streaming_chat_response("问答助手。")
    while dummy._operator_streaming_chat_pending_chars:
        dummy._operator_flush_streaming_chat_char()
    dummy._operator_finish_streaming_chat_response("我是问答助手。")

    assert dummy._operator_chat_messages == [("assistant", "我是问答助手。")]


def test_operator_busy_chat_final_result_replaces_completed_streaming_bubble():
    dummy = DummyOperator()
    rendered = []
    spoken = []
    archived = []
    logs = []
    status = {"text": ""}
    now = [100.0]
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_ai_answer_for_speech = lambda text: spoken.append(text)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: archived.append((args, kwargs))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_schedule_refresh = lambda: None

    dummy._operator_maybe_begin_streaming_chat_for_text("你好", use_deepseek=True)
    dummy._operator_busy_chat_stream_index = 0
    dummy._operator_append_streaming_chat_response("你好！我是小助手。")
    while dummy._operator_streaming_chat_pending_chars:
        dummy._operator_flush_streaming_chat_char()
    assert dummy._operator_streaming_chat_active is True

    now[0] = 106.0
    dummy._operator_complete_streaming_chat_response("你好！我是小助手。")
    assert dummy._operator_streaming_chat_active is False

    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "deepseek_chat", "你好", "闲聊"),),
        source="deepseek_chat",
        raw_text="你好",
        reason="你好！我是机械手自然语言交互系统的小助手。",
        semantic_level=1,
        semantic_label="闲聊咨询层",
    )

    dummy._operator_reply_busy_chat_plan(plan)

    assert dummy._operator_chat_messages == [
        ("assistant", "你好！我是机械手自然语言交互系统的小助手。")
    ]
    assert len(dummy._operator_chat_messages) == 1
    assert dummy._operator_chat_thinking_meta[-1]["active"] is False
    assert spoken == ["你好！我是机械手自然语言交互系统的小助手。"]
    assert archived[-1][1]["result"] == "chat"
    assert logs[-1][1] == "忙碌闲聊"


def test_operator_streaming_chat_updates_original_bubble_when_later_message_exists():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_streaming_chat_response()
    dummy._operator_chat_messages.append(("assistant", "另一条系统消息"))
    dummy._operator_chat_thinking_steps.append([])
    dummy._operator_chat_thinking_meta.append({})
    dummy._operator_append_streaming_chat_response("你好")
    while dummy._operator_streaming_chat_pending_chars:
        dummy._operator_flush_streaming_chat_char()
    dummy._operator_finish_streaming_chat_response("你好，流程继续执行。")
    while dummy._operator_streaming_chat_pending_chars:
        dummy._operator_flush_streaming_chat_char()

    assert dummy._operator_chat_messages == [
        ("assistant", "你好，流程继续执行。"),
        ("assistant", "另一条系统消息"),
    ]
    assert dummy._operator_chat_thinking_meta[0]["active"] is False
    assert dummy._operator_chat_thinking_meta[1] == {}


def test_operator_add_chat_message_replaces_stale_active_streaming_bubble():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = [("assistant", "你好，我是机械手自然")]
    dummy._operator_chat_thinking_steps = [["正在思考", "识别为普通问答", "检索本地资料"]]
    dummy._operator_chat_thinking_meta = [{"active": True, "started_sec": 10.0}]
    dummy._operator_streaming_chat_active = False
    dummy._operator_streaming_chat_message_index = 0
    dummy._operator_now_seconds = lambda: 13.0
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_add_chat_message("assistant", "你好，我是机械手自然语言交互系统的小助手。")

    assert dummy._operator_chat_messages == [
        ("assistant", "你好，我是机械手自然语言交互系统的小助手。")
    ]
    assert dummy._operator_chat_thinking_meta == [{"active": False, "elapsed_sec": 3}]


def test_operator_add_chat_message_appends_row_without_full_rerender_when_chat_is_rendered():
    dummy = DummyOperator()
    inserted = []
    rendered = []

    class Item:
        def __init__(self, widget):
            self._widget = widget

        def widget(self):
            return self._widget

    class Layout:
        def count(self):
            return 1

        def itemAt(self, index):
            return Item(None)

        def insertWidget(self, index, widget):
            inserted.append((index, widget))

    dummy._operator_chat_messages = [("assistant", "已有消息")]
    dummy._operator_chat_thinking_steps = [[]]
    dummy._operator_chat_thinking_meta = [{}]
    dummy._operator_chat_rendered = True
    dummy.operator_chat_layout = Layout()
    dummy._build_operator_chat_row = lambda *args, **kwargs: ("row", args, kwargs)
    dummy._render_operator_chat = lambda: rendered.append(True)
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_add_chat_message("user", "你好")

    assert dummy._operator_chat_messages[-1] == ("user", "你好")
    assert inserted
    assert inserted[-1][0] == 0
    assert rendered == []


def test_operator_add_chat_message_accepts_optional_kind_keyword():
    dummy = DummyOperator()
    dummy._operator_chat_rendered = False
    dummy._operator_replace_current_streaming_chat_message = lambda _text: False
    dummy._render_operator_chat = lambda: None

    dummy._operator_add_chat_message("assistant", "等待安全确认。", kind="warn")

    assert dummy._operator_chat_messages[-1] == ("assistant", "等待安全确认。")


def test_operator_unknown_nlp_plan_is_shown_in_chat_without_modal_warning():
    dummy = DummyOperator()
    chats = []
    spoken = []
    warnings = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_publish_ai_answer_for_speech = lambda text: spoken.append(text)
    dummy._show_warning = lambda *args, **kwargs: warnings.append(args)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "busy", busy)
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("unknown", None, "rule", "位置A的参数是什么样的", "生产指令缺少“小正或小兵”唤醒词，未执行"),),
        source="rule",
        raw_text="位置A的参数是什么样的",
        reason="生产指令缺少“小正或小兵”唤醒词，未执行",
        semantic_level=3,
        semantic_label="生产指令",
    )

    dummy._execute_nlp_plan(plan)

    assert warnings == []
    assert chats == [("assistant", "生产指令缺少“小正或小兵”唤醒词，未执行。没有触发机械手动作。")]
    assert spoken == ["生产指令缺少“小正或小兵”唤醒词，未执行。没有触发机械手动作。"]
    assert dummy.busy is False


def test_operator_ai_chat_bubble_has_readable_minimum_width():
    dummy = DummyOperator()

    ai_min, ai_max = dummy._operator_chat_bubble_width_bounds(is_user=False)
    user_min, user_max = dummy._operator_chat_bubble_width_bounds(is_user=True)

    assert ai_min >= 260
    assert ai_max >= 700
    assert user_min == 0
    assert user_max == ai_max


def test_voice_nlp_adapter_requires_confirmation_for_alarm_reset():
    from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter

    plan = VoiceNlpAdapter(table={}, flow_names=()).parse("小正，报警复位")

    assert plan.semantic_level == 4
    assert plan.actions[0].target == "alarm_reset"
    assert plan.requires_confirmation is True


def test_operator_ui_command_answers_execution_progress_query_without_nlp():
    dummy = DummyOperator()
    published = []
    statuses = []
    logs = []
    dummy._operator_desired_scene = lambda: "execute"
    dummy._operator_execution_progress = lambda: 50
    dummy._operator_flow_progress_text = lambda: "2 / 4"
    dummy._operator_current_task_text = lambda: "流程 取放流程 / 第2步"
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))

    handled = dummy._handle_operator_ui_command("现在进度多少")

    assert handled is True
    assert published[-1].kind == "progress"
    assert "当前执行进度约50%" in published[-1].text
    assert "2 / 4" in published[-1].text
    assert statuses[-1] == published[-1].text
    assert log_args(logs[-1])[0:3] == ("用户页面", "进度查询", "成功")


def test_operator_progress_query_answers_precheck_result():
    dummy = DummyOperator()
    dummy._operator_desired_scene = lambda: "precheck"
    dummy._operator_last_precheck_result = {"status": "fail", "items": []}

    text = dummy._operator_progress_query_text()

    assert "预检已完成" in text
    assert "fail" in text


def test_operator_progress_query_answers_confirm_summary():
    dummy = DummyOperator()
    dummy._operator_desired_scene = lambda: "confirm"
    dummy._operator_last_precheck_result = {"status": "pass"}
    dummy._operator_last_motion_plan_result = {"status": "unavailable"}
    dummy._operator_last_process_precheck_result = {"status": "fail"}

    text = dummy._operator_progress_query_text()

    assert "等待安全确认" in text
    assert "L1=pass" in text
    assert "L2=unavailable" in text
    assert "L3=fail" in text


def test_operator_toggle_microphone_starts_voice_session():
    dummy = DummyOperator()
    calls = []
    dummy._voice_session_active = False
    dummy._start_voice_session = lambda: calls.append("start")
    dummy._sync_operator_mic_button = lambda: None

    dummy._operator_toggle_microphone_recording()

    assert calls == ["start"]


def test_operator_toggle_microphone_stops_voice_session():
    dummy = DummyOperator()
    calls = []
    dummy._voice_session_active = True
    dummy._stop_voice_session = lambda: calls.append("stop")
    dummy._sync_operator_mic_button = lambda: None

    dummy._operator_toggle_microphone_recording()

    assert calls == ["stop"]


def test_operator_voice_session_text_routes_without_writing_manual_input():
    dummy = DummyOperator()
    calls = []
    dummy.operator_command_edit = SimpleNamespace(
        setText=lambda text: calls.append(("manual", text)),
        clear=lambda: calls.append(("manual_clear", "")),
    )
    dummy.nlp_input_edit = SimpleNamespace(
        value="",
        setPlainText=lambda text: setattr(dummy.nlp_input_edit, "value", text),
        clear=lambda: setattr(dummy.nlp_input_edit, "value", ""),
        toPlainText=lambda: dummy.nlp_input_edit.value,
    )
    dummy._operator_interrupt_current_speech_for_user_input = lambda: calls.append(("interrupt", ""))
    dummy._execute_nlp_text = lambda: calls.append(("execute", dummy.nlp_input_edit.toPlainText()))
    dummy._operator_archive_text_input = lambda text: calls.append(("archive", text))

    dummy._operator_handle_voice_session_text("你好")

    assert ("manual", "你好") not in calls
    assert ("execute", "你好") in calls
    assert ("archive", "你好") in calls
    assert dummy.nlp_input_edit.toPlainText() == ""


def test_operator_dialog_refresh_does_not_copy_voice_transfer_text_to_manual_input():
    dummy = DummyOperator()
    manual = {"text": ""}
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "语音识别文本")
    dummy.operator_command_edit = SimpleNamespace(
        hasFocus=lambda: False,
        text=lambda: manual["text"],
        setText=lambda text: manual.update(text=text),
    )
    dummy.operator_voice_label = SimpleNamespace(setText=lambda _text: None)
    dummy.operator_response_label = SimpleNamespace(setText=lambda _text: None)
    dummy.status_label = SimpleNamespace(text=lambda: "系统在线")
    dummy.operator_chat_scroll = object()
    dummy._operator_chat_rendered = True
    dummy._operator_last_user_text = "语音识别文本"

    dummy._refresh_operator_dialog_labels()

    assert manual["text"] == ""


def test_operator_voice_session_text_uses_same_submit_path_as_text_input():
    dummy = DummyOperator()
    calls = []
    dummy._operator_submit_nlp_text = lambda text, **kwargs: calls.append((text, kwargs)) or True

    dummy._operator_handle_voice_session_text("你好")

    assert calls == [("你好", {"input_mode": "voice", "add_user_message": False})]


def test_operator_voice_recognition_status_only_adds_final_user_text():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = []
    dummy._operator_chat_thinking_steps = []
    dummy._operator_chat_thinking_meta = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_voice_recognition_status()
    dummy._operator_update_voice_recognition_status("小镇移动")
    dummy._operator_finish_voice_recognition_status("小镇移动到位置A")

    assert dummy._operator_chat_messages == [("user", "小镇移动到位置A")]
    assert getattr(dummy, "_operator_voice_recognition_status_index", None) is None


def test_operator_voice_recognition_status_creates_interim_user_bubble():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._operator_chat_thinking_steps = []
    dummy._operator_chat_thinking_meta = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_voice_recognition_status()
    dummy._operator_update_voice_recognition_status("小镇移动")

    assert dummy._operator_chat_messages == [("user", "小镇移动")]
    assert getattr(dummy, "_operator_voice_recognition_status_index", None) == 0


def test_operator_voice_recognition_partial_update_reuses_same_bubble():
    dummy = DummyOperator()
    rendered = []
    dummy._operator_chat_messages = []
    dummy._operator_chat_thinking_steps = []
    dummy._operator_chat_thinking_meta = []
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_voice_recognition_status()
    rendered.clear()
    dummy._operator_update_voice_recognition_status("小镇移动")
    dummy._operator_update_voice_recognition_status("小镇移动到位置A")

    assert dummy._operator_chat_messages == [("user", "小镇移动到位置A")]
    assert len(dummy._operator_chat_messages) == 1
    assert rendered[-1] == (("user", "小镇移动到位置A"),)


def test_operator_voice_recognition_partial_update_uses_existing_label_without_rerender():
    dummy = DummyOperator()
    rendered = []
    label = SimpleNamespace(text="", setText=lambda text: setattr(label, "text", text))
    dummy._operator_chat_messages = []
    dummy._operator_chat_thinking_steps = []
    dummy._operator_chat_thinking_meta = []
    dummy._operator_voice_recognition_status_label = label
    dummy._render_operator_chat = lambda: rendered.append(tuple(dummy._operator_chat_messages))
    dummy._operator_scroll_chat_to_bottom = lambda: rendered.append("scroll")

    dummy._operator_begin_voice_recognition_status()
    dummy._operator_update_voice_recognition_status("小镇移动")
    rendered.clear()
    dummy._operator_voice_recognition_status_label = label
    dummy._operator_update_voice_recognition_status("小镇移动到位置A")

    assert label.text == "小镇移动到位置A"
    assert dummy._operator_chat_messages == [("user", "小镇移动到位置A")]
    assert rendered == ["scroll"]


def test_operator_voice_recognition_status_can_be_cleared():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._operator_chat_thinking_steps = []
    dummy._operator_chat_thinking_meta = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    dummy._operator_begin_voice_recognition_status()
    dummy._operator_update_voice_recognition_status("小镇")
    dummy._operator_clear_voice_recognition_status()

    assert dummy._operator_chat_messages == []
    assert getattr(dummy, "_operator_voice_recognition_status_index", None) is None


def test_operator_add_chat_from_log_queues_voice_recognition_result():
    dummy = DummyOperator()
    chat_messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    dummy._operator_add_chat_from_log(
        {
            "category": "语音",
            "action": "麦克风识别",
            "result": "成功",
            "detail": "现在进度多少",
        }
    )

    pending = dummy._operator_pending_broadcasts_for_delivery(0)
    assert [message.text for message in pending] == ["语音识别完成：现在进度多少"]
    assert chat_messages == [("assistant", "语音识别完成：现在进度多少")]


def test_operator_add_chat_from_log_routes_voice_text_to_local_ui_command():
    dummy = DummyOperator()
    handled = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._handle_operator_ui_command = lambda text: handled.append(text) or True

    dummy._operator_add_chat_from_log(
        {
            "category": "语音",
            "action": "麦克风识别",
            "result": "成功",
            "detail": "通讯正常吗",
        }
    )

    assert handled == ["通讯正常吗"]


def test_operator_voice_recognition_can_answer_dashboard_query():
    dummy = DummyOperator()
    published = []
    statuses = []
    logs = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "communication_faults": {
                "ecat_ok": True,
                "controller": "online",
                "realtime_feedback": "online",
                "io_status": "0",
            }
        }
    }
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._set_workspace_mode = lambda _mode: None

    dummy._operator_add_chat_from_log(
        {
            "category": "语音",
            "action": "麦克风识别",
            "result": "成功",
            "detail": "通讯正常吗",
        }
    )

    assert [message.text for message in published] == [
        "语音识别完成：通讯正常吗",
        "通讯正常，实时反馈在线，控制器状态 online，IO状态 0。",
    ]
    assert statuses[-1] == "通讯正常，实时反馈在线，控制器状态 online，IO状态 0。"
    assert log_args(logs[-1])[0:3] == ("用户页面", "看板查询", "成功")


def test_operator_voice_recognition_auto_sends_unhandled_text():
    dummy = DummyOperator()
    sent = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._handle_operator_ui_command = lambda _text: False
    dummy._operator_execute_text = lambda: sent.append(getattr(dummy.operator_command_edit, "text")())
    dummy.operator_command_edit = SimpleNamespace(
        value="",
        setText=lambda text: setattr(dummy.operator_command_edit, "value", text),
        text=lambda: dummy.operator_command_edit.value,
    )

    dummy._operator_add_chat_from_log(
        {
            "category": "语音",
            "action": "麦克风识别",
            "result": "成功",
            "detail": "小正 移动到A点",
        }
    )

    assert sent == ["小正 移动到A点"]


def test_operator_voice_recognition_archives_asr_confidence(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._handle_operator_ui_command = lambda _text: True

    dummy._operator_add_chat_from_log(
        {
            "category": "语音",
            "action": "麦克风识别",
            "result": "成功",
            "detail": "通讯正常吗",
            "asr_confidence": 0.91,
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["input"]["source"] == "voice"
    assert payload["input"]["raw_text"] == "通讯正常吗"
    assert payload["input"]["asr_confidence"] == 0.91


def test_operator_add_chat_from_log_does_not_route_empty_voice_text():
    dummy = DummyOperator()
    handled = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._handle_operator_ui_command = lambda text: handled.append(text) or True

    dummy._operator_add_chat_from_log(
        {
            "category": "语音",
            "action": "麦克风识别",
            "result": "成功",
            "detail": "-",
        }
    )

    assert handled == []


def test_operator_ui_command_handles_send_clear_button_equivalents():
    dummy = DummyOperator()
    calls = []
    logs = []
    dummy._operator_execute_text = lambda: calls.append("execute")
    dummy._operator_clear_text = lambda: calls.append("clear")
    dummy._append_log = lambda *args: logs.append(args)

    assert dummy._handle_operator_ui_command("发送当前指令") is True
    assert dummy._handle_operator_ui_command("执行当前指令") is True
    assert dummy._handle_operator_ui_command("清空输入") is True

    assert calls == ["execute", "execute", "clear"]
    assert [entry[1] for entry in logs] == ["按钮语音指令", "按钮语音指令", "按钮语音指令"]


def test_operator_ui_command_returns_chat_receipt_for_main_page_command():
    dummy = DummyOperator()
    chats = []
    logs = []
    modes = []
    calls = []
    dummy._set_workspace_mode = lambda mode: modes.append(mode)
    dummy._operator_go_home = lambda: calls.append("home")
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))

    handled = dummy._handle_operator_ui_command("主界面")

    assert handled is True
    assert modes == ["operator"]
    assert calls == ["home"]
    assert dummy.status_text == "已回到主界面。"
    assert chats[-1] == ("assistant", "已回到主界面。")
    assert log_args(logs[-1])[0:3] == ("用户页面", "按钮语音指令", "成功")


def test_operator_ui_command_returns_chat_receipt_for_execution_page_command():
    dummy = DummyOperator()
    chats = []
    logs = []
    modes = []
    calls = []
    dummy._set_workspace_mode = lambda mode: modes.append(mode)
    dummy._operator_show_execution = lambda: calls.append("execute")
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))

    handled = dummy._handle_operator_ui_command("流程执行")

    assert handled is True
    assert modes == ["operator"]
    assert calls == ["execute"]
    assert dummy.status_text == "已显示流程执行页面。"
    assert chats[-1] == ("assistant", "已显示流程执行页面。")
    assert log_args(logs[-1])[0:3] == ("用户页面", "按钮语音指令", "成功")


def test_operator_ui_command_does_not_treat_flow_status_question_as_execution_page_command():
    dummy = DummyOperator()

    handled = dummy._handle_operator_ui_command("现在流程执行的怎么样")

    assert handled is False


def test_operator_confirm_execute_without_pending_plan_reports_alarm_reason():
    dummy = DummyOperator()
    chats = []
    logs = []
    dummy.alarm_code = "ERR_000"
    dummy.alarm_text = "控制器报警"
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))

    handled = dummy._handle_operator_ui_command("确认执行这个命令")

    assert handled is True
    assert "当前没有待确认的执行计划" in chats[-1][1]
    assert "报警码 ERR_000" in chats[-1][1]
    assert "请先处理报警" in chats[-1][1]
    assert dummy.status_text == "当前没有待确认的执行计划，未执行。"
    assert log_args(logs[-1])[0:3] == ("用户页面", "安全确认", "提示")


def test_operator_show_execution_requests_execute_scene():
    dummy = DummyOperator()
    calls = []
    dummy._refresh_operator_view = lambda: calls.append("refresh")

    dummy._operator_show_execution()

    assert dummy._operator_scene_override == "execute"
    assert calls == ["refresh"]


def test_operator_ui_command_handles_tts_button_equivalents():
    dummy = DummyOperator()
    calls = []
    dummy._operator_set_tts_enabled = lambda enabled: calls.append(enabled)

    assert dummy._handle_operator_ui_command("开启语音播报") is True
    assert dummy._handle_operator_ui_command("关闭语音播报") is True

    assert calls == [True, False]


def test_operator_ui_command_handles_record_button_equivalent_when_allowed():
    dummy = DummyOperator()
    calls = []
    dummy._operator_toggle_microphone_recording = lambda: calls.append("record")
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("开始录音") is True

    assert calls == ["record"]


def test_operator_ui_command_ignores_record_button_equivalent_from_voice_route():
    dummy = DummyOperator()
    calls = []
    dummy._operator_voice_route_active = True
    dummy._operator_toggle_microphone_recording = lambda: calls.append("record")
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("开始录音") is False

    assert calls == []


def test_operator_ui_command_handles_low_risk_engineer_page_voice_commands():
    dummy = DummyOperator()
    workspace_indexes = []
    shown_pages = []
    logs = []
    status = {"text": ""}
    dummy._authenticated_role = "engineer"
    dummy.workspace_pages = SimpleNamespace(setCurrentIndex=lambda index: workspace_indexes.append(index))
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._show_page = lambda index: shown_pages.append(index)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("切到后台") is True
    assert dummy._handle_operator_ui_command("切到日志") is True

    assert workspace_indexes == [0, 0]
    assert shown_pages == [1, 2]
    assert status["text"] == "已打开工程师日志页。"
    assert [entry[0] for entry in logs] == ["工程师页语音指令", "工程师页语音指令"]


def test_operator_ui_command_handles_low_risk_engineer_tab_voice_commands():
    dummy = DummyOperator()
    shown_pages = []
    tab_indexes = []
    logs = []
    status = {"text": ""}
    dummy._authenticated_role = "engineer"
    dummy.workspace_pages = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy.engineer_right_tabs = SimpleNamespace(setCurrentIndex=lambda index: tab_indexes.append(index))
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._show_page = lambda index: shown_pages.append(index)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("切到系统参数") is True
    assert dummy._handle_operator_ui_command("打开流程管理") is True

    assert shown_pages == [1, 1]
    assert tab_indexes == [1, 3]
    assert status["text"] == "已打开工程师流程管理。"
    assert [entry[3] for entry in logs] == ["系统参数", "流程管理"]


def test_operator_ui_command_handles_low_risk_engineer_refresh_commands():
    dummy = DummyOperator()
    calls = []
    logs = []
    dummy._authenticated_role = "engineer"
    dummy.workspace_pages = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._show_page = lambda _index: None
    dummy._read_feedback = lambda: calls.append("read_feedback")
    dummy._refresh_microphone_devices = lambda: calls.append("refresh_microphones")
    dummy._refresh_logs = lambda: calls.append("refresh_logs")
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("读取控制器反馈") is True
    assert dummy._handle_operator_ui_command("刷新麦克风") is True
    assert dummy._handle_operator_ui_command("刷新日志") is True

    assert calls == ["read_feedback", "refresh_microphones", "refresh_logs"]
    assert [entry[3] for entry in logs] == ["读取反馈", "刷新设备", "刷新日志"]


def test_operator_ui_command_does_not_directly_execute_risky_engineer_commands():
    dummy = DummyOperator()
    calls = []
    logs = []
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._save_system_config = lambda: calls.append("save_system_config")
    dummy._clear_logs = lambda: calls.append("clear_logs")

    assert dummy._handle_operator_ui_command("保存系统参数") is True
    assert dummy._handle_operator_ui_command("清空日志") is True

    assert calls == []
    assert [entry[2] for entry in logs] == ["等待确认", "拒绝"]


def test_operator_ui_command_confirms_pending_engineer_confirm_command():
    dummy = DummyOperator()
    calls = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._save_system_config = lambda: calls.append("save_system_config")

    assert dummy._handle_operator_ui_command("保存系统参数") is True
    assert calls == []
    assert getattr(dummy, "_operator_pending_engineer_voice_spec").action == "save_system_config"
    assert "等待确认" in status["text"]

    assert dummy._handle_operator_ui_command("确认工程师操作") is True

    assert calls == ["save_system_config"]
    assert getattr(dummy, "_operator_pending_engineer_voice_spec") is None
    assert [entry[2] for entry in logs] == ["等待确认", "成功"]


def test_operator_ui_command_cancels_pending_engineer_confirm_command():
    dummy = DummyOperator()
    calls = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._save_flow = lambda: calls.append("save_flow")

    assert dummy._handle_operator_ui_command("保存流程") is True
    assert dummy._handle_operator_ui_command("取消工程师操作") is True

    assert calls == []
    assert getattr(dummy, "_operator_pending_engineer_voice_spec") is None
    assert status["text"] == "已取消工程师语音操作。"
    assert [entry[2] for entry in logs] == ["等待确认", "取消"]


def test_operator_ui_command_rejects_expired_engineer_confirm_command():
    dummy = DummyOperator()
    calls = []
    logs = []
    status = {"text": ""}
    now_values = iter([100.0, 100.0, 200.0, 200.0])
    dummy.axis_ranges = SimpleNamespace(operator_confirm_timeout_sec=60)
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_now_seconds = lambda: next(now_values)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._save_system_config = lambda: calls.append("save_system_config")

    assert dummy._handle_operator_ui_command("保存系统参数") is True
    assert dummy._handle_operator_ui_command("确认工程师操作") is True

    assert calls == []
    assert getattr(dummy, "_operator_pending_engineer_voice_spec") is None
    assert "已超时" in status["text"]
    assert [entry[2] for entry in logs] == ["等待确认", "超时"]


def test_operator_ui_command_overwrites_pending_engineer_confirm_command_with_audit_log():
    dummy = DummyOperator()
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("保存系统参数") is True
    assert dummy._handle_operator_ui_command("保存流程") is True

    assert getattr(dummy, "_operator_pending_engineer_voice_spec").action == "save_flow"
    assert "保存流程" in status["text"]
    assert [entry[2] for entry in logs] == ["等待确认", "覆盖", "等待确认"]


def test_operator_ui_command_publishes_response_when_rejecting_danger_engineer_command():
    dummy = DummyOperator()
    messages = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("删除流程") is True

    assert messages[0].kind == "result"
    assert messages[0].priority == "normal"
    assert "删除流程" in messages[0].text
    assert "未开放语音直接执行" in messages[0].text
    assert status["text"] == messages[0].text
    assert logs[0][2] == "拒绝"


def test_operator_ui_command_publishes_response_for_listed_only_engineer_command():
    dummy = DummyOperator()
    messages = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("打开授权") is True

    assert messages[0].kind == "result"
    assert messages[0].priority == "normal"
    assert "打开授权" in messages[0].text
    assert "仅清单保留" in messages[0].text
    assert status["text"] == messages[0].text
    assert logs[0][2] == "未接入"


def test_operator_ui_command_routes_emergency_text_to_emergency_channel_before_engineer_table():
    dummy = DummyOperator()
    messages = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("急停 A0X 急停") is True

    assert messages[0].kind == "result"
    assert messages[0].priority == "high"
    assert "急停" in messages[0].text
    assert "授权码无效" in messages[0].text
    assert status["text"] == messages[0].text
    assert logs[0][0] == "应急"


def test_operator_acknowledge_alarm_publishes_response_without_resetting():
    dummy = DummyOperator()
    published = []
    statuses = []
    logs = []
    reset_calls = []
    dummy.alarm_code = "ERR_9"
    dummy.alarm_text = "驱动器报警"
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._handle_system_action = lambda action: reset_calls.append(action)

    dummy._operator_acknowledge_alarm()

    assert reset_calls == []
    assert published[-1].kind == "alert"
    assert "ERR_9" in published[-1].text
    assert statuses[-1] == published[-1].text
    assert log_args(logs[-1])[0:3] == ("用户页面", "确认报警", "成功")


def test_operator_ui_command_handles_acknowledge_alarm_voice_equivalent():
    dummy = DummyOperator()
    calls = []
    dummy._operator_acknowledge_alarm = lambda: calls.append("ack")

    assert dummy._handle_operator_ui_command("确认报警") is True

    assert calls == ["ack"]


def test_operator_stop_current_sends_cancel_when_single_controller_action_is_running():
    dummy = DummyOperator()
    calls = []
    statuses = []
    logs = []
    dummy.flow_running = False
    dummy.busy = "运行中"
    dummy.run_state = "空闲"
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._handle_system_action = lambda action: calls.append(action)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._refresh_operator_view = lambda: None

    dummy._operator_stop_current()

    assert calls == ["sys_cancel"]
    assert statuses[-1] == "已发送取消当前任务命令。"
    assert log_args(logs[-1])[0:3] == ("用户页面", "停止当前任务", "成功")


def test_operator_stop_current_sends_cancel_and_stops_local_flow_when_flow_is_running():
    dummy = DummyOperator()
    calls = []
    stopped = []
    statuses = []
    logs = []
    dummy.flow_running = True
    dummy.busy = "运行中"
    dummy.run_state = "空闲"
    dummy.status_label = SimpleNamespace(setText=statuses.append)
    dummy._handle_system_action = lambda action: calls.append(action)
    dummy._stop_flow = lambda: stopped.append(True)
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    dummy._operator_stop_current()

    assert calls == ["sys_cancel"]
    assert stopped == [True]
    assert statuses[-1] == "已发送取消当前任务命令。"
    assert log_args(logs[-1])[0:3] == ("用户页面", "停止当前任务", "成功")


def test_operator_ui_command_handles_cancel_current_action_voice_equivalent():
    dummy = DummyOperator()
    calls = []
    dummy._operator_stop_current = lambda: calls.append("stop")

    assert dummy._handle_operator_ui_command("取消当前动作") is True
    assert dummy._handle_operator_ui_command("停止当前动作") is True

    assert calls == ["stop", "stop"]


def test_operator_add_chat_from_log_queues_alarm_acknowledged_without_chat_noise():
    dummy = DummyOperator()
    chat_messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    dummy._operator_add_chat_from_log(
        {
            "category": "用户页面",
            "action": "确认报警",
            "result": "成功",
            "detail": "ERR_9 | 驱动器报警",
        }
    )

    pending = dummy._operator_pending_broadcasts_for_delivery(0)
    assert [message.text for message in pending] == ["报警已确认：ERR_9 | 驱动器报警"]
    assert chat_messages == []


def test_operator_add_chat_from_log_queues_local_stop_current_result():
    dummy = DummyOperator()
    chat_messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._operator_archive_execution_from_log = lambda entry, response_text: True

    dummy._operator_add_chat_from_log(
        {
            "category": "用户页面",
            "action": "停止当前任务",
            "result": "成功",
            "detail": "已发送 Func104 取消当前函数",
        }
    )

    pending = dummy._operator_pending_broadcasts_for_delivery(0)
    assert [message.text for message in pending] == ["已发送取消当前任务命令。"]
    assert chat_messages == [("assistant", "已发送取消当前任务命令。")]


def test_operator_add_chat_from_log_queues_no_running_task_hint():
    dummy = DummyOperator()
    chat_messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._operator_archive_execution_from_log = lambda entry, response_text: True

    dummy._operator_add_chat_from_log(
        {
            "category": "用户页面",
            "action": "停止流程",
            "result": "提示",
            "detail": "当前没有正在运行的流程",
        }
    )

    pending = dummy._operator_pending_broadcasts_for_delivery(0)
    assert [message.text for message in pending] == ["当前没有正在运行的任务。"]
    assert chat_messages == [("assistant", "当前没有正在运行的任务。")]


def test_operator_add_chat_from_log_queues_core_system_command_results():
    cases = [
        ("系统命令 sys_pause", "成功", "任务1001", "系统已暂停。"),
        ("系统命令 sys_resume", "成功", "任务1002", "系统已继续运行。"),
        ("系统命令 alarm_reset", "成功", "任务1003", "报警复位已执行。"),
        ("系统命令 alarm_reset", "失败", "控制器无响应", "系统命令失败：控制器无响应。"),
        ("alarm_reset", "失败", "流程执行中", "系统命令失败：流程执行中。"),
    ]

    for action, result, detail, expected_text in cases:
        dummy = DummyOperator()
        chat_messages = []
        dummy.operator_response_builder = ResponseBuilder()
        dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
        dummy._operator_archive_execution_from_log = lambda entry, response_text: True

        dummy._operator_add_chat_from_log(
            {
                "category": "系统",
                "action": action,
                "result": result,
                "detail": detail,
            }
        )

        pending = dummy._operator_pending_broadcasts_for_delivery(0)
        assert [message.text for message in pending] == [expected_text]
        assert chat_messages == [("assistant", expected_text)]


def test_operator_full_status_sections_use_v21_seven_dashboard_boards():
    dummy = DummyOperator()
    snapshot = {
        "refresh_ms": 50,
        "boards": {
            "device_status": {
                "system_state": "运行中",
                "estop": False,
                "pause": False,
                "alarm": True,
                "alarm_code": "ERR_9",
                "mpos_j": (0, 1, 2, 3, 4, 5),
                "mpos_c": ("9", "19", "29", "0", "0", "0"),
                "dpos_j": (1, 2, 3, 4, 5, 6),
                "dpos_c": ("10", "20", "30"),
                "r_current": "120",
                "z_current": "30",
            },
            "action_feasibility": {
                "channel_idle": True,
                "precheck_status": "pass",
                "motion_status": "unavailable",
                "current_func": "FUNC108",
                "result": "0",
            },
            "safety_boundary": {
                "safe_r_range": (50, 700),
                "safe_z_range": (10, 650),
                "current_r": "120",
                "current_z": "30",
                "joint_limits": ((-180, 180), (-90, 90), (-120, 120), (-180, 180), (-120, 120), (-360, 360)),
            },
            "motion_limits": {
                "speed": "30%",
                "motion_percent": "25%",
                "safe_speed_max": 120,
                "safe_acc_max": 80,
                "safe_dec_max": 80,
                "axis_status": (10, 11, 12, 13, 14, 15),
                "motion_type": (20, 21, 22, 23, 24, 25),
            },
            "process_preview": {
                "flow_status": "预演中",
                "flow_current_step": "第2步",
                "current_flow_name": "取放流程",
                "l3_status": "fail",
                "progress_percent": 67,
                "risk_summary": ["目标 X 超出软限位"],
            },
            "process_adaptation": {
                "l2_status": "fail",
                "fstatus": 3,
                "singularity": True,
                "suggestion": "建议调整目标姿态",
            },
            "communication_faults": {
                "ecat_ok": False,
                "controller": "unknown",
                "realtime_feedback": "offline",
                "io_status": "128",
                "servo_enable": "0",
            },
        },
    }

    sections = dummy._operator_full_status_sections_from_snapshot(snapshot)

    assert [title for title, _rows in sections] == [
        "看板1 设备基础状态",
        "看板2 动作执行可行性",
        "看板3 全域安全边界",
        "看板4 运动极限参数",
        "看板5 工艺流程预演进度",
        "看板6 工艺适配评估",
        "看板7 通讯+设备故障诊断",
    ]
    flat_rows = {label: value for _title, rows in sections for label, value in rows}
    assert flat_rows["报警码"] == "ERR_9"
    assert flat_rows["MPOS关节"] == "0 / 1 / 2 / 3 / 4 / 5"
    assert flat_rows["DPOS关节"] == "1 / 2 / 3 / 4 / 5 / 6"
    assert flat_rows["MPOS空间"] == "9 / 19 / 29 / 0 / 0 / 0"
    assert flat_rows["DPOS空间"] == "10 / 20 / 30"
    assert flat_rows["L1/L2预检"] == "pass / unavailable"
    assert flat_rows["安全R范围"] == "50 ~ 700"
    assert flat_rows["关节软限位"] == "J1:-180 ~ 180 / J2:-90 ~ 90 / J3:-120 ~ 120 / J4:-180 ~ 180 / J5:-120 ~ 120 / J6:-360 ~ 360"
    assert flat_rows["预演进度"] == "67%"
    assert flat_rows["L3状态"] == "fail"
    assert flat_rows["轴状态"] == "10 / 11 / 12 / 13 / 14 / 15"
    assert flat_rows["运动类型"] == "20 / 21 / 22 / 23 / 24 / 25"
    assert flat_rows["通讯状态"] == "异常"


def test_operator_apply_scene_broadcasts_and_logs_real_transition_once():
    dummy = DummyOperator()
    stack_indexes = []
    messages = []
    logs = []
    now = [100.0]
    dummy._operator_scene_indexes = {"idle": 0, "execute": 2}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=stack_indexes.append)
    dummy._operator_current_scene = "idle"
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))

    dummy._operator_apply_scene("execute")
    now[0] = 101.0
    dummy._operator_apply_scene("execute")

    assert stack_indexes == [2, 2]
    assert len(messages) == 1
    assert messages[0].kind == "progress"
    assert "执行场景" in messages[0].text
    assert logs[0][0] == ("用户页面", "场景切换", "成功", "idle -> execute")
    assert logs[0][1]["extra"]["current"] == "execute"
    assert logs[0][1]["extra"]["previous"] == "idle"
    assert dummy._operator_scene_state.current == "execute"
    assert dummy._operator_scene_state.previous == "idle"
    assert dummy._operator_scene_state.reason == "operator_apply_scene"
    assert dummy._operator_scene_state.changed_at == 100.0


def test_operator_apply_scene_does_not_broadcast_initial_scene():
    dummy = DummyOperator()
    stack_indexes = []
    messages = []
    dummy._operator_scene_indexes = {"idle": 0}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=stack_indexes.append)
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: None

    dummy._operator_apply_scene("idle")

    assert stack_indexes == [0]
    assert messages == []
    assert dummy._operator_current_scene == "idle"
    assert dummy._operator_scene_state.current == "idle"
    assert dummy._operator_scene_state.previous is None
    assert dummy._operator_scene_state.reason == "initial"


def test_operator_request_scene_records_transition_reason():
    dummy = DummyOperator()
    messages = []
    logs = []
    dummy._operator_scene_indexes = {"idle": 0, "query": 5}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy._operator_current_scene = "idle"
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))

    dummy._operator_request_scene("query", reason="dashboard_query")

    assert dummy._operator_current_scene == "query"
    assert dummy._operator_scene_state.current == "query"
    assert dummy._operator_scene_state.previous == "idle"
    assert dummy._operator_scene_state.reason == "dashboard_query"
    assert logs[-1][1]["extra"]["reason"] == "dashboard_query"


def test_operator_apply_scene_uses_high_priority_alert_for_alarm_scene():
    dummy = DummyOperator()
    messages = []
    dummy._operator_scene_indexes = {"execute": 2, "alarm": 4}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy._operator_current_scene = "execute"
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: None

    dummy._operator_apply_scene("alarm")

    assert messages[-1].kind == "alert"
    assert messages[-1].priority == "high"
    assert "报警场景" in messages[-1].text


def test_operator_alarm_scene_restores_previous_scene_after_alarm_clears():
    dummy = DummyOperator()
    messages = []
    logs = []
    dummy._operator_scene_indexes = {"execute": 2, "alarm": 4}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy._operator_current_scene = "execute"
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy.alarm_code = "ERR_9"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"

    dummy._operator_apply_scene("alarm")
    dummy.alarm_code = "0"
    dummy.busy = "运行中"
    dummy.run_state = "运行中"

    assert dummy._operator_scene_before_alarm == "execute"
    assert dummy._operator_desired_scene() == "execute"
    assert dummy._operator_scene_before_alarm is None


def test_operator_alarm_scene_does_not_restore_stale_execute_after_alarm_clears():
    dummy = DummyOperator()
    dummy._operator_scene_indexes = {"execute": 2, "alarm": 4}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy._operator_current_scene = "execute"
    dummy._operator_publish_response = lambda _message: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy.alarm_code = "ERR_9"
    dummy.busy = "运行中"
    dummy.run_state = "运行中"

    dummy._operator_apply_scene("alarm")
    dummy.alarm_code = "0"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"

    assert dummy._operator_desired_scene() == "idle"
    assert dummy._operator_scene_before_alarm is None


def test_operator_alarm_scene_does_not_restore_stale_confirm_after_alarm_clears():
    dummy = DummyOperator()
    dummy._operator_scene_indexes = {"confirm": 3, "alarm": 4}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy._operator_current_scene = "confirm"
    dummy._operator_publish_response = lambda _message: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_a", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    dummy.alarm_code = "ERR_9"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"

    dummy._operator_apply_scene("alarm")
    dummy.alarm_code = "0"
    dummy._operator_pending_confirm_plan = None

    assert dummy._operator_desired_scene() == "idle"
    assert dummy._operator_scene_before_alarm is None


def test_operator_alarm_scene_restores_query_context_after_alarm_clears():
    dummy = DummyOperator()
    dummy._operator_scene_indexes = {"query": 5, "alarm": 4}
    dummy.operator_scene_stack = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy._operator_current_scene = "query"
    dummy._operator_publish_response = lambda _message: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy.alarm_code = "ERR_9"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy._operator_scene_override = "query"

    dummy._operator_apply_scene("alarm")
    dummy.alarm_code = "0"

    assert dummy._operator_desired_scene() == "query"
    assert dummy._operator_scene_before_alarm is None


def test_operator_l1_plan_dict_extracts_template_target_and_speed():
    dummy = DummyOperator()
    dummy.table = {
        "move_safe": QueryRecord(
            query_key="move_safe",
            func_num=108,
            params={
                "target_x": 10.0,
                "target_y": 20.0,
                "target_z": 30.0,
                "spd_pct": 40.0,
                "acc_pct": 50.0,
                "dec_pct": 60.0,
            },
        )
    }
    plan = SimpleNamespace(
        raw_text="移动到安全点",
        actions=(SimpleNamespace(action_type="template", target="move_safe"),),
    )

    result = dummy._operator_l1_plan_dict(plan)

    assert result["plan_id"] == "移动到安全点"
    assert result["target"] == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert result["speed"] == {"spd_pct": 40.0, "acc_pct": 50.0, "dec_pct": 60.0}


def test_operator_l1_plan_dict_extracts_joint_target_for_single_axis_move():
    dummy = DummyOperator()
    dummy.table = {
        "j2_move": QueryRecord(
            query_key="j2_move",
            func_num=106,
            params={
                "axis_no": 1,
                "pos_val": 45.0,
                "spd_pct": 40.0,
                "acc_pct": 50.0,
                "dec_pct": 60.0,
            },
        )
    }
    plan = SimpleNamespace(
        raw_text="J2到45度",
        actions=(SimpleNamespace(action_type="template", target="j2_move"),),
    )

    result = dummy._operator_l1_plan_dict(plan)

    assert result["target"]["joints"] == (None, 45.0, None, None, None, None)
    assert result["speed"] == {"spd_pct": 40.0, "acc_pct": 50.0, "dec_pct": 60.0}


def test_operator_precheck_summary_lists_failed_items():
    dummy = DummyOperator()
    result = {
        "status": "fail",
        "items": [
            {"label": "无紧急停止", "status": "pass", "message": "急停回路正常。"},
            {"label": "目标 X 在软限位内", "status": "fail", "message": "目标 X=150.0 超出软限位。"},
        ],
    }

    summary = dummy._operator_precheck_summary(result)

    assert "L1预检未通过" in summary
    assert "目标 X 在软限位内：目标 X=150.0 超出软限位。" in summary


def test_operator_confirm_detail_text_lists_risks_and_available_suggestions():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-100.0, 100.0),
        z=(0.0, 200.0),
        safe_speed_max=50.0,
        safe_acc_max=40.0,
        safe_dec_max=30.0,
    )
    dummy.table = {
        "move_risky": QueryRecord(
            query_key="move_risky",
            func_num=108,
            params={
                "target_x": 120.0,
                "target_y": 0.0,
                "target_z": 250.0,
                "target_rx": 1.0,
                "target_ry": 2.0,
                "target_rz": 3.0,
                "spd_pct": 60.0,
                "acc_pct": 50.0,
                "dec_pct": 40.0,
            },
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_risky", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {
        "status": "fail",
        "items": [
            {"label": "目标 X 在软限位内", "status": "fail", "message": "目标 X=120.0 超出软限位。"},
            {"label": "速度百分比未超限", "status": "fail", "message": "速度百分比=60.0 超过上限 50.0。"},
        ],
    }

    text = dummy._operator_confirm_detail_text()

    assert "风险项:" in text
    assert "目标 X 在软限位内: 目标 X=120.0 超出软限位。" in text
    assert "可采纳建议:" in text
    assert "目标 X 调整为 100.0" in text
    assert "速度百分比调整为 50.0" in text


def test_operator_confirm_detail_text_shows_confirm_timeout_remaining():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_safe", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 42.0
    dummy._operator_now_seconds = lambda: 30.4

    text = dummy._operator_confirm_detail_text()

    assert "确认有效期: 剩余 12 秒。" in text


def test_operator_confirm_detail_text_lists_l2_avoidance_suggestion():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1000, 1000), y=(-1000, 1000), z=(0, 1000))
    dummy.avoidance_config = AvoidanceConfig(
        mode="always",
        safe_points={"SAFE": SafePoint(name="SAFE", x=0, y=0, z=300)},
    )
    dummy.current_safe_point_key = "SAFE"
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        )
    }
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"id": "singularity", "status": "fail", "message": "路径接近奇异点。"}],
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    dummy._operator_pending_confirm_plan = plan

    text = dummy._operator_confirm_detail_text()

    assert "可采纳建议:" in text
    assert "增加安全中间点 SAFE" in text
    assert "move_pose" in text


def test_operator_confirm_detail_text_lists_atomic_risk_reason():
    dummy = DummyOperator()
    record = QueryRecord(
        query_key="atomic:virtual:8:1:30",
        func_num=107,
        params={
            "axis_no": 8,
            "pos_val": 30.0,
            "atomic_risk_level": "high",
            "atomic_risk_reason": "速度、加减速或步长较高。",
        },
    )
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", record.query_key, "atomic_rule", "上升30毫米", "虚拟轴原子动作"),),
        source="atomic_rule",
        raw_text="小正，上升30毫米",
        reason="虚拟轴原子动作",
        atomic_records={record.query_key: record},
        requires_confirmation=True,
    )
    dummy._operator_pending_confirm_plan = plan

    text = dummy._operator_confirm_detail_text()

    assert "原子风险:" in text
    assert "high" in text
    assert "速度、加减速或步长较高。" in text


def test_operator_confirm_detail_text_shows_l2_selected_fstatus_after_auto_avoidance():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_motion_plan_result = {
        "status": "pass",
        "selected_fstatus": 1,
        "rejected_fstatuses": (0,),
        "items": [{"id": "find_best_fstatus", "status": "pass", "message": "FSTATUS=0 接近奇异区，改选 FSTATUS=1。"}],
    }

    text = dummy._operator_confirm_detail_text()

    assert "运动规划: L2通过，已选 FSTATUS=1。" in text
    assert "已规避 FSTATUS: 0。" in text


def test_operator_accept_suggestion_available_reflects_l1_adjustment():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-100.0, 100.0), y=(-100.0, 100.0), z=(0.0, 200.0))
    dummy.table = {
        "move_safe": QueryRecord(
            query_key="move_safe",
            func_num=108,
            params={"target_x": 10.0, "target_y": 0.0, "target_z": 100.0},
        ),
        "move_risky": QueryRecord(
            query_key="move_risky",
            func_num=108,
            params={"target_x": 120.0, "target_y": 0.0, "target_z": 250.0},
        ),
    }
    safe_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_safe", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    risky_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_risky", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    assert dummy._operator_accept_suggestion_available(safe_plan) is False
    assert dummy._operator_accept_suggestion_available(risky_plan) is True


def test_operator_precheck_check_texts_reflect_l1_result():
    dummy = DummyOperator()
    result = {
        "status": "fail",
        "items": [
            {"label": "无紧急停止", "status": "pass", "message": "急停回路正常。"},
            {"label": "目标 X 在软限位内", "status": "fail", "message": "目标 X=150.0 超出软限位。"},
        ],
    }

    texts = dummy._operator_precheck_check_texts(result)

    assert texts == [
        "指令接收: 已收到",
        "设备状态检查: 通过",
        "安全参数检查: 未通过",
        "运动规划预演: 未接入",
    ]


def test_operator_precheck_check_texts_reflect_l2_failure():
    dummy = DummyOperator()
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"label": "路径奇异点检查", "status": "fail", "message": "插值点 J4=0.0 接近奇异阈值。"}],
    }
    result = {
        "status": "pass",
        "items": [
            {"label": "无紧急停止", "status": "pass", "message": "急停回路正常。"},
            {"label": "目标 X 在软限位内", "status": "pass", "message": "目标 X 正常。"},
        ],
    }

    texts = dummy._operator_precheck_check_texts(result)

    assert texts[-1] == "运动规划预演: 未通过"


def test_operator_precheck_check_texts_reflect_l2_skipped():
    dummy = DummyOperator()
    dummy._operator_last_motion_plan_result = {"status": "skipped"}
    result = {
        "status": "fail",
        "items": [
            {"label": "目标 X 在软限位内", "status": "fail", "message": "目标 X 超限。"},
        ],
    }

    texts = dummy._operator_precheck_check_texts(result)

    assert texts[-1] == "运动规划预演: 已跳过"


def test_operator_prepare_plan_prechecks_runs_l1_and_l2():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    l1_result = {"status": "pass", "items": []}
    l2_result = {"status": "unavailable", "items": []}
    calls = []
    dummy._operator_run_l1_precheck = lambda received: calls.append(("l1", received)) or l1_result
    dummy._operator_run_l2_motion_plan = lambda received: calls.append(("l2", received)) or l2_result

    dummy._operator_prepare_plan_prechecks(plan)

    assert calls == [("l1", plan), ("l2", plan)]
    assert dummy._operator_last_precheck_result is l1_result
    assert dummy._operator_last_motion_plan_result is l2_result


def test_operator_prepare_plan_prechecks_short_circuits_when_l1_fails():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    l1_result = {"status": "fail", "items": [{"id": "target_x_range", "status": "fail"}]}
    calls = []
    dummy._operator_run_l1_precheck = lambda received: calls.append(("l1", received)) or l1_result
    dummy._operator_run_l2_motion_plan = lambda received: calls.append(("l2", received)) or {"status": "pass"}
    dummy._operator_run_l3_process_precheck = lambda received: calls.append(("l3", received)) or {"status": "pass"}

    dummy._operator_prepare_plan_prechecks(plan)

    assert calls == [("l1", plan)]
    assert dummy._operator_last_precheck_result is l1_result
    assert dummy._operator_last_motion_plan_result["status"] == "skipped"
    assert dummy._operator_last_process_precheck_result is None


def test_operator_prepare_plan_prechecks_publishes_stage_progress_and_reassurance():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "device_status": {"estop": False, "pause": False, "alarm": False},
            "communication_faults": {"realtime_feedback": "online", "ecat_ok": True},
        }
    }
    dummy._operator_run_l1_precheck = lambda _plan: {"status": "pass", "items": []}
    dummy._operator_run_l2_motion_plan = lambda _plan: {"status": "unavailable", "items": []}

    dummy._operator_prepare_plan_prechecks(plan)

    texts = [message.text for message in messages]
    assert "设备状态正常，通讯正常，正在进行安全预检。" in texts
    assert "L1安全预检进度 33%。" in texts
    assert "L2运动预演进度 66%。" in texts
    assert "预检预演完成进度 100%。" in texts


def test_operator_periodic_reassurance_publishes_while_execution_active_and_throttles():
    dummy = DummyOperator()
    now = [10.0]
    messages = []
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_execution_or_pause_active = lambda: True
    dummy._operator_reassurance_interval_seconds = lambda: 2.0
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "device_status": {"estop": False, "pause": False, "alarm": False},
            "communication_faults": {"realtime_feedback": "online", "ecat_ok": True},
        }
    }

    assert dummy._operator_publish_periodic_reassurance_if_needed() is True
    now[0] = 11.0
    assert dummy._operator_publish_periodic_reassurance_if_needed() is False
    now[0] = 12.1
    assert dummy._operator_publish_periodic_reassurance_if_needed() is True

    assert [message.text for message in messages] == [
        "设备状态正常，通讯正常，当前任务仍在处理。",
        "设备状态正常，通讯正常，当前任务仍在处理。",
    ]


def test_operator_periodic_reassurance_resets_when_idle():
    dummy = DummyOperator()
    messages = []
    dummy._operator_now_seconds = lambda: 20.0
    dummy._operator_execution_or_pause_active = lambda: False
    dummy._operator_last_periodic_reassurance_sec = 12.0
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_publish_response = lambda message: messages.append(message)

    assert dummy._operator_publish_periodic_reassurance_if_needed() is False

    assert messages == []
    assert dummy._operator_last_periodic_reassurance_sec == 0.0


def test_operator_prepare_plan_prechecks_runs_l3_for_flow_action():
    dummy = DummyOperator()
    flow = FlowDefinition(name="demo", steps=("move_a",))
    dummy.service = SimpleNamespace(flows={"demo": flow})
    dummy.table = {
        "move_a": QueryRecord(
            query_key="move_a",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        )
    }
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_l1_result_for_record = lambda record, snapshot: {"status": "pass", "items": []}
    dummy._operator_l2_result_for_record = lambda record: {"status": "unavailable", "items": [], "suggestion": "未配置逆解"}
    dummy._operator_run_l1_precheck = lambda _plan: {"status": "pass", "items": []}
    dummy._operator_run_l2_motion_plan = lambda _plan: {"status": "unavailable", "items": []}
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "demo", "rule", "执行流程", "测试"),),
        source="rule",
        raw_text="执行流程",
        reason="测试",
    )

    dummy._operator_prepare_plan_prechecks(plan)

    assert dummy._operator_last_process_precheck_result["status"] == "pass"
    assert dummy._operator_last_process_precheck_result["flow_name"] == "demo"


def test_operator_l3_process_precheck_uses_configured_min_step_delay():
    dummy = DummyOperator()
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"), step_delay_ms=0)
    dummy.service = SimpleNamespace(flows={"demo": flow})
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), l3_min_step_delay_ms=100)
    dummy.table = {
        "move_a": QueryRecord(
            query_key="move_a",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        ),
        "move_b": QueryRecord(
            query_key="move_b",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        ),
    }
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_l1_result_for_record_key = lambda _key, _snapshot: {"status": "pass", "items": []}
    dummy._operator_l2_result_for_record = lambda _record: {"status": "pass", "items": []}
    dummy._append_log = lambda *args, **kwargs: None
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "demo", "rule", "执行流程", "测试"),),
        source="rule",
        raw_text="执行流程",
        reason="测试",
    )

    result = dummy._operator_run_l3_process_precheck(plan)

    assert result["status"] == "fail"
    assert any(item["id"] == "timing_state" for item in result["items"])


def test_operator_l3_process_precheck_publishes_step_progress():
    dummy = DummyOperator()
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))
    dummy.service = SimpleNamespace(flows={"demo": flow})
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.table = {
        "move_a": QueryRecord(query_key="move_a", func_num=108, params={}),
        "move_b": QueryRecord(query_key="move_b", func_num=108, params={}),
    }
    messages = []
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_l1_result_for_record_key = lambda _key, _snapshot: {"status": "pass", "items": []}
    dummy._operator_l2_result_for_record = lambda _record: {"status": "pass", "items": []}
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: None
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "demo", "rule", "执行流程", "测试"),),
        source="rule",
        raw_text="执行流程",
        reason="测试",
    )

    result = dummy._operator_run_l3_process_precheck(plan)

    assert result["status"] == "pass"
    message_texts = [message.text for message in messages]
    assert "流程预演进度 38%，已完成第1/2步：move_a。" in message_texts
    assert "流程预演进度 70%，已完成第2/2步：move_b。" in message_texts


def test_operator_l3_progress_updates_precheck_progress_bar():
    dummy = DummyOperator()
    messages = []
    dummy.operator_precheck_progress = progress_bar()
    dummy.operator_precheck_title = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_l3_progress(
        {"flow_name": "demo", "current_step": 1, "total_steps": 4, "step_key": "move_a", "percent": 25}
    )

    assert dummy._operator_l3_progress_percent == 25
    assert dummy._operator_l3_progress_text == "流程预演进度 25%，已完成第1/4步：move_a。"
    assert dummy.operator_precheck_progress.state["range"] == (0, 100)
    assert dummy.operator_precheck_progress.state["value"] == 25
    assert dummy.operator_precheck_progress.state["format"] == "L3预演 25%"
    assert messages[-1].text == "流程预演进度 25%，已完成第1/4步：move_a。"


def test_operator_l3_stage_progress_uses_stage_message():
    dummy = DummyOperator()
    messages = []
    dummy.operator_precheck_progress = progress_bar()
    dummy.operator_precheck_title = SimpleNamespace(setText=lambda text: setattr(dummy, "title", text))
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_l3_progress(
        {"flow_name": "demo", "stage": "timing_check", "percent": 80, "message": "正在检查流程步间隔。"}
    )

    assert dummy.title == "流程预演进行中"
    assert dummy._operator_l3_progress_percent == 80
    assert dummy._operator_l3_progress_text == "流程预演进度 80%，正在检查流程步间隔。"
    assert dummy.operator_precheck_progress.state["value"] == 80
    assert dummy.operator_precheck_progress.state["format"] == "L3预演 80%"
    assert messages[-1].text == "流程预演进度 80%，正在检查流程步间隔。"


def test_operator_l2_progress_updates_precheck_progress_bar():
    dummy = DummyOperator()
    messages = []
    dummy.operator_precheck_progress = progress_bar()
    dummy.operator_precheck_title = SimpleNamespace(setText=lambda text: setattr(dummy, "title", text))
    dummy._operator_publish_response = lambda message: messages.append(message)

    dummy._operator_publish_l2_progress(
        {"stage": "singularity_check", "percent": 75, "message": "正在检查 5 个插值点奇异风险。"}
    )

    assert dummy.title == "运动规划预演进行中"
    assert dummy.operator_precheck_progress.state["range"] == (0, 100)
    assert dummy.operator_precheck_progress.state["value"] == 75
    assert dummy.operator_precheck_progress.state["format"] == "L2预演 75%"
    assert messages[-1].text == "L2运动规划预演进度 75%，正在检查 5 个插值点奇异风险。"


def test_operator_dashboard_snapshot_dict_includes_configured_forbidden_boxes():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        l3_forbidden_boxes=({"id": "fixture", "x": [0, 1], "y": [0, 1], "z": [0, 1]},),
    )
    dummy.operator_dashboard_cache = SimpleNamespace(
        update_from_source=lambda _source: None,
        to_dict=lambda: {"position": {}, "safety": {}, "motion": {}, "connection": {}},
    )

    snapshot = dummy._operator_dashboard_snapshot_dict()

    assert snapshot["workspace"]["forbidden_boxes"][0]["id"] == "fixture"


def test_operator_apply_l1_suggestion_creates_adjusted_temp_template():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-100.0, 100.0),
        z=(0.0, 200.0),
        safe_speed_max=50.0,
        safe_acc_max=40.0,
        safe_dec_max=30.0,
    )
    dummy.table = {
        "move_risky": QueryRecord(
            query_key="move_risky",
            func_num=108,
            params={
                "target_x": 120.0,
                "target_y": 0.0,
                "target_z": 250.0,
                "target_rx": 1.0,
                "target_ry": 2.0,
                "target_rz": 3.0,
                "spd_pct": 60.0,
                "acc_pct": 50.0,
                "dec_pct": 40.0,
            },
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_risky", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    suggested = dummy._operator_apply_l1_suggestion_to_plan(plan)

    assert suggested is not None
    target = suggested.actions[0].target
    assert target in dummy.table
    adjusted = dummy.table[target]
    assert adjusted.params["target_x"] == 100.0
    assert adjusted.params["target_z"] == 200.0
    assert adjusted.params["spd_pct"] == 50.0
    assert adjusted.params["acc_pct"] == 40.0
    assert adjusted.params["dec_pct"] == 30.0


def test_operator_apply_l1_suggestion_adjusts_single_axis_joint_target():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-100.0, 100.0),
        z=(0.0, 200.0),
        joint_limits=(
            (-180.0, 180.0),
            (-90.0, 90.0),
            (-120.0, 120.0),
            (-180.0, 180.0),
            (-120.0, 120.0),
            (-360.0, 360.0),
        ),
    )
    dummy.table = {
        "j2_risky": QueryRecord(
            query_key="j2_risky",
            func_num=106,
            params={
                "axis_no": 1,
                "pos_val": 100.0,
                "spd_pct": 40.0,
                "acc_pct": 50.0,
                "dec_pct": 60.0,
            },
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "j2_risky", "rule", "J2到100度", "测试"),),
        source="rule",
        raw_text="J2到100度",
        reason="测试",
    )

    suggested = dummy._operator_apply_l1_suggestion_to_plan(plan)

    assert suggested is not None
    adjusted = dummy.table[suggested.actions[0].target]
    assert adjusted.params["pos_val"] == 90.0


def test_operator_apply_l2_avoidance_suggestion_creates_temp_flow_with_safe_point():
    dummy = DummyOperator()
    dummy.avoidance_config = AvoidanceConfig(
        mode="always",
        safe_points={
            "SAFE": SafePoint(name="SAFE", x=0, y=0, z=300, rx=0, ry=0, rz=0, speed_percent=20, acc_percent=30)
        },
    )
    dummy.current_safe_point_key = "SAFE"
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={
                "target_x": 100.0,
                "target_y": 200.0,
                "target_z": 80.0,
                "target_rx": 10.0,
                "target_ry": 20.0,
                "target_rz": 30.0,
                "spd_pct": 40.0,
                "acc_pct": 40.0,
                "dec_pct": 40.0,
            },
        )
    }
    dummy.service = SimpleNamespace(flows={})
    dummy.current_flow_name = None
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"id": "singularity", "status": "fail", "message": "检测到 J4 接近奇异区。"}],
    }

    suggested = dummy._operator_apply_l2_avoidance_suggestion_to_plan(plan)

    assert suggested is not None
    assert suggested.actions[0].action_type == "flow"
    flow_name = suggested.actions[0].target
    assert flow_name in dummy.service.flows
    flow = dummy.service.flows[flow_name]
    assert flow.steps[0].startswith("__operator_safe_SAFE")
    assert flow.steps[1] == "move_pose"
    assert dummy.table[flow.steps[0]].params["target_z"] == 300.0
    assert dummy.current_flow_name == flow_name
    assert "中间点" in suggested.reason


def test_operator_l2_target_pose_extracts_full_pose_from_template():
    dummy = DummyOperator()
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={
                "target_x": 1.0,
                "target_y": 2.0,
                "target_z": 3.0,
                "target_rx": 4.0,
                "target_ry": 5.0,
                "target_rz": 6.0,
            },
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    assert dummy._operator_l2_target_pose(plan) == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_operator_l2_target_pose_resolves_incremental_template_against_current_pose():
    dummy = DummyOperator()
    dummy.robot_x = "300"
    dummy.robot_y = "0"
    dummy.robot_z = "100"
    dummy.robot_r = "10 / 20 / 30"
    dummy.table = {
        "Z上升50": QueryRecord(
            query_key="Z上升50",
            func_num=108,
            params={
                "target_x": 0.0,
                "target_y": 0.0,
                "target_z": 50.0,
                "target_rx": 0.0,
                "target_ry": 0.0,
                "target_rz": 0.0,
                "fuzzy_pos": 1,
                "position_increment": 1,
            },
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "Z上升50", "rule", "上升", "测试"),),
        source="rule",
        raw_text="上升",
        reason="测试",
    )

    assert dummy._operator_l2_target_pose(plan) == (300.0, 0.0, 150.0, 10.0, 20.0, 30.0)


def test_operator_l2_unavailable_summary_does_not_block_execution():
    dummy = DummyOperator()
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    result = dummy._operator_run_l2_motion_plan(plan)

    assert result["status"] == "unavailable"
    assert dummy._operator_l2_should_block(result) is False
    summary = dummy._operator_l2_summary(result)
    assert "L1安全检查结果" in summary
    assert "L2运动规划预演暂不可用" in summary
    assert "现场确认" in summary


def test_operator_l2_summary_mentions_robot_safety_failure_details():
    result = {
        "status": "fail",
        "items": [],
        "robot_safety": {
            "safe": False,
            "position_ok": True,
            "ik_ok": False,
            "pose_ok": None,
            "blocking_level": "L2",
            "detail_zh": "L2逆解预判未通过：未找到满足关节限位的 FSTATUS。",
            "suggestion_zh": "请调整目标位姿或补充中间点后重试。",
        },
    }

    summary = DummyOperator._operator_l2_summary(result)

    assert "位置=通过" in summary
    assert "逆解=未通过" in summary
    assert "姿态=未检查" in summary
    assert "L2逆解预判未通过" in summary
    assert "请调整目标位姿" in summary


def test_operator_run_l2_motion_plan_attaches_robot_safety_details():
    class FakeKinematicsEngine:
        def inverse(self, pose, fstatus: int):
            return InverseKinematicsResult(True, (0.0, 10.0, 20.0, 30.0, 0.0, 0.0), fstatus)

    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1000.0, 1000.0),
        y=(-1000.0, 1000.0),
        z=(0.0, 2000.0),
        safe_r_min=0.0,
        safe_r_max=2000.0,
        safe_z_min=0.0,
        safe_z_max=2000.0,
        joint_limits=(
            (-180.0, 180.0),
            (-90.0, 90.0),
            (-120.0, 120.0),
            (-180.0, 180.0),
            (-120.0, 120.0),
            (-360.0, 360.0),
        ),
    )
    dummy.operator_kinematics_engine = FakeKinematicsEngine()
    dummy.robot_x = 0.0
    dummy.robot_y = 0.0
    dummy.robot_z = 100.0
    dummy.robot_r = "0/0/0"
    dummy._operator_publish_l2_progress = lambda _event: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={
                "target_x": 100.0,
                "target_y": 0.0,
                "target_z": 500.0,
                "target_rx": 0.0,
                "target_ry": 0.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "acc_pct": 50.0,
                "dec_pct": 50.0,
            },
        )
    }
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    result = dummy._operator_run_l2_motion_plan(plan)

    assert result["status"] == "pass"
    assert result["robot_safety"]["safe"] is True
    assert result["robot_safety"]["position_ok"] is True
    assert result["robot_safety"]["ik_ok"] is True
    assert result["robot_safety"]["pose_ok"] is True


def test_operator_execute_detail_renders_running_flow_as_compact_summary():
    dummy = DummyOperator()
    dummy.flow_running = True
    dummy.current_flow_name = "A到B"
    dummy.flow_current_step = "move_b"
    dummy.flow_step_index = 1
    dummy.table = {
        "move_a": QueryRecord(query_key="move_a", func_num=108, description="移动到位置A", params={}),
        "move_b": QueryRecord(query_key="move_b", func_num=108, description="移动到位置B", params={}),
    }
    dummy.service = SimpleNamespace(get_flow=lambda _name: FlowDefinition(name="A到B", steps=("move_a", "move_b")))

    text = dummy._operator_execute_detail_text("空闲")

    assert "流程：A到B" in text
    assert "步骤：2 / 2" in text
    assert "当前：移动到位置B" in text
    assert "┌" not in text
    assert "│" not in text
    assert "A到B / move_b" not in text


def test_operator_flow_execution_timeline_items_mark_current_step():
    dummy = DummyOperator()
    dummy.flow_running = True
    dummy.current_flow_name = "A到B"
    dummy.flow_step_index = 1
    dummy.table = {
        "move_a": QueryRecord(query_key="move_a", func_num=108, description="移动到位置A", params={}),
        "move_b": QueryRecord(query_key="move_b", func_num=108, description="移动到位置B", params={}),
        "move_c": QueryRecord(query_key="move_c", func_num=108, description="移动到位置C", params={}),
    }
    dummy.service = SimpleNamespace(get_flow=lambda _name: FlowDefinition(name="A到B", steps=("move_a", "move_b", "move_c")))

    items = dummy._operator_flow_execution_timeline_items()

    assert [(item["index"], item["status"], item["label"]) for item in items] == [
        (1, "done", "移动到位置A"),
        (2, "current", "移动到位置B"),
        (3, "pending", "移动到位置C"),
    ]


def test_operator_compact_flow_step_label_limits_long_text():
    text = "小臂上下点头: Ry正转，速度百分比50，加速度50，减速度50，等待执行完成"

    compact = DummyOperator._operator_compact_flow_step_label(text, limit=24)

    assert len(compact) <= 24
    assert compact.endswith("…")


def test_operator_execute_timeline_same_step_updates_progress_without_rebuild():
    dummy = DummyOperator()
    clears = []
    values = []
    item = {"index": 2, "status": "current", "label": "移动到位置B"}
    signature = ("flow", "A到B", ((2, "current", "移动到位置B"),))
    dummy.flow_running = True
    dummy.current_flow_name = "A到B"
    dummy.flow_step_index = 1
    dummy.operator_execute_timeline_layout = object()
    dummy._operator_execute_timeline_signature = signature
    dummy.operator_execute_step_progress_bars = {2: SimpleNamespace(setValue=lambda value: values.append(value))}
    dummy._operator_flow_execution_timeline_items = lambda: [item]
    dummy._operator_flow_execution_name = lambda: "A到B"
    dummy._operator_clear_layout = lambda _layout: clears.append(True)

    dummy._refresh_operator_execute_timeline("运行中", progress=43)

    assert clears == []
    assert values == [43]


def test_operator_schedule_scrolls_current_flow_step_widget():
    dummy = DummyOperator()
    calls = []
    widget = object()
    dummy._operator_execute_step_widgets = {2: widget}
    dummy.operator_execute_timeline_scroll = SimpleNamespace(ensureWidgetVisible=lambda target, *_args: calls.append(target))

    dummy._operator_scroll_current_flow_step(2)

    assert calls == [widget]


def test_operator_scroll_current_flow_step_uses_runtime_step_widget_registry():
    dummy = DummyOperator()
    calls = []
    widget = object()
    dummy.operator_execute_step_widgets = {7: widget}
    dummy.operator_execute_timeline_scroll = SimpleNamespace(ensureWidgetVisible=lambda target, *_args: calls.append(target))

    dummy._operator_scroll_current_flow_step(7)

    assert calls == [widget]


def test_operator_scroll_current_flow_step_centers_widget_when_scrollbar_available():
    dummy = DummyOperator()
    values = []

    class FakeBar:
        def minimum(self):
            return 0

        def maximum(self):
            return 1000

        def setValue(self, value):
            values.append(value)

    widget = SimpleNamespace(y=lambda: 360, height=lambda: 80)
    viewport = SimpleNamespace(height=lambda: 220)
    scroll = SimpleNamespace(
        ensureWidgetVisible=lambda *_args: None,
        verticalScrollBar=lambda: FakeBar(),
        viewport=lambda: viewport,
    )
    dummy._operator_execute_step_widgets = {5: widget}
    dummy.operator_execute_timeline_scroll = scroll

    dummy._operator_scroll_current_flow_step(5)

    assert values == [290]


def test_operator_execute_timeline_does_not_scroll_same_visible_step(monkeypatch):
    dummy = DummyOperator()
    scroll_calls = []
    timer_calls = []
    item = {"index": 2, "status": "current", "label": "移动到位置B"}
    signature = ("flow", "A到B", ((2, "current", "移动到位置B"),))
    dummy.flow_running = True
    dummy.current_flow_name = "A到B"
    dummy.flow_step_index = 1
    dummy.operator_execute_timeline_layout = object()
    dummy._operator_execute_timeline_signature = signature
    dummy._operator_last_visible_flow_step_index = 2
    dummy.operator_execute_step_progress_bars = {2: SimpleNamespace(setValue=lambda _value: None)}
    dummy._operator_flow_execution_timeline_items = lambda: [item]
    dummy._operator_flow_execution_name = lambda: "A到B"
    dummy._operator_clear_layout = lambda _layout: None
    dummy._operator_scroll_current_flow_step = lambda index: scroll_calls.append(index)
    monkeypatch.setattr(
        "robot_modbus_lite.operator_ui_mixin.QTimer.singleShot",
        lambda delay, callback: timer_calls.append((delay, callback)),
    )

    dummy._refresh_operator_execute_timeline("运行中", progress=43)

    assert timer_calls == []
    assert scroll_calls == []


def test_operator_execute_timeline_scrolls_when_visible_step_changes_without_rebuild(monkeypatch):
    dummy = DummyOperator()
    scroll_calls = []
    timer_delays = []
    item = {"index": 3, "status": "current", "label": "移动到位置C"}
    signature = ("flow", "A到B", ((3, "current", "移动到位置C"),))
    dummy.flow_running = True
    dummy.current_flow_name = "A到B"
    dummy.flow_step_index = 2
    dummy.operator_execute_timeline_layout = object()
    dummy._operator_execute_timeline_signature = signature
    dummy._operator_last_visible_flow_step_index = 2
    dummy.operator_execute_step_progress_bars = {3: SimpleNamespace(setValue=lambda _value: None)}
    dummy._operator_flow_execution_timeline_items = lambda: [item]
    dummy._operator_flow_execution_name = lambda: "A到B"
    dummy._operator_clear_layout = lambda _layout: None
    dummy._operator_scroll_current_flow_step = lambda index: scroll_calls.append(index)
    monkeypatch.setattr(
        "robot_modbus_lite.operator_ui_mixin.QTimer.singleShot",
        lambda delay, callback: (timer_delays.append(delay), callback()),
    )

    dummy._refresh_operator_execute_timeline("运行中", progress=50)

    assert timer_delays == [0, 60, 160, 320]
    assert scroll_calls == [3, 3, 3, 3]
    assert dummy._operator_last_visible_flow_step_index == 3


def test_operator_l2_summary_mentions_available_midpoint_suggestion():
    dummy = DummyOperator()
    result = {
        "status": "fail",
        "need_midpoint": True,
        "midpoint_pose": (5.0, 0.0, 0.0, 0.0, 5.0, 0.0),
        "suggestion": "检测到直线路径接近奇异区，建议经中点绕行后再执行。",
        "items": [{"label": "中点绕行建议", "status": "pass", "message": "建议经 RY 偏移中点绕行。"}],
    }

    text = dummy._operator_l2_summary(result)

    assert "建议经中点绕行" in text
    assert "5.0" in text


def test_operator_l2_summary_handles_skipped_state():
    result = {"status": "skipped", "suggestion": "L1安全预检未通过，已跳过L2运动预演和L3流程预演。"}

    text = DummyOperator._operator_l2_summary(result)

    assert text == "L1安全预检未通过，已跳过L2运动预演和L3流程预演。"


def test_operator_current_pose_tuple_reads_runtime_pose_fields():
    dummy = DummyOperator()
    dummy.robot_x = "1.0"
    dummy.robot_y = "2.0"
    dummy.robot_z = "3.0"
    dummy.robot_r = "4.0 / 5.0 / 6.0"

    assert dummy._operator_current_pose_tuple() == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_operator_controller_snapshot_provider_reuses_current_pose_and_safety_defaults():
    dummy = DummyOperator()
    dummy.robot_x = "1.0"
    dummy.robot_y = "2.0"
    dummy.robot_z = "3.0"
    dummy.robot_r = "4.0 / 5.0 / 6.0"
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        safe_speed_max=150.0,
        safe_acc_max=150.0,
        safe_dec_max=150.0,
        default_spd_pct=45.0,
        default_acc_pct=35.0,
        default_dec_pct=25.0,
    )
    dummy._operator_restricted_agent_is_moving = lambda: False

    snapshot = dummy._operator_controller_snapshot_provider()

    assert snapshot.current_pose == {
        "target_x": 1.0,
        "target_y": 2.0,
        "target_z": 3.0,
        "target_rx": 4.0,
        "target_ry": 5.0,
        "target_rz": 6.0,
    }
    assert snapshot.safety_params == {"spd_pct": 45.0, "acc_pct": 35.0, "dec_pct": 25.0}
    assert snapshot.is_moving is False
    assert snapshot.read_ok is True


def test_operator_controller_snapshot_provider_does_not_use_safe_max_as_motion_defaults():
    dummy = DummyOperator()
    dummy.robot_x = "1.0"
    dummy.robot_y = "2.0"
    dummy.robot_z = "3.0"
    dummy.robot_r = "4.0 / 5.0 / 6.0"
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        safe_speed_max=150.0,
        safe_acc_max=150.0,
        safe_dec_max=150.0,
    )
    dummy._operator_restricted_agent_is_moving = lambda: False

    snapshot = dummy._operator_controller_snapshot_provider()

    assert snapshot.safety_params == {"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0}


def test_operator_agent_runtime_bridge_receives_safety_precheck_dependencies():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy._append_log = lambda *args: None

    bridge = dummy._operator_agent_runtime_bridge()

    assert bridge.safety_review_agent_provider == dummy._operator_safety_review_agent
    assert bridge.runtime_snapshot_provider() == dummy._operator_dashboard_snapshot_dict(refresh=True)
    assert bridge.start_pose_provider == dummy._operator_current_pose_tuple


def test_operator_agent_runtime_bridge_receives_confirmation_dependencies_from_restricted_service():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy._append_log = lambda *args: None

    bridge = dummy._operator_agent_runtime_bridge()
    service = dummy._operator_restricted_agent_service()

    assert bridge.confirmation_agent_provider() is service.confirmation_agent
    assert bridge.clock == dummy._operator_now_seconds
    assert bridge.status_signature_provider == dummy._operator_restricted_agent_status_signature
    assert bridge.safety_signature_provider == dummy._operator_restricted_agent_safety_signature


def test_operator_confirm_execute_blocks_failed_l2_motion_plan():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    status_messages = []
    chat_messages = []
    executed = []
    logs = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [
            {"label": "路径奇异点检查", "status": "fail", "message": "插值点 J4=0.0 接近奇异阈值。"}
        ],
    }
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append

    dummy._operator_confirm_execute()

    assert executed == []
    assert status_messages[-1] == "L2运动规划预演未通过，已拒绝执行。"
    assert "插值点 J4=0.0 接近奇异阈值" in chat_messages[-1][1]
    assert log_args(logs[-1])[0:3] == ("运动预演", "确认执行", "拒绝")


def test_operator_confirm_execute_l2_rejection_mentions_robot_safety_detail():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    chat_messages = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"label": "FSTATUS 可达解", "status": "fail", "message": "未找到可达解。"}],
        "robot_safety": {
            "safe": False,
            "position_ok": True,
            "ik_ok": False,
            "pose_ok": None,
            "blocking_level": "L2",
            "detail_zh": "L2逆解预判未通过：未找到满足关节限位的 FSTATUS。",
            "suggestion_zh": "请调整目标位姿或补充中间点后重试。",
        },
    }
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._append_log = lambda *args: None
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = lambda _plan: None

    dummy._operator_confirm_execute()

    assert "L2逆解预判未通过" in chat_messages[-1][1]
    assert "逆解=未通过" in chat_messages[-1][1]


def test_operator_confirm_execute_fails_closed_when_l2_precheck_raises():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
    )
    status_messages = []
    chats = []
    archived = []
    logs = []
    executed = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "pass", "items": []}
    dummy._operator_last_process_precheck_result = {"status": "pass", "items": []}
    dummy._operator_l2_should_block = lambda _motion_plan: (_ for _ in ()).throw(RuntimeError("planner crashed"))
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append

    dummy._operator_confirm_execute()

    assert executed == []
    assert status_messages[-1] == "执行门禁异常，已拒绝执行。"
    assert "planner crashed" in chats[-1][1]
    assert archived[-1]["result"] == "blocked"
    assert log_args(logs[-1])[0:3] == ("执行门禁", "L2运动预演异常", "拒绝")


def test_operator_confirm_execute_blocks_failed_l3_process_precheck():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "demo", "rule", "执行流程", "测试"),),
        source="rule",
        raw_text="执行流程",
        reason="测试",
    )
    status_messages = []
    chat_messages = []
    executed = []
    logs = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "unavailable", "items": []}
    dummy._operator_last_process_precheck_result = {
        "status": "fail",
        "items": [{"label": "第1步 L1 预检", "status": "fail", "message": "目标 X 超出软限位。"}],
    }
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append

    dummy._operator_confirm_execute()

    assert executed == []
    assert status_messages[-1] == "L3流程预演未通过，已拒绝执行。"
    assert "目标 X 超出软限位" in chat_messages[-1][1]
    assert log_args(logs[-1])[0:3] == ("流程预演", "确认执行", "拒绝")


def test_operator_confirm_execute_runs_confirmed_flow_without_reconfirming():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "点头", "rule", "小兵，执行点头流程", "命中流程规则"),),
        source="rule",
        raw_text="小兵，执行点头流程",
        reason="命中流程规则",
        requires_confirmation=True,
    )
    status_messages = []
    chats = []
    logs = []
    run_calls = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "unavailable", "items": []}
    dummy._operator_last_process_precheck_result = {"status": "pass", "items": []}
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._run_next_nlp_action = lambda: run_calls.append(tuple(dummy._nlp_pending_actions))

    dummy._operator_confirm_execute()

    assert getattr(dummy, "_operator_pending_confirm_plan") is None
    assert getattr(dummy, "execute_busy") is True
    assert run_calls
    assert run_calls[0][0].action_type == "flow"
    assert run_calls[0][0].target == "点头"
    assert "确认收到" in status_messages[-1]
    assert not any(log_args(entry)[0:3] == ("用户页面", "等待确认", "提示") for entry in logs)


def test_operator_confirm_execute_archives_failure_when_confirmed_flow_execution_raises():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "点头", "rule", "小兵，执行点头流程", "命中流程规则"),),
        source="rule",
        raw_text="小兵，执行点头流程",
        reason="命中流程规则",
        requires_confirmation=True,
    )
    status_messages = []
    chats = []
    logs = []
    archived = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "unavailable", "items": []}
    dummy._operator_last_process_precheck_result = {"status": "pass", "items": []}
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    def failing_execute(_confirmed_plan):
        raise RuntimeError("controller write timeout")

    dummy._execute_nlp_plan = failing_execute

    dummy._operator_confirm_execute()

    assert getattr(dummy, "_operator_pending_confirm_plan") is None
    assert getattr(dummy, "execute_busy") is False
    assert "执行失败" in status_messages[-1]
    assert "controller write timeout" in status_messages[-1]
    assert chats[-1][2]["kind"] == "warn"
    assert archived[-1]["result"] == "failure"
    assert "controller write timeout" in archived[-1]["final_text"]
    assert log_args(logs[-1])[0:3] == ("用户页面", "确认执行", "失败")


def test_operator_confirmed_plan_execution_failure_records_runtime_state():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "点头", "rule", "小兵，执行点头流程", "命中流程规则"),),
        source="rule",
        raw_text="小兵，执行点头流程",
        reason="命中流程规则",
        requires_confirmation=False,
    )
    calls = []

    class Bridge:
        def record_execution_failure(self, *, thread_id, query_record, error):
            calls.append((thread_id, query_record, error))
            return ToolResult.failure(
                state="execution_failed",
                message=f"执行失败：{error}",
                code="EXECUTION_FAILED",
                data={"query_record": query_record, "error": error},
            )

    dummy.session_id = "ui-session-1"
    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "last_status", text))
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_archive_execution_result = lambda **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None

    dummy._operator_record_confirmed_plan_execution_failure(plan, RuntimeError("controller write timeout"))

    assert calls
    assert calls[0][0] == "ui-session-1"
    assert calls[0][1]["action_type"] == "flow"
    assert calls[0][1]["target"] == "点头"
    assert calls[0][2] == "controller write timeout"


def test_operator_confirm_execute_blocks_when_execution_gate_rejects():
    from robot_modbus_lite.agent_tools.tool_result import ToolResult

    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("flow", "点头", "rule", "小兵，执行点头流程", "命中流程规则"),),
        source="rule",
        raw_text="小兵，执行点头流程",
        reason="命中流程规则",
        requires_confirmation=True,
    )
    status_messages = []
    chats = []
    logs = []
    archived = []
    executed = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 999999999.0
    dummy._operator_now_seconds = lambda: 100.0
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "unavailable", "items": []}
    dummy._operator_last_process_precheck_result = {"status": "pass", "items": []}
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._set_nlp_execute_busy = lambda busy: setattr(dummy, "execute_busy", busy)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append
    dummy._operator_confirm_execution_gate_result = lambda _plan: ToolResult.failure(
        state="permission_denied",
        message="当前权限不允许执行该动作。",
        code="PERMISSION_DENIED",
    )

    dummy._operator_confirm_execute()

    assert executed == []
    assert status_messages[-1] == "执行门禁未通过，已拒绝执行。"
    assert "当前权限不允许执行该动作" in chats[-1][1]
    assert archived[-1]["result"] == "blocked"
    assert log_args(logs[-1])[0:3] == ("执行门禁", "确认执行", "拒绝")


def test_operator_confirm_execution_gate_reflects_latest_precheck_failure():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {"status": "fail", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "pass", "items": []}
    dummy._operator_last_process_precheck_result = {"status": "pass", "items": []}

    result = dummy._operator_confirm_execution_gate_result(plan)

    assert result.ok is False
    assert result.state == "bounds_failed"
    assert result.errors[0]["code"] == "PARAM_BOUNDS_FAILED"


def test_operator_confirm_execution_gate_fails_closed_when_internal_check_raises():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft1", "agent_orchestrator", "等待2秒", "等待确认"),),
        source="agent_orchestrator",
        raw_text="等待2秒",
        reason="等待确认",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft1"},
    )
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {"status": "pass", "items": []}
    dummy._operator_last_process_precheck_result = {"status": "pass", "items": []}
    dummy._operator_l2_should_block = lambda _motion_plan: (_ for _ in ()).throw(RuntimeError("l2 unavailable"))

    result = dummy._operator_confirm_execution_gate_result(plan)

    assert result.ok is False
    assert result.state == "execution_gate_failed"
    assert result.errors[0]["code"] == "EXECUTION_GATE_FAILED"
    assert "l2 unavailable" in result.message


def test_operator_l3_summary_mentions_flow_midpoint_suggestion():
    result = {
        "status": "fail",
        "flow_name": "demo",
        "items": [{"id": "step_l2", "status": "fail", "label": "第2步 L2 预演", "message": "路径接近奇异点。"}],
        "midpoint_suggestions": [
            {
                "step_index": 2,
                "step_key": "move_b",
                "midpoint_pose": (10.0, 20.0, 30.0, 0.0, 5.0, 0.0),
                "midpoint_fstatus": 3,
            }
        ],
    }

    text = DummyOperator._operator_l3_summary(result)

    assert "第2步 move_b 建议中点" in text
    assert "10.0" in text


def test_operator_confirm_execute_rejects_expired_pending_plan():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    status_messages = []
    chat_messages = []
    logs = []
    archived = []
    executed = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 9.0
    dummy._operator_scene_override = "confirm"
    dummy._operator_now_seconds = lambda: 10.0
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append

    dummy._operator_confirm_execute()

    assert executed == []
    assert dummy._operator_pending_confirm_plan is None
    assert dummy._operator_pending_confirm_deadline_sec == 0.0
    assert dummy._operator_scene_override is None
    assert "安全确认已超时" in status_messages[-1]
    assert "请重新输入指令" in chat_messages[-1][1]
    assert archived[-1]["result"] == "blocked"
    assert log_args(logs[-1])[0:3] == ("用户页面", "安全确认超时", "拒绝")


def test_operator_refresh_view_clears_expired_pending_plan_before_scene_selection():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    logs = []
    chats = []
    archived = []
    applied_scenes = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 5.0
    dummy._operator_scene_override = "confirm"
    dummy._operator_now_seconds = lambda: 6.0
    dummy.operator_scene_stack = SimpleNamespace()
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_refresh_dashboard_cache = lambda: None
    dummy._compute_overall_state = lambda: ("空闲", "#000", "空闲")
    dummy.operator_state_label = SimpleNamespace(setText=lambda _text: None, setStyleSheet=lambda _style: None)
    dummy._operator_alarm_active = lambda: False
    dummy._set_operator_badge = lambda *args: None
    dummy.operator_estop_badge = object()
    dummy.operator_pause_badge = object()
    dummy.operator_alarm_badge = object()
    dummy.monitor_label = SimpleNamespace(text=lambda: "实时监控运行中")
    dummy.operator_current_label = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_current_task_text = lambda: "空闲"
    dummy._refresh_operator_axis_labels = lambda: None
    dummy._refresh_operator_scene_content = lambda _detail: None
    dummy._refresh_operator_recent_events = lambda: None
    dummy._refresh_operator_dialog_labels = lambda: None
    dummy._refresh_operator_full_status = lambda: None
    dummy._sync_operator_mic_button = lambda: None
    dummy._operator_apply_scene = applied_scenes.append
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._append_log = lambda *args: logs.append(args)
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy.alarm_text = ""

    dummy._refresh_operator_view()

    assert dummy._operator_pending_confirm_plan is None
    assert applied_scenes == ["idle"]
    assert "安全确认已超时" in chats[-1][1]
    assert archived[-1]["result"] == "blocked"
    assert log_args(logs[-1])[0:3] == ("用户页面", "安全确认超时", "拒绝")


def test_operator_cancel_confirm_archives_cancelled_pending_plan():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    status_messages = []
    chat_messages = []
    logs = []
    archived = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 30.0
    dummy._operator_scene_override = "confirm"
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._refresh_operator_view = lambda: None

    dummy._operator_cancel_confirm()

    assert dummy._operator_pending_confirm_plan is None
    assert dummy._operator_pending_confirm_deadline_sec == 0.0
    assert dummy._operator_scene_override is None
    assert status_messages[-1] == "已取消待确认的执行计划。"
    assert chat_messages[-1] == ("assistant", "已取消待确认的执行计划。")
    assert archived[-1] == {"result": "cancelled", "final_text": "已取消待确认的执行计划。"}
    assert log_args(logs[-1])[0:3] == ("用户页面", "取消确认", "成功")


def test_operator_cancel_confirm_cancels_tool_chain_agent_draft():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("agent_draft", "draft-1", "agent_orchestrator", "等待2秒", "等待确认"),),
        source="agent_orchestrator",
        raw_text="等待2秒",
        reason="等待确认",
        requires_confirmation=True,
        flow_draft={"agent_kind": "waiting_confirmation", "draft_id": "draft-1"},
    )
    calls = []

    class Bridge:
        def cancel_pending_plan(self, draft_id, *, thread_id):
            calls.append(("cancel", draft_id, thread_id))
            return ToolResult.success(state="cancelled", message="已取消。")

        def clear_pending_confirm(self, *, thread_id):
            calls.append(("clear", thread_id))

    status_messages = []
    chat_messages = []
    logs = []
    archived = []
    dummy.session_id = "ui-session-1"
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 30.0
    dummy._operator_scene_override = "confirm"
    dummy._operator_agent_runtime_bridge = lambda: Bridge()
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._operator_archive_execution_result = lambda **kwargs: archived.append(kwargs)
    dummy._refresh_operator_view = lambda: None

    dummy._operator_cancel_confirm()

    assert calls[0] == ("cancel", "draft-1", "ui-session-1")
    assert calls[1] == ("clear", "ui-session-1")
    assert dummy._operator_pending_confirm_plan is None
    assert status_messages[-1] == "已取消待确认的执行计划。"
    assert archived[-1] == {"result": "cancelled", "final_text": "已取消待确认的执行计划。"}
    assert log_args(logs[-1])[0:3] == ("用户页面", "取消确认", "成功")


def test_operator_accept_suggestion_does_not_execute_when_no_adjustment_exists_for_failed_l2():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    status_messages = []
    chat_messages = []
    logs = []
    executed = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"label": "路径奇异点检查", "status": "fail", "message": "路径接近奇异点。"}],
    }
    dummy.axis_ranges = AxisRangeConfig(x=(-100, 100), y=(-100, 100), z=(0, 200))
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        )
    }
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._execute_nlp_plan = executed.append

    dummy._operator_accept_suggestion()

    assert executed == []
    assert status_messages[-1] == "当前没有可自动改写的安全建议，未执行原计划。"
    assert "路径接近奇异点" in chat_messages[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "采纳建议", "拒绝")


def test_operator_accept_suggestion_uses_l2_avoidance_flow_when_available():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )
    prepared = []
    confirmed = []
    status_messages = []
    chat_messages = []
    logs = []
    dummy._operator_pending_confirm_plan = plan
    dummy._operator_pending_confirm_deadline_sec = 100.0
    dummy._operator_now_seconds = lambda: 1.0
    dummy.axis_ranges = AxisRangeConfig(x=(-1000, 1000), y=(-1000, 1000), z=(0, 1000))
    dummy.avoidance_config = AvoidanceConfig(
        mode="always",
        safe_points={"SAFE": SafePoint(name="SAFE", x=0, y=0, z=300)},
    )
    dummy.current_safe_point_key = "SAFE"
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "target_rx": 4.0, "target_ry": 5.0, "target_rz": 6.0},
        )
    }
    dummy.service = SimpleNamespace(flows={})
    dummy.current_flow_name = None
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"id": "singularity", "status": "fail", "message": "路径接近奇异点。"}],
    }
    dummy._operator_prepare_plan_prechecks = prepared.append
    dummy._operator_confirm_execute = lambda: confirmed.append(dummy._operator_pending_confirm_plan)
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text, kwargs))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    dummy._operator_accept_suggestion()

    assert prepared
    assert confirmed == []
    assert dummy._operator_pending_confirm_plan.actions[0].action_type == "flow"
    assert dummy._operator_pending_confirm_plan.actions[0].target in dummy.service.flows
    assert status_messages[-1] == "已采纳安全建议，请重新核对后确认执行。"
    assert "已采纳安全建议" in chat_messages[-1][1]
    assert "请重新核对右侧待确认参数" in chat_messages[-1][1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "采纳建议", "成功")


def test_operator_execute_nlp_text_rejects_new_action_while_controller_is_running():
    dummy = DummyOperator()
    status_messages = []
    logs = []
    messages = []
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "移动到安全点")
    dummy.busy = "运行中"
    dummy.run_state = "空闲"
    dummy.flow_running = False
    dummy.nlp_sequence_running = False
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: None

    dummy._execute_nlp_text()

    assert status_messages[-1] == "当前任务未完成，已拒绝新的动作指令。"
    assert messages[-1].kind == "alert"
    assert "当前任务未完成" in messages[-1].text
    assert log_args(logs[-1])[0:3] == ("用户页面", "忙碌拒绝新指令", "拒绝")


def test_operator_execute_nlp_text_routes_non_wake_text_to_busy_chat_while_flow_running():
    dummy = DummyOperator()
    routed = []
    rejected = []
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "你好啊")
    dummy.flow_running = True
    dummy.nlp_sequence_running = False
    dummy.busy = "运行中"
    dummy.run_state = "运行中"
    dummy._operator_handle_busy_chat_text = lambda text: routed.append(text) or True
    dummy._operator_begin_busy_interruption = lambda text: rejected.append(text) or True

    dummy._execute_nlp_text()

    assert routed == ["你好啊"]
    assert rejected == []


def test_operator_execute_nlp_text_pauses_for_wake_command_while_flow_running():
    dummy = DummyOperator()
    routed = []
    interrupted = []
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "小正执行新的流程")
    dummy.flow_running = True
    dummy.nlp_sequence_running = False
    dummy.busy = "运行中"
    dummy.run_state = "运行中"
    dummy._operator_handle_busy_chat_text = lambda text: routed.append(text) or True
    dummy._operator_begin_busy_interruption = lambda text: interrupted.append(text) or True

    dummy._execute_nlp_text()

    assert interrupted == ["小正执行新的流程"]
    assert routed == []


def test_operator_execute_nlp_text_allows_progress_query_while_running():
    dummy = DummyOperator()
    published = []
    logs = []
    status_messages = []
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "现在进度多少")
    dummy.busy = "运行中"
    dummy.run_state = "空闲"
    dummy.flow_running = False
    dummy.nlp_sequence_running = False
    dummy._operator_desired_scene = lambda: "execute"
    dummy._operator_execution_progress = lambda: 50
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy._append_log = lambda *args: logs.append(args)

    dummy._execute_nlp_text()

    assert published[-1].kind == "progress"
    assert "当前执行进度约50%" in published[-1].text
    assert all(log[1] != "忙碌拒绝新指令" for log in logs)


def test_operator_execution_progress_estimates_nlp_sequence_by_action_index():
    dummy = DummyOperator()
    dummy.flow_running = False
    dummy.nlp_sequence_running = True
    dummy._nlp_pending_actions = [object(), object(), object(), object()]
    dummy._nlp_pending_index = 1
    dummy.motion_percent = "-"
    dummy.busy = "空闲"
    dummy.run_state = "空闲"

    assert dummy._operator_execution_progress() == 50


def test_operator_execution_progress_uses_conservative_running_fallback_without_motion_percent():
    dummy = DummyOperator()
    dummy.flow_running = False
    dummy.nlp_sequence_running = False
    dummy.motion_percent = "-"
    dummy.busy = "运行中"
    dummy.run_state = "空闲"

    assert dummy._operator_execution_progress() == 50


def test_operator_publish_response_deduplicates_repeated_context():
    dummy = DummyOperator()
    chat_messages = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    first = dummy._operator_publish_response(
        ResponseMessage(kind="alert", text="报警发生。", priority="high", context_id="alarm:1")
    )
    duplicate = dummy._operator_publish_response(
        ResponseMessage(kind="alert", text="报警发生。", priority="high", context_id="alarm:1")
    )

    assert first is not None
    assert duplicate is None
    assert chat_messages == [("assistant", "报警发生。")]


def test_operator_pending_broadcasts_for_delivery_uses_priority_order():
    dummy = DummyOperator()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_publish_response(ResponseMessage(kind="progress", text="预检中", priority="normal"))
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:2"))
    dummy._operator_publish_response(ResponseMessage(kind="result", text="完成", priority="normal", context_id="result:1"))

    pending = dummy._operator_pending_broadcasts_for_delivery(0)

    assert [message.text for message in pending] == ["报警", "预检中", "完成"]


def test_operator_add_chat_from_log_queues_natural_language_completion():
    dummy = DummyOperator()
    chat_messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列完成",
            "result": "成功",
            "detail": "共执行 2 步",
        }
    )

    pending = dummy._operator_pending_broadcasts_for_delivery(0)
    assert [message.text for message in pending] == ["执行完成：共执行 2 步"]
    assert chat_messages == [("assistant", "执行完成：共执行 2 步")]


def test_operator_add_chat_from_log_completion_resets_nlp_sequence_running():
    dummy = DummyOperator()
    busy_values = []
    dummy.nlp_sequence_running = True
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._set_nlp_execute_busy = lambda busy: busy_values.append(busy) or setattr(dummy, "nlp_sequence_running", busy)

    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列完成",
            "result": "成功",
            "detail": "共执行 1 步",
        }
    )

    assert dummy.nlp_sequence_running is False
    assert busy_values[-1] is False


def test_operator_add_chat_from_log_suppresses_outer_one_step_completion_after_flow_completion():
    dummy = DummyOperator()
    now = [10.0]
    chat_messages = []
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None

    dummy._operator_add_chat_from_log(
        {
            "category": "流程",
            "action": "流程完成 demo",
            "result": "成功",
            "detail": "共完成 7 步",
        }
    )
    now[0] = 10.2
    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列完成",
            "result": "成功",
            "detail": "共执行 1 步",
        }
    )

    pending = dummy._operator_pending_broadcasts_for_delivery(0)
    assert [message.text for message in pending] == ["流程完成：共完成 7 步"]
    assert chat_messages == [("assistant", "流程完成：共完成 7 步")]


def test_operator_schedule_refresh_skips_when_refresh_already_pending(monkeypatch):
    dummy = DummyOperator()
    calls = []
    dummy._operator_refresh_pending = True
    dummy.operator_scene_stack = object()
    dummy._operator_now_seconds = lambda: 10.0
    monkeypatch.setattr("robot_modbus_lite.operator_ui_mixin.QTimer.singleShot", lambda *args: calls.append(args))

    dummy._operator_schedule_refresh()

    assert calls == []


def test_operator_schedule_refresh_caps_rate_while_flow_is_running(monkeypatch):
    dummy = DummyOperator()
    calls = []
    dummy._operator_refresh_pending = False
    dummy.operator_scene_stack = object()
    dummy.flow_running = True
    dummy.nlp_sequence_running = False
    dummy.busy = "空闲"
    dummy.run_state = "空闲"
    dummy._operator_last_refresh_sec = 10.0
    dummy._operator_now_seconds = lambda: 10.05
    monkeypatch.setattr("robot_modbus_lite.operator_ui_mixin.QTimer.singleShot", lambda *args: calls.append(args))

    dummy._operator_schedule_refresh()

    assert calls
    assert calls[0][0] >= 190


def test_operator_refresh_view_skips_heavy_panels_while_user_is_typing():
    dummy = DummyOperator()
    calls = []
    dummy.operator_scene_stack = object()
    dummy._operator_refresh_pending = True
    dummy._operator_now_seconds = lambda: 10.0
    dummy._operator_clear_expired_pending_confirm_for_refresh = lambda: None
    dummy._compute_overall_state = lambda: ("空闲", "#22c55e", "空闲")
    dummy.operator_state_label = SimpleNamespace(setText=lambda text: calls.append(("state", text)), setStyleSheet=lambda text: None)
    dummy.operator_estop_badge = SimpleNamespace()
    dummy.operator_pause_badge = SimpleNamespace()
    dummy.operator_alarm_badge = SimpleNamespace()
    dummy._set_operator_badge = lambda *args: None
    dummy._operator_alarm_active = lambda: False
    dummy.busy = "运行中"
    dummy.run_state = "运行中"
    dummy.alarm_text = ""
    dummy.monitor_label = SimpleNamespace(text=lambda: "实时监控运行中")
    dummy.operator_current_label = SimpleNamespace(setText=lambda text: calls.append(("current", text)))
    dummy._operator_current_task_text = lambda: "流程 demo / step"
    dummy.operator_command_edit = SimpleNamespace(hasFocus=lambda: True, text=lambda: "你好")
    dummy._refresh_operator_axis_labels = lambda: calls.append(("axis", ""))
    dummy._refresh_operator_scene_content = lambda detail: calls.append(("scene", detail))
    dummy._refresh_operator_pending_flow_status = lambda: calls.append(("pending_flow", ""))
    dummy._refresh_operator_recent_events = lambda: calls.append(("recent", ""))
    dummy._refresh_operator_dialog_labels = lambda: calls.append(("dialog", ""))
    dummy._refresh_operator_full_status = lambda: calls.append(("full", ""))
    dummy._sync_operator_mic_button = lambda: calls.append(("mic", ""))
    dummy._operator_publish_periodic_reassurance_if_needed = lambda: calls.append(("reassurance", ""))
    dummy._operator_desired_scene = lambda: "execute"
    dummy._operator_request_scene = lambda scene, reason="": calls.append(("request_scene", scene))

    dummy._refresh_operator_view()

    names = [name for name, _value in calls]
    assert "recent" not in names
    assert "dialog" not in names
    assert "full" not in names
    assert "reassurance" not in names
    assert "request_scene" in names


def test_operator_refresh_view_throttles_heavy_panels_while_flow_running():
    dummy = DummyOperator()
    calls = []
    dummy.operator_scene_stack = object()
    dummy._operator_refresh_pending = True
    dummy._operator_now_seconds = lambda: 10.2
    dummy._operator_last_heavy_panel_refresh_sec = 10.0
    dummy._operator_clear_expired_pending_confirm_for_refresh = lambda: None
    dummy._compute_overall_state = lambda: ("运行中", "#2563eb", "运行中")
    dummy.operator_state_label = SimpleNamespace(setText=lambda text: calls.append(("state", text)), setStyleSheet=lambda text: None)
    dummy.operator_estop_badge = SimpleNamespace()
    dummy.operator_pause_badge = SimpleNamespace()
    dummy.operator_alarm_badge = SimpleNamespace()
    dummy._set_operator_badge = lambda *args: None
    dummy._operator_alarm_active = lambda: False
    dummy.busy = "运行中"
    dummy.run_state = "运行中"
    dummy.flow_running = True
    dummy.nlp_sequence_running = False
    dummy.alarm_text = ""
    dummy.monitor_label = SimpleNamespace(text=lambda: "实时监控运行中")
    dummy.operator_current_label = SimpleNamespace(setText=lambda text: calls.append(("current", text)))
    dummy._operator_current_task_text = lambda: "流程 demo / step"
    dummy.operator_command_edit = SimpleNamespace(hasFocus=lambda: False, text=lambda: "")
    dummy._refresh_operator_axis_labels = lambda: calls.append(("axis", ""))
    dummy._refresh_operator_scene_content = lambda detail: calls.append(("scene", detail))
    dummy._refresh_operator_pending_flow_status = lambda: calls.append(("pending_flow", ""))
    dummy._refresh_operator_recent_events = lambda: calls.append(("recent", ""))
    dummy._refresh_operator_dialog_labels = lambda: calls.append(("dialog", ""))
    dummy._refresh_operator_full_status = lambda: calls.append(("full", ""))
    dummy._sync_operator_mic_button = lambda: calls.append(("mic", ""))
    dummy._operator_publish_periodic_reassurance_if_needed = lambda: calls.append(("reassurance", ""))
    dummy._operator_desired_scene = lambda: "execute"
    dummy._operator_request_scene = lambda scene, reason="": calls.append(("request_scene", scene))

    dummy._refresh_operator_view()

    names = [name for name, _value in calls]
    assert "scene" in names
    assert "recent" not in names
    assert "full" not in names
    assert "dialog" in names


def test_operator_refresh_view_refreshes_heavy_panels_after_flow_throttle_window():
    dummy = DummyOperator()
    calls = []
    dummy.operator_scene_stack = object()
    dummy._operator_refresh_pending = True
    dummy._operator_now_seconds = lambda: 11.2
    dummy._operator_last_heavy_panel_refresh_sec = 10.0
    dummy._operator_clear_expired_pending_confirm_for_refresh = lambda: None
    dummy._compute_overall_state = lambda: ("运行中", "#2563eb", "运行中")
    dummy.operator_state_label = SimpleNamespace(setText=lambda text: None, setStyleSheet=lambda text: None)
    dummy.operator_estop_badge = SimpleNamespace()
    dummy.operator_pause_badge = SimpleNamespace()
    dummy.operator_alarm_badge = SimpleNamespace()
    dummy._set_operator_badge = lambda *args: None
    dummy._operator_alarm_active = lambda: False
    dummy.busy = "运行中"
    dummy.run_state = "运行中"
    dummy.flow_running = True
    dummy.nlp_sequence_running = False
    dummy.alarm_text = ""
    dummy.monitor_label = SimpleNamespace(text=lambda: "实时监控运行中")
    dummy.operator_current_label = SimpleNamespace(setText=lambda text: None)
    dummy._operator_current_task_text = lambda: "流程 demo / step"
    dummy.operator_command_edit = SimpleNamespace(hasFocus=lambda: False, text=lambda: "")
    dummy._refresh_operator_axis_labels = lambda: None
    dummy._refresh_operator_scene_content = lambda detail: None
    dummy._refresh_operator_pending_flow_status = lambda: None
    dummy._refresh_operator_recent_events = lambda: calls.append("recent")
    dummy._refresh_operator_dialog_labels = lambda: None
    dummy._refresh_operator_full_status = lambda: calls.append("full")
    dummy._sync_operator_mic_button = lambda: None
    dummy._operator_publish_periodic_reassurance_if_needed = lambda: None
    dummy._operator_desired_scene = lambda: "execute"
    dummy._operator_request_scene = lambda scene, reason="": None

    dummy._refresh_operator_view()

    assert calls == ["recent", "full"]
    assert dummy._operator_last_heavy_panel_refresh_sec == 11.2


def test_operator_dashboard_cache_refresh_is_throttled_while_user_is_typing():
    dummy = DummyOperator()
    calls = []
    snapshots = iter(["first", "second"])
    dummy._operator_now_seconds = lambda: 10.05
    dummy.operator_command_edit = SimpleNamespace(hasFocus=lambda: True, text=lambda: "你好")
    dummy.operator_dashboard_cache = SimpleNamespace(
        snapshot="cached",
        update_from_source=lambda source: calls.append(source) or next(snapshots),
    )
    dummy._operator_last_dashboard_cache_refresh_sec = 10.0
    dummy._operator_publish_dashboard_change_broadcasts = lambda snapshot: None

    result = dummy._operator_refresh_dashboard_cache()

    assert result == "cached"
    assert calls == []


def test_operator_archive_text_input_uses_cached_dashboard_snapshot(tmp_path: Path):
    dummy = DummyOperator()
    calls = []
    dummy.runtime_root = tmp_path
    dummy._log_dir = tmp_path
    dummy.session_id = "session-archive"
    dummy._operator_now_seconds = lambda: 10.0
    dummy._operator_scene_state_payload = lambda: {}
    dummy._operator_dashboard_snapshot_dict = lambda **kwargs: calls.append(kwargs) or {
        "ts": "2026-06-03T10:00:00.000",
        "refresh_ms": 50,
        "position": {"x": 1, "y": 2, "z": 3, "r": "0/0/0", "joints": (1, 2, 3, 4, 5, 6)},
        "safety": {"estop": False, "paused": False, "alarm_active": False, "alarm_code": "0"},
        "motion": {"running_state": "运行中", "current_func": "flow"},
        "connection": {"realtime_feedback": "online"},
        "hardware": {},
        "boards": {},
    }

    record = dummy._operator_archive_text_input("你好")

    assert record is not None
    assert calls == [{"refresh": False}]


def test_operator_add_chat_from_log_archives_natural_language_completion(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")

    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列完成",
            "result": "成功",
            "detail": "共执行 2 步",
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "success"
    assert payload["response"]["final"] == "执行完成：共执行 2 步"


def test_operator_add_chat_from_log_archives_flow_completion(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("执行流程")

    dummy._operator_add_chat_from_log(
        {
            "category": "流程",
            "action": "流程完成 demo",
            "result": "成功",
            "detail": "共完成 3 步",
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "success"
    assert payload["response"]["final"] == "流程完成：共完成 3 步"


def test_operator_add_chat_from_log_finalizes_pending_nlp_for_execution(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("保存并执行")

    dummy._operator_add_chat_from_log(
        {
            "category": "流程",
            "action": "流程完成 demo",
            "result": "成功",
            "detail": "共完成 1 步",
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["nlp_result"]["engine"] != "pending"
    assert payload["nlp_result"]["intent"] != "pending"
    assert payload["nlp_result"]["action_type"] == "execution"


def test_operator_add_chat_from_log_downgrades_success_when_state_after_has_alarm(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("保存并执行")

    dummy._operator_add_chat_from_log(
        {
            "category": "流程",
            "action": "流程完成 demo",
            "result": "成功",
            "detail": "共完成 1 步",
            "state_after": {
                "msg_type": "device_snapshot",
                "data": {"alarm": True, "ready": False, "ecat_ok": False, "alarm_code": "ERR_000"},
            },
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "warning"
    assert "执行后状态异常" in payload["response"]["final"]


def test_operator_add_chat_from_log_archives_flow_failure(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("执行流程")

    dummy._operator_add_chat_from_log(
        {
            "category": "流程",
            "action": "并行组失败 第2步",
            "result": "失败",
            "detail": "夹爪失败",
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "failure"
    assert payload["response"]["final"] == "流程异常：夹爪失败"


def test_operator_add_chat_from_log_archives_six_axis_completion(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")

    dummy._operator_add_chat_from_log(
        {
            "category": "六轴",
            "action": "执行完成 move_a",
            "result": "成功",
            "detail": "LONG(34)=1",
            "command_snapshot": {
                "query_key": "move_a",
                "func_num": 108,
                "writes": [
                    {"start_vr": 100, "values": [108, 1]},
                    {"start_vr": 120, "values": [30, 40, 50]},
                ],
            },
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "success"
    assert payload["execution"]["modbus_write"]["query_key"] == "move_a"
    assert payload["execution"]["modbus_write"]["func_num"] == 108
    assert payload["execution"]["modbus_write"]["writes"][1]["values"] == [30, 40, 50]
    assert payload["response"]["final"] == "动作执行完成：move_a。"


def test_operator_add_chat_from_log_updates_execution_detail_from_dispatch_log(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")

    dummy._operator_add_chat_from_log(
        {
            "category": "执行",
            "action": "发送指令 move_a",
            "result": "成功",
            "detail": "任务1001",
            "command_snapshot": {
                "query_key": "move_a",
                "func_num": 108,
                "writes": [{"start_vr": 100, "values": [108, 1]}],
            },
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "success"
    assert payload["execution"]["modbus_write"]["query_key"] == "move_a"
    assert payload["execution"]["modbus_write"]["writes"][0]["values"] == [108, 1]


def test_operator_add_chat_from_log_calculates_exec_duration_from_dispatch_logs(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")

    dummy._operator_add_chat_from_log(
        {
            "category": "执行",
            "action": "发送准备 move_a",
            "result": "成功",
            "detail": "ready",
            "monotonic_ms": 1000,
        }
    )
    dummy._operator_add_chat_from_log(
        {
            "category": "执行",
            "action": "发送指令 move_a",
            "result": "成功",
            "detail": "任务1001",
            "monotonic_ms": 1450,
            "command_snapshot": {
                "query_key": "move_a",
                "func_num": 108,
                "writes": [{"start_vr": 100, "values": [108, 1]}],
            },
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["exec_duration_ms"] == 450


def test_operator_sequence_failure_does_not_clear_existing_exec_duration(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")

    dummy._operator_add_chat_from_log(
        {
            "category": "执行",
            "action": "发送准备 move_a",
            "result": "成功",
            "detail": "ready",
            "monotonic_ms": 1000,
        }
    )
    dummy._operator_add_chat_from_log(
        {
            "category": "执行",
            "action": "发送指令 move_a",
            "result": "失败",
            "detail": "镜像确认失败",
            "monotonic_ms": 1460,
        }
    )
    dummy._operator_add_chat_from_log(
        {
            "category": "自然语言",
            "action": "动作序列终止",
            "result": "失败",
            "detail": "镜像确认失败",
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "failure"
    assert payload["execution"]["exec_duration_ms"] == 460


def test_operator_add_chat_from_log_archives_system_command_success(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("暂停")

    dummy._operator_add_chat_from_log(
        {
            "category": "系统",
            "action": "系统命令 sys_pause",
            "result": "成功",
            "detail": "任务1001",
            "command_snapshot": {
                "action_key": "sys_pause",
                "code": 2,
                "func_num": 104,
                "writes": [
                    {"start_vr": 0, "values": [104]},
                    {"start_vr": 10, "values": [1]},
                ],
            },
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "success"
    assert payload["execution"]["modbus_write"]["action_key"] == "sys_pause"
    assert payload["execution"]["modbus_write"]["writes"][0]["start_vr"] == 0
    assert payload["execution"]["modbus_write"]["writes"][1]["values"] == [1]
    assert payload["response"]["final"] == "系统已暂停。"


def test_operator_add_chat_from_log_calculates_system_exec_duration(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("暂停")

    dummy._operator_add_chat_from_log(
        {
            "category": "系统",
            "action": "系统命令准备 sys_pause",
            "result": "成功",
            "detail": "code=2",
            "monotonic_ms": 2000,
        }
    )
    dummy._operator_add_chat_from_log(
        {
            "category": "系统",
            "action": "系统命令 sys_pause",
            "result": "成功",
            "detail": "任务1002",
            "monotonic_ms": 2125,
            "command_snapshot": {"action_key": "sys_pause", "writes": [{"start_vr": 0, "values": [104]}]},
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["exec_duration_ms"] == 125
    assert payload["execution"]["modbus_write"]["action_key"] == "sys_pause"


def test_operator_add_chat_from_log_calculates_failed_system_exec_duration(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("复位")

    dummy._operator_add_chat_from_log(
        {
            "category": "系统",
            "action": "系统命令准备 alarm_reset",
            "result": "成功",
            "detail": "code=5",
            "monotonic_ms": 3000,
        }
    )
    dummy._operator_add_chat_from_log(
        {
            "category": "系统",
            "action": "系统命令 alarm_reset",
            "result": "失败",
            "detail": "控制器无响应",
            "monotonic_ms": 3308,
            "command_snapshot": {"action_key": "alarm_reset", "writes": [{"start_vr": 0, "values": [104]}]},
        }
    )

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["execution"]["result"] == "failure"
    assert payload["execution"]["exec_duration_ms"] == 308
    assert payload["execution"]["modbus_write"]["action_key"] == "alarm_reset"


def test_operator_consume_pending_broadcasts_advances_delivery_cursor_by_max_sequence():
    dummy = DummyOperator()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_publish_response(ResponseMessage(kind="progress", text="预检中", priority="normal"))
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:3"))

    first_batch = dummy._operator_consume_pending_broadcasts_for_delivery()
    second_batch = dummy._operator_consume_pending_broadcasts_for_delivery()

    assert [message.text for message in first_batch] == ["报警", "预检中"]
    assert second_batch == []
    assert dummy._operator_last_delivered_broadcast_seq == 2


def test_operator_deliver_pending_broadcasts_to_speech_advances_cursor_after_success():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    spoken = []
    dummy.operator_speech_sink = CallableSpeechSink(spoken.append)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_publish_response(ResponseMessage(kind="progress", text="预检中", priority="normal"))
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:4"))
    dummy._operator_publish_response(
        ResponseMessage(kind="result", text="我是AI回答。", priority="normal", context_id="chat:ai_answer")
    )

    result = dummy._operator_deliver_pending_broadcasts_to_speech()
    second = dummy._operator_deliver_pending_broadcasts_to_speech()

    assert result.success is True
    assert second.delivered_seq == ()
    assert spoken == ["我是AI回答。"]
    assert dummy._operator_last_delivered_broadcast_seq == 3


def test_operator_new_ai_answer_replaces_pending_speech_queue():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    spoken = []
    dummy.operator_speech_sink = CallableSpeechSink(spoken.append)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_publish_ai_answer_for_speech("旧回答")

    dummy._operator_publish_ai_answer_for_speech("新回答")
    result = dummy._operator_deliver_pending_broadcasts_to_speech()

    assert result.success is True
    assert spoken == ["新回答"]


def test_operator_ai_answer_uses_generated_speech_summary_for_long_flow_text():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    shown = []
    dummy._operator_add_chat_message = lambda role, text: shown.append((role, text))
    dummy.operator_speech_sink = CallableSpeechSink(lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    text = "流程“点头”共 10 步：\n01 移动到位置A\n02 移动到位置B\n03 移动到home\n"

    dummy._operator_publish_ai_answer_for_speech(text)

    pending = dummy.operator_broadcast_queue.messages_since(0)
    assert shown == []
    assert pending[-1].text == text.strip()
    assert pending[-1].speech_text == "流程共10步，前几步是：移动到位置A；移动到位置B；移动到home。详情请看屏幕。"


def test_operator_empty_text_hint_is_shown_in_chat_without_modal_warning():
    dummy = DummyOperator()
    chats = []
    warnings = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._show_warning = lambda *args, **kwargs: warnings.append(args)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._operator_submit_nlp_text("", input_mode="text", add_user_message=True) is False

    assert warnings == []
    assert chats == [("assistant", "请输入自然语言文本，例如：小正，执行点头流程。")]
    assert dummy.status_text == "请输入自然语言文本，例如：小正，执行点头流程。"
    assert logs[-1][0:3] == ("自然语言", "输入校验", "失败")


def test_operator_empty_text_hint_mentions_last_voice_text_without_resending():
    dummy = DummyOperator()
    chats = []
    warnings = []
    dummy._operator_last_user_text = "小正执行点头流程"
    dummy.status_label = SimpleNamespace(setText=lambda text: setattr(dummy, "status_text", text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text))
    dummy._show_warning = lambda *args, **kwargs: warnings.append(args)
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._operator_submit_nlp_text("", input_mode="text", add_user_message=True) is False

    assert warnings == []
    assert chats == [("assistant", "当前输入框为空。上一句语音“小正执行点头流程”已收到，如需新指令请继续说或输入文本。")]


def test_operator_ai_answer_accepts_explicit_speech_text():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy.operator_speech_sink = CallableSpeechSink(lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None

    dummy._operator_publish_ai_answer_for_speech("完整回答文本", speech_text="短播报")

    pending = dummy.operator_broadcast_queue.messages_since(0)
    assert pending[-1].text == "完整回答文本"
    assert pending[-1].speech_text == "短播报"


def test_operator_new_ai_answer_stops_current_speech():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    stops = []

    class StopSink(CallableSpeechSink):
        def stop(self) -> None:
            stops.append("stop")

    dummy.operator_speech_sink = StopSink(lambda _text: None)

    dummy._operator_publish_ai_answer_for_speech("新回答")

    assert stops == ["stop"]


def test_operator_user_input_interrupts_current_speech_and_discards_pending():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    stops = []

    class StopSink(CallableSpeechSink):
        def stop(self) -> None:
            stops.append("stop")

    dummy.operator_speech_sink = StopSink(lambda _text: None)
    dummy._operator_publish_ai_answer_for_speech("旧回答")
    stops.clear()

    dummy._operator_interrupt_current_speech_for_user_input()
    pending = dummy._operator_pending_broadcasts_for_delivery(
        int(getattr(dummy, "_operator_last_delivered_broadcast_seq", 0) or 0)
    )

    assert stops == ["stop"]
    assert pending == []


def test_operator_stop_current_speech_for_user_voice_only_stops_audio():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    stops = []

    class StopSink(CallableSpeechSink):
        def stop(self) -> None:
            stops.append("stop")

    dummy.operator_speech_sink = StopSink(lambda _text: None)
    dummy._operator_publish_ai_answer_for_speech("完整回答文本")
    before_seq = int(getattr(dummy, "_operator_last_delivered_broadcast_seq", 0) or 0)
    stops.clear()

    dummy._operator_stop_current_speech_for_user_voice_only()
    pending = dummy._operator_pending_broadcasts_for_delivery(before_seq)

    assert stops == ["stop"]
    assert dummy._operator_current_spoken_text == "完整回答文本"
    assert [message.text for message in pending] == ["完整回答文本"]


def test_operator_deliver_pending_broadcasts_skips_all_speech_before_login():
    dummy = DummyOperator()
    dummy._authenticated_role = ""
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    spoken = []
    dummy.operator_speech_sink = CallableSpeechSink(spoken.append)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_publish_response(
        ResponseMessage(
            kind="result",
            text="用户输入为问候和询问功能，没有触发机械手动作。",
            priority="normal",
            context_id="chat:greeting",
        )
    )
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="急停已触发", priority="high", context_id="alarm:before-login"))

    result = dummy._operator_deliver_pending_broadcasts_to_speech()
    second = dummy._operator_deliver_pending_broadcasts_to_speech()

    assert result.success is True
    assert second.delivered_seq == ()
    assert spoken == []
    assert dummy._operator_last_delivered_broadcast_seq == 2


def test_operator_deliver_pending_broadcasts_to_speech_keeps_cursor_on_failure():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    logs = []

    def fail(_text: str) -> None:
        raise RuntimeError("speaker offline")

    dummy.operator_speech_sink = CallableSpeechSink(fail)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_publish_response(
        ResponseMessage(kind="result", text="我是AI回答。", priority="normal", context_id="chat:ai_answer")
    )

    result = dummy._operator_deliver_pending_broadcasts_to_speech()

    assert result.success is False
    assert dummy._operator_last_delivered_broadcast_seq == 0
    assert log_args(logs[-1])[0:3] == ("语音播报", "主动播报", "失败")


def test_operator_enable_local_tts_sets_pyttsx3_sink():
    dummy = DummyOperator()
    engine = object()

    sink = dummy._operator_enable_local_tts(engine=engine)

    assert isinstance(sink, Pyttsx3SpeechSink)
    assert dummy.operator_speech_sink is sink


def test_operator_configure_tts_from_settings_enables_sink_when_configured(monkeypatch):
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "local")
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=True)

    sink = dummy._operator_configure_tts_from_settings()

    assert isinstance(sink, (Pyttsx3SpeechSink, WindowsSapiSpeechSink))
    assert dummy.operator_speech_sink is sink


def test_operator_configure_tts_from_settings_disables_sink_when_configured_off():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=False)
    dummy.operator_speech_sink = CallableSpeechSink(lambda _text: None)

    sink = dummy._operator_configure_tts_from_settings()

    assert sink is None
    assert dummy.operator_speech_sink is None


def test_operator_publish_response_uses_configured_dedupe_window():
    dummy = DummyOperator()
    now = [0.0]
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), broadcast_dedupe_window_sec=1.0)
    dummy.operator_broadcast_queue = BroadcastQueue(clock=lambda: now[0])
    dummy._operator_add_chat_message = lambda *args, **kwargs: None

    first = dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:6"))
    duplicate = dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:6"))
    now[0] = 1.1
    later = dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:6"))

    assert first is not None
    assert duplicate is None
    assert later is not None


def test_operator_publish_response_filters_automatic_status_from_chat():
    dummy = DummyOperator()
    chat_messages = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    published = [
        dummy._operator_publish_response(
            ResponseMessage(kind="alert", text="通讯异常：connect failed", priority="high", context_id="connection:failed")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="alert", text="报警发生，报警码 ERR_000。", priority="high", context_id="feedback:alarm:ERR_000")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="result", text="通讯状态已恢复，实时反馈在线。", context_id="feedback:comm:recovered")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="result", text="报警状态已解除，当前无报警。", context_id="dashboard:alarm:cleared")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="result", text="通讯状态已恢复，实时反馈在线。", context_id="dashboard:comm:online")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="alert", text="轴状态异常，当前轴状态 1 / 1。", context_id="dashboard:axis_status:abnormal")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="progress", text="进入执行场景，正在跟踪动作进度。", context_id="operator_scene:execute")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="progress", text="设备状态正常，通讯正常，当前任务仍在处理。", context_id="operator:periodic_reassurance")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="progress", text="本地规则未完全匹配，正在调用在线AI辅助识别中。", context_id="deepseek:fallback")
        ),
        dummy._operator_publish_response(
            ResponseMessage(kind="result", text="在线AI已匹配到：unknown:-，正在进入安全链路。", context_id="deepseek:success")
        ),
    ]

    assert all(item is not None for item in published)
    assert chat_messages == []


def test_operator_publish_response_filters_flow_step_completion_from_chat():
    dummy = DummyOperator()
    chat_messages = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    published = dummy._operator_publish_response(
        ResponseMessage(
            kind="result",
            text="动作执行完成：flowdraft:home点头流程:01。",
            context_id="six_axis:flowdraft:home点头流程:01:completed",
        )
    )

    assert published is not None
    assert chat_messages == []


def test_operator_initial_chat_messages_are_empty():
    assert DummyOperator._operator_initial_chat_messages() == []


def test_operator_status_text_is_compacted_for_footer():
    long_text = "当前系统已加载20个模板命令，" + "包括位置A、位置B、默认命令，" * 12

    compacted = DummyOperator._operator_footer_status_text(long_text)

    assert len(compacted) <= 90
    assert compacted.endswith("...")


def test_operator_publish_response_keeps_chat_answers_in_chat():
    dummy = DummyOperator()
    chat_messages = []
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chat_messages.append((role, text))

    published = dummy._operator_publish_response(
        ResponseMessage(kind="result", text="我是机械手自然语言交互系统的问答助手。", context_id="chat:identity")
    )

    assert published is not None
    assert chat_messages == [("assistant", "我是机械手自然语言交互系统的问答助手。")]


def test_operator_auto_deliver_broadcasts_only_runs_when_tts_enabled():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    spoken = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=False)
    dummy.operator_speech_sink = CallableSpeechSink(spoken.append)
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_publish_response(
        ResponseMessage(kind="result", text="我是AI回答。", priority="normal", context_id="chat:ai_answer")
    )

    disabled_result = dummy._operator_auto_deliver_broadcasts()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=True)
    enabled_result = dummy._operator_auto_deliver_broadcasts()

    assert disabled_result is None
    assert enabled_result is not None
    assert spoken == ["我是AI回答。"]


def test_operator_auto_deliver_broadcasts_queues_real_tts_without_blocking():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=True)
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    logs = []
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    calls = []

    class SlowVoice:
        def Speak(self, text: str) -> None:
            calls.append(text)
            time.sleep(0.2)

    dummy.operator_speech_sink = WindowsSapiSpeechSink(dispatch_factory=lambda _name: SlowVoice())
    dummy._operator_publish_response(
        ResponseMessage(kind="result", text="这是一条较长的AI回答。", priority="normal", context_id="chat:ai_answer")
    )

    started = time.perf_counter()
    result = dummy._operator_auto_deliver_broadcasts()
    elapsed = time.perf_counter() - started

    assert result is not None
    assert result.success is True
    assert elapsed < 0.1
    assert result.delivered_seq == (1,)
    assert dummy._operator_last_delivered_broadcast_seq == 1
    assert log_args(logs[-1])[0:3] == ("语音播报", "主动播报", "成功")
    assert "后台播报" in log_args(logs[-1])[3]


def test_operator_set_tts_enabled_updates_config_and_persists(tmp_path: Path):
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=False)
    dummy.system_config_path = tmp_path / "system_config.json"
    status_messages = []
    logs = []
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    dummy._operator_set_tts_enabled(True)

    assert dummy.axis_ranges.operator_tts_enabled is True
    assert "语音播报已启用" in status_messages[-1]
    assert log_args(logs[-1])[0:3] == ("用户页面", "语音播报开关", "成功")
    assert '"operator_tts_enabled": true' in dummy.system_config_path.read_text(encoding="utf-8")


def test_operator_auto_deliver_broadcasts_backs_off_after_failure():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    now = [10.0]
    logs = []

    def fail(_text: str) -> None:
        raise RuntimeError("speaker offline")

    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        operator_tts_enabled=True,
        tts_retry_delay_sec=2.0,
        tts_max_failures=3,
    )
    dummy.operator_speech_sink = CallableSpeechSink(fail)
    dummy._operator_now_seconds = lambda: now[0]
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_publish_response(
        ResponseMessage(kind="result", text="我是AI回答。", priority="normal", context_id="chat:ai_answer")
    )

    first = dummy._operator_auto_deliver_broadcasts()
    immediate_retry = dummy._operator_auto_deliver_broadcasts()
    now[0] = 12.1
    delayed_retry = dummy._operator_auto_deliver_broadcasts()

    assert first is not None and first.success is False
    assert immediate_retry is None
    assert delayed_retry is not None and delayed_retry.success is False
    assert dummy._operator_tts_failure_count == 2
    assert log_args(logs[-1])[0:3] == ("语音播报", "主动播报", "失败")


def test_operator_auto_deliver_broadcasts_disables_tts_after_max_failures():
    dummy = DummyOperator()
    dummy._authenticated_role = "operator"
    logs = []
    warnings = []
    status_messages = []
    chat_messages = []

    def fail(_text: str) -> None:
        raise RuntimeError("speaker offline")

    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        operator_tts_enabled=True,
        tts_retry_delay_sec=0.0,
        tts_max_failures=1,
    )
    dummy.operator_speech_sink = CallableSpeechSink(fail)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_add_chat_message = lambda *args, **kwargs: chat_messages.append(args)
    dummy._show_warning = lambda title, detail: warnings.append((title, detail))
    dummy.status_label = SimpleNamespace(setText=status_messages.append)
    dummy._operator_publish_response(
        ResponseMessage(kind="result", text="我是AI回答。", priority="normal", context_id="chat:ai_answer")
    )

    result = dummy._operator_auto_deliver_broadcasts()

    assert result is not None and result.success is False
    assert dummy.axis_ranges.operator_tts_enabled is False
    assert dummy.operator_speech_sink is None
    assert "语音播报连续失败" in status_messages[-1]
    assert warnings[-1][0] == "语音播报已自动暂停"
    assert "speaker offline" in warnings[-1][1]
    assert chat_messages[-1][1].startswith("语音播报连续失败")
    assert log_args(logs[-1])[0:3] == ("语音播报", "自动暂停", "失败")


def test_operator_tts_toggle_slot_uses_checkbox_state():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=False)
    calls = []
    dummy._operator_set_tts_enabled = calls.append

    dummy._operator_on_tts_toggled(True)

    assert calls == [True]


def test_operator_archive_text_input_writes_interaction_record(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_scene_state = OperatorSceneState(
        current="confirm",
        previous="precheck",
        reason="operator_apply_scene",
        changed_at=123.4,
    )
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "ts": "2026-05-20T10:30:00.050+08:00",
        "position": {},
        "safety": {"estop": False, "alarm_active": False},
        "motion": {},
        "connection": {"realtime_feedback": "online"},
        "hardware": {},
    }

    record = dummy._operator_archive_text_input("移动到安全点")

    assert record["msg_type"] == "interaction_record"
    assert record["session_id"] == "session-1"
    assert record["input"]["raw_text"] == "移动到安全点"
    assert record["input"]["normalized_text"] == "移动到安全点"
    assert record["input"]["scene_state"] == {
        "current": "confirm",
        "previous": "precheck",
        "reason": "operator_apply_scene",
        "changed_at": 123.4,
    }
    assert (tmp_path / "interaction_session_session-1.jsonl").exists()
    dialog_files = list((tmp_path / "dialogs").glob("dialog_*.jsonl"))
    assert len(dialog_files) == 1
    assert "移动到安全点" in dialog_files[0].read_text(encoding="utf-8")


def test_operator_archive_text_input_writes_normalized_text(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}

    record = dummy._operator_archive_text_input("我觉得坐标是 X 一百 Y零 Z一百 速度 五十")

    assert record["input"]["raw_text"] == "我觉得坐标是 X 一百 Y零 Z一百 速度 五十"
    assert record["input"]["normalized_text"] == "我觉得坐标是 X100 Y0 Z100 速度50"


def test_operator_archive_text_input_writes_asr_normalized_text(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}

    record = dummy._operator_archive_text_input("夫位")

    assert record["input"]["raw_text"] == "夫位"
    assert record["input"]["normalized_text"] == "复位"


def test_operator_archive_nlp_result_updates_last_interaction_record(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "spd_pct": 20.0},
        )
    }
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "移动", "测试"),),
        source="rule",
        raw_text="移动",
        reason="测试",
    )

    updated = dummy._operator_archive_nlp_result(plan)

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["nlp_result"]["intent"] == "command"
    assert payload["nlp_result"]["raw_text"] == "移动"
    assert payload["nlp_result"]["normalized_text"] == "移动"
    assert payload["nlp_result"]["func_id"] == 108
    assert payload["nlp_result"]["params"]["target_x"] == 1.0
    assert payload["nlp_result"]["command_intent"]["msg_type"] == "command_intent"
    assert payload["nlp_result"]["command_intent"]["func_id"] == 108


def test_operator_archive_safety_check_updates_last_interaction_record(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")
    dummy._operator_last_precheck_result = {"status": "pass", "items": [{"id": "estop", "status": "pass"}]}
    dummy._operator_last_motion_plan_result = {"status": "unavailable", "items": [], "suggestion": "未配置逆解"}
    dummy._operator_last_process_precheck_result = {"status": "fail", "items": [{"id": "timing_state", "status": "fail"}]}

    updated = dummy._operator_archive_safety_check()

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["safety_check"]["pc_precheck"] == "fail"
    assert payload["safety_check"]["pc_precheck_detail"]["l1"]["status"] == "pass"
    assert payload["safety_check"]["pc_precheck_detail"]["l2"]["status"] == "unavailable"
    assert payload["safety_check"]["pc_precheck_detail"]["l3"]["status"] == "fail"
    assert payload["safety_check"]["warnings"] == ["未配置逆解"]


def test_operator_archive_safety_check_preserves_l2_robot_safety_detail(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动到位置A")
    dummy._operator_last_precheck_result = {"status": "pass", "items": []}
    dummy._operator_last_motion_plan_result = {
        "status": "fail",
        "items": [{"id": "find_best_fstatus", "status": "fail", "message": "未找到满足关节限位的 FSTATUS。"}],
        "robot_safety": {
            "safe": False,
            "position_ok": True,
            "ik_ok": False,
            "pose_ok": None,
            "blocking_level": "L2",
            "detail_zh": "L2逆解预判未通过：未找到满足关节限位的 FSTATUS。",
            "suggestion_zh": "请调整目标位姿或补充中间点后重试。",
        },
    }
    dummy._operator_last_process_precheck_result = None

    updated = dummy._operator_archive_safety_check()

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    l2 = payload["safety_check"]["pc_precheck_detail"]["l2"]
    assert updated is True
    assert payload["safety_check"]["pc_precheck"] == "fail"
    assert l2["status"] == "fail"
    assert l2["robot_safety"]["position_ok"] is True
    assert l2["robot_safety"]["ik_ok"] is False
    assert l2["robot_safety"]["pose_ok"] is None
    assert l2["robot_safety"]["blocking_level"] == "L2"
    assert "L2逆解预判未通过" in l2["robot_safety"]["detail_zh"]


def test_operator_nlp_result_payload_uses_plan_semantic_metadata():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("system", "sys_cancel", "rule", "取消当前任务", "测试"),),
        source="rule",
        raw_text="取消当前任务",
        reason="测试",
        semantic_level=4,
        semantic_label="系统管理层",
        response_deadline_ms=2000,
        requires_precheck=False,
        requires_confirmation=False,
        priority="normal",
        tokens=("小正", "取消"),
        nlp_engine="jieba_rule",
    )
    dummy.table = {}

    payload = dummy._operator_nlp_result_payload(plan)

    assert payload["semantic_level"] == 4
    assert payload["semantic_label"] == "系统管理层"
    assert payload["response_deadline_ms"] == 2000
    assert payload["requires_precheck"] is False
    assert payload["requires_confirmation"] is False
    assert payload["priority"] == "normal"
    assert payload["tokens"] == ["小正", "取消"]
    assert payload["engine"] == "jieba_rule"
    assert payload["command_intent"]["semantic_level"] == 4


def test_operator_nlp_result_payload_uses_plan_normalized_text_when_available():
    dummy = DummyOperator()
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("template", "move_pose", "rule", "X 一百", "测试"),),
        source="rule",
        raw_text="X 一百",
        normalized_text="X100",
        reason="测试",
    )
    dummy.table = {
        "move_pose": QueryRecord(
            query_key="move_pose",
            func_num=108,
            params={"target_x": 100.0},
        )
    }

    payload = dummy._operator_nlp_result_payload(plan)

    assert payload["raw_text"] == "X 一百"
    assert payload["normalized_text"] == "X100"


def test_operator_nlp_result_payload_promotes_atomic_risk_metadata():
    dummy = DummyOperator()
    record = QueryRecord(
        query_key="atomic:virtual:8:1:30",
        func_num=107,
        params={
            "axis_no": 8,
            "pos_val": 30.0,
            "atomic_risk_level": "high",
            "atomic_risk_reason": "速度、加减速或步长较高。",
        },
    )
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", record.query_key, "atomic_rule", "上升30毫米", "虚拟轴原子动作"),),
        source="atomic_rule",
        raw_text="小正，上升30毫米",
        reason="虚拟轴原子动作",
        atomic_records={record.query_key: record},
        requires_confirmation=True,
    )
    dummy.table = {}

    payload = dummy._operator_nlp_result_payload(plan)

    assert payload["risk_level"] == "high"
    assert payload["risk_reason"] == "速度、加减速或步长较高。"


def test_operator_archive_execution_result_updates_last_interaction_record(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("移动")

    updated = dummy._operator_archive_execution_result(result="blocked", final_text="L1预检未通过。")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["execution"]["result"] == "blocked"
    assert payload["response"]["final"] == "L1预检未通过。"


def test_operator_archive_execution_result_writes_complete_dialogue_record(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._session_start_perf = time.perf_counter()
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._controller_mode_value = lambda: "real"
    dummy.host_edit = SimpleNamespace(text=lambda: "10.168.3.21")
    dummy._operator_archive_text_input("小正，移动到位置A")

    updated = dummy._archive_non_execution_result(result="clarification", final_text="请明确位置A的坐标。")

    payload = json.loads((tmp_path / "dialogue_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["msg_type"] == "dialogue_record"
    assert payload["session_id"] == "session-1"
    assert payload["host"] == "10.168.3.21"
    assert payload["controller_mode"] == "real"
    assert payload["category"] == "自然语言"
    assert payload["action"] == "澄清提示"
    assert payload["result"] == "提示"
    assert payload["detail"] == "请明确位置A的坐标。"
    assert payload["user"]["raw_text"] == "小正，移动到位置A"
    assert payload["assistant"]["final_text"] == "请明确位置A的坐标。"
    assert payload["execution"]["non_execution_result"] == "clarification"


def test_operator_archive_non_execution_result_marks_skipped_and_final(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("你好")

    updated = dummy._archive_non_execution_result(result="chat", final_text="你好，我可以解释系统状态。")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["execution"]["result"] == "skipped"
    assert payload["execution"]["non_execution_result"] == "chat"
    assert payload["response"]["final"] == "你好，我可以解释系统状态。"


def test_operator_archive_non_execution_result_finalizes_pending_nlp_result(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._operator_archive_text_input("你好")

    updated = dummy._archive_non_execution_result(result="streaming_chat", final_text="你好。")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["nlp_result"]["engine"] == "streaming_chat"
    assert payload["nlp_result"]["intent"] == "chat"
    assert payload["nlp_result"]["confidence"] == 1.0
    assert payload["execution"]["result"] == "skipped"
    assert payload["response"]["final"] == "你好。"


def test_operator_set_pending_confirm_plan_updates_session_state(tmp_path: Path):
    dummy = make_context_operator(tmp_path)
    dummy.session_id = "ui-session-1"
    dummy._operator_now_seconds = lambda: 10.0
    dummy._operator_confirm_timeout_seconds = lambda: 60.0
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
    )

    dummy._operator_set_pending_confirm_plan(plan)

    state = dummy._operator_session_state()
    assert state.mode == "waiting_confirm"
    assert state.pending_confirm["plan_id"]
    assert state.pending_confirm["expires_at"] == 70.0

    dummy._operator_set_pending_confirm_plan(None)

    cleared = dummy._operator_session_state()
    assert cleared.mode == "idle"
    assert cleared.pending_confirm == {}


def test_operator_expire_pending_confirm_marks_session_state_expired(tmp_path: Path):
    dummy = make_context_operator(tmp_path)
    dummy.session_id = "ui-session-1"
    dummy._operator_now_seconds = lambda: 10.0
    dummy._operator_confirm_timeout_seconds = lambda: 60.0
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "move", "test", "X100", "等待确认"),),
        source="test",
        raw_text="X100",
        reason="等待确认",
        requires_confirmation=True,
    )
    dummy._operator_set_pending_confirm_plan(plan)

    dummy._operator_expire_pending_confirm(refresh=False)

    state = dummy._operator_session_state()
    assert state.mode == "confirm_expired"
    assert state.pending_confirm == {}


def test_operator_set_pending_flow_draft_updates_session_state(tmp_path: Path):
    dummy = make_context_operator(tmp_path)
    dummy.session_id = "ui-session-1"
    draft = {"flow_name": "测试", "expanded_steps": []}

    dummy._operator_set_pending_flow_draft(draft)

    state = dummy._operator_session_state()
    assert dummy._operator_pending_flow_draft == draft
    assert state.mode == "editing_flow"
    assert state.current_flow_draft["flow_name"] == "测试"

    dummy._operator_set_pending_flow_draft(None)

    cleared = dummy._operator_session_state()
    assert cleared.mode == "idle"
    assert cleared.current_flow_draft == {}


def test_operator_dashboard_query_archives_final_response(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._set_workspace_mode = lambda _mode: None
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_dashboard_snapshot_dict = lambda: {
        "boards": {
            "action_feasibility": {
                "channel_idle": False,
                "precheck_status": "fail",
                "motion_status": "fail",
            }
        }
    }
    dummy._operator_archive_text_input("为什么不能执行，建议怎么处理")

    handled = dummy._operator_handle_dashboard_query("为什么不能执行，建议怎么处理")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert handled is True
    assert payload["execution"]["result"] == "answered"
    assert payload["nlp_result"]["engine"] == "answered"
    assert payload["nlp_result"]["intent"] == "answered"
    assert payload["response"]["final"].startswith("当前不建议执行。原因：")
    assert "建议：" in payload["response"]["final"]


def test_operator_engineer_low_risk_voice_command_archives_success(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy._authenticated_role = "engineer"
    dummy.workspace_pages = SimpleNamespace(setCurrentIndex=lambda _index: None)
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._show_page = lambda _index: None
    dummy._read_feedback = lambda: None
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("读取控制器反馈") is True

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["input"]["source"] == "engineer_voice"
    assert payload["input"]["raw_text"] == "读取控制器反馈"
    assert payload["nlp_result"]["semantic_level"] == 4
    assert payload["nlp_result"]["intent"] == "engineer_command"
    assert payload["nlp_result"]["action_type"] == "read_feedback"
    assert payload["execution"]["result"] == "success"


def test_operator_engineer_confirm_voice_command_archives_waiting_and_success(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._save_system_config = lambda: None

    assert dummy._handle_operator_ui_command("保存系统参数") is True
    assert dummy._handle_operator_ui_command("确认工程师操作") is True

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["input"]["source"] == "engineer_voice"
    assert payload["input"]["raw_text"] == "保存系统参数"
    assert payload["execution"]["result"] == "success"
    assert payload["response"]["final"] == "工程师语音操作已确认执行：保存配置。"


def test_operator_engineer_danger_voice_command_archives_blocked(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_publish_response = lambda _message: None
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("删除流程") is True

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["input"]["source"] == "engineer_voice"
    assert payload["execution"]["result"] == "blocked"
    assert "未开放语音直接执行" in payload["response"]["final"]


def test_operator_ui_command_answers_pending_engineer_confirm_query():
    dummy = DummyOperator()
    messages = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("保存系统参数") is True
    assert dummy._handle_operator_ui_command("当前待确认后台操作是什么") is True

    assert messages[-1].kind == "result"
    assert messages[-1].priority == "normal"
    assert "保存配置" in messages[-1].text
    assert "确认工程师操作" in messages[-1].text
    assert status["text"] == messages[-1].text


def test_operator_ui_command_answers_no_pending_engineer_confirm_query():
    dummy = DummyOperator()
    messages = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: None

    assert dummy._handle_operator_ui_command("工程师操作等我确认吗") is True

    assert messages[-1].kind == "result"
    assert "没有待确认" in messages[-1].text
    assert status["text"] == messages[-1].text


def test_operator_ui_command_answers_engineer_voice_capability_query():
    dummy = DummyOperator()
    messages = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("现在语音能控制哪些工程师操作") is True

    text = messages[-1].text
    assert messages[-1].kind == "result"
    assert "可直接语音执行" in text
    assert "需二次确认" in text
    assert "拒绝语音执行" in text
    assert "仅清单保留" in text
    assert "后台" in text
    assert "保存配置" in text
    assert status["text"] == DummyOperator._operator_footer_status_text(text)
    assert logs[-1][1] == "能力查询"


def test_operator_ui_command_answers_atomic_capability_query():
    dummy = DummyOperator()
    messages = []
    logs = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)

    assert dummy._handle_operator_ui_command("现在支持哪些原子命令") is True

    text = messages[-1].text
    assert messages[-1].kind == "result"
    assert "二次原子函数能力" in text
    assert "J 类关节命令" in text
    assert "保护性拒绝" in text
    assert status["text"] == DummyOperator._operator_footer_status_text(text)
    assert logs[-1][1] == "原子能力查询"


def test_operator_busy_rejection_archives_blocked_response(tmp_path: Path):
    dummy = DummyOperator()
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy.operator_broadcast_queue = BroadcastQueue()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy.busy = "运行中"
    dummy.run_state = "空闲"
    dummy.flow_running = False
    dummy.nlp_sequence_running = False
    dummy._operator_archive_text_input("移动到A点")

    rejected = dummy._operator_reject_new_action_while_busy("移动到A点")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rejected is True
    assert payload["execution"]["result"] == "blocked"
    assert payload["response"]["final"] == "当前任务未完成，已拒绝新的动作指令。可查询进度、暂停、继续、停止流程或使用应急编码。"


def test_operator_wake_command_while_flow_running_pauses_and_prompts_choice():
    dummy = DummyOperator()
    messages = []
    logs = []
    actions = []
    status = {"text": ""}
    dummy.status_label = SimpleNamespace(setText=lambda text: status.update(text=text))
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._refresh_operator_view = lambda: None
    dummy._handle_system_action = lambda action_key: actions.append(action_key)
    dummy.flow_running = True
    dummy.nlp_sequence_running = False
    dummy.busy = "运行中"
    dummy.run_state = "运行中"

    handled = dummy._operator_begin_busy_interruption("小正执行点头流程")

    assert handled is True
    assert actions == ["sys_pause"]
    assert dummy._operator_pending_interruption_text == "小正执行点头流程"
    assert "继续当前流程" in messages[-1].text
    assert "清除上一次流程并执行新的流程" in messages[-1].text
    assert status["text"] == "当前流程已暂停，等待用户选择。"
    assert logs[-1][1] == "新指令打断流程"


def test_operator_stale_pause_status_without_active_task_does_not_interrupt_new_wake_command():
    dummy = DummyOperator()
    dummy.flow_running = False
    dummy.nlp_sequence_running = False
    dummy.pause_active = False
    dummy.busy = "暂停"
    dummy.run_state = "暂停"

    handled = dummy._operator_reject_new_action_while_busy("小正，走到X1000等待2秒然后走到X1500")

    assert handled is False
    assert getattr(dummy, "_operator_pending_interruption_text", "") == ""


def test_operator_stale_pending_interruption_is_cleared_when_no_active_task():
    dummy = DummyOperator()
    dummy._operator_pending_interruption_text = "小正执行旧流程"
    dummy.flow_running = False
    dummy.nlp_sequence_running = False
    dummy.pause_active = False
    dummy.busy = "空闲"
    dummy.run_state = "空闲"

    handled = dummy._operator_handle_pending_interruption_command("小正，走到X1000等待2秒然后走到X1500")

    assert handled is False
    assert dummy._operator_pending_interruption_text == ""


def test_operator_pending_interruption_continue_resumes_current_flow():
    dummy = DummyOperator()
    messages = []
    actions = []
    dummy._operator_pending_interruption_text = "小正执行新流程"
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._handle_system_action = lambda action_key: actions.append(action_key)
    dummy.flow_running = True

    handled = dummy._handle_operator_ui_command("继续当前流程")

    assert handled is True
    assert actions == ["sys_resume"]
    assert dummy._operator_pending_interruption_text == ""
    assert "继续当前流程" in messages[-1].text


def test_operator_pending_interruption_clear_previous_executes_new_command():
    dummy = DummyOperator()
    messages = []
    stopped = []
    executed = []
    dummy._operator_pending_interruption_text = "小正执行新流程"
    dummy._operator_publish_response = lambda message: messages.append(message)
    dummy._operator_archive_execution_result = lambda *args, **kwargs: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._stop_flow = lambda: stopped.append(True)
    dummy._operator_execute_interruption_text = lambda text: executed.append(text)
    dummy.flow_running = True

    handled = dummy._handle_operator_ui_command("清除上一次流程并执行新的流程")

    assert handled is True
    assert stopped == [True]
    assert executed == ["小正执行新流程"]
    assert dummy._operator_pending_interruption_text == ""
    assert "已停止上一次流程" in messages[-1].text


def test_operator_archive_execution_result_records_state_before_and_after(tmp_path: Path):
    dummy = DummyOperator()
    snapshots = [
        {
            "ts": "2026-05-20T10:30:00.000+08:00",
            "position": {"x": 1.0, "y": 2.0, "z": 3.0, "joints": (1, 2, 3, 4, 5, 6)},
            "safety": {"estop": False, "paused": False, "alarm_active": False, "alarm_code": "0"},
            "motion": {"running_state": "idle", "current_func": "-"},
            "connection": {"realtime_feedback": "offline"},
            "hardware": {},
        },
        {
            "ts": "2026-05-20T10:30:00.200+08:00",
            "position": {"x": 4.0, "y": 5.0, "z": 6.0, "joints": (6, 5, 4, 3, 2, 1)},
            "safety": {"estop": False, "paused": False, "alarm_active": False, "alarm_code": "0"},
            "motion": {"running_state": "running", "current_func": "108"},
            "connection": {"realtime_feedback": "online"},
            "hardware": {},
        },
    ]
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_dashboard_snapshot_dict = lambda: snapshots[0]
    dummy._operator_archive_text_input("移动")
    dummy._operator_dashboard_snapshot_dict = lambda: snapshots[1]

    updated = dummy._operator_archive_execution_result(result="success", final_text="动作完成。")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["execution"]["state_before"]["data"]["system_state"] == "idle"
    assert payload["execution"]["state_before"]["data"]["ready"] is False
    assert payload["execution"]["state_before"]["data"]["dpos_c"] == [1.0, 2.0, 3.0]
    assert payload["execution"]["state_after"]["data"]["system_state"] == "running"
    assert payload["execution"]["state_after"]["data"]["ready"] is True
    assert payload["execution"]["state_after"]["data"]["dpos_c"] == [4.0, 5.0, 6.0]


def test_operator_publish_receipt_archives_ack_delay(tmp_path: Path):
    dummy = DummyOperator()
    now = [10.0]
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy.operator_broadcast_queue = BroadcastQueue(clock=lambda: now[0])
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_archive_text_input("移动")

    now[0] = 10.04
    dummy._operator_publish_response(ResponseMessage(kind="receipt", text="收到，正在处理。", priority="normal"))

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["response"]["ack"] == "收到，正在处理。"
    assert payload["response"]["ack_delay_ms"] == 40
    assert payload["response"]["ack_limit_ms"] == 50
    assert payload["response"]["ack_sla_passed"] is True


def test_operator_text_receipt_archives_sla_failure_when_over_50ms(tmp_path: Path):
    dummy = DummyOperator()
    now = [10.0]
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy.operator_broadcast_queue = BroadcastQueue(clock=lambda: now[0])
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_archive_text_input("移动")

    now[0] = 10.051
    dummy._operator_publish_response(ResponseMessage(kind="receipt", text="收到，正在处理。", priority="normal"))

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["response"]["ack_delay_ms"] == 51
    assert payload["response"]["ack_limit_ms"] == 50
    assert payload["response"]["ack_sla_passed"] is False


def test_operator_voice_receipt_records_200ms_acceptance_delay():
    dummy = DummyOperator()
    now = [20.0]
    published = []
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_voice_recording_active = lambda: False
    dummy.operator_response_builder = ResponseBuilder()
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy._sync_operator_mic_button = lambda: None

    def start_session():
        now[0] = 20.12

    dummy._start_voice_session = start_session

    dummy._operator_toggle_microphone_recording()

    assert published == []
    assert dummy._operator_last_voice_receipt_delay_ms == 120
    assert dummy._operator_last_voice_receipt_sla_passed is True


def test_operator_voice_receipt_sla_result_reports_overdue_delay():
    dummy = DummyOperator()
    dummy._operator_last_voice_receipt_delay_ms = 250
    dummy._operator_last_voice_receipt_sla_passed = False

    result = dummy._operator_voice_receipt_sla_result()

    assert result == {
        "ack_delay_ms": 250,
        "ack_limit_ms": 200,
        "ack_sla_passed": False,
    }


def test_operator_confirm_stage_modify_speed_updates_pending_plan():
    dummy = DummyOperator()
    logs = []
    record = QueryRecord(
        query_key="atomic:virtual:8:1:3",
        func_num=107,
        keywords="上升3毫米",
        description="原子函数：虚拟轴点动",
        safety_level=5,
        params={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", record.query_key, "rule", "小正，上升3毫米", "测试"),),
        source="rule",
        raw_text="小正，上升3毫米",
        reason="测试",
        atomic_records={record.query_key: record},
    )
    dummy.status_label = SimpleNamespace(setText=lambda text: logs.append(("status", text)))
    dummy._operator_add_chat_message = lambda *args: logs.append(args)
    dummy._append_log = lambda *args: logs.append(args)
    dummy._refresh_operator_view = lambda: logs.append(("refresh",))
    dummy._operator_prepare_plan_prechecks = lambda plan: logs.append(("precheck", plan.atomic_records[record.query_key].params["spd_pct"]))

    handled = dummy._handle_operator_ui_command("速度改成30")

    assert handled is True
    assert record.params["spd_pct"] == 30.0
    assert record.params["acc_pct"] == 30.0
    assert record.params["dec_pct"] == 30.0
    assert ("precheck", 30.0) in logs


def test_operator_confirm_stage_modify_step_updates_pending_plan():
    dummy = DummyOperator()
    record = QueryRecord(
        query_key="atomic:virtual:8:1:3",
        func_num=107,
        keywords="上升3毫米",
        description="原子函数：虚拟轴点动",
        safety_level=5,
        params={"axis_no": 8, "pos_val": 3.0, "spd_pct": 50.0},
    )
    dummy._operator_pending_confirm_plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", record.query_key, "rule", "小正，上升3毫米", "测试"),),
        source="rule",
        raw_text="小正，上升3毫米",
        reason="测试",
        atomic_records={record.query_key: record},
    )
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._operator_add_chat_message = lambda *args: None
    dummy._append_log = lambda *args: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_prepare_plan_prechecks = lambda _plan: None

    handled = dummy._handle_operator_ui_command("步长改成5毫米")

    assert handled is True
    assert record.params["pos_val"] == 5.0


def test_flow_management_start_rehearsal_audits_success():
    dummy = DummyFlowManager()
    logs = []
    dummy.flow_manage_name_edit = SimpleNamespace(text=lambda: "F")
    dummy.status_label = SimpleNamespace(setText=lambda text: logs.append(("status", text)))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._show_warning = lambda title, detail: logs.append(("warning", title, detail))
    dummy._current_permission_actor = lambda: "engineer"
    dummy.service = SimpleNamespace(start_flow_rehearsal=lambda name: (True, f"流程'{name}'演练模式启动，速度20%"))

    dummy._start_flow_rehearsal()

    assert ("后台", "演练流程", "成功", "流程'F'演练模式启动，速度20%") in logs


def test_flow_management_tree_columns_include_structured_entry_metadata():
    dummy = DummyFlowManager()
    entry = FlowEntry(
        name="打招呼",
        steps=[FlowStep(step_id=1, action="回home", func_id=108)],
        step_delay_ms=250,
        rehearsal_spd=20,
        confirmed=True,
        version=3,
        state="ready",
    )
    legacy = FlowDefinition(name="打招呼", steps=("legacy_home",), step_delay_ms=1000)
    dummy.service = SimpleNamespace(
        get_flow=lambda _name: legacy,
        get_flow_entry=lambda _name: entry,
    )

    columns = dummy._flow_manage_tree_columns("打招呼")

    assert columns[0] == "打招呼"
    assert "1步" in columns[1]
    assert "250ms" in columns[1]
    assert "v3" in columns[1]
    assert "已确认" in columns[1]
    assert "演练20%" in columns[1]
    assert "ready" in columns[1]


def test_flow_management_step_labels_support_structured_flow_steps():
    dummy = DummyFlowManager()
    entry = FlowEntry(
        name="打招呼",
        steps=[
            FlowStep(step_id=1, action="回home", func_id=108, params={"query_key": "home_move"}),
            FlowStep(step_id=2, action="小臂点头", func_id=107, description="Ry正转15度"),
        ],
    )

    labels = dummy._flow_manage_step_labels(entry)

    assert labels == ["home_move", "flow:打招呼:2"]


def test_flow_management_load_form_prefers_structured_entry_delay_and_steps():
    dummy = DummyFlowManager()
    loaded_steps = []
    dummy.flow_manage_name_edit = SimpleNamespace(value="", setText=lambda text: setattr(dummy.flow_manage_name_edit, "value", text))
    dummy.flow_manage_delay_edit = SimpleNamespace(value="", setText=lambda text: setattr(dummy.flow_manage_delay_edit, "value", text))
    dummy._refresh_flow_step_manage_tree = lambda steps: loaded_steps.extend(steps)
    entry = FlowEntry(
        name="打招呼",
        steps=[FlowStep(step_id=1, action="回home", func_id=108)],
        step_delay_ms=250,
    )
    legacy = FlowDefinition(name="打招呼", steps=("legacy_home",), step_delay_ms=1000)
    dummy.service = SimpleNamespace(get_flow_entry=lambda _name: entry)

    dummy._load_flow_into_manage_form(legacy)

    assert dummy.flow_manage_name_edit.value == "打招呼"
    assert dummy.flow_manage_delay_edit.value == "250"
    assert loaded_steps == ["flow:打招呼:1"]


def test_flow_management_save_preserves_structured_flow_steps_from_placeholders():
    dummy = DummyFlowManager()
    saved_entries = []
    logs = []
    entry = FlowEntry(
        name="打招呼",
        steps=[
            FlowStep(step_id=1, action="回home", func_id=108, params={"target_x": 1.0}),
            FlowStep(step_id=2, action="点头", func_id=107, params={"axis_no": 10, "pos_val": 15.0}),
        ],
        step_delay_ms=250,
        rehearsal_spd=20,
        confirmed=True,
        version=3,
    )
    dummy.current_flow_manage_name = "打招呼"
    dummy.current_flow_name = None
    dummy.table = {}
    dummy.flow_manage_name_edit = SimpleNamespace(text=lambda: "打招呼")
    dummy.flow_manage_delay_edit = SimpleNamespace(text=lambda: "300")
    dummy._collect_flow_steps = lambda: ["flow:打招呼:1", "flow:打招呼:2"]
    dummy._refresh_flow_combo = lambda: None
    dummy._refresh_flow_manage_tree = lambda: None
    dummy.status_label = SimpleNamespace(setText=lambda text: logs.append(("status", text)))
    dummy._append_log = lambda *args: logs.append(args)
    dummy._show_warning = lambda title, detail: logs.append(("warning", title, detail))
    dummy._current_permission_actor = lambda: "engineer"
    dummy.service = SimpleNamespace(
        flows={"打招呼": FlowDefinition(name="打招呼", steps=("旧步骤",), step_delay_ms=250)},
        get_flow_entry=lambda _name: entry,
        save_flow_entry=lambda item: saved_entries.append(item),
    )

    dummy._save_flow()

    assert len(saved_entries) == 1
    saved = saved_entries[0]
    assert saved.name == "打招呼"
    assert saved.step_delay_ms == 300
    assert saved.rehearsal_spd == 20
    assert saved.steps[0].func_id == 108
    assert saved.steps[1].params["axis_no"] == 10
    assert ("后台", "保存流程", "成功", "打招呼 | 2 步 | 延时 300ms") in logs


def test_operator_emergency_authorized_fast_path_publishes_ack_within_30ms():
    dummy = DummyOperator()
    now = [30.0]
    published = []
    system_actions = []
    dummy._operator_now_seconds = lambda: now[0]
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._refresh_operator_view = lambda: None
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy._handle_system_action = lambda action: system_actions.append(action)

    class FastEmergencyChannel:
        def evaluate(self, _text):
            now[0] = 30.02
            return EmergencyDecision(
                matched=True,
                authorized=True,
                action_key="sys_estop",
                message="急停授权码有效，正在执行急停。",
                reason="authorized",
            )

    dummy.operator_emergency_channel = FastEmergencyChannel()

    handled = dummy._operator_handle_emergency_text("急停 A1B2 急停")

    assert handled is True
    assert published[-1].text == "急停授权码有效，正在执行急停。"
    assert system_actions == ["sys_estop"]
    assert dummy._operator_last_emergency_ack_delay_ms == 20
    assert dummy._operator_last_emergency_ack_sla_passed is True


def test_operator_archive_execution_result_preserves_ack_and_uses_elapsed_final_delay(tmp_path: Path):
    dummy = DummyOperator()
    now = [10.0]
    dummy.session_id = "session-1"
    dummy._log_dir = tmp_path
    dummy._operator_now_seconds = lambda: now[0]
    dummy._operator_dashboard_snapshot_dict = lambda: {}
    dummy.operator_broadcast_queue = BroadcastQueue(clock=lambda: now[0])
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_archive_text_input("移动")
    now[0] = 10.05
    dummy._operator_publish_response(ResponseMessage(kind="receipt", text="收到，正在处理。", priority="normal"))

    now[0] = 10.345
    updated = dummy._operator_archive_execution_result(result="blocked", final_text="L1预检未通过。")

    payload = json.loads((tmp_path / "interaction_session_session-1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert updated is True
    assert payload["response"]["ack"] == "收到，正在处理。"
    assert payload["response"]["ack_delay_ms"] == 50
    assert payload["response"]["final"] == "L1预检未通过。"
    assert payload["response"]["final_delay_ms"] == 345
