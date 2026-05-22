from robot_modbus_lite.dashboard_query import DashboardQueryService


def test_dashboard_query_answers_communication_from_v21_board():
    snapshot = {
        "boards": {
            "communication_faults": {
                "ecat_ok": True,
                "controller": "online",
                "realtime_feedback": "online",
                "io_status": "0",
            }
        }
    }

    answer = DashboardQueryService().answer("通讯正常吗", snapshot)

    assert answer is not None
    assert answer.board_key == "communication_faults"
    assert "通讯正常" in answer.text
    assert "实时反馈在线" in answer.text


def test_dashboard_query_answers_stale_realtime_feedback_as_warning():
    snapshot = {
        "boards": {
            "communication_faults": {
                "ecat_ok": False,
                "controller": "stale",
                "realtime_feedback": "stale",
                "feedback_age_ms": 1350,
                "io_status": "0",
            }
        }
    }

    answer = DashboardQueryService().answer("通讯正常吗", snapshot)

    assert answer is not None
    assert answer.board_key == "communication_faults"
    assert answer.priority == "high"
    assert "实时反馈已过期" in answer.text
    assert "1350ms" in answer.text


def test_dashboard_query_answers_action_feasibility_with_block_reason():
    snapshot = {
        "boards": {
            "action_feasibility": {
                "channel_idle": False,
                "precheck_status": "pass",
                "motion_status": "unavailable",
                "current_func": "FUNC108",
            }
        }
    }

    answer = DashboardQueryService().answer("现在能不能执行", snapshot)

    assert answer is not None
    assert answer.board_key == "action_feasibility"
    assert "当前不建议执行" in answer.text
    assert "通道忙" in answer.text


def test_dashboard_query_answers_safety_and_process_preview():
    snapshot = {
        "boards": {
            "safety_boundary": {
                "current_r": "120.0",
                "current_z": "240.0",
                "safe_r_range": (50.0, 700.0),
                "safe_z_range": (10.0, 650.0),
            },
            "process_preview": {
                "l3_status": "fail",
                "current_flow_name": "取放流程",
                "flow_current_step": "第2步",
                "progress_percent": 67,
                "risk_summary": ["目标 X 超出软限位"],
            },
        }
    }

    safety = DashboardQueryService().answer("当前位置安全吗", snapshot)
    process = DashboardQueryService().answer("流程预演到哪了", snapshot)

    assert safety is not None
    assert safety.board_key == "safety_boundary"
    assert "当前R=120.0" in safety.text
    assert process is not None
    assert process.board_key == "process_preview"
    assert "取放流程" in process.text
    assert "进度 67%" in process.text
    assert "目标 X 超出软限位" in process.text


def test_dashboard_query_answers_joint_soft_limits():
    snapshot = {
        "boards": {
            "safety_boundary": {
                "current_r": "120.0",
                "current_z": "240.0",
                "safe_r_range": (50.0, 700.0),
                "safe_z_range": (10.0, 650.0),
                "joint_limits": (
                    (-180.0, 180.0),
                    (-90.0, 90.0),
                    (-120.0, 120.0),
                    (-180.0, 180.0),
                    (-120.0, 120.0),
                    (-360.0, 360.0),
                ),
            }
        }
    }

    answer = DashboardQueryService().answer("J2关节软限位是多少", snapshot)

    assert answer is not None
    assert answer.board_key == "safety_boundary"
    assert "J2=-90.0~90.0" in answer.text


def test_dashboard_query_answers_device_status_and_motion_adaptation():
    snapshot = {
        "boards": {
            "device_status": {
                "system_state": "运行中",
                "estop": False,
                "pause": False,
                "alarm": True,
                "dpos_c": ("1", "2", "3"),
            },
            "process_adaptation": {
                "l2_status": "fail",
                "fstatus": 3,
                "singularity": True,
                "suggestion": "建议调整目标姿态",
            },
        }
    }

    status = DashboardQueryService().answer("现在设备状态", snapshot)
    adaptation = DashboardQueryService().answer("这个位置能到吗", snapshot)

    assert status is not None
    assert status.priority == "high"
    assert "有报警" in status.text
    assert adaptation is not None
    assert adaptation.priority == "high"
    assert "建议调整目标姿态" in adaptation.text


def test_dashboard_query_explains_l2_midpoint_and_fstatus_details():
    snapshot = {
        "boards": {
            "process_adaptation": {
                "l2_status": "fail",
                "fstatus": "-",
                "singularity": True,
                "suggestion": "检测到直线路径接近奇异区，建议经中点绕行后再执行。",
                "need_midpoint": True,
                "midpoint_pose": (10.0, 20.0, 30.0, 0.0, 5.0, 0.0),
                "midpoint_fstatus": 3,
                "rejected_fstatuses": (0, 1),
            }
        }
    }

    answer = DashboardQueryService().answer("为什么奇异点，FSTATUS和中点建议是什么", snapshot)

    assert answer is not None
    assert answer.board_key == "process_adaptation"
    assert answer.priority == "high"
    assert "L2状态 fail" in answer.text
    assert "FSTATUS表示控制器逆解姿态候选" in answer.text
    assert "FSTATUS=0、1" in answer.text
    assert "建议中点=(10.0, 20.0, 30.0, 0.0, 5.0, 0.0)" in answer.text
    assert "中点FSTATUS=3" in answer.text


