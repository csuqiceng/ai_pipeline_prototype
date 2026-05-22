from robot_modbus_lite.system_config import AxisRangeConfig, DEFAULT_SYSTEM_CONFIG, validate_system_config


def test_default_system_config_contains_l3_process_precheck_settings():
    assert DEFAULT_SYSTEM_CONFIG["l3_min_step_delay_ms"] == 0
    assert DEFAULT_SYSTEM_CONFIG["l3_cumulative_error_limit_mm"] == 0.0
    assert DEFAULT_SYSTEM_CONFIG["l3_forbidden_boxes"] == []


def test_axis_range_config_round_trips_l3_process_precheck_settings():
    config = AxisRangeConfig.from_dict(
        {
            "x": [-1, 1],
            "y": [-2, 2],
            "z": [0, 3],
            "l3_min_step_delay_ms": 200,
            "l3_cumulative_error_limit_mm": 1.5,
            "l3_forbidden_boxes": [
                {"id": "fixture", "x": [0, 10], "y": [0, 10], "z": [0, 10]},
            ],
        }
    )

    assert config.l3_min_step_delay_ms == 200
    assert config.l3_cumulative_error_limit_mm == 1.5
    assert config.l3_forbidden_boxes == ({"id": "fixture", "x": [0, 10], "y": [0, 10], "z": [0, 10]},)
    assert config.to_dict()["l3_min_step_delay_ms"] == 200
    assert config.to_dict()["l3_cumulative_error_limit_mm"] == 1.5
    assert config.to_dict()["l3_forbidden_boxes"][0]["id"] == "fixture"


def test_validate_system_config_rejects_negative_l3_settings():
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), l3_min_step_delay_ms=-1))
        == "L3 最小步间隔不能小于 0 毫秒。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), l3_cumulative_error_limit_mm=-1.0)
        )
        == "L3 累计误差上限不能小于 0。"
    )


def test_axis_range_config_round_trips_joint_soft_limits():
    config = AxisRangeConfig.from_dict(
        {
            "x": [-1, 1],
            "y": [-2, 2],
            "z": [0, 3],
            "joint_limits": [
                [-180, 180],
                [-90, 90],
                [-120, 120],
                [-180, 180],
                [-120, 120],
                [-360, 360],
            ],
        }
    )

    assert config.joint_limits[1] == (-90.0, 90.0)
    assert config.to_dict()["joint_limits"][5] == [-360.0, 360.0]


def test_validate_system_config_rejects_invalid_joint_soft_limits():
    assert (
        validate_system_config(
            AxisRangeConfig(
                x=(-1, 1),
                y=(-1, 1),
                z=(0, 1),
                joint_limits=((-180.0, 180.0),),
            )
        )
        == "关节软限位必须为空或包含 6 组范围。"
    )
    assert (
        validate_system_config(
            AxisRangeConfig(
                x=(-1, 1),
                y=(-1, 1),
                z=(0, 1),
                joint_limits=(
                    (-180.0, 180.0),
                    (90.0, -90.0),
                    (-120.0, 120.0),
                    (-180.0, 180.0),
                    (-120.0, 120.0),
                    (-360.0, 360.0),
                ),
            )
        )
        == "J2 软限位最小值不能大于最大值。"
    )
