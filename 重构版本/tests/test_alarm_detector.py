from robot_modbus_lite.alarm_detector import AlarmDetector


def test_alarm_detector_maps_long38_speed_and_accel_bits_to_advice():
    detection = AlarmDetector().detect(alarm_code="ERR_8", alarm_text="速度异常", long38_raw=8 | 16)

    assert detection.active is True
    assert detection.codes == ("OVER_SPEED", "OVER_ACCEL")
    assert detection.severity == "critical"
    assert detection.auto_clear_allowed is False
    assert any(advice.code == "OVER_SPEED" for advice in detection.advices)


def test_alarm_detector_allows_auto_clear_only_for_transient_codes():
    detection = AlarmDetector().detect(realtime_feedback="stale")

    assert detection.codes == ("COMM_STALE",)
    assert detection.auto_clear_allowed is True


def test_alarm_detector_marks_estop_and_paused_as_distinct_causes():
    detection = AlarmDetector().detect(alarm_code="ERR_9", estop=True, paused=True)

    assert detection.codes[:2] == ("E_STOP", "PAUSED")
    assert detection.severity == "critical"
