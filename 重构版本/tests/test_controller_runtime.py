from types import SimpleNamespace

from robot_modbus_lite import controller_runtime_mixin as runtime_module
from robot_modbus_lite.controller_runtime_mixin import ControllerRuntimeMixin
from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.system_config import AxisRangeConfig


class DummyControllerRuntime(ControllerRuntimeMixin):
    pass


class FakePollClient:
    def __init__(self) -> None:
        self.float_reads: dict[int, int] = {}
        self.long_reads: dict[int, int] = {}

    def read_modbus_float(self, request):
        self.float_reads[request.start_vr] = self.float_reads.get(request.start_vr, 0) + 1
        if request.start_vr == 1600:
            return [1, 2, 3, 4, 5, 6]
        if request.start_vr == 1612:
            return [10, 20, 30, 40, 50, 60]
        if request.start_vr == 1500:
            return [101, 102, 103, 104, 105, 106]
        if request.start_vr == 1512:
            return [110, 120, 130, 140, 150, 160]
        if request.start_vr == 200:
            return list(range(12))
        if request.start_vr == 240:
            return list(range(20, 32))
        return [0]

    def read_modbus_long(self, request):
        self.long_reads[request.start_vr] = self.long_reads.get(request.start_vr, 0) + 1
        return [0]


def test_controller_realtime_poll_interval_uses_system_config():
    dummy = DummyControllerRuntime()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        controller_realtime_poll_ms=750,
    )

    assert dummy._controller_realtime_poll_interval_ms() == 750


def test_controller_realtime_poll_interval_falls_back_to_500_without_config():
    dummy = DummyControllerRuntime()

    assert dummy._controller_realtime_poll_interval_ms() == 500


def test_controller_poll_group_intervals_split_core_feedback_from_status_reads():
    dummy = DummyControllerRuntime()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        controller_realtime_poll_ms=50,
    )

    assert dummy._controller_poll_group_interval_ms("realtime_feedback") == 50
    assert dummy._controller_poll_group_interval_ms("status_detail") == 500


def test_controller_poll_group_due_tracks_group_timestamps_independently():
    dummy = DummyControllerRuntime()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        controller_realtime_poll_ms=50,
    )

    assert dummy._controller_poll_group_due("realtime_feedback", now_sec=10.0) is True
    dummy._controller_mark_poll_group("realtime_feedback", now_sec=10.0)

    assert dummy._controller_poll_group_due("realtime_feedback", now_sec=10.03) is False
    assert dummy._controller_poll_group_due("realtime_feedback", now_sec=10.05) is True
    assert dummy._controller_poll_group_due("status_detail", now_sec=10.05) is True


def test_poll_feedback_silent_skips_status_detail_until_group_interval(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: now[0])
    client = FakePollClient()
    dummy = DummyControllerRuntime()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), controller_realtime_poll_ms=50)
    dummy.service = RobotModbusService("", table={})
    dummy.host_edit = SimpleNamespace(text=lambda: "127.0.0.1")
    dummy.monitor_label = SimpleNamespace(setText=lambda _text: None)
    dummy._polling_feedback = False
    dummy._poll_started_logged = True
    dummy._last_poll_error = ""
    dummy._get_client = lambda _host: client
    dummy._fmt = lambda value: str(value)
    dummy._refresh_status_labels = lambda: None
    dummy._refresh_overall_state_indicator = lambda: None
    dummy._append_log = lambda *args, **kwargs: None
    dummy._disconnect_client = lambda: None
    dummy._log_exception_fields = lambda _exc: {}
    dummy._log_realtime_state_change_if_needed = lambda: None

    dummy._poll_feedback_silent()
    now[0] = 10.05
    dummy._poll_feedback_silent()

    assert client.float_reads[1600] == 2
    assert client.float_reads[1612] == 2
    assert client.float_reads[1500] == 2
    assert client.float_reads[1512] == 2
    assert client.long_reads[34] == 1
    assert client.long_reads[36] == 1
    assert client.long_reads[38] == 1
    assert client.float_reads[324] == 1
    assert client.float_reads[56] == 1
    assert client.float_reads[200] == 1
    assert client.float_reads[240] == 1
    assert dummy.robot_mpos_joints == (1, 2, 3, 4, 5, 6)
    assert dummy.robot_mpos_pose == (10, 20, 30, 40, 50, 60)
    assert dummy.robot_dpos_joints == (101, 102, 103, 104, 105, 106)
    assert dummy.robot_dpos_pose == (110, 120, 130, 140, 150, 160)
    assert dummy.axis_status == tuple(range(12))
    assert dummy.motion_type == tuple(range(20, 32))


def test_controller_status_poll_updates_running_execution_monitor_on_alarm():
    dummy = DummyControllerRuntime()
    dummy.robot_dpos_pose = (1000.0, 200.0, 800.0, 0.0, 45.0, 0.0)
    dummy._operator_last_execution_monitor_snapshot = {
        "status": "running",
        "query_key": "move_a",
        "func_id": 108,
        "started_at": 10.0,
        "updated_at": 10.0,
    }

    dummy._update_execution_monitor_from_status_poll(
        alarm_active=True,
        alarm_text="报警: 机械臂超限",
        channel_idle=False,
        current_func=108,
        result_code="268435456",
    )

    snapshot = dummy._operator_last_execution_monitor_snapshot
    assert snapshot["status"] == "failed"
    assert "机械臂超限" in snapshot["detail"]


def test_controller_status_poll_marks_running_execution_completed_when_channel_idle():
    dummy = DummyControllerRuntime()
    dummy.robot_dpos_pose = (1000.0, 200.0, 800.0, 0.0, 45.0, 0.0)
    dummy._operator_last_execution_monitor_snapshot = {
        "status": "running",
        "query_key": "move_a",
        "func_id": 108,
        "started_at": 10.0,
        "updated_at": 10.0,
    }

    dummy._update_execution_monitor_from_status_poll(
        alarm_active=False,
        alarm_text="系统正常",
        channel_idle=True,
        current_func=0,
        result_code="0",
    )

    snapshot = dummy._operator_last_execution_monitor_snapshot
    assert snapshot["status"] == "completed"
    assert snapshot["progress_pct"] == 100
    assert snapshot["feedback"][:3] == [1000.0, 200.0, 800.0]
