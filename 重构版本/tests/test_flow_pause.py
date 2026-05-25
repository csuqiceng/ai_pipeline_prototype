from robot_modbus_lite.flow_execution_mixin import FlowExecutionMixin
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.models import FlowDefinition


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
