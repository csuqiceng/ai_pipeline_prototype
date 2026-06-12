from robot_modbus_lite.flow_execution_mixin import FlowExecutionMixin
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.models import FlowDefinition, QueryRecord


class DummyFlow(FlowExecutionMixin):
    pass


def test_flow_auto_continue_is_deferred_when_operator_pause_is_active():
    dummy = DummyFlow()
    logs = []
    refreshed = []
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))
    dummy.flow_paused = True
    dummy.flow_status = "运行中"
    dummy._refresh_flow_status_panel = lambda: refreshed.append(True)
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._flow_log_extra = lambda *_args, **_kwargs: {}

    deferred = dummy._defer_flow_auto_continue_if_paused(
        flow=flow,
        run_id=7,
        completed_step_index=1,
        next_step="move_b",
    )

    assert deferred is True
    assert dummy.flow_status == "已暂停"
    assert refreshed == [True]
    assert logs[-1][0:3] == ("流程", "流程暂停 demo", "提示")


def test_flow_auto_continue_is_not_deferred_when_not_paused():
    dummy = DummyFlow()
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))
    dummy.flow_paused = False

    assert dummy._defer_flow_auto_continue_if_paused(flow=flow, run_id=7, completed_step_index=1, next_step="move_b") is False


def test_flow_step_failure_while_paused_waits_for_resume_instead_of_failing():
    dummy = DummyFlow()
    logs = []
    summaries = []
    refreshed = []
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))
    dummy.table = {"move_a": object()}
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": flow},
            "get_flow": lambda self, name: self.flows[name],
        },
    )()
    dummy.current_flow_name = "demo"
    dummy.flow_run_id = 11
    dummy._flow_run_started_id = 11
    dummy.flow_running = True
    dummy.flow_paused = True
    dummy.flow_step_index = 0
    dummy.flow_status = "运行中"
    dummy.flow_current_step = "-"
    dummy._flow_done_callback = lambda ok: logs.append(("callback", ok))
    dummy._refresh_flow_steps = lambda: refreshed.append("steps")
    dummy._refresh_flow_status_panel = lambda: refreshed.append("status")
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._append_flow_summary = lambda *args, **kwargs: summaries.append((args, kwargs))
    dummy._flow_log_extra = lambda *_args, **_kwargs: {}
    dummy._build_parallel_flow_group = lambda *_args, **_kwargs: None
    dummy._flow_step_record = lambda *_args, **_kwargs: object()
    dummy._execute_query_key = lambda _key, on_done=None, **_kwargs: on_done(False)
    dummy._finish_flow_run = lambda status: logs.append(("finish", status))

    dummy._run_current_flow_step(auto_continue=True, run_id=11)

    assert dummy.flow_running is True
    assert dummy.flow_paused is True
    assert dummy.flow_status == "已暂停"
    assert dummy.flow_step_index == 0
    assert summaries == []
    assert ("callback", False) not in logs
    assert ("finish", "失败") not in logs
    assert any(entry[1] == "流程暂停 demo" for entry in logs if isinstance(entry, tuple) and len(entry) >= 2)


def test_mark_flow_run_started_clears_previous_pause_state():
    dummy = DummyFlow()
    dummy.flow_paused = True

    dummy._mark_flow_run_started(9)

    assert dummy.flow_paused is False
    assert dummy._flow_run_started_id == 9


def test_finish_flow_run_clears_flow_pause_state():
    dummy = DummyFlow()
    dummy.flow_running = True
    dummy.flow_paused = True
    dummy.flow_status = "已暂停"
    dummy.flow_current_step = "move_a"
    refreshed = []
    dummy._refresh_flow_steps = lambda: refreshed.append("steps")
    dummy._refresh_flow_status_panel = lambda: refreshed.append("status")

    dummy._finish_flow_run("完成")

    assert dummy.flow_running is False
    assert dummy.flow_paused is False
    assert dummy.flow_status == "完成"
    assert dummy.flow_current_step == "-"
    assert refreshed == ["steps", "status"]


