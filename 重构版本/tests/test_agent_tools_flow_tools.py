from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot
from robot_modbus_lite.agent_tools.flow_tools import (
    answer_flow_clarification,
    append_flow_step,
    cancel_flow_draft,
    edit_flow_draft_params,
    prepare_registered_flow_execution,
    query_current_draft,
    query_registered_flow,
    save_flow_draft,
    set_flow_draft,
    query_flow_draft,
)
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.execution_plan_service import ExecutionPlanService
from robot_modbus_lite.service import RobotModbusService


def _draft_missing_pose():
    return {
        "flow_name": "测试流程",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动到位置A",
                "func_id": 108,
                "description": "移动到位置A",
                "params": {"spd_pct": 50.0},
            }
        ],
    }


def _snapshot():
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


def test_set_flow_draft_wraps_execution_plan_service_and_reports_clarification():
    service = ExecutionPlanService()

    result = set_flow_draft(service, _draft_missing_pose())

    assert result.ok is False
    assert result.state == "flow_draft_needs_clarification"
    assert result.data["flow_name"] == "测试流程"
    assert result.data["step_count"] == 1
    assert result.data["missing_fields"] == ["target_pose"]
    assert result.data["accepted_answer_types"] == ["pose"]
    assert "第1步缺少目标坐标" in result.message


def test_append_flow_step_creates_missing_pose_clarification():
    service = ExecutionPlanService()
    set_flow_draft(service, {"flow_name": "测试流程", "expanded_steps": []})

    result = append_flow_step(service, step_text="添加第一步是移动到位置 A。")

    assert result.ok is False
    assert result.state == "flow_draft_needs_clarification"
    assert result.data["flow_name"] == "测试流程"
    assert result.data["step_count"] == 1
    assert result.data["missing_fields"] == ["target_pose"]
    assert result.data["draft"]["expanded_steps"][0]["action"] == "移动到位置A"


def test_append_flow_step_parses_inline_delay_without_clarification():
    service = ExecutionPlanService()
    set_flow_draft(service, {"flow_name": "测试流程", "expanded_steps": []})

    result = append_flow_step(service, step_text="添加下一步等待 2 秒。")

    assert result.ok is True
    assert result.state == "flow_draft_updated"
    assert result.data["missing_fields"] == []
    step = result.data["draft"]["expanded_steps"][0]
    assert step["func_id"] == 109
    assert step["params"]["delay_sec"] == 2.0


def test_append_flow_step_parses_inline_io_without_clarification():
    service = ExecutionPlanService()
    set_flow_draft(service, {"flow_name": "测试流程", "expanded_steps": []})

    result = append_flow_step(service, step_text="添加下一步输出1打开。")

    assert result.ok is True
    assert result.state == "flow_draft_updated"
    assert result.data["missing_fields"] == []
    step = result.data["draft"]["expanded_steps"][0]
    assert step["func_id"] == 120
    assert step["params"]["io_no"] == 1
    assert step["params"]["io_action"] == 1


def test_append_flow_step_appends_spoken_multi_step_with_inline_delay():
    service = ExecutionPlanService()
    set_flow_draft(service, {"flow_name": "测试流程", "expanded_steps": []})

    result = append_flow_step(
        service,
        step_text="步骤一，移动到位置 A。步骤二，等待 2 秒。",
    )

    assert result.ok is False
    assert result.state == "flow_draft_needs_clarification"
    assert result.data["missing_fields"] == ["target_pose"]
    steps = result.data["draft"]["expanded_steps"]
    assert len(steps) == 2
    assert steps[0]["action"] == "移动到位置A"
    assert steps[0]["func_id"] == 108
    assert steps[1]["action"] == "等待2秒"
    assert steps[1]["func_id"] == 109
    assert steps[1]["params"]["delay_sec"] == 2.0


def test_answer_flow_clarification_fills_pose_with_chinese_number_and_speed():
    service = ExecutionPlanService()
    set_flow_draft(service, {"flow_name": "测试流程", "expanded_steps": []})
    append_flow_step(service, step_text="添加第一步是移动到位置 A。")

    result = answer_flow_clarification(
        service,
        "我觉得坐标是 X 一百， Y 0， Z 100，速度 50。",
        snapshot_provider=_snapshot,
    )

    step = result.data["draft"]["expanded_steps"][0]
    assert result.ok is True
    assert result.state == "flow_draft_updated"
    assert result.data["missing_fields"] == []
    assert step["params"]["target_x"] == 100.0
    assert step["params"]["target_y"] == 0.0
    assert step["params"]["target_z"] == 100.0
    assert step["params"]["target_rx"] == 0.0
    assert step["params"]["spd_pct"] == 50.0


def test_save_flow_draft_persists_structured_flow_entry(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    draft = {
        "flow_name": "测试流程",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动到位置A",
                "func_id": 108,
                "description": "移动到位置A",
                "params": {
                    "target_x": 100.0,
                    "target_y": 0.0,
                    "target_z": 100.0,
                    "target_rx": 0.0,
                    "target_ry": 0.0,
                    "target_rz": 0.0,
                    "spd_pct": 50.0,
                },
            }
        ],
    }

    result = save_flow_draft(service, draft)

    saved = service.get_flow_entry("测试流程")
    assert result.ok is True
    assert result.state == "flow_draft_saved"
    assert result.data["flow_name"] == "测试流程"
    assert result.data["step_count"] == 1
    assert saved is not None
    assert saved.steps[0].func_id == 108
    assert saved.steps[0].params["target_x"] == 100.0
    assert service.get_flow("测试流程").steps == ("移动到位置A",)


