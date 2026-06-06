from robot_modbus_lite.kinematics_engine import InverseKinematicsResult
from robot_modbus_lite.motion_plan import MotionPlanService


class FakeKinematicsEngine:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def inverse(self, pose, fstatus: int):
        self.calls.append((tuple(pose), fstatus))
        return self.results.get(fstatus, InverseKinematicsResult(False, (), fstatus, "no solution"))


def test_motion_plan_selects_best_valid_fstatus_by_joint_score():
    engine = FakeKinematicsEngine(
        {
            0: InverseKinematicsResult(True, (100.0, 20.0, 10.0, 30.0, 0.0, 0.0), 0),
            1: InverseKinematicsResult(True, (10.0, 20.0, 10.0, 30.0, 0.0, 0.0), 1),
            2: InverseKinematicsResult(False, (), 2, "invalid"),
        }
    )
    service = MotionPlanService(engine=engine)

    result = service.plan(target_pose=(1, 2, 3, 4, 5, 6))

    assert result["status"] == "pass"
    assert result["selected_fstatus"] == 1
    assert result["joints"] == (10.0, 20.0, 10.0, 30.0, 0.0, 0.0)
    assert result["items"][0]["id"] == "find_best_fstatus"


def test_motion_plan_fails_when_path_contains_j4_singularity():
    engine = FakeKinematicsEngine(
        {
            0: InverseKinematicsResult(True, (10.0, 20.0, 10.0, 2.0, 0.0, 0.0), 0),
        }
    )
    service = MotionPlanService(engine=engine, singular_j4_abs_min=5.0)

    result = service.plan(target_pose=(1, 2, 3, 4, 5, 6), fstatus_candidates=(0,))

    assert result["status"] == "fail"
    assert result["selected_fstatus"] is None
    assert any(item["id"] == "path_singularity" and item["status"] == "fail" for item in result["items"])


def test_motion_plan_chooses_next_fstatus_when_best_score_is_singular():
    engine = FakeKinematicsEngine(
        {
            0: InverseKinematicsResult(True, (1.0, 1.0, 1.0, 2.0, 0.0, 0.0), 0),
            1: InverseKinematicsResult(True, (20.0, 20.0, 20.0, 30.0, 0.0, 0.0), 1),
        }
    )
    service = MotionPlanService(engine=engine, singular_j4_abs_min=5.0)

    result = service.plan(target_pose=(1, 2, 3, 4, 5, 6), fstatus_candidates=(0, 1))

    assert result["status"] == "pass"
    assert result["selected_fstatus"] == 1
    assert any(item["id"] == "path_singularity" and item["status"] == "pass" for item in result["items"])
    assert "FSTATUS=0" in result["items"][0]["message"]


def test_motion_plan_checks_five_interpolated_path_points_for_singularity():
    class InterpolationEngine:
        def __init__(self):
            self.calls = []

        def inverse(self, pose, fstatus: int):
            self.calls.append(tuple(pose))
            x = float(pose[0])
            j4 = 1.0 if abs(x - 4.0) < 0.01 else 10.0
            return InverseKinematicsResult(True, (0.0, 0.0, 0.0, j4, 0.0, 0.0), fstatus)

    engine = InterpolationEngine()
    service = MotionPlanService(engine=engine, singular_j4_abs_min=5.0)

    result = service.plan(start_pose=(0, 0, 0, 0, 0, 0), target_pose=(10, 0, 0, 0, 0, 0), fstatus_candidates=(0,))

    assert result["status"] == "fail"
    assert any(item["id"] == "path_singularity" and item["status"] == "fail" for item in result["items"])
    assert (4.0, 0.0, 0.0, 0.0, 0.0, 0.0) in engine.calls


def test_motion_plan_suggests_ry_offset_midpoint_when_direct_path_is_singular():
    class MidpointEngine:
        def __init__(self):
            self.calls = []

        def inverse(self, pose, fstatus: int):
            pose = tuple(float(value) for value in pose)
            self.calls.append((pose, fstatus))
            if abs(pose[0] - 4.0) < 0.01 and abs(pose[4]) < 0.01:
                return InverseKinematicsResult(True, (0.0, 0.0, 0.0, 1.0, 0.0, 0.0), fstatus)
            if abs(pose[0] - 5.0) < 0.01 and abs(pose[4] - 5.0) < 0.01:
                return InverseKinematicsResult(True, (0.0, 0.0, 0.0, 20.0, 0.0, 0.0), fstatus)
            return InverseKinematicsResult(True, (0.0, 0.0, 0.0, 20.0, 0.0, 0.0), fstatus)

    engine = MidpointEngine()
    service = MotionPlanService(engine=engine, singular_j4_abs_min=5.0, midpoint_ry_offset_deg=5.0)

    result = service.plan(start_pose=(0, 0, 0, 0, 0, 0), target_pose=(10, 0, 0, 0, 0, 0), fstatus_candidates=(0,))

    assert result["status"] == "fail"
    assert result["need_midpoint"] is True
    assert result["midpoint_pose"] == (5.0, 0.0, 0.0, 0.0, 5.0, 0.0)
    assert result["midpoint_fstatus"] == 0
    assert "中点" in result["suggestion"]
    assert (result["midpoint_pose"], 0) in engine.calls


def test_motion_plan_is_unavailable_without_kinematics_engine():
    service = MotionPlanService(engine=None)

    result = service.plan(target_pose=(1, 2, 3, 4, 5, 6))

    assert result["status"] == "unavailable"
    assert result["selected_fstatus"] is None
    assert result["suggestion"] == "L2运动规划预演暂不可用：未配置运动学逆解引擎。已保留L1安全检查结果，请现场确认后再执行。"


def test_motion_plan_reports_fine_grained_progress_events():
    events = []
    engine = FakeKinematicsEngine(
        {
            0: InverseKinematicsResult(True, (10.0, 20.0, 10.0, 30.0, 0.0, 0.0), 0),
        }
    )
    service = MotionPlanService(engine=engine, progress_callback=events.append)

    result = service.plan(target_pose=(1, 2, 3, 4, 5, 6), fstatus_candidates=(0,))

    assert result["status"] == "pass"
    assert [(event["stage"], event["percent"]) for event in events] == [
        ("start", 0),
        ("fstatus_scan", 25),
        ("candidate_scored", 55),
        ("singularity_check", 75),
        ("complete", 100),
    ]
