from robot_modbus_lite.agent.execution_monitor import ExecutionMonitorAgent, ExecutionMonitorSnapshot
from robot_modbus_lite.models import QueryRecord


def test_execution_monitor_records_successful_motion_completion():
    agent = ExecutionMonitorAgent()
    record = QueryRecord(
        query_key="move_a",
        func_num=108,
        params={"target_x": 1000, "target_y": 200, "target_z": 800},
        description="移动到A点",
    )

    snapshot = agent.record_dispatch_result(
        record,
        ok=True,
        error="",
        feedback=[0.0, 1000.0, 200.0, 800.0, 0.0, 45.0, 0.0],
        context={"dispatch_id": 7, "plan_step_index": 1, "plan_step_total": 1},
        now=10.0,
    )

    assert snapshot.status == "completed"
    assert snapshot.query_key == "move_a"
    assert snapshot.func_id == 108
    assert snapshot.result_code == "0.0"
    assert "动作执行完成：move_a" in agent.answer_completion_query(snapshot)
    assert "当前位置 1000.0 / 200.0 / 800.0" in agent.answer_completion_query(snapshot)


def test_execution_monitor_records_failed_dispatch_with_reason():
    agent = ExecutionMonitorAgent()
    record = QueryRecord(query_key="move_a", func_num=108, params={})

    snapshot = agent.record_dispatch_result(record, ok=False, error="镜像确认失败", now=12.0)

    assert snapshot.status == "failed"
    assert "镜像确认失败" in agent.answer_completion_query(snapshot)


def test_execution_monitor_answers_running_snapshot():
    agent = ExecutionMonitorAgent()
    snapshot = ExecutionMonitorSnapshot(
        status="running",
        query_key="move_a",
        func_id=108,
        started_at=10.0,
        updated_at=15.0,
        detail="控制器仍在运行",
        progress_pct=50,
    )

    text = agent.answer_completion_query(snapshot)

    assert "还在执行" in text
    assert "50%" in text


def test_execution_monitor_answers_timeout_for_long_running_snapshot():
    agent = ExecutionMonitorAgent(default_timeout_sec=30)
    snapshot = ExecutionMonitorSnapshot(
        status="running",
        query_key="move_a",
        func_id=108,
        started_at=10.0,
        updated_at=10.0,
        detail="控制器仍在运行",
    )

    text = agent.answer_completion_query(snapshot, now=45.0)

    assert "可能超时" in text
    assert "35.0秒" in text


def test_execution_monitor_warns_when_completed_position_deviates_from_target():
    agent = ExecutionMonitorAgent(position_tolerance_mm=2.0)
    record = QueryRecord(
        query_key="move_a",
        func_num=108,
        params={"target_x": 1000, "target_y": 200, "target_z": 800},
    )

    snapshot = agent.record_dispatch_result(
        record,
        ok=True,
        error="",
        feedback=[0.0, 1005.0, 200.0, 799.0],
        now=10.0,
    )

    assert snapshot.status == "completed_with_warning"
    text = agent.answer_completion_query(snapshot)
    assert "位置偏差" in text
    assert "5.0mm" in text


def test_execution_monitor_marks_running_snapshot_failed_on_alarm():
    agent = ExecutionMonitorAgent()
    snapshot = ExecutionMonitorSnapshot(
        status="running",
        query_key="move_a",
        func_id=108,
        started_at=10.0,
        updated_at=12.0,
    )

    updated = agent.update_from_runtime_state(
        snapshot,
        alarm_active=True,
        alarm_text="报警: 机械臂超限",
        channel_idle=False,
        now=13.0,
    )

    assert updated.status == "failed"
    assert "机械臂超限" in agent.answer_completion_query(updated)