def test_edit_flow_draft_params_updates_speed_on_existing_step():
    service = ExecutionPlanService()
    set_flow_draft(
        service,
        {
            "flow_name": "测试流程",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动到位置B",
                    "func_id": 108,
                    "params": {
                        "target_x": 475.0,
                        "target_y": 0.0,
                        "target_z": 545.0,
                        "target_rx": 0.0,
                        "target_ry": 0.0,
                        "target_rz": 0.0,
                        "spd_pct": 30.0,
                        "acc_pct": 30.0,
                        "dec_pct": 30.0,
                    },
                }
            ],
        },
    )

    result = edit_flow_draft_params(service, text="那就改成20%")

    assert result.state == "flow_draft_updated"
    step = result.data["draft"]["expanded_steps"][0]
    assert step["params"]["spd_pct"] == 20.0
    assert step["params"]["acc_pct"] == 30.0
    assert "速度" in result.message


def test_edit_flow_draft_params_updates_explicit_acceleration():
    service = ExecutionPlanService()
    set_flow_draft(
        service,
        {
            "flow_name": "测试流程",
            "expanded_steps": [
                {
                    "step_id": 1,
                    "action": "移动到位置B",
                    "func_id": 108,
                    "params": {
                        "target_x": 475.0,
                        "target_y": 0.0,
                        "target_z": 545.0,
                        "target_rx": 0.0,
                        "target_ry": 0.0,
                        "target_rz": 0.0,
                        "spd_pct": 30.0,
                        "acc_pct": 30.0,
                        "dec_pct": 30.0,
                    },
                }
            ],
        },
    )

    result = edit_flow_draft_params(service, text="我要加速度改成20%")

    assert result.state == "flow_draft_updated"
    step = result.data["draft"]["expanded_steps"][0]
    assert step["params"]["spd_pct"] == 30.0
    assert step["params"]["acc_pct"] == 20.0
    assert step["params"]["dec_pct"] == 30.0


def test_save_flow_draft_rejects_missing_step_params(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )

    result = save_flow_draft(service, _draft_missing_pose())

    assert result.ok is False
    assert result.state == "flow_draft_not_ready"
    assert result.errors[0]["fields"] == ["target_pose"]
    assert service.list_flow_names() == []


def test_query_registered_flow_returns_saved_flow_entry(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.save_flow_entry(
        FlowEntry(
            name="点头",
            steps=[
                FlowStep(
                    step_id=1,
                    action="移动到位置A",
                    func_id=108,
                    params={"target_x": 100.0},
                    description="移动到位置A",
                )
            ],
        )
    )

    result = query_registered_flow(service, "点头")

    assert result.ok is True
    assert result.state == "registered_flow_loaded"
    assert result.data["flow_name"] == "点头"
    assert result.data["step_count"] == 1
    assert result.data["entry"]["steps"][0]["func_id"] == 108
    assert result.data["generates_command"] is False


def test_query_registered_flow_lists_names_when_name_missing(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.save_flow_entry(FlowEntry(name="点头", steps=[]))
    service.save_flow_entry(FlowEntry(name="打招呼", steps=[]))

    result = query_registered_flow(service, "")

    assert result.ok is True
    assert result.state == "registered_flow_list"
    assert result.data["flow_names"] == ["打招呼", "点头"]


def test_prepare_registered_flow_execution_returns_gate_bound_draft(tmp_path):
    service = RobotModbusService(
        "unused.csv",
        flows_path=tmp_path / "flows.json",
        flow_registry_path=tmp_path / "flow_registry.json",
        table={},
    )
    service.save_flow_entry(
        FlowEntry(
            name="点头",
            steps=[
                FlowStep(
                    step_id=1,
                    action="移动到位置A",
                    func_id=108,
                    params={"target_x": 100.0},
                    description="移动到位置A",
                )
            ],
        )
    )

    result = prepare_registered_flow_execution(service, "点头")

    assert result.ok is True
    assert result.state == "registered_flow_execution_draft"
    assert result.data["flow_name"] == "点头"
    assert result.data["mode"] == "start"
    assert result.data["requires_execution_gate"] is True
    assert result.data["requires_confirmation"] is True
    assert result.data["generates_command"] is False
    assert result.data["entry"]["steps"][0]["params"]["target_x"] == 100.0


def test_query_current_draft_returns_structured_draft():
    service = ExecutionPlanService()
    set_flow_draft(service, _draft_missing_pose())

    result = query_current_draft(service)

    assert result.ok is True
    assert result.state == "flow_draft_loaded"
    assert result.data["flow_name"] == "测试流程"
    assert result.data["step_count"] == 1


def test_query_flow_draft_returns_session_state_draft_without_service():
    draft = {
        "flow_name": "测试流程",
        "expanded_steps": [
            {
                "step_id": 1,
                "action": "移动到位置A",
                "func_id": 108,
                "description": "移动到位置A",
                "params": {"target_x": 100.0},
            }
        ],
    }

    result = query_flow_draft(draft)

    assert result.ok is True
    assert result.state == "flow_draft_loaded"
    assert result.data["flow_name"] == "测试流程"
    assert result.data["step_count"] == 1
    assert result.data["draft"]["expanded_steps"][0]["params"]["target_x"] == 100.0


def test_cancel_flow_draft_clears_pending_plan():
    service = ExecutionPlanService()
    set_flow_draft(service, _draft_missing_pose())

    result = cancel_flow_draft(service)

    assert result.ok is True
    assert result.state == "flow_draft_cancelled"
    assert service.pending_plan is None
