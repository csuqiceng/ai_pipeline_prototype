from robot_modbus_lite.alarm_advice import AlarmAdviceBook


def test_alarm_advice_book_contains_ten_codes():
    book = AlarmAdviceBook.default()

    assert len(book.codes()) >= 10
    assert book.get("E_STOP").severity == "critical"
    assert book.get("OVER_SPEED").auto_clear is False


def test_unknown_alarm_returns_safe_fallback():
    advice = AlarmAdviceBook.default().get("UNKNOWN_CODE")

    assert advice.code == "UNKNOWN_CODE"
    assert advice.severity == "unknown"
    assert "工程师" in advice.operator_hint


def test_auto_clear_policy_is_explicit():
    book = AlarmAdviceBook.default()

    assert isinstance(book.get("COMM_STALE").auto_clear, bool)
    assert isinstance(book.get("CONTROLLER_NOT_READY").auto_clear, bool)
