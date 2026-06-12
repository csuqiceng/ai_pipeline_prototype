from robot_modbus_lite.kinematics_engine import InverseKinematicsResult
from robot_modbus_lite.motion_plan import MotionPlanService
from robot_modbus_lite.robot_safety_checker import RobotSafetyChecker
from robot_modbus_lite.safety_precheck import SafetyPrecheckService
from robot_modbus_lite.system_config import AxisRangeConfig


class FakeKinematicsEngine:
    def __init__(self, joints=(0.0, 10.0, 20.0, 30.0, 0.0, 0.0)):
        self.joints = joints

    def inverse(self, pose, fstatus: int):
        return InverseKinematicsResult(True, tuple(self.joints), fstatus)


class NoSolutionKinematicsEngine:
    def inverse(self, pose, fstatus: int):
        return InverseKinematicsResult(False, (), fstatus, "no solution")


def make_config() -> AxisRangeConfig:
    return AxisRangeConfig(
        x=(-1000.0, 1000.0),
        y=(-1000.0, 1000.0),
        z=(0.0, 2000.0),
        safe_r_min=0.0,
        safe_r_max=2000.0,
        safe_z_min=0.0,
        safe_z_max=2000.0,
        safe_speed_max=80.0,
        safe_acc_max=80.0,
        safe_dec_max=80.0,
        joint_limits=(
            (-180.0, 180.0),
            (-90.0, 90.0),
            (-120.0, 120.0),
            (-180.0, 180.0),
            (-120.0, 120.0),
            (-360.0, 360.0),
        ),
        pose_upper_angle=45.0,
        pose_lower_angle=45.0,
        pose_cw_angle=45.0,
        pose_ccw_angle=45.0,
    )


def make_snapshot() -> dict:
    return {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }


def test_robot_safety_checker_reports_l1_position_failure():
    checker = RobotSafetyChecker(
        l1_service=SafetyPrecheckService(make_config(), max_sphere_radius=0.0),
        motion_plan_service=MotionPlanService(engine=FakeKinematicsEngine()),
    )

    result = checker.check_target(
        target_pose=(1500.0, 0.0, 100.0, 0.0, 0.0, 0.0),
        snapshot=make_snapshot(),
        speed={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )

    assert result["safe"] is False
    assert result["position_ok"] is False
    assert result["ik_ok"] is None
    assert result["blocking_level"] == "L1"
    assert "目标 X=1500.0" in result["detail_zh"]


def test_robot_safety_checker_l1_failure_suggestion_tells_operator_what_to_change():
    config = make_config()
    config = AxisRangeConfig(
        **{
            **config.to_dict(),
            "safe_r_min": 200.0,
        }
    )
    checker = RobotSafetyChecker(
        l1_service=SafetyPrecheckService(config, max_sphere_radius=0.0),
        motion_plan_service=MotionPlanService(engine=FakeKinematicsEngine()),
    )

    result = checker.check_target(
        target_pose=(0.0, 0.0, -50.0, 0.0, 0.0, 0.0),
        snapshot=make_snapshot(),
        speed={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )

    assert result["blocking_level"] == "L1"
    assert "目标 Z=-50.0" in result["detail_zh"]
    assert "目标 Z 调整到软限位" in result["suggestion_zh"]
    assert "目标 X/Y 调整到安全半径" in result["suggestion_zh"]
    assert "安全高度" in result["suggestion_zh"]


def test_robot_safety_checker_l2_failure_reports_ik_reason_and_fix_options():
    checker = RobotSafetyChecker(
        l1_service=SafetyPrecheckService(make_config(), max_sphere_radius=0.0),
        motion_plan_service=MotionPlanService(engine=NoSolutionKinematicsEngine()),
    )

    result = checker.check_target(
        target_pose=(300.0, 0.0, 500.0, 0.0, 0.0, 0.0),
        snapshot=make_snapshot(),
        speed={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )

    assert result["blocking_level"] == "L2"
    assert result["position_ok"] is True
    assert result["ik_ok"] is False
    assert "L1安全检查通过" in result["detail_zh"]
    assert "L2逆解失败" in result["detail_zh"]
    assert "未找到满足关节限位的 FSTATUS" in result["detail_zh"]
    assert "调整 RX/RY/RZ 姿态" in result["suggestion_zh"]
    assert "增加中间点" in result["suggestion_zh"]
    assert "检查关节软限位" in result["suggestion_zh"]


def test_robot_safety_checker_returns_ik_result_when_l2_passes():
    checker = RobotSafetyChecker(
        l1_service=SafetyPrecheckService(make_config(), max_sphere_radius=0.0),
        motion_plan_service=MotionPlanService(engine=FakeKinematicsEngine()),
    )

    result = checker.check_target(
        target_pose=(100.0, 0.0, 500.0, 0.0, 0.0, 0.0),
        snapshot=make_snapshot(),
        speed={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )

    assert result["safe"] is True
    assert result["position_ok"] is True
    assert result["ik_ok"] is True
    assert result["pose_ok"] is True
    assert result["ik_result"]["selected_fstatus"] == 0
    assert result["ik_result"]["joints"] == (0.0, 10.0, 20.0, 30.0, 0.0, 0.0)


def test_robot_safety_checker_blocks_pose_angle_failure():
    checker = RobotSafetyChecker(
        l1_service=SafetyPrecheckService(make_config(), max_sphere_radius=0.0),
        motion_plan_service=MotionPlanService(engine=FakeKinematicsEngine()),
    )

    result = checker.check_target(
        target_pose=(100.0, 0.0, 500.0, 0.0, 90.0, 0.0),
        snapshot=make_snapshot(),
        speed={"spd_pct": 50.0, "acc_pct": 50.0, "dec_pct": 50.0},
    )

    assert result["safe"] is False
    assert result["position_ok"] is True
    assert result["ik_ok"] is True
    assert result["pose_ok"] is False
    assert result["blocking_level"] == "POSE"
    assert "姿态夹角" in result["detail_zh"]
