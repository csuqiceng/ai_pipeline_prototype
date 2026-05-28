from robot_modbus_lite.system_config import AxisRangeConfig, DEFAULT_SYSTEM_CONFIG, validate_system_config


def test_default_system_config_contains_emergency_codes():
    assert DEFAULT_SYSTEM_CONFIG["emergency_codes"] == ["A1B2"]


def test_axis_range_config_round_trips_emergency_codes():
    config = AxisRangeConfig.from_dict(
        {
            "x": [-1, 1],
            "y": [-2, 2],
            "z": [0, 3],
            "emergency_codes": ["A1B2", "B2C3"],
        }
    )

    assert config.emergency_codes == ("A1B2", "B2C3")
    assert config.to_dict()["emergency_codes"] == ["A1B2", "B2C3"]


def test_axis_range_config_has_dedicated_six_accept_timeout():
    assert DEFAULT_SYSTEM_CONFIG["six_accept_timeout_sec"] == 5.0

    config = AxisRangeConfig.from_dict(
        {
            "x": [-1, 1],
            "y": [-2, 2],
            "z": [0, 3],
            "six_accept_timeout_sec": 1.5,
            "six_busy_timeout_sec": 6.0,
            "six_ready_recovery_timeout_sec": 7.0,
            "six_post_trigger_settle_sec": 0.12,
            "six_status_poll_interval_sec": 0.08,
            "six_accept_poll_interval_sec": 0.03,
        }
    )

    assert config.six_accept_timeout_sec == 1.5
    assert config.six_busy_timeout_sec == 6.0
    assert config.six_ready_recovery_timeout_sec == 7.0
    assert config.six_post_trigger_settle_sec == 0.12
    assert config.six_status_poll_interval_sec == 0.08
    assert config.six_accept_poll_interval_sec == 0.03
    assert config.to_dict()["six_accept_timeout_sec"] == 1.5
    assert config.to_dict()["six_busy_timeout_sec"] == 6.0
    assert config.to_dict()["six_ready_recovery_timeout_sec"] == 7.0
    assert config.to_dict()["six_post_trigger_settle_sec"] == 0.12
    assert config.to_dict()["six_status_poll_interval_sec"] == 0.08
    assert config.to_dict()["six_accept_poll_interval_sec"] == 0.03


def test_validate_system_config_rejects_invalid_six_accept_timeout():
    config = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), six_accept_timeout_sec=0)

    assert validate_system_config(config) == "六轴接受确认超时时间必须大于 0 秒。"


def test_validate_system_config_rejects_invalid_six_timing_fields():
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), six_busy_timeout_sec=0))
        == "六轴通道空闲等待超时时间必须大于 0 秒。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), six_ready_recovery_timeout_sec=0)
        )
        == "六轴就绪恢复超时时间必须大于 0 秒。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), six_post_trigger_settle_sec=-1)
        )
        == "六轴触发后稳定等待时间不能小于 0 秒。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), six_status_poll_interval_sec=0)
        )
        == "六轴状态轮询间隔必须大于 0 秒。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), six_accept_poll_interval_sec=0)
        )
        == "六轴接受确认轮询间隔必须大于 0 秒。"
    )
