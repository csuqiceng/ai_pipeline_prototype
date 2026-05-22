from robot_modbus_lite.emergency_channel import EmergencyChannel, EmergencyDecision


def test_plain_emergency_keyword_requires_authorization_code():
    channel = EmergencyChannel(authorized_codes={"A1B2"})

    decision = channel.evaluate("急停")

    assert decision == EmergencyDecision(
        matched=True,
        authorized=False,
        action_key=None,
        message="已识别到急停意图。请按“急停 授权码 急停”格式确认。",
        reason="missing_code",
    )


def test_authorized_three_part_emergency_code_returns_estop_action():
    channel = EmergencyChannel(authorized_codes={"A1B2"})

    decision = channel.evaluate("急停 A1B2 急停")

    assert decision.authorized is True
    assert decision.action_key == "sys_estop"
    assert decision.reason == "authorized"


def test_invalid_three_part_emergency_code_is_rejected():
    channel = EmergencyChannel(authorized_codes={"A1B2"})

    decision = channel.evaluate("急停 0000 急停")

    assert decision.matched is True
    assert decision.authorized is False
    assert decision.action_key is None
    assert decision.message == "急停授权码无效，未执行急停。"
    assert decision.reason == "invalid_code"


def test_non_emergency_text_is_ignored():
    channel = EmergencyChannel(authorized_codes={"A1B2"})

    decision = channel.evaluate("查看当前状态")

    assert decision.matched is False
    assert decision.action_key is None
