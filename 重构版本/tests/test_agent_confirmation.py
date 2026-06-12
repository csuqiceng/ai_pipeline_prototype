import pytest

from robot_modbus_lite.agent.confirmation import (
    ConfirmationAgent,
    ConfirmationError,
    DraftStatus,
)
from robot_modbus_lite.agent.drafts import CommandDraft


def _linear_draft(**overrides):
    params = {
        "target_x": 1000.0,
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
    sources = {
        "target_x": "specified",
        "target_y": "inherited",
        "target_z": "specified",
        "target_rx": "inherited",
        "target_ry": "inherited",
        "target_rz": "inherited",
        "spd_pct": "specified",
        "acc_pct": "controller",
        "dec_pct": "controller",
        "stop_cmd": "default",
        "fuzzy_pos": "default",
        "fuzzy_spd": "default",
        "fuzzy_acc": "default",
        "fuzzy_dec": "default",
        "move_type": "default",
    }
    data = {
        "draft_id": "draft1",
        "func_id": 108,
        "intent": "move_linear",
        "params": params,
        "param_sources": sources,
        "raw_text": "走到 X1000 Z300",
        "confidence": 0.9,
        "precheck_result": {"valid": True, "summary": "L1通过，L2待接入。"},
    }
    data.update(overrides)
    return CommandDraft(**data)


def test_render_confirmation_text_includes_params_sources_and_precheck():
    text = ConfirmationAgent().render_confirmation_text(_linear_draft())

    assert "【复述确认】Func108 直线插补/PTP" in text
    assert "X=1000.0（指定）  Y=20.0（继承当前）  Z=300.0（指定）" in text
    assert "RX=1.0°（继承当前）  RY=2.0°（继承当前）  RZ=3.0°（继承当前）" in text
    assert "速度=60.0%（指定）" in text
    assert "加速度=45.0%（继承安全参数）" in text
    assert "模式：绝对定位" in text
    assert "安全预检：通过，L1通过，L2待接入。" in text
    assert text.endswith("确认执行？")


def test_render_confirmation_text_marks_incremental_motion_mode():
    text = ConfirmationAgent().render_confirmation_text(
        _linear_draft(
            params={
                **_linear_draft().params,
                "target_x": 0.0,
                "target_y": 0.0,
                "target_z": 50.0,
                "position_increment": 1,
                "fuzzy_pos": 1,
            },
            param_sources={
                **_linear_draft().param_sources,
                "target_x": "inherited",
                "target_y": "inherited",
                "target_z": "incremental",
                "position_increment": "specified",
                "fuzzy_pos": "specified",
            },
            raw_text="小正，上升50mm",
        )
    )

    assert "【复述确认】Func108 直线插补/PTP" in text
    assert "Z=50.0（增量计算）" in text
    assert "模式：增量定位" in text


def test_render_confirmation_text_includes_auxiliary_params():
    delay_text = ConfirmationAgent().render_confirmation_text(
        _linear_draft(
            func_id=109,
            intent="delay_blocking",
            params={"delay_sec": 2.0},
            param_sources={"delay_sec": "specified"},
            raw_text="等待2秒",
            precheck_result={"valid": True, "summary": "L1通过。"},
        )
    )
    parallel_text = ConfirmationAgent().render_confirmation_text(
        _linear_draft(
            func_id=110,
            intent="delay_parallel",
            params={"delay_sec": 5.0},
            param_sources={"delay_sec": "specified"},
            raw_text="并行延时5秒",
            precheck_result={"valid": True, "summary": "L1通过。"},
        )
    )
    io_text = ConfirmationAgent().render_confirmation_text(
        _linear_draft(
            func_id=120,
            intent="io",
            params={"io_no": 1, "io_action": 1},
            param_sources={"io_no": "specified", "io_action": "specified"},
            raw_text="IO1开",
            precheck_result={"valid": True, "summary": "L1通过。"},
        )
    )

    assert "阻塞延时 2.0 秒" in delay_text
    assert "并行延时 5.0 秒" in parallel_text
    assert "打开 IO-1" in io_text


def test_confirm_waiting_draft_returns_query_record_once():
    agent = ConfirmationAgent(timeout_sec=10.0)
    session = agent.begin(_linear_draft(), now=100.0, status_signature="s1", safety_signature="p1")

    record = agent.confirm(session.draft_id, now=105.0, status_signature="s1", safety_signature="p1")

    assert record.query_key == "agent:draft1"
    assert record.func_num == 108
    assert record.params["target_x"] == 1000.0
    assert agent.get_status("draft1") == DraftStatus.CONFIRMED
    with pytest.raises(ConfirmationError, match="已结束"):
        agent.confirm("draft1", now=106.0, status_signature="s1", safety_signature="p1")


def test_reject_discards_waiting_draft():
    agent = ConfirmationAgent(timeout_sec=10.0)
    agent.begin(_linear_draft(), now=100.0, status_signature="s1", safety_signature="p1")

    agent.reject("draft1")

    assert agent.get_status("draft1") == DraftStatus.REJECTED
    with pytest.raises(ConfirmationError, match="已结束"):
        agent.confirm("draft1", now=101.0, status_signature="s1", safety_signature="p1")


def test_confirm_rejects_expired_draft():
    agent = ConfirmationAgent(timeout_sec=5.0)
    agent.begin(_linear_draft(), now=100.0, status_signature="s1", safety_signature="p1")

    with pytest.raises(ConfirmationError, match="已过期"):
        agent.confirm("draft1", now=106.0, status_signature="s1", safety_signature="p1")

    assert agent.get_status("draft1") == DraftStatus.EXPIRED


def test_confirm_rejects_when_controller_signature_changed():
    agent = ConfirmationAgent(timeout_sec=10.0)
    agent.begin(_linear_draft(), now=100.0, status_signature="s1", safety_signature="p1")

    with pytest.raises(ConfirmationError, match="控制器状态已变化"):
        agent.confirm("draft1", now=101.0, status_signature="s2", safety_signature="p1")

    assert agent.get_status("draft1") == DraftStatus.EXPIRED


def test_precheck_failed_blocks_confirmation():
    agent = ConfirmationAgent(timeout_sec=10.0)
    agent.begin(_linear_draft(), now=100.0, status_signature="s1", safety_signature="p1")

    agent.mark_precheck_failed("draft1", {"valid": False, "summary": "越界"})

    assert agent.get_status("draft1") == DraftStatus.PRECHECK_FAILED
    with pytest.raises(ConfirmationError, match="预检失败"):
        agent.confirm("draft1", now=101.0, status_signature="s1", safety_signature="p1")
