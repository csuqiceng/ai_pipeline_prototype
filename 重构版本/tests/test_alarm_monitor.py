from types import SimpleNamespace

from robot_modbus_lite.alarm_monitor import AlarmMonitor


def test_alarm_monitor_samples_source_at_configured_interval():
    source = SimpleNamespace(
        alarm_code="ERR_8",
        alarm_text="速度超限",
        six_long38=8,
        estop_active=False,
        pause_active=False,
        monitor_label=SimpleNamespace(text=lambda: "实时监控运行中"),
    )

    sample = AlarmMonitor(interval_ms=50).sample_from_source(source)

    assert sample.interval_ms == 50
    assert sample.detection.codes == ("OVER_SPEED",)
    assert sample.to_dict()["interval_ms"] == 50


def test_alarm_monitor_uses_connection_state_when_feedback_is_offline():
    source = SimpleNamespace(
        alarm_code="0",
        alarm_text="正常",
        estop_active=False,
        pause_active=False,
        monitor_label=SimpleNamespace(text=lambda: "离线"),
    )

    sample = AlarmMonitor(interval_ms=50).sample_from_source(source)

    assert sample.detection.codes == ("COMM_STALE", "CONTROLLER_NOT_READY")
    assert sample.detection.active is True
