from robot_modbus_lite.models import FlowDefinition, QueryRecord
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.process_precheck import ProcessPrecheckService


def move_record(key: str, x: float = 1.0) -> QueryRecord:
    return QueryRecord(
        query_key=key,
        func_num=108,
        params={
            "target_x": x,
            "target_y": 2.0,
            "target_z": 3.0,
            "target_rx": 4.0,
            "target_ry": 5.0,
            "target_rz": 6.0,
            "spd_pct": 20.0,
            "acc_pct": 30.0,
            "dec_pct": 40.0,
        },
    )


def test_process_precheck_fails_when_flow_step_template_is_missing():
    service = ProcessPrecheckService()
    flow = FlowDefinition(name="demo", steps=("move_a", "missing"))

    result = service.run_l3(flow=flow, table={"move_a": move_record("move_a")}, snapshot={})

    assert result["status"] == "fail"
    assert result["progress_percent"] == 0
    assert result["items"][0]["id"] == "missing_template"
    assert "missing" in result["items"][0]["message"]


def test_process_precheck_runs_l1_for_each_step_and_reports_failure():
    calls = []

    def l1_runner(_snapshot, plan):
        calls.append(plan["plan_id"])
        return {"status": "fail" if plan["plan_id"] == "move_b" else "pass", "items": []}

    service = ProcessPrecheckService(l1_runner=l1_runner)
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))

    result = service.run_l3(flow=flow, table={"move_a": move_record("move_a"), "move_b": move_record("move_b")}, snapshot={})

    assert calls == ["move_a", "move_b"]
    assert result["status"] == "fail"
    assert result["progress_percent"] == 100
    assert any(item["id"] == "step_l1" and item["status"] == "fail" for item in result["items"])


def test_process_precheck_accepts_structured_flow_step_without_query_table_template():
    calls = []
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, plan: calls.append(plan["plan_id"]) or {"status": "pass", "items": []},
        l2_runner=lambda record: {"status": "pass", "items": [], "query_key": record.query_key},
    )
    flow = FlowEntry(
        name="structured",
        steps=[
            FlowStep(
                step_id=1,
                action="移动",
                func_id=108,
                params={
                    "target_x": 10.0,
                    "target_y": 20.0,
                    "target_z": 30.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 30.0,
                },
                description="结构化移动",
            )
        ],
        step_delay_ms=10,
    )

    result = service.run_l3(flow=flow, table={}, snapshot={})

    assert result["status"] == "pass"
    assert calls == ["flow:structured:1"]
    assert result["timing"]["step_count"] == 1


def test_process_precheck_reports_step_progress_callback():
    progress_events = []
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "pass", "items": []},
        progress_callback=progress_events.append,
    )
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b", "move_c"))

    result = service.run_l3(
        flow=flow,
        table={
            "move_a": move_record("move_a"),
            "move_b": move_record("move_b"),
            "move_c": move_record("move_c"),
        },
        snapshot={},
    )

    assert result["status"] == "pass"
    step_events = [
        {key: event[key] for key in ("flow_name", "current_step", "total_steps", "step_key", "percent")}
        for event in progress_events
        if event["stage"] == "step_complete"
    ]
    assert step_events == [
        {"flow_name": "demo", "current_step": 1, "total_steps": 3, "step_key": "move_a", "percent": 27},
        {"flow_name": "demo", "current_step": 2, "total_steps": 3, "step_key": "move_b", "percent": 48},
        {"flow_name": "demo", "current_step": 3, "total_steps": 3, "step_key": "move_c", "percent": 70},
    ]


def test_process_precheck_reports_l3_stage_progress_events():
    progress_events = []
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "pass", "items": []},
        progress_callback=progress_events.append,
        min_step_delay_ms=1,
        cumulative_error_limit_mm=10.0,
    )
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"), step_delay_ms=5)

    result = service.run_l3(
        flow=flow,
        table={"move_a": move_record("move_a"), "move_b": move_record("move_b")},
        snapshot={"workspace": {"forbidden_boxes": []}},
    )

    assert result["status"] == "pass"
    assert [(event["stage"], event["percent"]) for event in progress_events] == [
        ("start", 0),
        ("template_check", 5),
        ("step_complete", 38),
        ("step_complete", 70),
        ("timing_check", 80),
        ("error_budget", 88),
        ("interference_check", 95),
        ("complete", 100),
    ]
    assert progress_events[1]["message"] == "正在检查流程模板完整性。"


