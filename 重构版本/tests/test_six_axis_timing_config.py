from robot_modbus_lite.six_axis_command_mixin import SixAxisCommandMixin
from robot_modbus_lite.models import QueryRecord, SixAxisCommand
from robot_modbus_lite.models import VrReadRequest
from robot_modbus_lite.system_config import AxisRangeConfig


class DummySixAxis(SixAxisCommandMixin):
    def __init__(self):
        self.service = FakeSixAxisService()
        self.logs = []

    def _append_log(self, *args):
        self.logs.append(args)

    @staticmethod
    def _fmt(value):
        return str(value)


class FakeAcceptClient:
    def __init__(self, ack: float):
        self.ack = ack

    def read_modbus_float(self, request):
        assert request.start_vr == 312
        return [self.ack]

    def read_modbus_long(self, request):
        if request.start_vr == 34:
            return [0]
        if request.start_vr == 36:
            return [0]
        return [0]


class FakeSixAxisService:
    def build_six_accept_confirm_read(self):
        return VrReadRequest(start_vr=312, count=1)

    def build_six_status_read(self):
        return VrReadRequest(start_vr=34, count=1)

    def build_six_system_state_read(self):
        return VrReadRequest(start_vr=36, count=1)

    def parse_six_system_state(self, values):
        return int(values[0] if values else 0)


def test_six_axis_mixin_uses_system_config_timing_fields():
    dummy = DummySixAxis()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        six_busy_timeout_sec=6.0,
        six_ready_recovery_timeout_sec=7.0,
        six_post_trigger_settle_sec=0.12,
        six_status_poll_interval_sec=0.08,
    )

    assert dummy._six_busy_timeout() == 6.0
    assert dummy._six_ready_recovery_timeout() == 7.0
    assert dummy._six_post_trigger_settle() == 0.12
    assert dummy._six_status_poll_interval() == 0.08


def test_six_axis_accept_confirm_passes_when_ieee312_is_zero():
    dummy = DummySixAxis()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        six_accept_timeout_sec=0.001,
        six_accept_poll_interval_sec=0.001,
    )

    dummy._wait_six_command_accepted(
        FakeAcceptClient(0.0),
        SixAxisCommand(func_num=108),
        QueryRecord(query_key="home", func_num=108, params={}),
    )

    assert any("接受确认" in entry[1] for entry in dummy.logs)


def test_six_axis_accept_confirm_waits_when_ieee312_is_one():
    dummy = DummySixAxis()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        six_accept_timeout_sec=0.001,
        six_accept_poll_interval_sec=0.001,
    )

    try:
        dummy._wait_six_command_accepted(
            FakeAcceptClient(1.0),
            SixAxisCommand(func_num=108),
            QueryRecord(query_key="home", func_num=108, params={}),
        )
    except RuntimeError as exc:
        assert "六轴接受确认超时" in str(exc)
        assert "IEEE(312)=1.0" in str(exc)
    else:
        raise AssertionError("IEEE(312)=1 should not pass after reversing the accepted state")