def test_flow_execution_builds_record_from_structured_flow_step_without_table_template():
    dummy = DummyFlow()
    dummy.table = {}
    step = FlowStep(
        step_id=1,
        action="移动",
        func_id=108,
        params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0, "spd_pct": 20.0},
        description="结构化移动",
    )

    record = dummy._flow_step_record(step, flow_name="demo", index=1)

    assert record.query_key == "flow:demo:1"
    assert record.func_num == 108
    assert record.params["target_x"] == 1.0
    assert record.description == "结构化移动"


def test_current_flow_definition_prefers_structured_flow_entry_from_service():
    dummy = DummyFlow()
    entry = FlowEntry(
        name="demo",
        steps=[FlowStep(step_id=1, action="移动", func_id=108, params={"target_x": 1.0})],
    )
    legacy = FlowDefinition(name="demo", steps=("结构化移动",))
    dummy.current_flow_name = "demo"
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": legacy},
            "get_flow": lambda self, name: self.flows[name],
            "get_effective_flow": lambda self, name: entry,
        },
    )()

    flow = dummy._current_flow_definition()

    assert flow is entry
    assert flow.steps[0].func_id == 108


def test_start_flow_resets_completed_step_index_when_flow_was_extended(monkeypatch):
    dummy = DummyFlow()
    logs = []
    refreshed = []
    callbacks = []
    flow = FlowDefinition(name="demo", steps=tuple(f"move_{index}" for index in range(8)))
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": flow},
            "get_flow": lambda self, name: self.flows[name],
        },
    )()
    dummy.current_flow_name = "demo"
    dummy.flow_running = False
    dummy.flow_paused = False
    dummy.flow_step_index = 5
    dummy.flow_current_step = "-"
    dummy.flow_status = "完成"
    dummy.flow_run_id = 0
    dummy.host_edit = type("Host", (), {"text": lambda self: "127.0.0.1"})()
    dummy._show_info = lambda *args: None
    dummy._show_warning = lambda *args: None
    dummy._refresh_flow_steps = lambda: refreshed.append("steps")
    dummy._refresh_flow_status_panel = lambda: refreshed.append("status")
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._flow_step_record = lambda step, **kwargs: object()
    dummy._pause_polling = lambda: None
    dummy._resume_polling = lambda: None
    dummy._wait_controller_ready_for_flow = lambda host: (True, "ready")
    dummy._run_in_background = lambda work, done: done(work())
    dummy._run_next_flow_step = lambda *, run_id=None: callbacks.append(("next", run_id, dummy.flow_step_index))
    monkeypatch.setattr("robot_modbus_lite.flow_execution_mixin.QTimer.singleShot", lambda _ms, callback: callback())

    dummy._start_flow()

    assert dummy.flow_step_index == 0
    assert callbacks == [("next", 1, 0)]
    assert any(
        kwargs.get("extra", {}).get("start_step_index") == 1
        for _args, kwargs in logs
    )


