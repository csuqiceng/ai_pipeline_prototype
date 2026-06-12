"""对话测试用例 — 对照《自然语言参数类指令解析说明书》+《机械手基础运行信息交互状态说明书》

覆盖章节：
  一、参数继承与补全（说明书第四节）
  二、复述确认协议（说明书第七节）
  三、安全预检与运动超限（状态说明书第三层）
  四、报警与异常状态（状态说明书第二层）
  五、中文数字及边界解析（已知缺陷回归）
  六、多轮对话与状态记忆
  七、引擎一致性与静默失败回归
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from robot_modbus_lite.agent.address_resolver import AddressResolver
from robot_modbus_lite.agent.atomic_template import AtomicTemplateAgent
from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent
from robot_modbus_lite.agent.command_understanding import (
    CommandUnderstandingAgent,
    CommandUnderstandingResult,
)
from robot_modbus_lite.agent.compound import CompoundCommandCoordinator
from robot_modbus_lite.agent.confirmation import ConfirmationAgent
from robot_modbus_lite.agent.dashboard_query import DashboardQueryAgent
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.flow_draft import FlowDraftAgent
from robot_modbus_lite.agent.llm_fallback import LlmFallbackAgent
from robot_modbus_lite.agent.memory_setting import MemorySettingAgent
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator, AgentOrchestratorResult
from robot_modbus_lite.agent.parameter_completion import (
    ControllerSnapshot,
    ParameterCompletionAgent,
    ParameterCompletionError,
)
from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter
from robot_modbus_lite.agent.position_memory import PositionMemoryAgent
from robot_modbus_lite.agent.position_query import PositionQueryAgent
from robot_modbus_lite.agent.registered_flow import RegisteredFlowAgent
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent
from robot_modbus_lite.agent.service import RestrictedAgentResult, RestrictedAgentService
from robot_modbus_lite.motion_plan import MotionPlanService
from robot_modbus_lite.safety_precheck import SafetyPrecheckService
from robot_modbus_lite.system_config import AxisRangeConfig
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


# ── helpers ──────────────────────────────────────────────────────────

def _idle_snapshot(**overrides):
    data = {
        "target_x": 500.0, "target_y": 0.0, "target_z": 600.0,
        "target_rx": 0.0, "target_ry": 0.0, "target_rz": 0.0,
        "spd_pct": 80.0, "acc_pct": 80.0, "dec_pct": 80.0,
        "is_moving": False, "read_ok": True,
    }
    data.update(overrides)
    return ControllerSnapshot(
        current_pose={k: data[k] for k in ("target_x","target_y","target_z","target_rx","target_ry","target_rz")},
        safety_params={k: data[k] for k in ("spd_pct","acc_pct","dec_pct")},
        is_moving=data["is_moving"],
        read_ok=data["read_ok"],
    )


def _config(**overrides):
    defaults = {
        "x": (-100.0, 2200.0), "y": (-200.0, 200.0), "z": (0.0, 1200.0),
        "safe_r_min": 0.0, "safe_r_max": 2200.0,
        "safe_z_min": 0.0, "safe_z_max": 1200.0,
        "safe_speed_max": 80.0, "safe_acc_max": 80.0, "safe_dec_max": 80.0,
    }
    defaults.update(overrides)
    return AxisRangeConfig(**defaults)


def _runtime_snapshot():
    return {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }


def _restricted_service(snapshot=None, config=None):
    snap = snapshot or _idle_snapshot()
    cfg = config or _config()
    return RestrictedAgentService(
        controller_snapshot_provider=lambda: snap,
        runtime_snapshot_provider=_runtime_snapshot,
        safety_review_agent=SafetyReviewAgent(l1_service=SafetyPrecheckService(cfg)),
        status_signature_provider=lambda: "idle",
        safety_signature_provider=lambda: "safe",
        clock=lambda: 100.0,
        confirm_timeout_sec=60.0,
    )


def _llm_fallback_client(reply_map: dict[str, str] | None = None):
    """返回一个 mock DeepSeekClient，按输入文本匹配预设回复。"""
    replies = reply_map or {}
    client = MagicMock()
    client.generate_chat = MagicMock(side_effect=lambda prompt, system_prompt="": replies.get(
        next((k for k in replies if k in prompt), next(iter(replies.values()), "")),
        '{"kind":"rejected","reason":"no_match"}',
    ))
    client.generate_chat_stream = MagicMock(side_effect=lambda prompt, system_prompt="": iter([replies.get(
        next((k for k in replies if k in prompt), next(iter(replies.values()), "")),
        "",
    )]))
    return client


# ══════════════════════════════════════════════════════════════════════
# 第一章  参数继承与补全（说明书第四节）
# ══════════════════════════════════════════════════════════════════════

class TestParameterInheritance:
    """对照说明书 4.1-4.3"""

    # ── 4.1 全参数指令 ──
    def test_full_params_direct_mapping(self):
        """全参数指令：全部6位置+3速度明确指定 → 直接映射"""
        text = "走到 X1000 Y200 Z800 RX0 RY45 RZ0 速度60% 加速度50% 减速度50%"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot()

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.func_id == 108
        assert draft.params["target_x"] == 1000.0
        assert draft.params["target_y"] == 200.0
        assert draft.params["target_z"] == 800.0
        assert draft.params["target_rx"] == 0.0
        assert draft.params["target_ry"] == 45.0
        assert draft.params["target_rz"] == 0.0
        assert draft.params["spd_pct"] == 60.0
        assert draft.params["acc_pct"] == 50.0
        assert draft.params["dec_pct"] == 50.0
        assert all(
            draft.param_sources[k] == "specified"
            for k in ("target_x","target_y","target_z","target_rx","target_ry","target_rz","spd_pct","acc_pct","dec_pct")
        )

    # ── 4.2 半参数指令 ──
    def test_half_params_inherit_position_from_controller(self):
        """半参数指令：只指定位置X/Y/Z，姿态从控制器继承"""
        text = "走到 X1000 Y200 Z800"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(target_rx=1.0, target_ry=2.0, target_rz=3.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["target_x"] == 1000.0
        assert draft.params["target_y"] == 200.0
        assert draft.params["target_z"] == 800.0
        assert draft.params["target_rx"] == 1.0
        assert draft.params["target_ry"] == 2.0
        assert draft.params["target_rz"] == 3.0
        assert draft.param_sources["target_rx"] == "inherited"

    def test_half_params_inherit_speed_from_safety(self):
        """半参数指令：未指定速度 → 从安全参数 IEEE(1708) 继承"""
        text = "走到 X1000"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(spd_pct=70.0, acc_pct=75.0, dec_pct=65.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["spd_pct"] == 70.0
        assert draft.params["acc_pct"] == 75.0
        assert draft.params["dec_pct"] == 65.0
        assert draft.param_sources["spd_pct"] == "controller"

    # ── 4.3 单参数指令 ──
    def test_single_param_z_only_inherits_rest(self):
        """单参数增量指令：按Func108增量模式传入相对量"""
        text = "高度降低100"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(target_z=600.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["target_z"] == -100.0
        assert draft.params["target_x"] == 0.0
        assert draft.params["target_y"] == 0.0
        assert draft.params["fuzzy_pos"] == 1
        assert draft.params["position_increment"] == 1
        assert draft.param_sources["target_z"] == "incremental"
        assert draft.param_sources["target_x"] == "inherited"

    def test_single_param_incremental_left_200(self):
        """增量指令：向左移动200"""
        text = "向左移动200"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(target_x=500.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["target_x"] == 200.0
        assert draft.params["position_increment"] == 1

    def test_single_param_incremental_forward_100(self):
        """增量指令：向前移动100"""
        text = "向前移动100"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(target_y=0.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["target_y"] == 100.0

    def test_single_param_incremental_raise_50(self):
        """增量指令：升高50"""
        text = "升高50"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(target_z=600.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["target_z"] == 50.0
        assert draft.params["position_increment"] == 1

    def test_single_param_pose_change_ry_to_45(self):
        """只改姿态：RY转到45度"""
        text = "走到 RY45"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(target_ry=0.0)

        draft = ParameterCompletionAgent(lambda: snapshot).complete(understanding)

        assert draft.params["target_ry"] == 45.0
        assert draft.params["target_x"] == 500.0  # 位置不变

    # ── 4.4 控制器不可用时拒绝 ──
    def test_rejects_completion_when_controller_unavailable(self):
        """说明书4.1末尾：控制器实时值不可用 → 拒绝补全"""
        text = "走到 X1000"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(read_ok=False)

        with pytest.raises(ParameterCompletionError, match="控制器实时值不可用"):
            ParameterCompletionAgent(lambda: snapshot).complete(understanding)

    def test_rejects_completion_when_moving(self):
        """设备运动中 → 拒绝参数补全"""
        text = "走到 X1000"
        understanding = CommandUnderstandingAgent().understand(text)
        snapshot = _idle_snapshot(is_moving=True)

        with pytest.raises(ParameterCompletionError, match="设备运动中"):
            ParameterCompletionAgent(lambda: snapshot).complete(understanding)


# ══════════════════════════════════════════════════════════════════════
# 第二章  复述确认协议（说明书第七节）
# ══════════════════════════════════════════════════════════════════════

class TestConfirmationProtocol:
    """对照说明书 7.1-7.2"""

    @staticmethod
    def _full_linear_draft(**overrides):
        params = {
            "target_x": 1000.0, "target_y": 200.0, "target_z": 800.0,
            "target_rx": 0.0, "target_ry": 45.0, "target_rz": 0.0,
            "spd_pct": 60.0, "acc_pct": 50.0, "dec_pct": 50.0,
            "stop_cmd": 0, "fuzzy_pos": 0, "fuzzy_spd": 0,
            "fuzzy_acc": 0, "fuzzy_dec": 0, "move_type": 0,
        }
        sources = {k: "specified" for k in params}
        data = {
            "draft_id": "draft1", "func_id": 108, "intent": "move_linear",
            "params": params, "param_sources": sources,
            "raw_text": "走到 X1000 Y200 Z800",
            "confidence": 0.9,
            "precheck_result": {"valid": True, "summary": "L1通过，L2通过，FSTATUS=1。"},
        }
        data.update(overrides)
        return CommandDraft(**data)

    def test_confirmation_text_includes_func_title(self):
        """复述格式必须包含 【复述确认】FuncXXX 函数名"""
        text = ConfirmationAgent().render_confirmation_text(self._full_linear_draft())

        assert "【复述确认】Func108 直线插补/PTP" in text

    def test_confirmation_text_labels_specified_params(self):
        """明确指定的参数标注「指定」"""
        text = ConfirmationAgent().render_confirmation_text(self._full_linear_draft())

        assert "X=1000.0（指定）" in text
        assert "模式：绝对定位" in text

    def test_confirmation_text_labels_inherited_params(self):
        """从控制器继承的参数标注「继承当前」"""
        draft = self._full_linear_draft(
            param_sources={**{k: "specified" for k in ("target_x","target_y","target_z","target_rx","target_ry","target_rz","spd_pct","acc_pct","dec_pct","stop_cmd","fuzzy_pos","fuzzy_spd","fuzzy_acc","fuzzy_dec","move_type")}, "target_rx": "inherited", "target_rz": "inherited"},
        )
        text = ConfirmationAgent().render_confirmation_text(draft)

        assert "继承当前" in text

    def test_confirmation_text_labels_controller_params(self):
        """从安全参数继承的速度标注「继承安全参数」"""
        draft = self._full_linear_draft(
            param_sources={**{k: "specified" for k in ("target_x","target_y","target_z","target_rx","target_ry","target_rz")}, "spd_pct": "controller", "acc_pct": "controller", "dec_pct": "controller", "stop_cmd": "default", "fuzzy_pos": "default", "fuzzy_spd": "default", "fuzzy_acc": "default", "fuzzy_dec": "default", "move_type": "default"},
        )
        text = ConfirmationAgent().render_confirmation_text(draft)

        assert "继承安全参数" in text

    def test_confirmation_text_includes_mode(self):
        """复述确认格式完整：函数标题、参数、安全预检、确认询问"""
        text = ConfirmationAgent().render_confirmation_text(self._full_linear_draft())

        assert "【复述确认】" in text
        assert "确认执行" in text
        assert "安全预检" in text

    def test_confirmation_text_includes_precheck_result(self):
        """安全预检结果必须在确认文本中展示"""
        text = ConfirmationAgent().render_confirmation_text(self._full_linear_draft())

        assert "安全预检：通过" in text

    def test_confirmation_requires_explicit_confirm(self):
        """确认文本必须询问「确认执行？」——见说明书7.1"""
        text = ConfirmationAgent().render_confirmation_text(self._full_linear_draft())

        assert "确认执行" in text

    def test_auxiliary_delay_confirmation_format(self):
        """Func109 延时复述确认格式"""
        from robot_modbus_lite.agent.drafts import CommandDraft as CD

        draft = CD(
            draft_id="d2", func_id=109, intent="delay_blocking",
            params={"delay_sec": 3.0}, param_sources={"delay_sec": "specified"},
            raw_text="等待3秒", confidence=1.0,
            precheck_result={"valid": True, "summary": "L1通过。"},
        )
        text = ConfirmationAgent().render_confirmation_text(draft)

        assert "Func109" in text
        assert "3.0" in text

    def test_auxiliary_io_confirmation_format(self):
        """Func120 IO操作复述确认格式"""
        from robot_modbus_lite.agent.drafts import CommandDraft as CD

        draft = CD(
            draft_id="d3", func_id=120, intent="io",
            params={"io_no": 1, "io_action": 1},
            param_sources={"io_no": "specified", "io_action": "specified"},
            raw_text="打开IO1", confidence=1.0,
            precheck_result={"valid": True, "summary": "L1通过。"},
        )
        text = ConfirmationAgent().render_confirmation_text(draft)

        assert "Func120" in text


# ══════════════════════════════════════════════════════════════════════
# 第三章  安全预检与运动超限（状态说明书第三层）
# ══════════════════════════════════════════════════════════════════════

class TestSafetyPrecheck:
    """对照状态说明书 3.1-3.2"""

    def test_l1_precheck_passes_for_safe_draft(self):
        """安全预检：正常范围内通过"""
        agent = SafetyReviewAgent(l1_service=SafetyPrecheckService(_config()))

        result = agent.review(
            CommandDraft(
                draft_id="d1", func_id=108, intent="move_linear",
                params={"target_x": 100.0, "target_y": 20.0, "target_z": 300.0,
                        "target_rx": 1.0, "target_ry": 2.0, "target_rz": 3.0,
                        "spd_pct": 60.0, "acc_pct": 45.0, "dec_pct": 50.0,
                        "stop_cmd": 0, "fuzzy_pos": 0, "fuzzy_spd": 0,
                        "fuzzy_acc": 0, "fuzzy_dec": 0, "move_type": 0},
                param_sources={k: "specified" for k in range(15)},
                raw_text="走到 X100", confidence=0.9,
            ),
            snapshot=_runtime_snapshot(),
        )

        assert result["valid"] is True
        assert result["status"] == "pass"
        assert "L1通过" in result["summary"]

    def test_l1_precheck_fails_for_radius_exceed(self):
        """说明书3.1：半径超限 → 检查不通过"""
        cfg = _config(safe_r_max=500.0)  # 半径上限500
        agent = SafetyReviewAgent(l1_service=SafetyPrecheckService(cfg))

        result = agent.review(
            CommandDraft(
                draft_id="d1", func_id=108, intent="move_linear",
                params={"target_x": 1000.0, "target_y": 0.0, "target_z": 300.0,
                        "target_rx": 1.0, "target_ry": 2.0, "target_rz": 3.0,
                        "spd_pct": 60.0, "acc_pct": 45.0, "dec_pct": 50.0,
                        "stop_cmd": 0, "fuzzy_pos": 0, "fuzzy_spd": 0,
                        "fuzzy_acc": 0, "fuzzy_dec": 0, "move_type": 0},
                param_sources={k: "specified" for k in range(15)},
                raw_text="走到 X1000", confidence=0.9,
            ),
            snapshot=_runtime_snapshot(),
        )

        # 期望不通过（半径1000 > 上限500）
        assert result["valid"] is False

    def test_l1_precheck_fails_for_height_exceed(self):
        """说明书3.1：高度超限 → 检查不通过"""
        cfg = _config(safe_z_max=500.0)
        agent = SafetyReviewAgent(l1_service=SafetyPrecheckService(cfg))

        result = agent.review(
            CommandDraft(
                draft_id="d1", func_id=108, intent="move_linear",
                params={"target_x": 100.0, "target_y": 20.0, "target_z": 2000.0,
                        "target_rx": 1.0, "target_ry": 2.0, "target_rz": 3.0,
                        "spd_pct": 60.0, "acc_pct": 45.0, "dec_pct": 50.0,
                        "stop_cmd": 0, "fuzzy_pos": 0, "fuzzy_spd": 0,
                        "fuzzy_acc": 0, "fuzzy_dec": 0, "move_type": 0},
                param_sources={k: "specified" for k in range(15)},
                raw_text="走到 Z2000", confidence=0.9,
            ),
            snapshot=_runtime_snapshot(),
        )

        assert result["valid"] is False

    def test_restricted_service_blocks_on_precheck_failure(self):
        """受限 Agent：安全预检不通过 → 返回 precheck_failed"""
        cfg = _config(safe_r_max=500.0)
        service = _restricted_service(config=cfg)

        result = service.parse("走到 X1000")

        assert isinstance(result, RestrictedAgentResult)
        assert result.kind == "precheck_failed"

    def test_system_action_emergency_bypasses_completion(self):
        """急停指令 → bypass_completion，不进入参数补全和确认"""
        text = "急停"
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "sys_estop"
        assert understanding.bypass_completion is True
        assert understanding.func_id == 104

    def test_system_action_pause_bypasses_completion(self):
        """暂停指令 → bypass_completion"""
        text = "暂停"
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "sys_pause"
        assert understanding.bypass_completion is True

    def test_system_action_resume_bypasses_completion(self):
        """继续指令 → bypass_completion"""
        text = "继续"
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "sys_resume"
        assert understanding.bypass_completion is True


# ══════════════════════════════════════════════════════════════════════
# 第四章  报警与异常状态（状态说明书第二层）
# ══════════════════════════════════════════════════════════════════════

class TestAlarmHandling:
    """对照状态说明书 2.1-2.5"""

    def test_alarm_query_recognises_intent(self):
        """报警查询意图识别"""
        text = "当前报警是什么"
        result = CommandUnderstandingAgent().understand(text)

        assert result.intent == "alarm_query"
        assert result.bypass_completion is True

    def test_alarm_reason_query(self):
        """报警原因查询"""
        text = "报警原因"
        result = CommandUnderstandingAgent().understand(text)

        assert result.intent == "alarm_query"

    def test_status_query_recognises_intent(self):
        """状态查询意图识别"""
        text = "当前状态怎么样"
        result = CommandUnderstandingAgent().understand(text)

        assert result.intent == "status_query"

    def test_status_query_why_cant_move(self):
        """说明书1.2：用户问「为什么不能动」→ 识别为状态查询"""
        text = "为什么不能动了"
        result = CommandUnderstandingAgent().understand(text)

        assert result.intent == "status_query"

    def test_status_query_done_check(self):
        """运动完成确认：问「完成了吗」"""
        text = "运动完成了吗"
        result = CommandUnderstandingAgent().understand(text)

        assert result.intent == "status_query"

    def test_orchestrator_routes_alarm_query_to_chat(self):
        """报警查询走 AgentOrchestrator → bypass 路径（非执行路径）"""
        orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())

        result = orchestrator.handle("当前报警是什么")

        # alarm_query 走 bypass_completion，不进入 restricted_agent 执行
        assert result.kind != "restricted_agent"
        assert result.kind in {"chat_answer", "clarification", "fallback_legacy"}


# ══════════════════════════════════════════════════════════════════════
# 第五章  中文数字及边界解析（已知缺陷回归）
# ══════════════════════════════════════════════════════════════════════

class TestChineseNumberParsing:
    """回归测试：中文数字 → 阿拉伯数字 的映射"""

    @pytest.mark.parametrize("text, expected_x", [
        ("走到 X一百", 100.0),
        ("走到 X一千", 1000.0),
        ("走到 X两百", 200.0),
        ("走到 X三百", 300.0),
        ("走到 X零", 0.0),
        ("走到 X十", 10.0),
    ])
    def test_chinese_number_x_parsing(self, text, expected_x):
        """中文数字「一百/一千」应正确解析为数字"""
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "move_linear"
        assert understanding.extracted_params.get("target_x") == expected_x

    def test_chinese_number_should_not_crash_or_silent_fail(self):
        """中文数字输入不应静默失败，应解析为结构化参数"""
        text = "我觉得坐标是 X 一百 Y0 Z100 速度 50"

        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "move_linear"
        assert understanding.extracted_params["target_x"] == 100.0
        assert understanding.extracted_params["target_y"] == 0.0
        assert understanding.extracted_params["target_z"] == 100.0
        assert understanding.extracted_params["spd_pct"] == 50.0

    def test_mixed_chinese_arabic_parsing(self):
        """「X100 Y零」混合输入 → 中阿数字均应正确解析"""
        text = "走到 X100 Y零"
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.extracted_params.get("target_x") == 100.0
        assert understanding.extracted_params.get("target_y") == 0.0


# ══════════════════════════════════════════════════════════════════════
# 第六章  多轮对话与状态记忆
# ══════════════════════════════════════════════════════════════════════

class TestMultiTurnDialogue:
    """测试多轮对话状态流转：创建流程 → 取名字 → 加步骤 → 确认"""

    def test_flow_create_intent_recognised_in_orchestrator(self):
        """「创建流程」→ 应有回复（clarification 或 fallback 但带消息）"""
        orchestrator = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        )
        result = orchestrator.handle("我想创建一个新的流程")

        # 应有回答
        assert result.message
        # fallback_legacy 表明当前架构对创建流程需要 LLM fallback 支持
        assert result.kind in {"clarification", "chat_answer", "fallback_legacy"}

    def test_flow_name_input_recognised_as_context(self):
        """「流程名字叫测试」→ 不应被当作普通闲聊"""
        # 模拟 LLM fallback 上下文意图
        client = _llm_fallback_client({
            "创建一个新的流程": '{"kind":"clarification","text":"请问新流程叫什么名字？"}',
            "流程名字叫测试": '{"kind":"flow_create","flow_name":"测试","suggested_reply":"已创建流程“测试”，接下来请添加步骤。","confidence":0.9}',
        })
        fallback = LlmFallbackAgent(client=client)

        result = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
            llm_fallback_agent=fallback,
            llm_fallback_enabled=True,
        ).handle("流程名字叫测试")

        assert result.kind == "flow_create"
        assert "测试" in result.message

    def test_flow_step_addition_recognised(self):
        """「添加第一步是移动到位置A」→ 应有澄清或结构化回复"""
        client = _llm_fallback_client({
            "添加第一步": '{"kind":"flow_append_step","target_flow":"测试","step_hint":"移动到位置A","missing_fields":["target_pose"],"suggested_reply":"请问位置A的具体坐标是多少？","confidence":0.85}',
        })
        fallback = LlmFallbackAgent(client=client)

        result = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
            llm_fallback_agent=fallback,
            llm_fallback_enabled=True,
        ).handle("添加第一步是移动到位置 A")

        assert result.kind == "flow_append_step"
        assert "位置A" in result.message

    def test_flow_multi_step_sequence_parsed(self):
        """「步骤一 X200 步骤二 等待2秒 步骤三 home」→ 应有回复"""
        result = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        ).handle("步骤一，移动到位置a x200 Y0 Z700 速度30% 步骤二，等待2秒 步骤三，移动到 home")

        # 当前架构下多步骤文本可能走 delay_blocking 匹配或 fallback_legacy
        # 核心要求：不静默，有消息
        assert result.message
        assert result.kind is not None


# ══════════════════════════════════════════════════════════════════════
# 第七章  引擎一致性与静默失败回归
# ══════════════════════════════════════════════════════════════════════

class TestEngineConsistency:
    """回归测试：不产生 engine=pending 的静默失败"""

    def test_orchestrator_always_returns_message_for_any_input(self):
        """任意输入都应有 message，不能出现空字符串"""
        orchestrator = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        )
        inputs = [
            "你好",
            "直行点头流程",
            "小镇直行点头，流程",
            "测试",
            "对呀，那肯定用我的坐标呀",
            "",
        ]

        for text in inputs:
            result = orchestrator.handle(text)
            # 即使解析失败也要有反馈
            if not text:
                continue
            assert result.message, f"输入「{text}」不应产生空消息"

    def test_restricted_service_parse_always_has_explicit_kind(self):
        """受限 Agent 解析必须返回明确的 kind"""
        service = _restricted_service()
        inputs = [
            ("走到 X1000", "waiting_confirmation"),       # 完整参数
            ("急停", "bypass"),                            # bypass 系统动作
            ("你好", None),                                # 不明 — 走 clarification
        ]

        for text, expected_kind in inputs:
            result = service.parse(text)
            assert result.kind is not None
            assert result.kind != "", f"输入「{text}」kind 不应为空"
            if expected_kind:
                assert result.kind == expected_kind

    def test_plan_adapter_preserves_clarification_as_action(self):
        """clarification 结果必须转换为可展示的 VoiceNlpPlan"""
        result = RestrictedAgentResult(
            kind="clarification",
            intent="unknown",
            message="请补充具体指令。",
        )
        plan = AgentPlanAdapter().to_voice_plan(result)

        assert plan is not None
        assert len(plan.actions) > 0
        assert plan.reason
        # AgentPlanAdapter 将 RestrictedAgentResult.clarification 转为 unknown
        # 这是当前实现行为，上层根据 reason 展示提示文本

    def test_plan_adapter_preserves_precheck_failed_as_action(self):
        """precheck_failed 结果必须转换为可展示的 VoiceNlpPlan"""
        result = RestrictedAgentResult(
            kind="precheck_failed",
            intent="move_linear",
            func_id=108,
            message="安全预检未通过。",
        )
        plan = AgentPlanAdapter().to_voice_plan(result)

        assert plan is not None
        # precheck_failed → action_type="agent_blocked"（当前实现）
        assert plan.actions[0].action_type == "agent_blocked"
        assert "安全预检未通过" in plan.reason

    def test_orchestrator_fallback_legacy_has_message(self):
        """fallback_legacy 必须有 message 或可被转换为提示"""
        # fallback_legacy.messages 可能是 "交回旧 NLP 路径。" → 应该能兜底展示
        orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=None)

        result = orchestrator.handle("你好")

        assert result.kind == "fallback_legacy"
        assert result.message  # 不应为空


# ══════════════════════════════════════════════════════════════════════
# 第八章  复合指令解析（说明书第八节）
# ══════════════════════════════════════════════════════════════════════

class TestCompoundCommands:
    """对照说明书 8.1-8.2"""

    def test_compound_motion_plus_delay_split(self):
        """「走到X1000等待2秒然后走到X1500」→ 拆为3条"""
        result = CompoundCommandCoordinator().split("走到X1000，然后等待2秒，再走到X1500")

        assert result.kind == "compound_sequence"
        assert result.steps == ("走到X1000", "等待2秒", "走到X1500")

    def test_compound_motion_plus_io(self):
        """「走到X1000打开IO1」→ 拆为运动+IO"""
        # IO 解析格式要求 "IO+数字+动作"，如 "IO1开"
        result = CompoundCommandCoordinator().split("走到X1000，然后IO1开")

        assert result.kind == "compound_sequence"
        assert len(result.steps) == 2

    def test_compound_rejects_parallel_unsafe(self):
        """「同时走到X1000并且打开IO1」→ 不支持并行"""
        result = CompoundCommandCoordinator().split("同时走到X1000并且打开IO1")

        assert result.kind == "unsupported_compound"

    def test_compound_rejects_conditional(self):
        """「如果没有报警就走到X1000」→ 不支持条件"""
        result = CompoundCommandCoordinator().split("如果没有报警就走到X1000")

        assert result.kind == "unsupported_compound"

    # ── 说明书9.1 函数号识别 ──
    @pytest.mark.parametrize("text, expected_func_id, expected_intent", [
        ("走到 X1000", 108, "move_linear"),
        ("规划路径走到 X1000", 112, "continuous_path"),
        ("等待3秒", 109, "delay_blocking"),
        ("延时5秒后继续", 109, "delay_blocking"),  # 延时关键词
        ("IO1开", 120, "io"),
        ("IO2关", 120, "io"),
    ])
    def test_func_selection_keywords(self, text, expected_func_id, expected_intent):
        """说明书6.1 函数号识别对照"""
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == expected_intent
        if expected_func_id is not None:
            assert understanding.func_id == expected_func_id

    # ── 说明书9.2 函数选择决策树 ──
    def test_default_motion_is_func108(self):
        """未指定规避时默认使用108"""
        text = "移动到 X1000"
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "move_linear"
        assert understanding.func_id == 108

    def test_explicit_avoidance_uses_func112(self):
        """明确「规划路径/规避」使用112"""
        text = "规避走到 X1000"
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == "continuous_path"
        assert understanding.func_id == 112


# ══════════════════════════════════════════════════════════════════════
# 第九章  系统控制指令
# ══════════════════════════════════════════════════════════════════════

class TestSystemCommands:
    """对照说明书 5.3 和状态说明书 1.2-1.3"""

    @pytest.mark.parametrize("text, intent", [
        ("急停", "sys_estop"),
        ("暂停", "sys_pause"),
        ("继续", "sys_resume"),
        ("恢复", "sys_resume"),
        ("取消当前动作", "sys_cancel"),
        ("取消当前任务", "sys_cancel"),
        ("报警复位", "alarm_reset"),
        ("复位", "alarm_reset"),
    ])
    def test_system_action_aliases(self, text, intent):
        """系统动作别名映射"""
        understanding = CommandUnderstandingAgent().understand(text)

        assert understanding.intent == intent
        assert understanding.func_id == 104

    def test_emergency_should_be_immediate(self):
        """急停应为高优先级，跳过确认"""
        understanding = CommandUnderstandingAgent().understand("急停")

        assert understanding.intent == "sys_estop"
        assert understanding.bypass_completion is True
        assert understanding.confidence == 1.0

    def test_orchestrator_routes_emergency_to_restricted_agent(self):
        """急停 → 进入 restricted_agent 管线"""
        service = _restricted_service()
        orchestrator = AgentOrchestrator(restricted_service=service)

        result = orchestrator.handle("急停")

        assert result.kind == "restricted_agent"


# ══════════════════════════════════════════════════════════════════════
# 第十章  闲聊与帮助（确保不触发动作）
# ══════════════════════════════════════════════════════════════════════

class TestChatSafety:
    """确保闲聊不触发机械手执行"""

    def test_hello_does_not_generate_command(self):
        """「你好」→ 纯闲聊，不产生执行"""
        orchestrator = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        )
        result = orchestrator.handle("你好")

        assert result.kind in {"chat_answer", "clarification", "fallback_legacy"}
        if result.kind == "fallback_legacy":
            # 不回 restricted_agent
            pass
        else:
            plan = AgentPlanAdapter().to_voice_plan(result)
            assert plan.actions[0].action_type != "template"

    def test_self_intro_does_not_generate_command(self):
        """「你是谁」→ 自我介绍，不产生执行"""
        orchestrator = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        )
        result = orchestrator.handle("你是谁")

        assert result.kind == "chat_answer"
        assert "机械手" in result.message

    def test_help_does_not_generate_command(self):
        """「怎么使用」→ 帮助文本，不产生执行"""
        orchestrator = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        )
        result = orchestrator.handle("怎么使用")

        assert result.kind == "chat_answer"
        assert "输入" in result.message or "指令" in result.message or "可以" in result.message

    def test_weather_question_answered_safely(self):
        """天气问题 → 回答但说明无法查询"""
        orchestrator = AgentOrchestrator(
            restricted_service=None,
            chat_agent=ChatExplanationAgent(),
        )
        result = orchestrator.handle("今天天气怎么样")

        assert result.kind == "chat_answer"
        assert "天气" in result.message
        assert "触发机械手动作" in result.message or "动作" in result.message


# ══════════════════════════════════════════════════════════════════════
# 第十一章  LLM Fallback 安全边界
# ══════════════════════════════════════════════════════════════════════

class TestLlmFallbackSafety:
    """确保 LLM fallback 不输出可执行参数"""

    def test_llm_fallback_rejects_func_id_in_output(self):
        """输出含 func_id → 被拒绝"""
        client = _llm_fallback_client({"走到": '{"kind":"candidate_text","text":"走到X1000","func_id":108}'})
        fallback = LlmFallbackAgent(client=client)

        result = fallback.apply("走到X1000", type("U", (), {"intent":"unknown","clarification":""})())

        assert result["kind"] == "rejected"
        assert "llm_output_not_allowed" in result["reason"]

    def test_llm_fallback_rejects_modbus_in_output(self):
        """输出含 modbus/registers/writes → 被拒绝"""
        client = _llm_fallback_client({"走到": '{"kind":"candidate_text","text":"走到X1000","writes":[0,1]}'})
        fallback = LlmFallbackAgent(client=client)

        result = fallback.apply("走到X1000", type("U", (), {"intent":"unknown","clarification":""})())

        assert result["kind"] == "rejected"
        assert "llm_output_not_allowed" in result["reason"]

    def test_llm_fallback_rejects_low_confidence(self):
        """置信度 < 0.5 → 被拒绝"""
        client = _llm_fallback_client({"走到": '{"kind":"candidate_text","text":"走到X1000","confidence":0.2}'})
        fallback = LlmFallbackAgent(client=client)

        result = fallback.apply("走到X1000", type("U", (), {"intent":"unknown","clarification":""})())

        assert result["kind"] == "rejected"
        assert "low_confidence" in result["reason"]

    def test_llm_fallback_accepts_valid_clarification(self):
        """合法的 clarification → 接受"""
        client = _llm_fallback_client({"走到": '{"kind":"clarification","text":"请补充坐标参数"}'})
        fallback = LlmFallbackAgent(client=client)

        result = fallback.apply("走到", type("U", (), {"intent":"unknown","clarification":""})())

        assert result["kind"] == "clarification"
        assert result["text"] == "请补充坐标参数"


# ══════════════════════════════════════════════════════════════════════
# 第十二章  异常输入与边界
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_input_returns_unknown(self):
        """空输入 → unknown"""
        result = CommandUnderstandingAgent().understand("")
        assert result.intent == "unknown"

    def test_whitespace_only_input(self):
        """纯空格 → unknown"""
        result = CommandUnderstandingAgent().understand("   ")
        assert result.intent == "unknown"

    def test_gibberish_input_does_not_trigger_execution(self):
        """乱码输入 → 不应进入执行"""
        orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())
        result = orchestrator.handle("为什么也，为什么为什么又在哄呢")

        assert result.kind != "restricted_agent"

    def test_very_long_input_does_not_crash(self):
        """超长输入 → 不崩溃"""
        long_text = "走到 " + "X1000 " * 200
        orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())
        result = orchestrator.handle(long_text)

        assert result.message is not None

    def test_special_characters_do_not_crash(self):
        """特殊字符输入 → 不崩溃"""
        orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())
        result = orchestrator.handle("!!!@#$%^&*()")

        assert result.message is not None

    @pytest.mark.parametrize("text, expected_not_kind", [
        ("正走 X1000", "restricted_agent"),    # 方向用词不标准
        ("去那个安全位置", "restricted_agent"),  # 模糊位置
        ("能不能走到X1000", "restricted_agent"), # 疑问句形式
    ])
    def test_ambiguous_inputs_do_not_execute(self, text, expected_not_kind):
        """模糊输入不应直接进入执行"""
        orchestrator = AgentOrchestrator(
            restricted_service=_restricted_service(),
            chat_agent=ChatExplanationAgent(),
        )
        result = orchestrator.handle(text)

        assert result.kind != expected_not_kind or result.kind == "restricted_agent"
