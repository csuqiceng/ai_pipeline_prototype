from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.pose_angle import PoseAngleSafetyChecker


def _draft(**overrides):
    params = {
        "target_x": 1000.0,
        "target_y": 200.0,
        "target_z": 800.0,
        "target_rx": 0.0,
        "target_ry": 45.0,
        "target_rz": 0.0,
    }
    params.update(overrides)
    return CommandDraft(
        draft_id="draft1",
        func_id=108,
        intent="move_linear",
        params=params,
        param_sources={key: "specified" for key in params},
        raw_text="走到目标",
        confidence=0.95,
    )


def test_pose_angle_checker_passes_default_90_degree_limits():
    result = PoseAngleSafetyChecker()(
        draft=_draft(),
        l2_result={"selected_fstatus": 0, "joints": (0, 20, 0, 40, 0, 0)},
        snapshot={},
    )

    assert result["status"] == "pass"
    assert all(item["status"] == "pass" for item in result["items"])


def test_pose_angle_checker_blocks_upper_angle_exceed():
    result = PoseAngleSafetyChecker()(
        draft=_draft(target_ry=-120.0),
        l2_result={"selected_fstatus": 0, "joints": (0, 20, 0, 40, 0, 0)},
        snapshot={},
    )

    assert result["status"] == "fail"
    assert any(item["id"] == "pose_upper_angle" and item["status"] == "fail" for item in result["items"])
    assert "上夹角超限" in result["suggestion"]


def test_pose_angle_checker_blocks_ccw_angle_exceed_with_configured_limit():
    checker = PoseAngleSafetyChecker({"pose_ccw_angle": 60.0})

    result = checker(
        draft=_draft(target_rz=90.0),
        l2_result={"selected_fstatus": 0, "joints": (0, 20, 0, 40, 0, 0)},
        snapshot={},
    )

    assert result["status"] == "fail"
    assert any(item["id"] == "pose_ccw_angle" and item["status"] == "fail" for item in result["items"])


def test_pose_angle_checker_uses_l2_joint_arm_direction_for_zero_offset():
    result = PoseAngleSafetyChecker({"pose_upper_angle": 5.0, "pose_lower_angle": 5.0, "pose_cw_angle": 5.0, "pose_ccw_angle": 5.0})(
        draft=_draft(target_ry=0.0, target_rz=0.0),
        l2_result={"selected_fstatus": 0, "joints": (0, 20, 0, 40, 0, 0)},
        snapshot={},
    )

    assert result["status"] == "pass"
    assert result["pitch_angle"] == 0.0
    assert result["yaw_angle"] == 0.0


def test_pose_angle_checker_blocks_pitch_against_l2_arm_direction():
    result = PoseAngleSafetyChecker({"pose_upper_angle": 30.0})(
        draft=_draft(target_ry=-45.0, target_rz=0.0),
        l2_result={"selected_fstatus": 0, "joints": (0, 20, 0, 40, 0, 0)},
        snapshot={},
    )

    assert result["status"] == "fail"
    assert result["pitch_angle"] > 30.0
    assert any(item["id"] == "pose_upper_angle" and item["status"] == "fail" for item in result["items"])


def test_pose_angle_checker_blocks_cw_yaw_against_l2_arm_direction():
    result = PoseAngleSafetyChecker({"pose_cw_angle": 30.0})(
        draft=_draft(target_ry=0.0, target_rz=0.0),
        l2_result={"selected_fstatus": 0, "joints": (90, 20, 0, 40, 0, 0)},
        snapshot={},
    )

    assert result["status"] == "fail"
    assert result["yaw_angle"] > 30.0
    assert any(item["id"] == "pose_cw_angle" and item["status"] == "fail" for item in result["items"])
