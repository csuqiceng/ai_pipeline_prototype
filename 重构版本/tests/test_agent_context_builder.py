from types import SimpleNamespace

from robot_modbus_lite.agent.context_builder import AgentContextBuilder


def test_agent_context_builder_includes_pending_confirm_and_recent_dialogue():
    plan = SimpleNamespace(
        flow_draft={
            "agent_kind": "waiting_confirmation",
            "func_id": 108,
            "confirmation_text": "【复述确认】Func108 直线插补\nX=100.0mm\n速度=50.0%",
            "params": {"target_x": 100.0, "spd_pct": 50.0},
        }
    )

    text = AgentContextBuilder().build_text(
        current_scene="confirm",
        pending_confirm_plan=plan,
        recent_messages=(("user", "X100。"), ("assistant", "等待确认执行。")),
        last_execution_result="上次执行结果：完成。",
    )

    assert "当前页面：confirm" in text
    assert "待确认指令：Func108" in text
    assert "X=100.0mm" in text
    assert "用户：X100。" in text
    assert "上次执行结果：完成。" in text


def test_agent_context_builder_limits_dialogue_and_total_length():
    messages = tuple(("user", f"第{i}轮" + "很长" * 80) for i in range(20))

    text = AgentContextBuilder(max_total_chars=700, dialogue_limit=4, message_max_chars=40).build_text(
        recent_messages=messages,
        position_lines=tuple(f"P{i}=(1,2,3,0,0,0)" for i in range(20)),
    )

    assert "第0轮" not in text
    assert "第19轮" in text
    assert len(text) <= 700
    assert text.endswith("...") or len(text) < 700