def test_start_flow_answers_unsupported_legacy_jog_template_without_popup(monkeypatch):
    dummy = DummyFlow()
    warnings = []
    chats = []
    logs = []
    callbacks = []
    flow = FlowDefinition(name="demo", steps=("jog_step",))
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": flow},
            "get_flow": lambda self, name: self.flows[name],
        },
    )()
    dummy.table = {
        "jog_step": QueryRecord(
            query_key="jog_step",
            func_num=107,
            description="小臂点头",
            params={"axis_no": 10, "pos_val": 15.0},
        )
    }
    dummy.current_flow_name = "demo"
    dummy.flow_running = False
    dummy.flow_paused = False
    dummy.flow_step_index = 0
    dummy.flow_current_step = "-"
    dummy.flow_status = "空闲"
    dummy.flow_run_id = 0
    dummy.host_edit = type("Host", (), {"text": lambda self: "127.0.0.1"})()
    dummy._show_info = lambda *args: None
    dummy._show_warning = lambda title, text: warnings.append((title, text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._refresh_flow_steps = lambda: None
    dummy._refresh_flow_status_panel = lambda: None
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._pause_polling = lambda: callbacks.append("pause")
    dummy._run_in_background = lambda *_args, **_kwargs: callbacks.append("background")
    monkeypatch.setattr("robot_modbus_lite.flow_execution_mixin.QTimer.singleShot", lambda _ms, callback: callback())

    dummy._start_flow()

    assert warnings == []
    assert chats
    assert chats[-1][0] == "assistant"
    assert chats[-1][2]["kind"] == "warn"
    assert "Func107" in chats[-1][1]
    assert dummy.flow_running is False
    assert callbacks == []
    assert logs[-1][0][0:3] == ("流程", "流程预检查 demo", "失败")


def test_start_flow_blocks_when_qt_flow_precheck_fails(monkeypatch):
    dummy = DummyFlow()
    logs = []
    chats = []
    callbacks = []
    flow = FlowDefinition(name="demo", steps=("move_a",))
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": flow},
            "get_flow": lambda self, name: self.flows[name],
        },
    )()
    dummy.table = {
        "move_a": QueryRecord(
            query_key="move_a",
            func_num=108,
            description="移动A",
            params={
                "target_x": 1000.0,
                "target_y": 0.0,
                "target_z": 800.0,
                "target_rx": 0.0,
                "target_ry": 90.0,
                "target_rz": 0.0,
                "spd_pct": 50.0,
                "acc_pct": 50.0,
                "dec_pct": 50.0,
            },
        )
    }
    dummy.current_flow_name = "demo"
    dummy.flow_running = False
    dummy.flow_paused = False
    dummy.flow_step_index = 0
    dummy.flow_current_step = "-"
    dummy.flow_status = "空闲"
    dummy.flow_run_id = 0
    dummy.host_edit = type("Host", (), {"text": lambda self: "127.0.0.1"})()
    dummy._show_info = lambda *args: None
    dummy._show_warning = lambda title, text: logs.append(("warning", title, text))
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._refresh_flow_steps = lambda: None
    dummy._refresh_flow_status_panel = lambda: None
    dummy._append_log = lambda *args, **kwargs: logs.append((args, kwargs))
    dummy._pause_polling = lambda: callbacks.append("pause")
    dummy._run_in_background = lambda *_args, **_kwargs: callbacks.append("background")
    dummy._operator_run_qt_flow_precheck = lambda flow: {
        "status": "fail",
        "flow_name": flow.name,
        "items": [{"label": "第1步 L2 预演", "status": "fail", "message": "move_a: L2 运动规划预演未通过。"}],
        "suggestion": "请修复失败步骤后再执行流程。",
    }
    monkeypatch.setattr("robot_modbus_lite.flow_execution_mixin.QTimer.singleShot", lambda _ms, callback: callback())

    dummy._start_flow(on_done=lambda ok: callbacks.append(("done", ok)))

    assert dummy.flow_running is False
    assert callbacks == [("done", False)]
    assert chats
    assert chats[-1][0] == "assistant"
    assert chats[-1][2]["kind"] == "warn"
    assert "流程预检未通过" in chats[-1][1]
    assert any(entry[0][0:3] == ("流程", "流程预检查 demo", "失败") for entry in logs if isinstance(entry, tuple) and entry)


def test_start_flow_precheck_warning_prefers_robot_safety_detail(monkeypatch):
    dummy = DummyFlow()
    chats = []
    callbacks = []
    flow = FlowDefinition(name="demo", steps=("move_a",))
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": flow},
            "get_flow": lambda self, name: self.flows[name],
        },
    )()
    dummy.table = {"move_a": QueryRecord(query_key="move_a", func_num=108, params={})}
    dummy.current_flow_name = "demo"
    dummy.flow_running = False
    dummy.flow_paused = False
    dummy.flow_step_index = 0
    dummy.flow_current_step = "-"
    dummy.flow_status = "空闲"
    dummy.flow_run_id = 0
    dummy.host_edit = type("Host", (), {"text": lambda self: "127.0.0.1"})()
    dummy._show_info = lambda *args: None
    dummy._show_warning = lambda *args: None
    dummy._operator_add_chat_message = lambda role, text, **kwargs: chats.append((role, text, kwargs))
    dummy._refresh_flow_steps = lambda: None
    dummy._refresh_flow_status_panel = lambda: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._pause_polling = lambda: callbacks.append("pause")
    dummy._run_in_background = lambda *_args, **_kwargs: callbacks.append("background")
    dummy._operator_run_qt_flow_precheck = lambda _flow: {
        "status": "fail",
        "flow_name": "demo",
        "items": [
            {
                "label": "第1步 L2 预演",
                "status": "fail",
                "message": "move_a: L2 运动规划预演未通过。",
                "robot_safety": {
                    "detail_zh": "L2逆解预判未通过：未找到满足关节限位的 FSTATUS。",
                    "suggestion_zh": "请调整目标位姿或补充中间点后重试。",
                },
            }
        ],
    }
    monkeypatch.setattr("robot_modbus_lite.flow_execution_mixin.QTimer.singleShot", lambda _ms, callback: callback())

    dummy._start_flow(on_done=lambda ok: callbacks.append(("done", ok)))

    assert callbacks == [("done", False)]
    assert "L2逆解预判未通过" in chats[-1][1]
    assert "请调整目标位姿" in chats[-1][1]


