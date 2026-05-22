from robot_modbus_lite.system_config import AxisRangeConfig, DEFAULT_SYSTEM_CONFIG, validate_system_config


def test_default_system_config_contains_speech_broadcast_settings():
    assert DEFAULT_SYSTEM_CONFIG["operator_tts_enabled"] is False
    assert DEFAULT_SYSTEM_CONFIG["broadcast_dedupe_window_sec"] == 5.0
    assert DEFAULT_SYSTEM_CONFIG["tts_retry_delay_sec"] == 5.0
    assert DEFAULT_SYSTEM_CONFIG["tts_max_failures"] == 3
    assert DEFAULT_SYSTEM_CONFIG["operator_confirm_timeout_sec"] == 60.0
    assert DEFAULT_SYSTEM_CONFIG["operator_dashboard_refresh_ms"] == 50
    assert DEFAULT_SYSTEM_CONFIG["operator_view_refresh_ms"] == 500
    assert DEFAULT_SYSTEM_CONFIG["controller_realtime_poll_ms"] == 500
    assert DEFAULT_SYSTEM_CONFIG["dashboard_stale_after_ms"] == 1000


def test_axis_range_config_reads_and_writes_speech_broadcast_settings():
    config = AxisRangeConfig.from_dict(
        {
            "x": [-1, 1],
            "y": [-2, 2],
            "z": [0, 3],
            "operator_tts_enabled": True,
            "broadcast_dedupe_window_sec": 8.5,
            "tts_retry_delay_sec": 2.5,
            "tts_max_failures": 4,
            "operator_confirm_timeout_sec": 45.0,
            "operator_dashboard_refresh_ms": 80,
            "operator_view_refresh_ms": 600,
            "controller_realtime_poll_ms": 750,
            "dashboard_stale_after_ms": 2000,
        }
    )

    assert config.operator_tts_enabled is True
    assert config.broadcast_dedupe_window_sec == 8.5
    assert config.tts_retry_delay_sec == 2.5
    assert config.tts_max_failures == 4
    assert config.operator_confirm_timeout_sec == 45.0
    assert config.operator_dashboard_refresh_ms == 80
    assert config.operator_view_refresh_ms == 600
    assert config.controller_realtime_poll_ms == 750
    assert config.dashboard_stale_after_ms == 2000
    assert config.to_dict()["operator_tts_enabled"] is True
    assert config.to_dict()["broadcast_dedupe_window_sec"] == 8.5
    assert config.to_dict()["tts_retry_delay_sec"] == 2.5
    assert config.to_dict()["tts_max_failures"] == 4
    assert config.to_dict()["operator_confirm_timeout_sec"] == 45.0
    assert config.to_dict()["operator_dashboard_refresh_ms"] == 80
    assert config.to_dict()["operator_view_refresh_ms"] == 600
    assert config.to_dict()["controller_realtime_poll_ms"] == 750
    assert config.to_dict()["dashboard_stale_after_ms"] == 2000


def test_validate_system_config_rejects_negative_broadcast_dedupe_window():
    config = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), broadcast_dedupe_window_sec=-1.0)

    assert validate_system_config(config) == "主动播报去重窗口不能小于 0 秒。"


def test_validate_system_config_rejects_invalid_tts_retry_settings():
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), tts_retry_delay_sec=-1.0))
        == "TTS 重试间隔不能小于 0 秒。"
    )
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), tts_max_failures=0))
        == "TTS 最大连续失败次数必须大于 0。"
    )


def test_validate_system_config_rejects_invalid_confirm_timeout():
    config = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_confirm_timeout_sec=0)

    assert validate_system_config(config) == "安全确认超时时间必须大于 0 秒。"


def test_validate_system_config_rejects_invalid_operator_refresh_intervals():
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_dashboard_refresh_ms=0)
        )
        == "用户页看板刷新周期必须大于 0 毫秒。"
    )
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), operator_view_refresh_ms=0))
        == "用户页界面刷新周期必须大于 0 毫秒。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), controller_realtime_poll_ms=0)
        )
        == "控制器实时轮询周期必须大于 0 毫秒。"
    )
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), dashboard_stale_after_ms=0))
        == "看板过期阈值必须大于 0 毫秒。"
    )
