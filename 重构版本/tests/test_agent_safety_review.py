from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent
from robot_modbus_lite.kinematics_engine import InverseKinematicsResult
from robot_modbus_lite.motion_plan import MotionPlanService
from robot_modbus_lite.safety_precheck import SafetyPrecheckService
from robot_modbus_lite.system_config import AxisRangeConfig


class FakeKinematicsEngine:
    def __init__(self, results):
        self.results = results

    def inverse(self, pose, fstatus: int):
        return self.results.get(fstatus, InverseKinematicsResult(False, (), fstatus, "no solution"))


def _config():
    return AxisRangeConfig(
        x=(-100.0, 1200.0),
        y=(-200.0, 200.0),
        z=(0.0, 500.0),
        safe_r_min=0.0,
        safe_r_max=1200.0,
        safe_z_min=0.0,
        safe_z_max=500.0,
        safe_speed_max=80.0,
        safe_acc_max=70.0,
        safe_dec_max=60.0,
    )


def _snapshot():
    return {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }


def _draft(**param_overrides):
    params = {
        "target_x": 100.0,
        "target_y": 20.0,
        "target_z": 300.0,
        "target_rx": 1.0,
        "target_ry": 2.0,
        "target_rz": 3.0,
        "spd_pct": 60.0,
        "acc_pct": 45.0,
        "dec_pct": 50.0,
        "stop_cmd": 0,
        "fuzzy_pos": 0,
        "fuzzy_spd": 0,
        "fuzzy_acc": 0,
        "fuzzy_dec": 0,
        "move_type": 0,
    }
    params.update(param_overrides)
    return CommandDraft(
        draft_id="draft1",
        func_id=108,
        intent="move_linear",
        params=params,
        param_sources={key: "specified" for key in params},
        raw_text="走到 X100",
        confidence=0.9,
    )


def test_l1_review_passes_for_safe_draft():
    agent = SafetyReviewAgent(l1_service=SafetyPrecheckService(_config()))

    result = agent.review(_draft(), snapshot=_snapshot())

    assert result["valid"] is True
    assert result["status"] == "pass"
    assert result["l1"]["status"] == "pass"
    assert result["l2"] is None
    assert "L1通过" in result["summary"]


def test_l1_review_fails_and_blocks_confirmation():
    agent = SafetyReviewAgent(l1_service=SafetyPrecheckService(_config()))

    result = agent.review(_draft(target_x=1300.0), snapshot=_snapshot())

    assert result["valid"] is False
    assert result["status"] == "fail"
    assert result["l1"]["status"] == "fail"
    assert result["blocking_level"] == "L1"
    assert any(item["id"] == "target_x_range" and item["status"] == "fail" for item in result["items"])


def test_l2_review_passes_when_motion_plan_passes():
    engine = FakeKinematicsEngine(
        {0: InverseKinematicsResult(True, (10.0, 20.0, 30.0, 40.0, 0.0, 0.0), 0)}
    )
    agent = SafetyReviewAgent(
        l1_service=SafetyPrecheckService(_config()),
        motion_plan_service=MotionPlanService(engine=engine),
    )

    result = agent.review(_draft(), snapshot=_snapshot(), start_pose=(0, 0, 0, 0, 0, 0))

    assert result["valid"] is True
    assert result["status"] == "pass"
    assert result["l2"]["status"] == "pass"
    assert result["selected_fstatus"] == 0


def test_l2_review_failure_blocks_confirmation():
    engine = FakeKinematicsEngine(
        {0: InverseKinematicsResult(True, (10.0, 20.0, 30.0, 1.0, 0.0, 0.0), 0)}
    )
    agent = SafetyReviewAgent(
        l1_service=SafetyPrecheckService(_config()),
        motion_plan_service=MotionPlanService(engine=engine, singular_j4_abs_min=5.0),
    )

    result = agent.review(_draft(), snapshot=_snapshot(), start_pose=(0, 0, 0, 0, 0, 0))

    assert result["valid"] is False
    assert result["status"] == "fail"
    assert result["blocking_level"] == "L2"
    assert result["l2"]["status"] == "fail"


def test_l2_unavailable_is_warning_by_default():
    agent = SafetyReviewAgent(
        l1_service=SafetyPrecheckService(_config()),
        motion_plan_service=MotionPlanService(engine=None),
    )

    result = agent.review(_draft(), snapshot=_snapshot())

    assert result["valid"] is True
    assert result["status"] == "warning"
    assert result["l2"]["status"] == "unavailable"
    assert result["blocking_level"] is None


def test_l2_unavailable_blocks_in_strict_mode():
    agent = SafetyReviewAgent(
        l1_service=SafetyPrecheckService(_config()),
        motion_plan_service=MotionPlanService(engine=None),
        strict_l2=True,
    )

    result = agent.review(_draft(), snapshot=_snapshot())

    assert result["valid"] is False
    assert result["status"] == "fail"
    assert result["blocking_level"] == "L2"


def test_pose_angle_checker_passes_after_l2_success():
    engine = FakeKinematicsEngine(
        {0: InverseKinematicsResult(True, (10.0, 20.0, 30.0, 40.0, 0.0, 0.0), 0)}
    )
    calls = []

    def checker(*, draft, l2_result, snapshot):
        calls.append((draft.draft_id, l2_result["selected_fstatus"], snapshot["motion"]["running_state"]))
        return {
            "status": "pass",
            "items": [
                {"id": "pose_upper_angle", "level": "L2", "label": "姿态上夹角", "status": "pass", "message": "姿态夹角通过。"}
            ],
            "suggestion": None,
        }

    agent = SafetyReviewAgent(
        l1_service=SafetyPrecheckService(_config()),
        motion_plan_service=MotionPlanService(engine=engine),
        pose_angle_checker=checker,
    )

    result = agent.review(_draft(), snapshot=_snapshot(), start_pose=(0, 0, 0, 0, 0, 0))

    assert result["valid"] is True
    assert result["pose_angles"]["status"] == "pass"
    assert any(item["id"] == "pose_upper_angle" for item in result["items"])
    assert calls == [("draft1", 0, "idle")]


def test_pose_angle_checker_failure_blocks_confirmation():
    engine = FakeKinematicsEngine(
        {0: InverseKinematicsResult(True, (10.0, 20.0, 30.0, 40.0, 0.0, 0.0), 0)}
    )

    def checker(**_kwargs):
        return {
            "status": "fail",
            "items": [
                {"id": "pose_upper_angle", "level": "L2", "label": "姿态上夹角", "status": "fail", "message": "上夹角超限。"}
            ],
            "suggestion": "请调整RY姿态。",
        }

    agent = SafetyReviewAgent(
        l1_service=SafetyPrecheckService(_config()),
        motion_plan_service=MotionPlanService(engine=engine),
        pose_angle_checker=checker,
    )

    result = agent.review(_draft(), snapshot=_snapshot(), start_pose=(0, 0, 0, 0, 0, 0))

    assert result["valid"] is False
    assert result["blocking_level"] == "POSE"
    assert result["pose_angles"]["status"] == "fail"
    assert result["suggestion"] == "请调整RY姿态。"