def test_process_precheck_progress_callback_failure_does_not_abort_precheck():
    def fail_progress(_event):
        raise RuntimeError("speech queue offline")

    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "pass", "items": []},
        progress_callback=fail_progress,
    )
    flow = FlowDefinition(name="demo", steps=("move_a",))

    result = service.run_l3(flow=flow, table={"move_a": move_record("move_a")}, snapshot={})

    assert result["status"] == "pass"
    assert result["progress_percent"] == 100


def test_process_precheck_treats_l2_unavailable_as_warning_not_failure():
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "unavailable", "items": [], "suggestion": "未配置逆解"},
    )
    flow = FlowDefinition(name="demo", steps=("move_a",))

    result = service.run_l3(flow=flow, table={"move_a": move_record("move_a")}, snapshot={})

    assert result["status"] == "pass"
    assert result["items"][-1]["status"] == "warn"


def test_process_precheck_blocks_l2_failure():
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "fail", "items": [{"message": "奇异点"}]},
    )
    flow = FlowDefinition(name="demo", steps=("move_a",))

    result = service.run_l3(flow=flow, table={"move_a": move_record("move_a")}, snapshot={})

    assert result["status"] == "fail"
    assert any(item["id"] == "step_l2" and item["status"] == "fail" for item in result["items"])


def test_process_precheck_collects_l2_midpoint_suggestions_per_step():
    def l2_runner(record):
        if record.query_key == "move_b":
            return {
                "status": "fail",
                "need_midpoint": True,
                "midpoint_pose": (10.0, 20.0, 30.0, 0.0, 5.0, 0.0),
                "midpoint_fstatus": 3,
                "suggestion": "建议经 RY 偏移中点绕行。",
                "items": [{"message": "路径接近奇异点。"}],
            }
        return {"status": "pass", "items": []}

    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=l2_runner,
    )
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))

    result = service.run_l3(
        flow=flow,
        table={"move_a": move_record("move_a"), "move_b": move_record("move_b")},
        snapshot={},
    )

    assert result["status"] == "fail"
    assert result["midpoint_suggestions"] == [
        {
            "step_index": 2,
            "step_key": "move_b",
            "midpoint_pose": (10.0, 20.0, 30.0, 0.0, 5.0, 0.0),
            "midpoint_fstatus": 3,
            "suggestion": "建议经 RY 偏移中点绕行。",
        }
    ]
    assert "第2步 move_b 建议中点绕行" in result["suggestion"]
    assert any(item["id"] == "step_midpoint_suggestion" for item in result["items"])


def test_process_precheck_blocks_when_step_delay_is_below_state_transition_minimum():
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "pass", "items": []},
        min_step_delay_ms=100,
    )
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"), step_delay_ms=0)

    result = service.run_l3(
        flow=flow,
        table={"move_a": move_record("move_a"), "move_b": move_record("move_b")},
        snapshot={},
    )

    assert result["status"] == "fail"
    assert any(item["id"] == "timing_state" and item["status"] == "fail" for item in result["items"])
    assert result["timing"]["estimated_total_delay_ms"] == 0


def test_process_precheck_blocks_when_cumulative_error_exceeds_limit():
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "pass", "items": []},
        cumulative_error_limit_mm=1.0,
    )
    move_a = move_record("move_a")
    move_b = move_record("move_b")
    move_a.params["expected_error_mm"] = 0.7
    move_b.params["expected_error_mm"] = 0.6
    flow = FlowDefinition(name="demo", steps=("move_a", "move_b"))

    result = service.run_l3(flow=flow, table={"move_a": move_a, "move_b": move_b}, snapshot={})

    assert result["status"] == "fail"
    assert any(item["id"] == "cumulative_error" and item["status"] == "fail" for item in result["items"])
    assert result["error_budget"]["estimated_cumulative_error_mm"] == 1.3


def test_process_precheck_blocks_target_inside_forbidden_box():
    service = ProcessPrecheckService(
        l1_runner=lambda _snapshot, _plan: {"status": "pass", "items": []},
        l2_runner=lambda _record: {"status": "pass", "items": []},
    )
    flow = FlowDefinition(name="demo", steps=("move_a",))
    snapshot = {
        "workspace": {
            "forbidden_boxes": [
                {"id": "fixture", "x": (0, 10), "y": (0, 10), "z": (0, 10)},
            ]
        }
    }

    result = service.run_l3(flow=flow, table={"move_a": move_record("move_a", x=5.0)}, snapshot=snapshot)

    assert result["status"] == "fail"
    assert any(item["id"] == "interference_box" and item["status"] == "fail" for item in result["items"])
