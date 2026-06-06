from robot_modbus_lite.agent.address_resolver import AddressConfig, AddressResolver
from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot, ParameterCompletionAgent
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent


class PassingL1:
    def __init__(self) -> None:
        self.plan = None

    def run_l1(self, snapshot, plan):
        self.plan = plan
        return {"status": "pass", "items": []}


def test_address_resolver_defaults_match_current_agent_protocol():
    resolver = AddressResolver()

    assert resolver.continuous_path_func == 112
    assert resolver.absolute_motion_func == 108
    assert resolver.cartesian_current == 1500
    assert resolver.safe_speed_max == 1708


def test_command_understanding_uses_injected_continuous_path_func():
    resolver = AddressResolver(AddressConfig(continuous_path_func=111))

    result = CommandUnderstandingAgent(address_resolver=resolver).understand("规划路径走到X1000")

    assert result.intent == "continuous_path"
    assert result.func_id == 111


def test_parameter_completion_accepts_injected_continuous_path_func():
    resolver = AddressResolver(AddressConfig(continuous_path_func=111))
    understanding = CommandUnderstandingAgent(address_resolver=resolver).understand("规划路径走到X1000")
    snapshot = ControllerSnapshot(
        current_pose={
            "target_x": 10.0,
            "target_y": 20.0,
            "target_z": 30.0,
            "target_rx": 1.0,
            "target_ry": 2.0,
            "target_rz": 3.0,
        },
        safety_params={"spd_pct": 40.0, "acc_pct": 45.0, "dec_pct": 50.0},
        is_moving=False,
        read_ok=True,
    )

    draft = ParameterCompletionAgent(lambda: snapshot, address_resolver=resolver).complete(understanding)

    assert draft.func_id == 111
    assert draft.intent == "continuous_path"
    assert draft.params["target_x"] == 1000.0


def test_parameter_completion_accepts_injected_absolute_motion_func8():
    resolver = AddressResolver(AddressConfig(absolute_motion_func=8))
    understanding = CommandUnderstandingAgent(address_resolver=resolver).understand("走到X1000")
    snapshot = ControllerSnapshot(
        current_pose={
            "target_x": 10.0,
            "target_y": 20.0,
            "target_z": 30.0,
            "target_rx": 1.0,
            "target_ry": 2.0,
            "target_rz": 3.0,
        },
        safety_params={"spd_pct": 40.0, "acc_pct": 45.0, "dec_pct": 50.0},
        is_moving=False,
        read_ok=True,
    )

    draft = ParameterCompletionAgent(lambda: snapshot, address_resolver=resolver).complete(understanding)

    assert understanding.func_id == 8
    assert draft.func_id == 8
    assert draft.params["target_x"] == 1000.0


def test_safety_review_treats_continuous_path_intent_as_motion_plan():
    l1 = PassingL1()
    draft = CommandDraft(
        draft_id="cp111",
        func_id=111,
        intent="continuous_path",
        params={
            "target_x": 1000.0,
            "target_y": 200.0,
            "target_z": 800.0,
            "target_rx": 0.0,
            "target_ry": 45.0,
            "target_rz": 0.0,
            "spd_pct": 60.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
        },
        param_sources={},
        raw_text="规划路径走到X1000",
        confidence=0.95,
    )

    result = SafetyReviewAgent(l1_service=l1).review(draft, snapshot={})

    assert result["valid"] is True
    assert l1.plan["action_type"] == "move"
    assert l1.plan["func_id"] == 111
