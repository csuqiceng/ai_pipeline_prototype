import json
import time
from types import SimpleNamespace
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStackedWidget

from robot_modbus_lite.avoidance_config import AvoidanceConfig, SafePoint
from robot_modbus_lite.broadcast_queue import BroadcastQueue
from robot_modbus_lite.clarification_state import PendingClarification
from robot_modbus_lite.dashboard import DashboardCache
from robot_modbus_lite.emergency_channel import EmergencyDecision
from robot_modbus_lite.execution_plan import ExecutionPlanStatus
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.models import FlowDefinition, QueryRecord
from robot_modbus_lite.flow_management_mixin import FlowManagementMixin
from robot_modbus_lite.operator_ui_mixin import OperatorSceneState, OperatorUiMixin
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionRegistry
from robot_modbus_lite.response_builder import ResponseBuilder, ResponseMessage
from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.speech_broadcast import CallableSpeechSink, Pyttsx3SpeechSink, WindowsSapiSpeechSink
from robot_modbus_lite.system_config import AxisRangeConfig
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


class DummyOperator(OperatorUiMixin):
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
    draft = flow_draft_payload()
    draft["expanded_steps"][0]["params"] = {"spd_pct": 50.0}
    dummy._operator_pending_flow_draft = draft
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
    assert params["target_x"] == 900.0
    assert params["target_z"] == 1000.0
    assert updated["needs_precheck"] is True
    assert service.current_clarification() is None


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


def test_operator_pending_flow_draft_save_and_execute_starts_saved_flow(tmp_path):
    dummy = make_flow_draft_operator(tmp_path)
    dummy._operator_pending_flow_draft = flow_draft_payload()
    started = []
    dummy._start_flow = lambda on_done=None: started.append(dummy.current_flow_name) or (on_done and on_done(True))

    handled = dummy._operator_handle_pending_flow_draft_command("保存并执行")

    assert handled is True
    assert started == ["打招呼"]
    assert dummy.current_flow_name == "打招呼"


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


def test_operator_maybe_begin_streaming_chat_starts_immediately_for_deepseek_chat():
    dummy = DummyOperator()
    dummy._operator_chat_messages = []
    dummy._render_operator_chat = lambda: None
    dummy._operator_scroll_chat_to_bottom = lambda: None

    started = dummy._operator_maybe_begin_streaming_chat_for_text("你好", use_deepseek=True)

    assert started is True
    assert dummy._operator_chat_messages == [("assistant", "")]
    assert dummy._operator_chat_thinking_meta[-1]["active"] is True


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
        ["识别为普通问答", "基于本地资料整理回答", "DeepSeek 生成回答，未触发机械手动作"]
    ]
    assert dummy._operator_chat_thinking_meta[-1] == {"active": False, "elapsed_sec": 4}


def test_operator_ai_chat_row_contains_collapsible_process_summary():
    app = QApplication.instance() or QApplication([])
    dummy = DummyOperator()

    row = dummy._build_operator_chat_row(
        "assistant",
        "我是问答助手。",
        thinking_steps=["识别为普通问答", "基于本地资料整理回答", "DeepSeek 生成回答，未触发机械手动作"],
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
        actions=(VoiceNlpAction("unknown", None, "rule", "位置A的参数是什么样的", "生产指令缺少“小正”唤醒词，未执行"),),
        source="rule",
        raw_text="位置A的参数是什么样的",
        reason="生产指令缺少“小正”唤醒词，未执行",
        semantic_level=3,
        semantic_label="生产指令",
    )

    dummy._execute_nlp_plan(plan)

    assert warnings == []
    assert chats == [("assistant", "生产指令缺少“小正”唤醒词，未执行。没有触发机械手动作。")]
    assert spoken == ["生产指令缺少“小正”唤醒词，未执行。没有触发机械手动作。"]
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
    assert "未配置运动学逆解引擎" in dummy._operator_l2_summary(result)


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
    dummy._append_log = lambda *args: logs.append(args)

    dummy._operator_accept_suggestion()

    assert prepared
    assert confirmed
    assert dummy._operator_pending_confirm_plan.actions[0].action_type == "flow"
    assert dummy._operator_pending_confirm_plan.actions[0].target in dummy.service.flows
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
