"""AI 对话模拟器 — 按两本说明书逐条发问，记录系统回答，并自动追问。

用法: python tools/ai_dialogue_simulator.py

对照文档：
  - 机械手基础运行信息交互状态说明书_用于上位机对接.md
  - 自然语言参数类指令解析说明书_上位机对接.md
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent
from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent
from robot_modbus_lite.agent.compound import CompoundCommandCoordinator
from robot_modbus_lite.agent.confirmation import ConfirmationAgent
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator, AgentOrchestratorResult
from robot_modbus_lite.agent.parameter_completion import (
    ControllerSnapshot,
    ParameterCompletionAgent,
)
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent
from robot_modbus_lite.agent.service import RestrictedAgentResult, RestrictedAgentService
from robot_modbus_lite.agent_runtime.operator_bridge import OperatorAgentRuntimeBridge
from robot_modbus_lite.execution_plan_service import ExecutionPlanService
from robot_modbus_lite.safety_precheck import SafetyPrecheckService
from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.system_config import AxisRangeConfig


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


# ── 测试环境搭建 ──────────────────────────────────────────────────

def _idle_snapshot():
    return ControllerSnapshot(
        current_pose={
            "target_x": 500.0, "target_y": 0.0, "target_z": 600.0,
            "target_rx": 0.0, "target_ry": 0.0, "target_rz": 0.0,
        },
        safety_params={"spd_pct": 80.0, "acc_pct": 80.0, "dec_pct": 80.0},
        is_moving=False,
        read_ok=True,
    )


def _config():
    return AxisRangeConfig(
        x=(-100.0, 2200.0), y=(-200.0, 200.0), z=(0.0, 1200.0),
        safe_r_min=0.0, safe_r_max=2200.0,
        safe_z_min=0.0, safe_z_max=1200.0,
        safe_speed_max=80.0, safe_acc_max=80.0, safe_dec_max=80.0,
    )


def _runtime_snapshot():
    return {
        "safety": {"estop": False, "alarm_active": False, "paused": False},
        "connection": {"controller": "online", "realtime_feedback": "online"},
        "motion": {"running_state": "idle", "active_plan_id": None},
        "position": {"cartesian": {"r": 100.0, "z": 100.0}},
    }


def build_orchestrator():
    snap = _idle_snapshot()
    cfg = _config()
    service = RestrictedAgentService(
        controller_snapshot_provider=lambda: snap,
        runtime_snapshot_provider=_runtime_snapshot,
        safety_review_agent=SafetyReviewAgent(l1_service=SafetyPrecheckService(cfg)),
        status_signature_provider=lambda: "idle",
        safety_signature_provider=lambda: "safe",
        clock=lambda: 100.0,
        confirm_timeout_sec=60.0,
    )
    return AgentOrchestrator(
        restricted_service=service,
        chat_agent=ChatExplanationAgent(),
    )


@dataclass
class DialogueRunner:
    handle_func: Callable[[str], AgentOrchestratorResult]

    def handle(self, text: str) -> AgentOrchestratorResult:
        return self.handle_func(text)


def build_runtime_bridge() -> OperatorAgentRuntimeBridge:
    snap = _idle_snapshot()
    cfg = _config()
    safety_agent = SafetyReviewAgent(l1_service=SafetyPrecheckService(cfg))
    confirmation_agent = ConfirmationAgent(timeout_sec=60.0)
    service = RobotModbusService(Path(__file__).resolve().parent.parent / "data" / "query_table.json")
    execution_plan_service = ExecutionPlanService()
    restricted_service = RestrictedAgentService(
        controller_snapshot_provider=lambda: snap,
        runtime_snapshot_provider=_runtime_snapshot,
        safety_review_agent=safety_agent,
        confirmation_agent=confirmation_agent,
        status_signature_provider=lambda: "idle",
        safety_signature_provider=lambda: "safe",
        clock=lambda: 100.0,
        confirm_timeout_sec=60.0,
    )
    return OperatorAgentRuntimeBridge(
        runtime_root=Path(__file__).resolve().parent.parent,
        restricted_service_provider=lambda: restricted_service,
        flow_service_provider=lambda: service,
        execution_plan_service_provider=lambda: execution_plan_service,
        controller_snapshot_provider=lambda: snap,
        safety_review_agent_provider=lambda: safety_agent,
        runtime_snapshot_provider=_runtime_snapshot,
        start_pose_provider=lambda: (500.0, 0.0, 600.0, 0.0, 0.0, 0.0),
        confirmation_agent_provider=lambda: confirmation_agent,
        clock=lambda: 100.0,
        status_signature_provider=lambda: "idle",
        safety_signature_provider=lambda: "safe",
        langchain_available=False,
    )


def build_dialogue_runner(runtime: str = "agent_runtime") -> DialogueRunner:
    if runtime == "legacy":
        orchestrator = build_orchestrator()
        return DialogueRunner(orchestrator.handle)
    if runtime != "agent_runtime":
        raise ValueError(f"unsupported runtime: {runtime}")
    bridge = build_runtime_bridge()
    legacy = build_orchestrator()
    return DialogueRunner(
        lambda text: bridge.handle_text(
            text,
            thread_id="dialogue-simulator",
            legacy_fallback=legacy.handle,
        )
    )


# ── 完整问题检查器 ────────────────────────────────────────────────

def _check_issues(
    result: AgentOrchestratorResult,
    reply: str,
    user_text: str,
    check_desc: str,
) -> list[str]:
    """对照两本说明书，从多个维度检查回答质量。"""
    issues = []

    # ── A. 静默失败 ──
    if not reply.strip():
        issues.append("A1: 静默失败 — 系统未返回任何消息")
        return issues  # 最严重，不再继续检查

    # ── B. 引擎与路径 ──
    if result.kind == "fallback_legacy" and "交回旧" in (result.message or ""):
        issues.append("B1: 引擎回退 — AgentOrchestrator 无法处理，退回旧 NLP 路径，回答来源不一致")
    if result.kind not in {"restricted_agent", "chat_answer", "clarification",
                             "compound_plan_draft", "unsupported_compound",
                             "fallback_legacy", "dashboard_query_action",
                             "command_catalog", "confirm_plan", "confirm_rejected",
                             "followup_rejected", "atomic_template_action",
                             "precheck_failed", "flow_draft", "confirm_result",
                             "confirm_cancelled"}:
        issues.append(f"B2: 未知 kind={result.kind} — 不在预期范围内")

    # ── C. kind vs 输入意图不匹配 ──
    intent_mismatches = _check_kind_intent_mismatch(result, user_text)
    issues.extend(intent_mismatches)

    # ── D. 确认文本格式（说明书 §7.1） ──
    if result.kind == "restricted_agent":
        issues.extend(_check_confirmation_format(result, reply, user_text))

    # ── E. 虚假回复 ──
    issues.extend(_check_fake_reply(result, reply))

    # ── F. 答非所问 ──
    issues.extend(_check_off_topic(user_text, result, reply))

    # ── G. 安全预检反馈质量（说明书 §3.1-3.2） ──
    issues.extend(_check_safety_feedback(result, reply))

    # ── H. 参数继承标注（说明书 §4.2） ──
    issues.extend(_check_param_labels(result, reply))

    # ── I. 中文数字解析 ──
    issues.extend(_check_chinese_number(user_text, reply))

    # ── J. 闲聊类误判 ──
    issues.extend(_check_chat_intent(user_text, result))

    # ── K. 确认缺失 ──
    issues.extend(_check_confirm_missing(result, reply, user_text))

    # ── L. 错误吞没 ──
    issues.extend(_check_error_swallowed(result, user_text))

    # ── M. 上下文丢失 ──
    issues.extend(_check_context_loss(user_text, result, reply))

    return issues


# ── 各检查子函数 ──────────────────────────────────────────────────

def _check_kind_intent_mismatch(result: AgentOrchestratorResult, user_text: str) -> list[str]:
    """检查 kind 是否与用户输入意图一致。"""
    issues = []
    compact = re.sub(r"\s+", "", user_text or "")

    # 明确的系统指令 — 应该进 restricted_agent
    system_keywords = ["急停", "暂停", "继续", "复位", "取消当前"]
    if any(kw in compact for kw in system_keywords):
        if compact == "紧急停止":
            pass
        elif result.kind != "restricted_agent":
            issues.append(f"C1: 系统指令「{compact}」应进入 restricted_agent，实际 kind={result.kind}")

    # 明确的运动指令含坐标 — 应该进 restricted_agent 或 compound
    if _is_explicit_motion_request(compact):
        if result.kind not in {"restricted_agent", "compound_plan_draft", "unsupported_compound",
                               "confirm_plan", "atomic_template_action", "precheck_failed",
                               "flow_draft"}:
            issues.append(f"C2: 运动指令「{compact}」应进入 restricted_agent/compound，实际 kind={result.kind}")

    # 闲聊类 — 不应进 restricted_agent
    chat_intents = ["你好", "你是谁", "介绍一下", "能做什么", "怎么用", "帮助", "天气"]
    if any(p in compact for p in chat_intents):
        if result.kind == "restricted_agent":
            issues.append(f"C3: 闲聊「{compact}」不应进入 restricted_agent")

    # 明确的状态/报警查询 — 应 bypass/chat，不进入确认
    if any(p in compact for p in ("报警", "状态", "完成了吗", "为什么不能动")):
        if result.kind == "restricted_agent":
            agent_payload = result.payload
            if isinstance(agent_payload, RestrictedAgentResult):
                if agent_payload.kind == "waiting_confirmation":
                    issues.append(f"C4: 状态查询「{compact}」被错误地发起了确认流程")

    return issues


def _is_explicit_motion_request(compact: str) -> bool:
    if not any(word in compact for word in ("走到", "移动到", "规划路径")):
        return False
    if any(word in compact for word in ("添加", "新增", "第一步", "第1步", "步骤")):
        return False
    has_numeric_pose = bool(re.search(r"(?:X|Y|Z|RX|RY|RZ|x|y|z|rx|ry|rz)-?\d", compact))
    has_template_position = bool(re.search(r"位置[A-Za-z0-9一二三四五六七八九十]+", compact))
    has_direction_or_delta = any(word in compact for word in ("向左", "向右", "向前", "向后", "升高", "降低", "下降", "上升"))
    has_specific_home = any(word in compact for word in ("home", "Home", "休息姿态"))
    return has_numeric_pose or has_template_position or has_direction_or_delta or has_specific_home


def _check_confirmation_format(
    result: AgentOrchestratorResult, reply: str, user_text: str
) -> list[str]:
    """对照说明书 §7.1 检查确认文本格式。"""
    issues = []
    agent_payload = result.payload
    if not isinstance(agent_payload, RestrictedAgentResult):
        return issues

    if agent_payload.kind == "waiting_confirmation":
        # 说明书 §7.1: 必须包含 【复述确认】FuncXXX
        if "【复述确认】" not in reply:
            issues.append("D1: 确认文本缺少「【复述确认】」标题（说明书 §7.1）")
        if "Func" not in reply:
            issues.append("D2: 确认文本未标注函数号 FuncXXX（说明书 §7.1）")
        if "确认执行" not in reply:
            issues.append("D3: 确认文本未询问「确认执行？」（说明书 §7.1）")
        if "安全预检" not in reply:
            issues.append("D4: 确认文本未展示安全预检结果（说明书 §7.2）")

        # 说明书 §4.2: 参数来源标注
        if agent_payload.draft:
            sources = agent_payload.draft.param_sources
            if "inherited" in sources.values() and "继承当前" not in reply:
                issues.append("D5: 有继承参数但未标注「继承当前」（说明书 §4.2）")
            if "controller" in sources.values() and "继承安全参数" not in reply:
                issues.append("D6: 有安全参数但未标注「继承安全参数」（说明书 §4.2）")

        # 半参数指令检查
        if "速度" in user_text or "加速度" in user_text or "减速度" in user_text:
            if agent_payload.draft:
                p = agent_payload.draft.params
                if p.get("spd_pct", 0) != 80.0:
                    # 用户指定了速度，但实际用了其他值 — 检查是否一致
                    pass

    elif agent_payload.kind == "bypass":
        # bypass 类（急停/暂停等）应给出明确回复
        if len(reply) < 5:
            issues.append("D7: bypass 回复过短，应给出确认信息")

    elif agent_payload.kind == "precheck_failed":
        if "超限" not in reply and "超" not in reply and "限位" not in reply:
            issues.append("D8: precheck_failed 回复未说明具体超限原因（说明书 §3.1）")

    return issues


def _check_fake_reply(result: AgentOrchestratorResult, reply: str) -> list[str]:
    """检查是否出现声称做了但实际没做的虚假回复。"""
    issues = []
    if result.kind in {"chat_answer", "clarification", "fallback_legacy"}:
        fake_words = [
            ("已创建", "声称创建了流程但 modbus_write 为空"),
            ("登记成功", "声称登记成功但实际未写入任何数据"),
            ("已确认", "声称已确认但未进入确认状态机"),
            ("已保存", "声称保存但无实际文件写入"),
            ("已添加", "声称添加了步骤但流程草案为空"),
            ("已执行", "声称已执行但未触发控制器写入"),
            ("已开始", "声称已开始但无任务启动"),
            ("已停止", "声称已停止但未取消任务"),
        ]
        for word, desc in fake_words:
            if word in reply:
                issues.append(f"E1: 虚假回复 — 「{word}」：{desc}")

    # 在 restricted_agent 中，bypass 或 clarification 不应该声称执行了
    if result.kind == "restricted_agent":
        agent_payload = result.payload
        if isinstance(agent_payload, RestrictedAgentResult):
            if agent_payload.kind in {"bypass", "clarification", "blocked"}:
                if any(w in reply for w in ("已执行", "已完成", "已发送")):
                    issues.append("E2: 非执行类回复中出现了执行确认用语")
    return issues


def _check_off_topic(user_text: str, result: AgentOrchestratorResult, reply: str) -> list[str]:
    """检查是否存在答非所问。"""
    issues = []
    compact = re.sub(r"\s+", "", user_text or "")

    # 用户问"为什么"但回复是"请补充坐标"
    if "为什么" in compact and result.kind == "clarification":
        if "请补充" in reply or "坐标" in reply:
            issues.append("F1: 答非所问 — 用户问「为什么」，回复却是补充坐标")

    # 用户问能力范围，回复却是指令澄清
    if any(p in compact for p in ("能做什么", "有什么功能", "介绍一下")):
        if result.kind == "clarification" and "坐标" in reply:
            issues.append("F2: 答非所问 — 问能力范围，回复问坐标")

    # 用户要求"推荐"，回复却是问参数
    if "推荐" in compact:
        if result.kind == "clarification" and "请补充" in reply:
            issues.append("F3: 答非所问 — 请求推荐却反问参数")

    # 用户说"对呀/那肯定"，回复仍是问句
    if any(w in compact for w in ("对呀", "肯定", "当然", "是的")):
        if result.kind == "clarification":
            issues.append("F4: 答非所问 — 用户确认了但系统仍在反问")

    return issues


def _check_safety_feedback(
    result: AgentOrchestratorResult, reply: str
) -> list[str]:
    """对照说明书 §3.1，检查安全预检失败时的回复质量。"""
    issues = []
    if result.kind == "restricted_agent":
        agent_payload = result.payload
        if isinstance(agent_payload, RestrictedAgentResult):
            if agent_payload.kind == "precheck_failed":
                msg = agent_payload.message or ""
                # 说明书 §3.1: 超限回复应包含：超限方向、当前值、上限、建议
                if "超" not in msg and "限" not in msg:
                    issues.append("G1: 安全预检失败但未说明超限类型")
                if re.search(r"\d+\.?\d*mm", msg) is None and re.search(r"\d+\.?\d*°", msg) is None:
                    issues.append("G2: 安全预检失败但未给出超限数值")
                # 说明书 §3.1 要求给出修正建议
                if "建议" not in msg and "收回" not in msg and "降低" not in msg and "调整" not in msg:
                    issues.append("G3: 安全预检失败但未给出修正建议（说明书 §3.1）")

    return issues


def _check_param_labels(
    result: AgentOrchestratorResult, reply: str
) -> list[str]:
    """检查确认文本中的参数来源标注是否正确（说明书 §4.2, §7.1）。"""
    issues = []
    if result.kind != "restricted_agent":
        return issues

    agent_payload = result.payload
    if not isinstance(agent_payload, RestrictedAgentResult):
        return issues

    if agent_payload.kind == "waiting_confirmation" and agent_payload.draft:
        draft = agent_payload.draft
        params = draft.params
        sources = draft.param_sources
        raw = (draft.raw_text or "").replace(" ", "")

        # 用户明确指定的参数 — 标注应该为"指定"
        _check_specified_param(raw, "target_x", params, sources, reply, "X", issues)
        _check_specified_param(raw, "target_y", params, sources, reply, "Y", issues)
        _check_specified_param(raw, "target_z", params, sources, reply, "Z", issues)
        _check_specified_param(raw, "spd_pct", params, sources, reply, "速度", issues)
        _check_specified_param(raw, "acc_pct", params, sources, reply, "加速度", issues)
        _check_specified_param(raw, "dec_pct", params, sources, reply, "减速度", issues)

        # 速度从安全参数继承的 — 标注应为"继承安全参数"
        if sources.get("spd_pct") == "controller":
            if "继承安全参数" not in reply:
                issues.append(f"H1: 速度从安全参数继承但未标注「继承安全参数」")
        if sources.get("acc_pct") == "controller":
            if "继承安全参数" not in reply:
                issues.append(f"H2: 加速度从安全参数继承但未标注「继承安全参数」")

    return issues


def _check_specified_param(
    raw_text: str, key: str, params: dict, sources: dict,
    reply: str, label: str, issues: list,
) -> None:
    """检查用户明确指定的参数是否被正确标注为「指定」。"""
    user_specified = False
    if label in ("X", "Y", "Z", "RX", "RY", "RZ"):
        user_specified = bool(re.search(rf"{label}\s*\d+", raw_text, re.IGNORECASE))
    elif label in ("速度", "加速度", "减速度"):
        user_specified = bool(re.search(rf"{label}\s*\d+", raw_text))
    if user_specified and sources.get(key) != "specified":
        issues.append(f"H3: 用户指定了{label}，但来源标注为{sources.get(key)}而非指定")


def _check_chinese_number(user_text: str, reply: str) -> list[str]:
    """检查中文数字解析问题。"""
    issues = []
    compact = re.sub(r"\s+", "", user_text or "")
    cn_num = re.search(r"([零一二三四五六七八九十百千万]+)", compact)
    if cn_num:
        cn_str = cn_num.group(1)
        # 如果中文数字出现在 X/Y/Z/速度等上下文中
        if (
            re.search(r"[XYZ][零一二三四五六七八九十百千万]", compact, re.IGNORECASE)
            and "已补齐" not in reply
            and "已解析" not in reply
            and "已创建" not in reply
        ):
            issues.append(f"I1: 中文数字未解析 — 「{cn_str}」未被转换为阿拉伯数字（已知缺陷）")
    return issues


def _check_chat_intent(user_text: str, result: AgentOrchestratorResult) -> list[str]:
    """检查闲聊是否被错误地路由到执行管线。"""
    issues = []
    compact = re.sub(r"\s+", "", user_text or "")
    chat_keywords = ("你好", "你是谁", "介绍一下", "怎么使用", "使用方法", "帮助", "能做什么")
    if any(kw in compact for kw in chat_keywords):
        if result.kind == "restricted_agent":
            agent_payload = result.payload
            if isinstance(agent_payload, RestrictedAgentResult):
                if agent_payload.kind == "waiting_confirmation":
                    issues.append("J1: 闲聊被错误路由到确认流程，应走 chat_answer")

    # 确认类回复（对呀/肯定）不应进 restricted_agent 的 clarification
    if any(w in compact for w in ("对呀", "肯定", "当然", "是的", "那就用", "就用")):
        if result.kind == "restricted_agent":
            agent_payload = result.payload
            if isinstance(agent_payload, RestrictedAgentResult):
                if agent_payload.kind == "clarification":
                    issues.append("J2: 用户确认语句被当成澄清疑问处理")
    return issues


def _check_confirm_missing(
    result: AgentOrchestratorResult, reply: str, user_text: str
) -> list[str]:
    """检查是否缺少必要的确认步骤。"""
    issues = []

    # 运动指令（含坐标）在 restricted_agent 中应该是 waiting_confirmation
    compact = re.sub(r"\s+", "", user_text or "")
    if re.search(r"走到|移动到|X\d+", compact):
        if result.kind == "restricted_agent":
            agent_payload = result.payload
            if isinstance(agent_payload, RestrictedAgentResult):
                if agent_payload.kind == "bypass":
                    # 运动指令不应该 bypass
                    if "急停" not in compact and "暂停" not in compact:
                        issues.append("K1: 运动指令被 bypass，跳过了确认流程")
                elif agent_payload.kind == "clarification":
                    # 坐标指令不应停留在 clarification
                    if re.search(r"X\d+|Y\d+|Z\d+", compact):
                        issues.append("K2: 含明确坐标的指令被当成了澄清疑问")

    return issues


def _check_error_swallowed(
    result: AgentOrchestratorResult, user_text: str
) -> list[str]:
    """检查异常是否被吞没。"""
    issues = []
    # 输入无法识别应给出 feedback，不应静默 fallback
    if result.kind == "fallback_legacy":
        msg = result.message or ""
        if msg == "交回旧 NLP 路径。" and len((user_text or "").strip()) > 2:
            issues.append("L1: 异常被吞 — 无法处理但未给出任何有意义的错误提示")

    return issues


def _check_context_loss(
    user_text: str, result: AgentOrchestratorResult, reply: str
) -> list[str]:
    """检查多轮对话中上下文丢失。"""
    issues = []
    compact = re.sub(r"\s+", "", user_text or "")

    # 用户给出了流程名但系统不识别
    if "名字叫" in compact or "叫" in compact:
        if result.kind == "fallback_legacy":
            issues.append("M1: 上下文丢失 — 用户给了流程名但引擎回退")
        if result.kind == "clarification" and "什么" in reply and "名字" in reply:
            issues.append("M2: 上下文丢失 — 用户已说名字但系统又问名字")

    # 步骤继承的上下文
    if "步骤" in compact and "添加" in compact:
        if result.kind == "fallback_legacy":
            issues.append("M3: 上下文丢失 — 添加步骤的意图未被识别")
        if result.kind == "clarification" and "请问" in reply and "什么流程" in reply:
            issues.append("M4: 上下文丢失 — 用户说了添加步骤但系统问什么流程")

    return issues


# ── 回复格式化 ────────────────────────────────────────────────────

def _format_reply(result: AgentOrchestratorResult) -> str:
    if result.kind == "fallback_legacy":
        return f"[fallback] {result.message}"
    elif result.kind in {"chat_answer", "clarification"}:
        return result.message
    elif result.kind == "restricted_agent":
        payload = result.payload
        if isinstance(payload, RestrictedAgentResult):
            if payload.kind == "waiting_confirmation" and payload.draft:
                return ConfirmationAgent().render_confirmation_text(payload.draft)
            return f"[{payload.kind}] {payload.message}"
        return f"[restricted_agent] {payload}"
    elif result.kind == "compound_plan_draft":
        return f"[复合草案] {result.message}"
    elif result.kind == "unsupported_compound":
        return f"[不支持] {result.message}"
    elif result.kind in {
        "dashboard_query_action", "command_catalog", "confirm_plan", "confirm_rejected",
        "followup_rejected", "atomic_template_action", "precheck_failed", "flow_draft",
        "confirm_result", "confirm_cancelled",
    }:
        return result.message
    return f"[{result.kind}] {result.message}"


# ══════════════════════════════════════════════════════════════════
# 场景定义 — 每项为 [用户输入, 检验说明]
# ══════════════════════════════════════════════════════════════════

ALL_SCENARIOS: list[dict[str, Any]] = [
    {
        "title": "基本运动指令",
        "description": "全参数/半参数/增量指令",
        "spec_ref": "参数说明书 §3-5",
        "turns": [
            ["走到 X1000 Y200 Z800 RX0 RY45 RZ0 速度60% 加速度50% 减速度50%",
             "§4.1 全参数：6位置+3速度 → Func108 确认，全部标注「指定」"],
            ["高度降低100",
             "追问：单参数改Z，继承当前 X=500 Y=0 → Z=500，标注「继承当前」"],
            ["向左移动200",
             "追问：增量运动 → X=700，position_increment=1"],
            ["走到安全位置",
             "模糊指令 → 无坐标，应有澄清/不应进确认"],
            ["走到 X800 Z300",
             "追问补全坐标 → 应成功解析为 waiting_confirmation"],
        ],
    },
    {
        "title": "系统控制与急停",
        "description": "急停/暂停/继续/取消/复位",
        "spec_ref": "状态说明书 §1.2-1.3, 参数说明书 §3.3",
        "turns": [
            ["急停", "sys_estop, bypass, 不应进 waiting_confirmation"],
            ["报警复位", "alarm_reset, bypass"],
            ["暂停", "sys_pause, bypass"],
            ["继续", "sys_resume, bypass"],
            ["取消当前动作", "sys_cancel, bypass"],
            ["紧急停止", "「紧急停止」→ 不属于系统别名白名单，应识别意图或 clarification"],
        ],
    },
    {
        "title": "状态与报警查询",
        "description": "报警原因/状态/不能动/完成确认",
        "spec_ref": "状态说明书 §1.1, §2",
        "turns": [
            ["当前报警是什么", "alarm_query → chat_answer, 不应进入确认"],
            ["为什么不能动了", "说明书1.2 → status_query, 应查急停/暂停/报警状态"],
            ["运动完成了吗", "完成确认 → status_query, 不应进确认"],
            ["报警什么原因", "报警原因查询 → chat_answer"],
            ["当前状态怎么样", "普通状态查询 → chat_answer"],
            ["系统就绪了吗", "说明书1.1: 就绪条件检查"],
        ],
    },
    {
        "title": "创建流程（多轮对话）",
        "description": "模拟日志 session 172840 失败路径",
        "spec_ref": "参数说明书 §8, 日志 session 172840",
        "turns": [
            ["你好，我先创建一个新的流程",
             "创建流程意图 → engine不应回退，应询问名字"],
            ["现在流程名字叫测试",
             "【关键】不应虚假「已登记成功」"],
            ["添加第一步是移动到位置 A",
             "位置A未知 → 应 clarification 问具体坐标"],
            ["我觉得坐标是 X 一百 Y0 Z100 速度 50",
             "【缺陷】X一百 中文数字，至少 Y0 Z100 被解析"],
            ["为什么也，为什么为什么又在哄呢",
             "追问原因 — 不应出现 home位置参数 幻觉"],
            ["对呀，那肯定用我的坐标呀",
             "确认 — 应给出有效反馈而非反问"],
        ],
    },
    {
        "title": "安全预检与运动超限",
        "description": "半径/高度超限时预检拒绝",
        "spec_ref": "状态说明书 §3.1-3.2",
        "turns": [
            ["走到 X100 Y0 Z300", "安全范围内 → waiting_confirmation"],
            ["走到 X3000 Y0 Z300", "半径3000 > 2200 → precheck_failed, 应给出超限数值和建议"],
            ["为什么不能走", "追问原因 — 应复述超限原因，而非「请补充坐标」"],
            ["那走到 X1500 Y0 Z300", "球面半径1529 > Zmax1200 → precheck_failed"],
            ["那走到 X500 Y0 Z800", "范围内 → waiting_confirmation"],
            ["高度降低500",
             "600-500=100，但100 < Zmin=0 → precheck_failed, 应提示Z超下界"],
        ],
    },
    {
        "title": "复合指令",
        "description": "复合指令拆分, 并行/条件拒绝",
        "spec_ref": "参数说明书 §8.1-8.2",
        "turns": [
            ["走到 X1000 然后等待2秒 再走到 X1500",
             "运动+延时+运动 → compound 3步"],
            ["走到 X500 然后 IO1 开",
             "运动+IO → compound 2步"],
            ["同时走到 X1000 并且 IO1 开",
             "并行 → unsupported_compound"],
            ["如果没有报警就走到 X1000",
             "条件 → unsupported_compound"],
            ["那串行执行总可以吧",
             "追问 — 应识别为拆分意图，不应 fallback"],
        ],
    },
    {
        "title": "闲聊安全",
        "description": "闲聊不触发机械手执行",
        "spec_ref": "状态说明书 §1, 参数说明书 §7.3",
        "turns": [
            ["你好", "闲聊 → chat_answer, 不应进 restricted_agent"],
            ["你是谁", "自我介绍 → chat_answer"],
            ["今天天气怎么样", "天气 → 回答无法查询，不触发动作"],
            ["那你到底能做什么", "能力范围 → chat_answer, 不应被当 clarification"],
            ["L2是什么", "概念解释 → chat_answer"],
            ["为什么要确认", "确认原因说明 → chat_answer"],
        ],
    },
    {
        "title": "边界与错误输入",
        "description": "空输入/乱码/超长/特殊字符",
        "spec_ref": "通用",
        "turns": [
            ["", "空输入 → unknown 或 提示"],
            ["!!!@@@###", "乱码 → 不崩溃，不进入 execution"],
            ["正走 X1000", "方向用词「正走」不标准 → 应识别意图或澄清"],
            ["去那个安全位置", "模糊位置 → 不应进 waiting_confirmation"],
            ["能不能走到 X1000", "疑问句 → 不应进确认，应回答可行性"],
            ["走到 X1000 Y200 Z800 然后等待 然后 IO1 开 然后走到 home",
             "超长复合指令 → 不崩溃"],
        ],
    },
    {
        "title": "参数继承验证",
        "description": "半参数/单参数指令的继承正确性",
        "spec_ref": "参数说明书 §4.2",
        "turns": [
            ["速度30%走到 X500",
             "速度指定30%，Y/Z/姿态继承 → 确认文本标注「指定」vs「继承当前」"],
            ["走到 Y200",
             "只改Y → X/Z不变，标注继承"],
            ["RY转到45度",
             "只改姿态RY → 位置不变"],
            ["走到 X500 Z800 速度 50 加速度 40",
             "X/Z/速度/加速度指定 → 减速度继承安全参数80%"],
        ],
    },
]


# ── 执行 ──────────────────────────────────────────────────────────

def run_all(runtime: str = "agent_runtime") -> None:
    runner = build_dialogue_runner(runtime)
    all_turns: list[dict[str, Any]] = []
    total_issues = 0

    for si, scenario in enumerate(ALL_SCENARIOS):
        print(f"\n{'═' * 72}")
        print(f"  场景 {si + 1}：{scenario['title']}")
        print(f"  说明：{scenario['description']}")
        print(f"  对照：{scenario['spec_ref']}")
        print(f"{'─' * 72}")

        for ti, (text, check) in enumerate(scenario["turns"]):
            result = runner.handle(text)
            reply = _format_reply(result)
            issues = _check_issues(result, reply, text, check)

            prefix = "👤 用户" if ti == 0 else "👤 追问"
            print(f"\n  [{ti + 1}] {prefix}：{text}")
            print(f"      🤖 系统：{reply[:280]}")
            print(f"      kind={result.kind}")
            print(f"      📋 预期：{check}")
            if issues:
                for iss in issues:
                    print(f"      ⚠ {iss}")
                total_issues += len(issues)

            all_turns.append({
                "scenario": scenario["title"],
                "turn": ti + 1,
                "user": text,
                "reply": reply[:200],
                "kind": result.kind,
                "check": check,
                "issues": issues,
            })

    # 统计
    print(f"\n{'═' * 72}")
    print(f"  汇总")
    kind_counts: dict[str, int] = {}
    issue_by_code: dict[str, int] = {}
    for t in all_turns:
        kind_counts[t["kind"]] = kind_counts.get(t["kind"], 0) + 1
        for iss in t["issues"]:
            code = iss.split(":")[0].strip() if ":" in iss else iss
            issue_by_code[code] = issue_by_code.get(code, 0) + 1

    print(f"  总轮次: {len(all_turns)}")
    print(f"  发现问题: {total_issues}")
    print(f"  kind 分布: {dict(sorted(kind_counts.items(), key=lambda x: -x[1]))}")
    if issue_by_code:
        print(f"  问题分类:")
        for code, count in sorted(issue_by_code.items(), key=lambda x: -x[1]):
            print(f"    {code}: {count}")

    # JSON 报告
    report_path = Path(__file__).resolve().parent.parent / "data" / "exported_logs" / "dialogue_sim_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        "summary": {"runtime": runtime, "total_turns": len(all_turns), "total_issues": total_issues,
                     "kind_distribution": kind_counts, "issue_categories": issue_by_code},
        "turns": all_turns,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  JSON 报告: {report_path}")


if __name__ == "__main__":
    _configure_console_encoding()
    runtime = "agent_runtime"
    if "--runtime" in sys.argv:
        index = sys.argv.index("--runtime")
        if index + 1 < len(sys.argv):
            runtime = sys.argv[index + 1]
    run_all(runtime=runtime)
