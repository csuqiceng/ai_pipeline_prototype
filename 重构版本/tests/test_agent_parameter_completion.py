import pytest

from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent
from robot_modbus_lite.agent.parameter_completion import (
    ControllerSnapshot,
    ParameterCompletionAgent,
    ParameterCompletionError,
)


def test_completes_linear_move_from_explicit_and_inherited_pose():
    understanding = CommandUnderstandingAgent().understand("走到 X1000 Z300 速度60%")
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

    draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

    assert draft.func_id == 108
    assert draft.params["target_x"] == 1000.0
    assert draft.params["target_y"] == 20.0
    assert draft.params["target_z"] == 300.0
    assert draft.params["target_rx"] == 1.0
    assert draft.params["target_ry"] == 2.0
    assert draft.params["target_rz"] == 3.0
    assert draft.params["spd_pct"] == 60.0
    assert draft.params["acc_pct"] == 45.0
    assert draft.params["dec_pct"] == 50.0
    assert draft.param_sources["target_x"] == "specified"
    assert draft.param_sources["target_y"] == "inherited"
    assert draft.param_sources["acc_pct"] == "controller"


def test_completion_applies_incremental_cartesian_offsets_from_current_pose():
    understanding = CommandUnderstandingAgent().understand("向左移动200")
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

    draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

    assert draft.params["target_x"] == 210.0
    assert draft.params["target_y"] == 20.0
    assert draft.params["target_z"] == 30.0
    assert draft.params["fuzzy_pos"] == 0
    assert draft.params["position_increment"] == 1
    assert draft.param_sources["target_x"] == "incremental"
    assert draft.param_sources["target_y"] == "inherited"
    assert draft.param_sources["position_increment"] == "specified"


def test_completion_marks_absolute_cartesian_position_increment_zero():
    understanding = CommandUnderstandingAgent().understand("走到 X1000")
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

    draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

    assert draft.params["position_increment"] == 0
    assert draft.param_sources["position_increment"] == "specified"


def test_completion_builds_func112_executable_draft_with_linear_parameters():
    understanding = CommandUnderstandingAgent().understand("规划路径走到 X1000 Z300")
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

    draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

    assert draft.func_id == 112
    assert draft.intent == "continuous_path"
    assert draft.params["target_x"] == 1000.0
    assert draft.params["target_y"] == 20.0
    assert draft.params["target_z"] == 300.0
    assert draft.params["position_increment"] == 0


def test_completion_blocks_when_controller_is_moving():
    understanding = CommandUnderstandingAgent().understand("走到 X1000")
    snapshot = ControllerSnapshot(
        current_pose={},
        safety_params={},
        is_moving=True,
        read_ok=True,
    )

    with pytest.raises(ParameterCompletionError, match="当前设备运动中"):
        ParameterCompletionAgent(lambda: snapshot).complete(understanding)


def test_completion_blocks_when_controller_read_failed():
    understanding = CommandUnderstandingAgent().understand("走到 X1000")
    snapshot = ControllerSnapshot(
        current_pose={},
        safety_params={},
        is_moving=False,
        read_ok=False,
    )

    with pytest.raises(ParameterCompletionError, match="控制器实时值不可用"):
        ParameterCompletionAgent(lambda: snapshot).complete(understanding)


def test_completion_blocks_when_inherited_pose_value_missing():
    understanding = CommandUnderstandingAgent().understand("走到 X1000")
    snapshot = ControllerSnapshot(
        current_pose={"target_x": 0.0},
        safety_params={"spd_pct": 40.0, "acc_pct": 45.0, "dec_pct": 50.0},
        is_moving=False,
        read_ok=True,
    )

    with pytest.raises(ParameterCompletionError, match="缺少实时位姿"):
        ParameterCompletionAgent(lambda: snapshot).complete(understanding)


def test_completion_builds_delay_and_io_drafts_without_controller_snapshot():
    def fail_snapshot():
        raise AssertionError("non-motion drafts must not read controller pose")

    agent = ParameterCompletionAgent(fail_snapshot)

    delay = agent.complete(CommandUnderstandingAgent().understand("等待2秒"))
    parallel_delay = agent.complete(CommandUnderstandingAgent().understand("并行延时5秒"))
    io_on = agent.complete(CommandUnderstandingAgent().understand("IO1开"))

    assert delay.func_id == 109
    assert delay.params == {"delay_sec": 2.0}
    assert delay.param_sources == {"delay_sec": "specified"}
    assert parallel_delay.func_id == 110
    assert parallel_delay.params == {"delay_sec": 5.0}
    assert io_on.func_id == 120
    assert io_on.params == {"io_no": 1, "io_action": 1}


def test_completion_builds_joint_and_virtual_jog_drafts():
    snapshot = ControllerSnapshot(
        safety_params={"spd_pct": 40.0, "acc_pct": 45.0, "dec_pct": 50.0},
        is_moving=False,
        read_ok=True,
    )
    agent = ParameterCompletionAgent(lambda: snapshot)

    joint = agent.complete(CommandUnderstandingAgent().understand("小正，J1转到45度30%速度"))
    virtual = agent.complete(CommandUnderstandingAgent().understand("小正，RY反转15度"))

    assert joint.func_id == 106
    assert joint.intent == "joint_jog"
    assert joint.params["axis_no"] == 0
    assert joint.params["pos_val"] == 45.0
    assert joint.params["spd_pct"] == 30.0
    assert joint.params["acc_pct"] == 45.0
    assert joint.params["dec_pct"] == 50.0
    assert joint.params["fuzzy_pos"] == 0
    assert joint.param_sources["spd_pct"] == "specified"
    assert joint.param_sources["acc_pct"] == "controller"
    assert virtual.func_id == 107
    assert virtual.params["axis_no"] == 10
    assert virtual.params["pos_val"] == -15.0
    assert virtual.params["spd_pct"] == 40.0


def test_completion_blocks_joint_jog_when_controller_is_moving():
    understanding = CommandUnderstandingAgent().understand("小正，J1转到45度")
    snapshot = ControllerSnapshot(is_moving=True, read_ok=True)

    with pytest.raises(ParameterCompletionError, match="当前设备运动中"):
        ParameterCompletionAgent(lambda: snapshot).complete(understanding)
