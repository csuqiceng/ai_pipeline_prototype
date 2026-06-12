from types import SimpleNamespace
from pathlib import Path

from robot_modbus_lite.settings_mixin import SettingsMixin
from robot_modbus_lite.system_config import AxisRangeConfig, load_system_config, validate_system_config


class DummySettings(SettingsMixin):
    pass


def test_runtime_system_config_enables_nonzero_safety_limits():
    config = load_system_config(Path("data/system_config.json"))

    assert config.safe_r_max > 0
    assert config.safe_z_max > 0
    assert config.safe_speed_max > 0
    assert config.safe_acc_max > 0
    assert config.safe_dec_max > 0
    assert config.default_spd_pct == 50.0
    assert config.default_acc_pct == 50.0
    assert config.default_dec_pct == 50.0


def test_motion_defaults_are_separate_from_safety_maxima():
    config = AxisRangeConfig.from_dict(
        {
            "x": [-1, 1],
            "y": [-1, 1],
            "z": [0, 1],
            "safe_speed_max": 150.0,
            "safe_acc_max": 150.0,
            "safe_dec_max": 150.0,
        }
    )

    assert config.default_spd_pct == 50.0
    assert config.default_acc_pct == 50.0
    assert config.default_dec_pct == 50.0


def test_validate_system_config_rejects_motion_defaults_outside_tool_schema_range():
    config = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        default_spd_pct=150.0,
    )

    assert validate_system_config(config) == "默认速度必须大于 0 且不超过 100%。"


def edit(value: object):
    state = {"value": str(value)}
    return SimpleNamespace(text=lambda: state["value"], setText=lambda text: state.update(value=str(text)))


def checkbox(value: bool):
    state = {"value": bool(value)}
    return SimpleNamespace(isChecked=lambda: state["value"], setChecked=lambda checked: state.update(value=bool(checked)))


def timer(interval: int):
    state = {"interval": interval}
    return SimpleNamespace(setInterval=lambda value: state.update(interval=int(value)), state=state)


def test_collect_system_config_preserves_non_form_runtime_fields():
    dummy = DummySettings()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-10, 10),
        y=(-20, 20),
        z=(0, 30),
        six_accept_timeout_sec=1.75,
        six_busy_timeout_sec=6.0,
        six_ready_recovery_timeout_sec=7.0,
        six_post_trigger_settle_sec=0.12,
        six_status_poll_interval_sec=0.08,
        six_accept_poll_interval_sec=0.03,
        echo_retry_interval_sec=0.25,
        echo_retry_count=7,
        echo_write_rounds=4,
        echo_compare_epsilon=0.02,
        emergency_codes=("A1B2", "C3D4"),
        operator_tts_enabled=True,
        broadcast_dedupe_window_sec=9.0,
        tts_retry_delay_sec=3.0,
        tts_max_failures=5,
        operator_confirm_timeout_sec=45.0,
        l3_min_step_delay_ms=200,
        l3_cumulative_error_limit_mm=1.5,
        l3_forbidden_boxes=({"id": "fixture", "x": [0, 1], "y": [0, 1], "z": [0, 1]},),
        joint_limits=(
            (-180.0, 180.0),
            (-90.0, 90.0),
            (-120.0, 120.0),
            (-180.0, 180.0),
            (-120.0, 120.0),
            (-360.0, 360.0),
        ),
    )
    dummy.range_x_min_edit = edit(-1)
    dummy.range_x_max_edit = edit(1)
    dummy.range_y_min_edit = edit(-2)
    dummy.range_y_max_edit = edit(2)
    dummy.range_z_min_edit = edit(0)
    dummy.range_z_max_edit = edit(3)
    dummy.safe_r_min_edit = edit(4)
    dummy.safe_r_max_edit = edit(5)
    dummy.safe_z_min_edit = edit(6)
    dummy.safe_z_max_edit = edit(7)
    dummy.safe_speed_max_edit = edit(8)
    dummy.safe_acc_max_edit = edit(9)
    dummy.safe_dec_max_edit = edit(10)
    dummy.default_spd_pct_edit = edit(31)
    dummy.default_acc_pct_edit = edit(32)
    dummy.default_dec_pct_edit = edit(33)
    dummy.motion_timeout_edit = edit(11)
    dummy.operator_tts_enabled_check = checkbox(False)
    dummy.broadcast_dedupe_window_edit = edit(1)
    dummy.tts_retry_delay_edit = edit(2)
    dummy.tts_max_failures_edit = edit(3)
    dummy.operator_confirm_timeout_edit = edit(12)
    dummy.l3_min_step_delay_edit = edit(4)
    dummy.l3_cumulative_error_limit_edit = edit(5)
    dummy.joint_limit_edits = [(edit(index), edit(index + 10)) for index in range(6)]

    config = dummy._collect_system_config()

    assert config.emergency_codes == ("A1B2", "C3D4")
    assert config.operator_tts_enabled is False
    assert config.broadcast_dedupe_window_sec == 1.0
    assert config.default_spd_pct == 31.0
    assert config.default_acc_pct == 32.0
    assert config.default_dec_pct == 33.0
    assert config.tts_retry_delay_sec == 2.0
    assert config.tts_max_failures == 3
    assert config.operator_confirm_timeout_sec == 12.0
    assert config.six_accept_timeout_sec == 1.75
    assert config.six_busy_timeout_sec == 6.0
    assert config.six_ready_recovery_timeout_sec == 7.0
    assert config.six_post_trigger_settle_sec == 0.12
    assert config.six_status_poll_interval_sec == 0.08
    assert config.six_accept_poll_interval_sec == 0.03
    assert config.echo_retry_count == 7
    assert config.l3_min_step_delay_ms == 4
    assert config.l3_cumulative_error_limit_mm == 5.0
    assert config.l3_forbidden_boxes == ({"id": "fixture", "x": [0, 1], "y": [0, 1], "z": [0, 1]},)
    assert config.joint_limits[2] == (2.0, 12.0)


