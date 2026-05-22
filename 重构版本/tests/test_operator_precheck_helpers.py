import json
from types import SimpleNamespace
from pathlib import Path

from robot_modbus_lite.avoidance_config import AvoidanceConfig, SafePoint
from robot_modbus_lite.broadcast_queue import BroadcastQueue
from robot_modbus_lite.dashboard import DashboardCache
from robot_modbus_lite.emergency_channel import EmergencyDecision
from robot_modbus_lite.models import FlowDefinition, QueryRecord
from robot_modbus_lite.operator_ui_mixin import OperatorSceneState, OperatorUiMixin
from robot_modbus_lite.response_builder import ResponseBuilder, ResponseMessage
from robot_modbus_lite.speech_broadcast import CallableSpeechSink, Pyttsx3SpeechSink
from robot_modbus_lite.system_config import AxisRangeConfig
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


class DummyOperator(OperatorUiMixin):
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


def test_operator_toggle_microphone_publishes_voice_receipt_when_starting():
    dummy = DummyOperator()
    published = []
    toggled = []
    dummy._local_voice_streaming = False
    dummy._proxy_mic_capturing = False
    dummy._mic_process = None
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy._toggle_microphone_recording = lambda: toggled.append(True)
    dummy._sync_operator_mic_button = lambda: None
    dummy.operator_response_builder = ResponseBuilder()

    dummy._operator_toggle_microphone_recording()

    assert toggled == [True]
    assert published[-1].kind == "receipt"
    assert "正在识别" in published[-1].text


def test_operator_toggle_microphone_does_not_publish_voice_receipt_when_stopping():
    dummy = DummyOperator()
    published = []
    dummy._local_voice_streaming = True
    dummy._operator_publish_response = lambda message: published.append(message)
    dummy._toggle_microphone_recording = lambda: None
    dummy._sync_operator_mic_button = lambda: None
    dummy.operator_response_builder = ResponseBuilder()

    dummy._operator_toggle_microphone_recording()

    assert published == []


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


def test_operator_ui_command_handles_parse_execute_clear_button_equivalents():
    dummy = DummyOperator()
    calls = []
    logs = []
    dummy._operator_parse_text = lambda: calls.append("parse")
    dummy._operator_execute_text = lambda: calls.append("execute")
    dummy._operator_clear_text = lambda: calls.append("clear")
    dummy._append_log = lambda *args: logs.append(args)

    assert dummy._handle_operator_ui_command("解析当前指令") is True
    assert dummy._handle_operator_ui_command("执行当前指令") is True
    assert dummy._handle_operator_ui_command("清空输入") is True

    assert calls == ["parse", "execute", "clear"]
    assert [entry[1] for entry in logs] == ["按钮语音指令", "按钮语音指令", "按钮语音指令"]


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


def test_operator_add_chat_from_log_queues_alarm_acknowledged():
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
    assert chat_messages == [("assistant", "报警已确认：ERR_9 | 驱动器报警")]


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
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    spoken = []
    dummy.operator_speech_sink = CallableSpeechSink(spoken.append)
    dummy._append_log = lambda *args, **kwargs: None
    dummy._operator_publish_response(ResponseMessage(kind="progress", text="预检中", priority="normal"))
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:4"))

    result = dummy._operator_deliver_pending_broadcasts_to_speech()
    second = dummy._operator_deliver_pending_broadcasts_to_speech()

    assert result.success is True
    assert second.delivered_seq == ()
    assert spoken == ["报警", "预检中"]
    assert dummy._operator_last_delivered_broadcast_seq == 2


def test_operator_deliver_pending_broadcasts_to_speech_keeps_cursor_on_failure():
    dummy = DummyOperator()
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    logs = []

    def fail(_text: str) -> None:
        raise RuntimeError("speaker offline")

    dummy.operator_speech_sink = CallableSpeechSink(fail)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:5"))

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


def test_operator_configure_tts_from_settings_enables_sink_when_configured():
    dummy = DummyOperator()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=True)

    sink = dummy._operator_configure_tts_from_settings()

    assert isinstance(sink, Pyttsx3SpeechSink)
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


def test_operator_auto_deliver_broadcasts_only_runs_when_tts_enabled():
    dummy = DummyOperator()
    spoken = []
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=False)
    dummy.operator_speech_sink = CallableSpeechSink(spoken.append)
    dummy._operator_add_chat_message = lambda *args, **kwargs: None
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:7"))

    disabled_result = dummy._operator_auto_deliver_broadcasts()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_tts_enabled=True)
    enabled_result = dummy._operator_auto_deliver_broadcasts()

    assert disabled_result is None
    assert enabled_result is not None
    assert spoken == ["报警"]


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
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:8"))

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
    dummy._operator_publish_response(ResponseMessage(kind="alert", text="报警", priority="high", context_id="alarm:9"))

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
    assert status["text"] == text
    assert logs[-1][1] == "能力查询"


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

    def start_recording():
        now[0] = 20.12

    dummy._toggle_microphone_recording = start_recording

    dummy._operator_toggle_microphone_recording()

    assert published[-1].kind == "receipt"
    assert dummy._operator_last_voice_receipt_delay_ms == 120
    assert dummy._operator_last_voice_receipt_sla_passed is True


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
