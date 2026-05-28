from robot_modbus_lite.six_axis_command_mixin import SixAxisCommandMixin
from robot_modbus_lite.system_config import AxisRangeConfig


class DummySixAxis(SixAxisCommandMixin):
    pass


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