def test_qt_flow_precheck_stores_l3_result_and_archives(monkeypatch):
    dummy = DummyFlow()
    archived = []
    flow = FlowDefinition(name="demo", steps=("move_a",))
    expected = {
        "status": "fail",
        "flow_name": "demo",
        "items": [{"label": "第1步 L2 预演", "status": "fail", "message": "move_a: 越界。"}],
        "suggestion": "请修复失败步骤后再执行流程。",
    }

    class FakeProcessPrecheckService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_l3(self, *, flow, table, snapshot):
            assert flow.name == "demo"
            assert "move_a" in table
            assert snapshot == {"snapshot": True}
            return expected

    dummy.table = {"move_a": QueryRecord(query_key="move_a", func_num=108, params={})}
    dummy.axis_ranges = type("AxisRanges", (), {"l3_cumulative_error_limit_mm": 0.0, "l3_min_step_delay_ms": 0})()
    dummy._operator_dashboard_snapshot_dict = lambda: {"snapshot": True}
    dummy._operator_l1_result_for_record_key = lambda _key, _snapshot: {"status": "pass", "items": []}
    dummy._operator_l2_result_for_record = lambda _record: {"status": "fail", "items": []}
    dummy._operator_archive_safety_check = lambda: archived.append(dummy._operator_last_process_precheck_result) or True
    dummy._append_log = lambda *args, **kwargs: None
    monkeypatch.setattr("robot_modbus_lite.flow_execution_mixin.ProcessPrecheckService", FakeProcessPrecheckService)

    result = dummy._operator_run_qt_flow_precheck(flow)

    assert result is expected
    assert dummy._operator_last_process_precheck_result is expected
    assert archived == [expected]


def test_reset_flow_clears_flow_pause_state():
    dummy = DummyFlow()
    logs = []
    refreshed = []
    flow = FlowDefinition(name="demo", steps=("move_a",))
    dummy.service = type(
        "Service",
        (),
        {
            "flows": {"demo": flow},
            "get_flow": lambda self, name: self.flows[name],
        },
    )()
    dummy.current_flow_name = "demo"
    dummy.flow_run_id = 3
    dummy.flow_running = True
    dummy.flow_paused = True
    dummy.flow_step_index = 1
    dummy.flow_status = "已暂停"
    dummy.flow_current_step = "move_a"
    dummy._flow_done_callback = lambda ok: logs.append(("callback", ok))
    dummy._refresh_flow_steps = lambda: refreshed.append("steps")
    dummy._refresh_flow_status_panel = lambda: refreshed.append("status")
    dummy._append_log = lambda *args, **kwargs: logs.append(args)
    dummy._flow_log_extra = lambda *_args, **_kwargs: {}
    dummy._append_flow_summary = lambda *args, **kwargs: logs.append(("summary", args, kwargs))

    dummy._reset_flow()

    assert dummy.flow_running is False
    assert dummy.flow_paused is False
    assert dummy.flow_step_index == 0
    assert dummy.flow_status == "空闲"
    assert dummy.flow_current_step == "-"
    assert ("callback", False) in logs
    assert refreshed == ["steps", "status"]