def test_dashboard_query_explains_why_action_is_blocked_and_how_to_handle():
    snapshot = {
        "boards": {
            "action_feasibility": {
                "channel_idle": False,
                "precheck_status": "fail",
                "motion_status": "fail",
                "current_func": "FUNC108",
                "result": "12",
            }
        }
    }

    answer = DashboardQueryService().answer("为什么不能执行，建议怎么处理", snapshot)

    assert answer is not None
    assert answer.board_key == "action_feasibility"
    assert answer.priority == "high"
    assert "原因：" in answer.text
    assert "通道忙" in answer.text
    assert "L1安全预检未通过" in answer.text
    assert "L2运动规划未通过" in answer.text
    assert "建议：" in answer.text
    assert "等待通道空闲" in answer.text
    assert "先处理安全预检失败项" in answer.text


def test_dashboard_query_explains_risk_summary_with_suggestions():
    snapshot = {
        "boards": {
            "process_preview": {
                "l3_status": "fail",
                "current_flow_name": "取放流程",
                "flow_current_step": "第2步",
                "progress_percent": 67,
                "risk_summary": ["目标 X 超出软限位", "预计累计误差超限"],
            },
        }
    }

    answer = DashboardQueryService().answer("为什么有风险，建议怎么处理", snapshot)

    assert answer is not None
    assert answer.board_key == "process_preview"
    assert answer.priority == "high"
    assert "风险原因：" in answer.text
    assert "目标 X 超出软限位" in answer.text
    assert "预计累计误差超限" in answer.text
    assert "处理建议：" in answer.text
    assert "采纳安全建议" in answer.text


def test_dashboard_query_answers_why_motion_not_allowed():
    snapshot = {
        "boards": {
            "action_feasibility": {
                "channel_idle": False,
                "precheck_status": "fail",
                "motion_status": "normal",
            }
        }
    }

    answer = DashboardQueryService().answer("为什么现在不能动", snapshot)

    assert answer is not None
    assert answer.board_key == "action_feasibility"
    assert "不能执行" in answer.text
    assert "通道忙" in answer.text
    assert "L1安全预检未通过" in answer.text


def test_dashboard_query_answers_process_preview_progress():
    snapshot = {
        "boards": {
            "process_preview": {
                "l3_status": "running",
                "current_flow_name": "A到B",
                "flow_current_step": "第3步",
                "progress_percent": 45,
                "risk_summary": [],
            },
        }
    }

    answer = DashboardQueryService().answer("流程预演到哪一步了", snapshot)

    assert answer is not None
    assert answer.board_key == "process_preview"
    assert "流程预演" in answer.text
    assert "第3步" in answer.text
    assert "45%" in answer.text


def test_dashboard_query_answers_recovery_after_alarm():
    snapshot = {
        "boards": {
            "device_status": {
                "system_state": "报警",
                "estop": False,
                "pause": False,
                "alarm": True,
                "alarm_code": "E12",
                "alarm_text": "J2速度超限",
            }
        }
    }

    answer = DashboardQueryService().answer("报警后我该怎么处理", snapshot)

    assert answer is not None
    assert answer.board_key == "device_status"
    assert answer.priority == "high"
    assert "报警" in answer.text
    assert "复位" in answer.text or "确认" in answer.text


def test_dashboard_query_answers_v20_position_and_joint_queries():
    snapshot = {
        "boards": {
            "device_status": {
                "dpos_c": (350.0, 200.0, 500.0, 0.0, 90.0, 0.0),
                "dpos_j": (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
            }
        }
    }

    position = DashboardQueryService().answer("小正，当前位置", snapshot)
    j1 = DashboardQueryService().answer("小正，J1角度", snapshot)
    all_joints = DashboardQueryService().answer("小正，各轴角度", snapshot)

    assert position is not None
    assert position.board_key == "device_status"
    assert "X=350.0" in position.text
    assert j1 is not None
    assert "J1=1.0" in j1.text
    assert all_joints is not None
    assert "J6=6.0" in all_joints.text


def test_dashboard_query_answers_v20_alarm_speed_and_feasibility_queries():
    snapshot = {
        "boards": {
            "device_status": {"alarm": True, "alarm_code": "E12", "alarm_text": "J2速度超限"},
            "motion_limits": {"speed": "35%", "motion_percent": "空闲", "safe_speed_max": 100},
            "action_feasibility": {"channel_idle": True, "precheck_status": "pass", "motion_status": "normal"},
        }
    }

    alarm = DashboardQueryService().answer("小正，有没有报警", snapshot)
    speed = DashboardQueryService().answer("小正，速度多少", snapshot)
    feasibility = DashboardQueryService().answer("小正，能不能到350 200 500", snapshot)

    assert alarm is not None
    assert alarm.priority == "high"
    assert "E12" in alarm.text
    assert speed is not None
    assert speed.board_key == "motion_limits"
    assert "35%" in speed.text
    assert feasibility is not None
    assert feasibility.board_key == "action_feasibility"
    assert "当前可执行" in feasibility.text


def test_dashboard_query_checks_v20_target_reachability_against_xyz_limits():
    snapshot = {
        "boards": {
            "action_feasibility": {"channel_idle": True, "precheck_status": "pass", "motion_status": "normal"},
            "safety_boundary": {
                "x_range": (-1000.0, 1000.0),
                "y_range": (-1000.0, 1000.0),
                "z_range": (0.0, 1000.0),
            },
        }
    }

    reachable = DashboardQueryService().answer("小正，能不能到 350 200 500", snapshot)
    blocked = DashboardQueryService().answer("小正，能不能到 5000 200 500", snapshot)

    assert reachable is not None
    assert reachable.board_key == "action_feasibility"
    assert "目标点 X=350.0，Y=200.0，Z=500.0" in reachable.text
    assert "基础边界检查通过" in reachable.text
    assert blocked is not None
    assert blocked.priority == "high"
    assert "目标 X=5000.0 超出软限位 -1000.0~1000.0" in blocked.text
