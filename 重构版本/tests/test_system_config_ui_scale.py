from robot_modbus_lite.system_config import AxisRangeConfig, DEFAULT_SYSTEM_CONFIG, validate_system_config


def test_default_system_config_contains_ui_scale_auto():
    assert DEFAULT_SYSTEM_CONFIG["ui_scale"] == "auto"
    config = AxisRangeConfig.from_dict({"x": [-1, 1], "y": [-1, 1], "z": [0, 1]})
    assert config.ui_scale == "auto"
    assert config.to_dict()["ui_scale"] == "auto"


def test_system_config_accepts_numeric_ui_scale():
    config = AxisRangeConfig.from_dict({"x": [-1, 1], "y": [-1, 1], "z": [0, 1], "ui_scale": 0.9})
    assert config.ui_scale == 0.9
    assert config.to_dict()["ui_scale"] == 0.9


def test_validate_system_config_rejects_invalid_ui_scale():
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale=0.4))
        == "UI 缩放比例必须在 0.6 到 1.2 之间，或使用 auto。"
    )
    assert (
        validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale="bad"))
        == "UI 缩放比例必须在 0.6 到 1.2 之间，或使用 auto。"
    )
