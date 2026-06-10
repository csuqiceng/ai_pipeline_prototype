from pathlib import Path

from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult


def test_ai_dialogue_simulator_configures_utf8_console_before_running():
    script = Path("tools/ai_dialogue_simulator.py").read_text(encoding="utf-8")

    assert "def _configure_console_encoding" in script
    assert "stream.reconfigure(encoding=\"utf-8\")" in script
    assert "_configure_console_encoding()" in script


def test_ai_dialogue_simulator_default_runner_uses_agent_runtime(monkeypatch):
    import tools.ai_dialogue_simulator as simulator

    calls: list[tuple[str, str]] = []

    class FakeBridge:
        def handle_text(self, text: str, *, thread_id: str, legacy_fallback):
            calls.append((text, thread_id))
            return AgentOrchestratorResult(kind="chat_answer", message="runtime answer")

    monkeypatch.setattr(simulator, "build_runtime_bridge", lambda: FakeBridge())

    runner = simulator.build_dialogue_runner()
    result = runner.handle("小正，移动到位置a")

    assert result.kind == "chat_answer"
    assert result.message == "runtime answer"
    assert calls == [("小正，移动到位置a", "dialogue-simulator")]


def test_ai_dialogue_simulator_allows_vague_motion_clarification():
    import tools.ai_dialogue_simulator as simulator

    result = AgentOrchestratorResult(kind="clarification", message="请补充明确的坐标、方向或参数。")

    issues = simulator._check_kind_intent_mismatch(result, "走到安全位置")

    assert not issues


def test_ai_dialogue_simulator_allows_flow_draft_step_handling():
    import tools.ai_dialogue_simulator as simulator

    result = AgentOrchestratorResult(kind="flow_draft", message="第1步缺少目标坐标，请输入 X,Y,Z,RX,RY,RZ。")

    issues = simulator._check_kind_intent_mismatch(result, "添加第一步是移动到位置 A")

    assert not issues


def test_ai_dialogue_simulator_allows_non_whitelisted_emergency_phrase_clarification():
    import tools.ai_dialogue_simulator as simulator

    result = AgentOrchestratorResult(kind="clarification", message="请补充明确的问题、状态查询或控制指令。")

    issues = simulator._check_kind_intent_mismatch(result, "紧急停止")

    assert not issues


def test_ai_dialogue_simulator_does_not_flag_chinese_number_after_successful_fill():
    import tools.ai_dialogue_simulator as simulator

    issues = simulator._check_chinese_number("我觉得坐标是 X 一百 Y0 Z100 速度 50", "已补齐第1步目标坐标。")

    assert not issues