def test_load_system_config_into_form_populates_tts_and_l3_fields(tmp_path):
    dummy = DummySettings()
    dummy._fmt = lambda value: str(int(value)) if float(value).is_integer() else str(value)
    dummy.system_config_path = tmp_path / "system_config.json"
    dummy.system_config_path.write_text(
        """
        {
          "x": [-1, 1],
          "y": [-2, 2],
          "z": [0, 3],
          "operator_tts_enabled": true,
          "broadcast_dedupe_window_sec": 8.5,
          "tts_retry_delay_sec": 2.5,
          "tts_max_failures": 4,
          "operator_confirm_timeout_sec": 45,
          "l3_min_step_delay_ms": 200,
          "l3_cumulative_error_limit_mm": 1.5,
          "joint_limits": [[-180, 180], [-90, 90], [-120, 120], [-180, 180], [-120, 120], [-360, 360]]
        }
        """,
        encoding="utf-8",
    )
    for name in [
        "range_x_min_edit",
        "range_x_max_edit",
        "range_y_min_edit",
        "range_y_max_edit",
        "range_z_min_edit",
        "range_z_max_edit",
        "safe_r_min_edit",
        "safe_r_max_edit",
        "safe_z_min_edit",
        "safe_z_max_edit",
        "safe_speed_max_edit",
        "safe_acc_max_edit",
        "safe_dec_max_edit",
        "default_spd_pct_edit",
        "default_acc_pct_edit",
        "default_dec_pct_edit",
        "motion_timeout_edit",
        "broadcast_dedupe_window_edit",
        "tts_retry_delay_edit",
        "tts_max_failures_edit",
        "operator_confirm_timeout_edit",
        "l3_min_step_delay_edit",
        "l3_cumulative_error_limit_edit",
    ]:
        setattr(dummy, name, edit(""))
    dummy.joint_limit_edits = [(edit(""), edit("")) for _ in range(6)]
    dummy.operator_tts_enabled_check = checkbox(False)

    dummy._load_system_config_into_form()

    assert dummy.operator_tts_enabled_check.isChecked() is True
    assert dummy.broadcast_dedupe_window_edit.text() == "8.5"
    assert dummy.tts_retry_delay_edit.text() == "2.5"
    assert dummy.tts_max_failures_edit.text() == "4"
    assert dummy.operator_confirm_timeout_edit.text() == "45"
    assert dummy.default_spd_pct_edit.text() == "50"
    assert dummy.default_acc_pct_edit.text() == "50"
    assert dummy.default_dec_pct_edit.text() == "50"
    assert dummy.l3_min_step_delay_edit.text() == "200"
    assert dummy.l3_cumulative_error_limit_edit.text() == "1.5"
    assert dummy.joint_limit_edits[1][0].text() == "-90"
    assert dummy.joint_limit_edits[5][1].text() == "360"


