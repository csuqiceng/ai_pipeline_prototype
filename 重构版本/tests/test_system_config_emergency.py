from robot_modbus_lite.system_config import AxisRangeConfig, DEFAULT_SYSTEM_CONFIG


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
