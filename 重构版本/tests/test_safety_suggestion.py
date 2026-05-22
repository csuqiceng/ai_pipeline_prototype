from robot_modbus_lite.safety_suggestion import SafetySuggestionService
from robot_modbus_lite.system_config import AxisRangeConfig


def make_config() -> AxisRangeConfig:
    return AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-200.0, 200.0),
        z=(0.0, 300.0),
        safe_speed_max=80.0,
        safe_acc_max=70.0,
        safe_dec_max=60.0,
    )


def test_suggestion_clamps_target_and_speed_to_config_limits():
    service = SafetySuggestionService(make_config())

    suggestion = service.suggest(
        {
            "plan_id": "p1",
            "target": {"x": 150.0, "y": -250.0, "z": 320.0},
            "speed": {"spd_pct": 90.0, "acc_pct": 80.0, "dec_pct": 70.0},
        }
    )

    assert suggestion["available"] is True
    assert suggestion["adjusted_plan"]["target"] == {"x": 100.0, "y": -200.0, "z": 300.0}
    assert suggestion["adjusted_plan"]["speed"] == {"spd_pct": 80.0, "acc_pct": 70.0, "dec_pct": 60.0}
    assert "目标 X 调整为 100.0" in suggestion["messages"]
    assert "速度百分比调整为 80.0" in suggestion["messages"]


def test_suggestion_is_unavailable_when_plan_has_no_adjustable_values():
    service = SafetySuggestionService(make_config())

    suggestion = service.suggest({"plan_id": "p1"})

    assert suggestion["available"] is False
    assert suggestion["adjusted_plan"] == {"plan_id": "p1"}
    assert suggestion["messages"] == []


def test_suggestion_clamps_joint_targets_to_soft_limits():
    config = AxisRangeConfig(
        x=(-100.0, 100.0),
        y=(-200.0, 200.0),
        z=(0.0, 300.0),
        joint_limits=(
            (-180.0, 180.0),
            (-90.0, 90.0),
            (-120.0, 120.0),
            (-180.0, 180.0),
            (-120.0, 120.0),
            (-360.0, 360.0),
        ),
    )
    service = SafetySuggestionService(config)

    suggestion = service.suggest({"target": {"joints": (None, 100.0, None, None, None, None)}})

    assert suggestion["available"] is True
    assert suggestion["adjusted_plan"]["target"]["joints"][1] == 90.0
    assert "目标 J2 调整为 90.0" in suggestion["messages"]