def test_save_system_config_syncs_operator_tts_checkbox(tmp_path):
    dummy = DummySettings()
    dummy.axis_ranges = AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1))
    dummy.system_config_path = tmp_path / "system_config.json"
    dummy.range_x_min_edit = edit(-1)
    dummy.range_x_max_edit = edit(1)
    dummy.range_y_min_edit = edit(-1)
    dummy.range_y_max_edit = edit(1)
    dummy.range_z_min_edit = edit(0)
    dummy.range_z_max_edit = edit(1)
    dummy.safe_r_min_edit = edit(0)
    dummy.safe_r_max_edit = edit(0)
    dummy.safe_z_min_edit = edit(0)
    dummy.safe_z_max_edit = edit(0)
    dummy.safe_speed_max_edit = edit(0)
    dummy.safe_acc_max_edit = edit(0)
    dummy.safe_dec_max_edit = edit(0)
    dummy.default_spd_pct_edit = edit(50)
    dummy.default_acc_pct_edit = edit(50)
    dummy.default_dec_pct_edit = edit(50)
    dummy.motion_timeout_edit = edit(180)
    dummy.operator_tts_enabled_check = checkbox(True)
    dummy.broadcast_dedupe_window_edit = edit(5)
    dummy.tts_retry_delay_edit = edit(5)
    dummy.tts_max_failures_edit = edit(3)
    dummy.operator_confirm_timeout_edit = edit(60)
    dummy.l3_min_step_delay_edit = edit(0)
    dummy.l3_cumulative_error_limit_edit = edit(0)
    dummy.joint_limit_edits = [(edit(-180), edit(180)) for _ in range(6)]
    dummy.operator_tts_check = checkbox(False)
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy.host_edit = edit("")
    dummy._append_log = lambda *args, **kwargs: None
    dummy._show_warning = lambda *args, **kwargs: None

    dummy._save_system_config()

    assert dummy.axis_ranges.operator_tts_enabled is True
    assert dummy.operator_tts_check.isChecked() is True
    assert dummy.axis_ranges.joint_limits[0] == (-180.0, 180.0)


def test_save_system_config_applies_runtime_timer_intervals(tmp_path):
    dummy = DummySettings()
    dummy.axis_ranges = AxisRangeConfig(
        x=(-1, 1),
        y=(-1, 1),
        z=(0, 1),
        operator_dashboard_refresh_ms=80,
        operator_view_refresh_ms=600,
        controller_realtime_poll_ms=750,
        dashboard_stale_after_ms=2000,
    )
    dummy.system_config_path = tmp_path / "system_config.json"
    dummy.range_x_min_edit = edit(-1)
    dummy.range_x_max_edit = edit(1)
    dummy.range_y_min_edit = edit(-1)
    dummy.range_y_max_edit = edit(1)
    dummy.range_z_min_edit = edit(0)
    dummy.range_z_max_edit = edit(1)
    dummy.safe_r_min_edit = edit(0)
    dummy.safe_r_max_edit = edit(0)
    dummy.safe_z_min_edit = edit(0)
    dummy.safe_z_max_edit = edit(0)
    dummy.safe_speed_max_edit = edit(0)
    dummy.safe_acc_max_edit = edit(0)
    dummy.safe_dec_max_edit = edit(0)
    dummy.default_spd_pct_edit = edit(50)
    dummy.default_acc_pct_edit = edit(50)
    dummy.default_dec_pct_edit = edit(50)
    dummy.motion_timeout_edit = edit(180)
    dummy.operator_tts_enabled_check = checkbox(False)
    dummy.broadcast_dedupe_window_edit = edit(5)
    dummy.tts_retry_delay_edit = edit(5)
    dummy.tts_max_failures_edit = edit(3)
    dummy.operator_confirm_timeout_edit = edit(60)
    dummy.l3_min_step_delay_edit = edit(0)
    dummy.l3_cumulative_error_limit_edit = edit(0)
    dummy.joint_limit_edits = []
    dummy.operator_dashboard_timer = timer(50)
    dummy.operator_refresh_timer = timer(500)
    dummy.realtime_timer = timer(500)
    dummy.operator_dashboard_cache = SimpleNamespace(refresh_ms=50, stale_after_ms=1000)
    dummy.status_label = SimpleNamespace(setText=lambda _text: None)
    dummy.host_edit = edit("")
    dummy._append_log = lambda *args, **kwargs: None
    dummy._show_warning = lambda *args, **kwargs: None

    dummy._save_system_config()

    assert dummy.operator_dashboard_timer.state["interval"] == 80
    assert dummy.operator_refresh_timer.state["interval"] == 600
    assert dummy.realtime_timer.state["interval"] == 750
    assert dummy.operator_dashboard_cache.refresh_ms == 80
    assert dummy.operator_dashboard_cache.stale_after_ms == 2000
