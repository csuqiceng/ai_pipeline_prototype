#!/usr/bin/env python3
"""AI 多轮对话测试工具 —— 直接调用新 Agent 管线（AgentOrchestrator + RestrictedAgentService）。

构造完整的 Agent 链路（含受限 Agent），模拟操作员多轮对话，规则检查 + LLM 评估。

用法:
    python tools/ai_nlp_tester.py
    python tools/ai_nlp_tester.py --api glm
    python tools/ai_nlp_tester.py --turns 20
    python tools/ai_nlp_tester.py --scenario full|safety|chat|commands|confirm|param|alarm
    python tools/ai_nlp_tester.py -o report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

import requests  # noqa: E402

# ── 项目模块 ──
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter, VoiceNlpAction, VoiceNlpPlan  # noqa: E402
from robot_modbus_lite.query_table import load_query_table_json  # noqa: E402
from robot_modbus_lite.flow_store import load_flows_json  # noqa: E402
from robot_modbus_lite.runtime_paths import resolve_runtime_data_file  # noqa: E402
from robot_modbus_lite.atomic_memory import AtomicMemory  # noqa: E402
from robot_modbus_lite.atomic_parser import AtomicParser  # noqa: E402
from robot_modbus_lite.atomic_resolver import AtomicResolver  # noqa: E402
from robot_modbus_lite.assistant_knowledge_base import AssistantKnowledgeBase  # noqa: E402
from robot_modbus_lite.system_config import AxisRangeConfig, load_system_config  # noqa: E402
from robot_modbus_lite.safety_precheck import SafetyPrecheckService  # noqa: E402

# ── Agent 管线 ──
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator  # noqa: E402
from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter  # noqa: E402
from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent  # noqa: E402
from robot_modbus_lite.agent.compound import CompoundCommandCoordinator  # noqa: E402
from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent  # noqa: E402
from robot_modbus_lite.agent.dashboard_query import DashboardQueryAgent  # noqa: E402
from robot_modbus_lite.agent.position_query import PositionQueryAgent  # noqa: E402
from robot_modbus_lite.agent.memory_setting import MemorySettingAgent  # noqa: E402
from robot_modbus_lite.agent.position_memory import PositionMemoryAgent  # noqa: E402
from robot_modbus_lite.agent.atomic_template import AtomicTemplateAgent  # noqa: E402
from robot_modbus_lite.agent.flow_draft import FlowDraftAgent  # noqa: E402
from robot_modbus_lite.agent.registered_flow import RegisteredFlowAgent  # noqa: E402
from robot_modbus_lite.agent.service import RestrictedAgentService  # noqa: E402
from robot_modbus_lite.agent.parameter_completion import ControllerSnapshot  # noqa: E402
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent  # noqa: E402
from robot_modbus_lite.agent.confirmation import ConfirmationAgent  # noqa: E402
from robot_modbus_lite.agent.address_resolver import AddressResolver  # noqa: E402
from robot_modbus_lite.voice_wake_words import strip_wake_word_from_compact  # noqa: E402

# ── API 配置 ──
API_BASE_URL = {"deepseek": "https://api.deepseek.com/v1/chat/completions", "glm": "https://open.bigmodel.cn/api/paas/v4/chat/completions"}
API_DEFAULT_MODEL = {"deepseek": "deepseek-chat", "glm": "glm-4-flash"}
API_ENV_KEY = {"deepseek": "DEEPSEEK_API_KEY", "glm": "GLM_API_KEY"}
DOCS_DIR = PROJECT_ROOT / "docs"


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
# 数据类
# ══════════════════════════════════════════════════════════════════════
@dataclass
class Turn:
    no: int
    input: str
    agent_kind: str = ""
    plan_dict: dict | None = None
    reply: str = ""
    actions: str = ""
    ms: float = 0.0
    violations: list[str] = field(default_factory=list)


@dataclass
class Report:
    ts: str
    provider: str
    model: str
    scenario: str
    turns_total: int = 0
    sec: float = 0.0
    violations_total: int = 0
    turns: list[Turn] = field(default_factory=list)
    analysis: str = ""


# ══════════════════════════════════════════════════════════════════════
# LLM
# ══════════════════════════════════════════════════════════════════════
class LLM:
    def __init__(self, api="deepseek", key=None, model=None):
        self.api = api
        self.url = API_BASE_URL.get(api, API_BASE_URL["deepseek"])
        self.key = key or os.getenv(API_ENV_KEY.get(api, ""), "")
        self.model = model or API_DEFAULT_MODEL.get(api, "deepseek-chat")
        if not self.key:
            self._env()
            self.key = os.getenv(API_ENV_KEY.get(api, ""), "")
        if not self.key:
            raise ValueError(f"需要 {API_ENV_KEY.get(api, '')} 环境变量")

    def _env(self):
        f = PROJECT_ROOT / ".env"
        if not f.exists(): return
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, _, v = ln.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

    def chat(self, sys, user, t=0.3):
        return self._call([{"role": "system", "content": sys}, {"role": "user", "content": user}], t)

    def hist(self, msgs, t=0.3):
        return self._call(msgs, t)

    def _call(self, msgs, t):
        r = requests.post(self.url, headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
                          json={"model": self.model, "messages": msgs, "temperature": t, "max_tokens": 2048}, timeout=120)
        r.raise_for_status()
        return str((r.json().get("choices") or [{}])[0].get("message", {}).get("content", ""))


# ══════════════════════════════════════════════════════════════════════
# Agent 测试环境 — 完整新管线
# ══════════════════════════════════════════════════════════════════════
class TestBed:
    def __init__(self):
        self.table = load_query_table_json(resolve_runtime_data_file("query_table.json"))
        flows = load_flows_json(resolve_runtime_data_file("flows.json"))
        self.flow_names = list(flows.keys())

        ap = resolve_runtime_data_file("atomic_state.json")
        self.mem = AtomicMemory.load(ap) if Path(ap).exists() else AtomicMemory()
        parser = AtomicParser()
        resolver = AtomicResolver(self.mem)
        ua = CommandUnderstandingAgent(parser=parser)

        # VoiceNlpAdapter (供 FlowDraftAgent/RegisteredFlowAgent 内部使用)
        self.adapter = VoiceNlpAdapter(self.table, self.flow_names, atomic_memory=self.mem, knowledge_base=AssistantKnowledgeBase.load())
        self._setup_ds()

        # ── RestrictedAgentService ──
        config = self._load_axis_config()
        l1 = SafetyPrecheckService(config)
        safety = SafetyReviewAgent(l1_service=l1)
        addr = AddressResolver()

        self._snapshot_data = self._mock_snapshot()
        self._t0 = time.monotonic()

        restricted = RestrictedAgentService(
            controller_snapshot_provider=lambda: ControllerSnapshot(
                current_pose={"target_x": 900, "target_y": 0, "target_z": 1000,
                              "target_rx": 0, "target_ry": 90, "target_rz": 0},
                safety_params={"spd_pct": 50, "acc_pct": 60, "dec_pct": 60},
                is_moving=False, read_ok=True,
            ),
            runtime_snapshot_provider=lambda: self._snapshot_data,
            safety_review_agent=safety,
            status_signature_provider=lambda: "mock",
            safety_signature_provider=lambda: "mock",
            clock=lambda: time.monotonic() - self._t0,
            understanding_agent=ua,
            address_resolver=addr,
        )

        # ── AgentOrchestrator ──
        self.orch = AgentOrchestrator(
            restricted_service=restricted,
            chat_agent=ChatExplanationAgent(),
            position_query_agent=PositionQueryAgent(lookup=self._pos_lookup),
            memory_setting_agent=MemorySettingAgent(memory=self.mem, parser=parser),
            position_memory_agent=PositionMemoryAgent(parser=parser),
            atomic_template_agent=AtomicTemplateAgent(memory=self.mem, parser=parser, resolver=resolver),
            dashboard_query_agent=DashboardQueryAgent(),
            flow_draft_agent=FlowDraftAgent(parse_func=self.adapter.parse),
            registered_flow_agent=RegisteredFlowAgent(parse_func=self.adapter.parse),
            understanding_agent=ua,
            compound_coordinator=CompoundCommandCoordinator(restricted_service=restricted, understanding_agent=ua),
            llm_fallback_agent=None,
            llm_fallback_enabled=False,
        )
        self.restricted = restricted
        self.pa = AgentPlanAdapter()

    def _setup_ds(self):
        try:
            from robot_modbus_lite.deepseek_client import DeepSeekClient
            self.adapter.set_deepseek_client(DeepSeekClient.from_env())
            self.has_ds = True
        except Exception:
            self.has_ds = False

    def _load_axis_config(self):
        try:
            return load_system_config(resolve_runtime_data_file("system_config.json"))
        except Exception:
            return AxisRangeConfig.from_dict({})

    def _mock_snapshot(self):
        return {
            "connection": {"controller": "online", "realtime_feedback": "online"},
            "safety": {"estop": False, "paused": False, "alarm_active": False, "alarm_code": 0},
            "motion": {"running_state": "idle", "speed_percent": 30},
            "position": {"cartesian": {"x": 900, "y": 0, "z": 1000, "r": 900}, "joint": {"j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 90, "j6": 0}},
        }

    def _pos_lookup(self, name):
        p = self.mem.get_position(name)
        if p: return list(p)
        r = self.table.get(name)
        if r and isinstance(getattr(r, "params", None), dict):
            keys = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
            if all(k in r.params for k in keys):
                return [float(r.params[k]) for k in keys]
        return None

    def parse(self, text):
        """返回 (VoiceNlpPlan | None, result_kind, latency_ms)。"""
        t0 = time.monotonic()
        raw_text = str(text or "")
        result = self.orch.handle(raw_text)
        stripped_text = strip_wake_word_from_compact(raw_text) or raw_text
        if stripped_text != raw_text and _is_weak_agent_result(result):
            stripped_result = self.orch.handle(stripped_text)
            if _is_better_agent_result(stripped_result, result):
                result = stripped_result
        if _is_weak_agent_result(result):
            legacy_plan = self.adapter.parse(raw_text)
            if legacy_plan is not None and not _is_unknown_plan(legacy_plan):
                ms = (time.monotonic() - t0) * 1000
                return legacy_plan, "legacy_adapter", ms
        ms = (time.monotonic() - t0) * 1000
        kind = str(getattr(result, "kind", "") or "")
        if kind == "fallback_legacy":
            return None, kind, ms

        # 拆包: orchestrator 返回 AgentOrchestratorResult(kind, payload=...)
        # AgentPlanAdapter 期望内层 payload（RestrictedAgentResult 等）
        inner = result
        if kind == "restricted_agent":
            inner = getattr(result, "payload", result)

        try:
            plan = self.pa.to_voice_plan(inner)
            return plan, kind, ms
        except Exception:
            # 兜底: 从 result 构造最小 plan
            msg = str(getattr(result, "message", "") or "")
            payload = getattr(result, "payload", None)
            if isinstance(payload, dict):
                ud = payload.get("understanding") or {}
                reason = msg or str(ud.get("clarification", "")) or kind
            else:
                reason = msg or kind
            plan = VoiceNlpPlan(
                actions=(VoiceNlpAction("unknown", None, "agent_orchestrator", text, reason),),
                source="agent_orchestrator", raw_text=text, reason=reason,
                semantic_level=1, semantic_label="解释层",
                nlp_engine="agent_orchestrator",
            )
            return plan, kind, ms

    def confirm(self, draft_id: str):
        """确认待确认的计划。"""
        try:
            record = self.restricted.confirm(draft_id)
            return record
        except Exception as exc:
            return None

    def reject(self, draft_id: str):
        """拒绝待确认的计划。"""
        try:
            self.restricted.reject(draft_id)
        except Exception:
            pass

    def reset(self):
        self.adapter._pending_flow_draft_payload = None
        self.adapter._pending_flow_missing_gesture = None


def _is_weak_agent_result(result: AgentOrchestratorResult) -> bool:
    kind = str(getattr(result, "kind", "") or "")
    if kind == "fallback_legacy":
        return True
    if kind != "clarification":
        return False
    payload = getattr(result, "payload", None)
    if isinstance(payload, dict):
        return bool(payload.get("needs_model", False))
    return True


def _is_better_agent_result(candidate: AgentOrchestratorResult, current: AgentOrchestratorResult) -> bool:
    candidate_kind = str(getattr(candidate, "kind", "") or "")
    current_kind = str(getattr(current, "kind", "") or "")
    if candidate_kind == "fallback_legacy":
        return False
    if current_kind == "fallback_legacy":
        return True
    return not _is_weak_agent_result(candidate) and _is_weak_agent_result(current)


def _is_unknown_plan(plan: VoiceNlpPlan) -> bool:
    actions = tuple(getattr(plan, "actions", ()) or ())
    if not actions:
        return True
    first = actions[0]
    action_type = str(getattr(first, "action_type", "") or "")
    return action_type == "unknown"


# ══════════════════════════════════════════════════════════════════════
# 规则检查
# ══════════════════════════════════════════════════════════════════════
def check(no: int, text: str, pd: dict, kind: str) -> list[str]:
    v = []
    acts = pd.get("actions", [])
    src = pd.get("source", "")
    reason = str(pd.get("reason", "") or "")
    lvl = pd.get("semanticLevel", 0)
    f = acts[0] if acts else {}
    at = str(f.get("actionType") or f.get("action_type") or "")
    tgt = str(f.get("target") or "")

    # A1: 静默失败
    if at == "unknown" and not reason:
        v.append(f"A1: T{no} 静默失败")

    # B1: 带唤醒词的急停必须匹配 sys_estop（无唤醒词的急停由 B2 覆盖）
    wake = "小正" in text or "小兵" in text
    if "急停" in text and wake and (at != "system" or tgt != "sys_estop"):
        v.append(f"B1: T{no} 急停(有唤醒词)→{at}:{tgt}")

    # B2: 无唤醒词不执行
    wake = "小正" in text or "小兵" in text
    ctrl_kw = ("到位置", "移动到", "去位置", "抓取", "放下", "回零", "回家", "暂停", "继续", "报警复位", "归零")
    if not wake and any(k in text for k in ctrl_kw) and at not in ("unknown", "chat", "clarification"):
        v.append(f"B2: T{no} 无唤醒词→{at}:{tgt}")

    # C1: 急停 L5
    if at == "system" and tgt == "sys_estop" and lvl != 5:
        v.append(f"C1: T{no} 急停L{lvl}≠L5")

    # C2: 暂停/继续 L4
    if at == "system" and tgt in ("sys_pause", "sys_resume", "sys_cancel") and lvl != 4:
        v.append(f"C2: T{no} {tgt} L{lvl}≠L4")

    # D1: 模板需确认
    if at in ("template", "atomic_template", "agent_draft") and not pd.get("requiresConfirmation"):
        v.append(f"D1: T{no} {at}未要求确认")

    # E1: 聊天不触发控制
    chat_kw = ("你好", "您好", "你能做什么", "怎么用", "天气", "谢谢")
    if any(k in text for k in chat_kw) and at in ("template", "atomic_template", "system", "flow"):
        v.append(f"E1: T{no} 聊天→{at}:{tgt}")

    # F1: 复合多步
    if ("先" in text and ("然后" in text or "再" in text)) and len(acts) < 2:
        v.append(f"F1: T{no} 复合→{len(acts)}步")

    # G1: fallback
    if kind == "fallback_legacy":
        v.append(f"G1: T{no} Agent fallback")

    return v


# ══════════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════════
def reply_of(plan: VoiceNlpPlan) -> str:
    if not plan.actions:
        return plan.reason or "（无动作）"
    a = plan.actions[0]
    at, t = a.action_type, a.target or ""
    m = {"sys_estop": "急停已识别。", "sys_pause": "暂停已识别。", "sys_resume": "继续已识别。",
         "sys_cancel": "取消已识别。", "alarm_reset": "报警复位已识别。"}
    if at == "chat": return plan.reason or "（无回复）"
    if at == "query": return f"查询: {t}"
    if at == "system": return m.get(t, f"系统: {t}")
    if at in ("template", "atomic_template"): return f"已匹配: {t}，等待确认。"
    if at == "flow": return f"流程: {t}"
    if at == "flow_draft": return plan.reason or f"草案: {t}"
    if at == "clarification": return plan.reason or "需补充。"
    if at == "compound_plan": return plan.reason or "复合指令。"
    if at in ("agent_draft", "agent_blocked"): return plan.reason or f"Agent: {t}"
    return plan.reason or f"({at}: {t})"

def acts_of(plan: VoiceNlpPlan) -> str:
    if not plan.actions: return "无动作"
    p = [f"{a.action_type}:{a.target or '-'}" for a in plan.actions[:5]]
    s = " → ".join(p)
    return s + (f" (+{len(plan.actions) - 5})" if len(plan.actions) > 5 else "")


# ══════════════════════════════════════════════════════════════════════
# 上下文 + 场景
# ══════════════════════════════════════════════════════════════════════
def ctx() -> str:
    parts = []
    # 需求文档
    for n in ["机械手自然语言交互系统_项目需求与技术路线建议书_V2.1.md", "智能协同机械手操作方案.md"]:
        p = DOCS_DIR / n
        if p.exists(): parts.append(f"=== {n} ===\n{p.read_text('utf-8')[:3000]}")
    # 编程手册 — 含确认协议、参数继承规则
    for n in ["机械手自然语言交互系统_编程手册_V1.1.md", "机械手自然语言交互系统_分步开发说明书_V1.0.md"]:
        p = DOCS_DIR / n
        if p.exists(): parts.append(f"=== {n} ===\n{p.read_text('utf-8')[:3000]}")
    # 参数解析说明书 — 含关键词映射、安全预检逻辑
    p = DOCS_DIR / "自然语言参数类指令解析说明书_上位机对接.md"
    if p.exists(): parts.append(f"=== 参数指令说明书 ===\n{p.read_text('utf-8')[:2000]}")
    # 状态说明书
    p = DOCS_DIR / "机械手基础运行信息交互状态说明书_用于上位机对接.md"
    if p.exists(): parts.append(f"=== 状态说明书 ===\n{p.read_text('utf-8')[:2000]}")
    qt = PROJECT_ROOT / "data" / "query_table.json"
    if qt.exists():
        rs = json.loads(qt.read_text("utf-8")).get("records", [])
        parts.append(f"=== 模板 ({len(rs)}) ===\n" + "\n".join(f"  - {r['query_key']}: F{r['func_num']} {r.get('description', '')} kw:{r.get('keywords', '')}" for r in rs))
    fl = PROJECT_ROOT / "data" / "flows.json"
    if fl.exists():
        raw = json.loads(fl.read_text("utf-8"))
        flow_list = raw.get("flows", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        parts.append("=== 流程 ===\n" + "\n".join(f"  - {f['name']}: {f.get('steps', [])}" for f in flow_list if isinstance(f, dict)))
    return "\n\n".join(parts)

SCENARIOS = {
    "full": "完整操作流程: 打招呼→查状态→运动指令(带唤醒词)→确认→查结果→系统命令→安全边界→闲聊→流程→复合指令",
    "safety": "安全专项: 急停→sys_estop(L5), 缺唤醒词→拒绝, 三段式应急编码, 报警复位, 暂停/继续切换, 以下应拒绝: '到位置A' '回家' '抓取' '急停'",
    "chat": "闲聊查询: '你好'→闲聊, '你能做什么'→功能介绍, '怎么用'→使用指引, 天气→拒绝, '查一下当前位置'→查询, '现在速度'→查询",
    "commands": "指令匹配: '小正到位置A'/'去A点'/'移动到位置A'→template:位置A, '回家'/'回零'/'归零'/'回home'→home, '休息姿态'/'休息了'→休息, '抓取'/'夹紧'→抓取, '放下'/'松开'→放下, 复合→多步",
    "confirm": "确认协议: 模板→需确认, 确认→预检通过, 取消→正确处理, 复合确认, 流程确认",
    "param": "参数: 'J1到10度'→关节106, 'X前进50'→虚拟107, 'Z上升50'→直线108, '延时1秒'→109, 速度加速度参数",
    "alarm": "报警: '报警状态'→查询, '有没有报警'→查询, '系统状态'→查询, 报警复位后确认",
}

DRIVER_SYS = """\
你扮演真实机械手操作员与系统对话。用自然口语，有时忘记唤醒词"小正"，根据回复决定下一步。
控制指令带"小正"，闲聊查询不需要。只输出下一句话，结束说[DONE]。
"""


# ══════════════════════════════════════════════════════════════════════
# 对话驱动
# ══════════════════════════════════════════════════════════════════════
def run(llm, bed, scenario, context, max_turns):
    sd = SCENARIOS.get(scenario, SCENARIOS["full"])
    report = Report(ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), provider=llm.api, model=llm.model, scenario=scenario)
    bed.reset()

    th = []
    for k in sorted(bed.table)[:20]:
        r = bed.table[k]
        th.append(f"- {r.query_key}: {r.description or r.keywords}")
    sys_msg = DRIVER_SYS + f"\n\n## 模板\n" + "\n".join(th) + f"\n\n## 场景\n{sd}\n\n## 资料\n{context[:2000]}"

    msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": "开始对话，说第一句。"}]
    t0 = time.monotonic()

    for no in range(1, max_turns + 1):
        print(f"\n  ── T{no} ──")
        try:
            inp = llm.hist(msgs, t=0.6).strip().strip("\"'`")
        except Exception as e:
            print(f"  ⚠ LLM: {e}"); break
        if not inp or inp == "[DONE]": break
        print(f"  🧑 {inp}")

        plan, kind, ms = bed.parse(inp)
        t = Turn(no=no, input=inp, agent_kind=kind, ms=ms)

        if plan:
            t.plan_dict = plan.to_preview_dict()
            t.reply = reply_of(plan)
            t.actions = acts_of(plan)
            t.violations = check(no, inp, t.plan_dict, kind)
            print(f"  🤖 [{kind}] {t.actions} ({ms:.0f}ms)")
            print(f"  📝 {t.reply[:100]}")
        else:
            t.plan_dict = {"_err": True, "kind": kind}
            t.reply = f"(Agent异常: {kind})"
            t.violations = check(no, inp, t.plan_dict, kind)
            print(f"  ⚠ {kind} ({ms:.0f}ms)")

        for vl in t.violations:
            print(f"  🚨 {vl}")

        report.turns.append(t)
        report.violations_total += len(t.violations)

        msgs.append({"role": "assistant", "content": f"我说: 「{inp}」"})
        info = f"Agent:\n- kind: {kind}\n- 动作: {t.actions}\n- 回复: {t.reply[:150]}\n- {ms:.0f}ms\n"
        if t.violations: info += f"- 违规: {'; '.join(t.violations)}\n"
        info += "\n下一句（或[DONE]）:"
        msgs.append({"role": "user", "content": info})

    report.turns_total = len(report.turns)
    report.sec = round(time.monotonic() - t0, 1)
    print(f"\n  结束: {report.turns_total}轮 {report.sec}s {report.violations_total}违规")
    return report


# ══════════════════════════════════════════════════════════════════════
# 评估 + 报告
# ══════════════════════════════════════════════════════════════════════
EVAL_SYS = "分析对话记录，评估意图理解/即时回应/安全合规/回复质量/流程闭环/异常处理/响应速度。输出Markdown: 每轮(✅/⚠️/❌)+问题汇总+改进建议+评分(A/B/C/D)。"

def eval_report(llm, report):
    print("  评估中...")
    log = []
    for t in report.turns:
        e = {"turn": t.no, "input": t.input, "kind": t.agent_kind}
        if t.plan_dict and not t.plan_dict.get("_err"):
            e["src"] = t.plan_dict.get("source")
            e["actions"] = t.actions
            e["reason"] = (t.plan_dict.get("reason") or "")[:80]
            e["level"] = f"L{t.plan_dict.get('semanticLevel', '?')}"
        e["reply"] = t.reply[:150]
        e["ms"] = round(t.ms)
        if t.violations: e["violations"] = t.violations
        log.append(e)
    prompt = f"{report.scenario} | {report.turns_total}轮 | {report.violations_total}违规\n```json\n{json.dumps(log, ensure_ascii=False, indent=2)}\n```"
    try: return llm.chat(EVAL_SYS, prompt, t=0.2)
    except Exception as e: return f"*评估失败: {e}*"

def gen_report(report):
    lines = []
    for t in report.turns:
        vl = f"\n- 🚨 {'; '.join(t.violations)}" if t.violations else ""
        lines.append(f"### T{t.no}\n- **输入**: `{t.input}`\n- **kind**: `{t.agent_kind}`\n- **动作**: `{t.actions}`\n- **回复**: {t.reply[:200]}\n- **耗时**: {t.ms:.0f}ms{vl}")
    return f"""\
# 🤖 Agent 多轮对话测试报告

**时间**: {report.ts} | **AI**: {report.provider}/{report.model} | **场景**: {report.scenario}
**轮数**: {report.turns_total} | **耗时**: {report.sec}s | **违规**: {report.violations_total}条

---

## 对话

{chr(10).join(lines)}

---

## 分析

{report.analysis}

---
*`tools/ai_nlp_tester.py`*"""


# ══════════════════════════════════════════════════════════════════════
# Golden cases — 固定输入 + 期望输出，可复现回归
# ══════════════════════════════════════════════════════════════════════
GOLDEN = [
    # (input, expected_kind, expected_action_type, expected_target_contains, expected_level)
    ("小正，到位置A",          "legacy_adapter",   "template",           "位置A",       3),
    ("小正，休息姿态",         "atomic_template_action", "atomic_template", "rest_pose",  3),
    ("小正，急停",            "restricted_agent", "system",             "sys_estop",   5),
    ("小正，暂停",            "restricted_agent", "system",             "sys_pause",   4),
    ("小正，继续",            "restricted_agent", "system",             "sys_resume",  4),
    ("小正，报警复位",         "restricted_agent", "system",             "alarm_reset", 4),
    ("到位置A",              "clarification",    "clarification",      None,          None),
    ("小正，J1到10度",        "restricted_agent", "agent_draft",        None,          3),
    ("小正，X前进50",         "restricted_agent", "agent_blocked",      None,          3),
    ("小正，Z上升50",         "legacy_adapter",   "atomic_template",    None,          3),
    ("你能做什么",            "chat_answer",      "chat",               None,          1),
    ("你好",                 "chat_answer",      "chat",               None,          1),
]


def run_golden(bed: TestBed) -> str:
    """运行固定 golden cases，输出确定性回归报告。不需要 LLM。"""
    print(f"  运行 {len(GOLDEN)} 条 golden cases...")
    lines = []
    passed = 0
    failed = 0

    for inp, exp_kind, exp_at, exp_tgt, exp_lvl in GOLDEN:
        plan, kind, ms = bed.parse(inp)
        bed.reset()

        if plan:
            pd = plan.to_preview_dict()
            a = plan.actions[0] if plan.actions else None
            at = a.action_type if a else "?"
            tgt = str(a.target or "") if a else ""
            lvl = pd.get("semanticLevel", "?")
        else:
            at, tgt, lvl = "?", "", "?"

        # 判定
        ok = True
        issues = []
        if kind != exp_kind:
            ok = False; issues.append(f"kind={kind}≠{exp_kind}")
        if exp_at and at != exp_at:
            ok = False; issues.append(f"action={at}≠{exp_at}")
        if exp_tgt and exp_tgt not in tgt:
            ok = False; issues.append(f"target={tgt}不含{exp_tgt}")
        if exp_lvl is not None and str(lvl) != str(exp_lvl):
            ok = False; issues.append(f"level={lvl}≠{exp_lvl}")

        icon = "✅" if ok else "❌"
        if ok: passed += 1
        else: failed += 1
        detail = "; ".join(issues) if issues else ""

        lines.append(f"| {icon} | `{inp}` | {kind} | {at}:{tgt} | L{lvl} | {ms:.0f}ms | {detail} |")
        print(f"  {icon} {inp:<28} kind={kind:<28} {at}:{tgt}")

    total = len(GOLDEN)
    pct = passed / total * 100 if total else 0

    md = f"""\
# 🤖 Agent Golden Cases 回归报告

**时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**通过**: {passed}/{total} ({pct:.0f}%)
**失败**: {failed}

| 状态 | 输入 | kind | 动作 | 层级 | 耗时 | 详情 |
|------|------|------|------|------|------|------|
{chr(10).join(lines)}

---
*Golden cases 不依赖 LLM，可复现。*
"""
    print(f"  结果: {passed}/{total} 通过 ({pct:.0f}%)")
    return md


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════
def main():
    _configure_console_encoding()
    ap = argparse.ArgumentParser(description="AI Agent 多轮对话测试")
    ap.add_argument("--api", choices=["deepseek", "glm"], default="deepseek")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--turns", type=int, default=15)
    ap.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="full")
    ap.add_argument("--golden", action="store_true", help="只运行固定 golden cases（不需要 LLM）")
    args = ap.parse_args()

    print("=" * 60)
    print("  Agent 多轮对话测试 (新管线)")
    print("=" * 60)

    print("初始化 Agent...")
    try:
        bed = TestBed()
        print(f"✓ Agent: {len(bed.table)}模板 {len(bed.flow_names)}流程 DS={'✓' if bed.has_ds else '✗'}")
    except Exception as e:
        print(f"❌ {e}"); import traceback; traceback.print_exc(); sys.exit(1)

    # ── Golden mode: 不需要 LLM ──
    if args.golden:
        md = run_golden(bed)
        out = Path(args.output) if args.output else PROJECT_ROOT / "tools" / f"golden_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        out.write_text(md, encoding="utf-8")
        print(f"\n📄 {out}")
        return

    try:
        llm = LLM(api=args.api, key=args.api_key, model=args.model)
        print(f"✓ 测试AI: {args.api}/{llm.model}")
    except ValueError as e:
        print(f"❌ {e}"); sys.exit(1)

    print("\n[1/3] 上下文...")
    c = ctx()
    print(f"  ✓ {len(c)}字")

    print(f"\n[2/3] 对话 ({args.scenario}, {args.turns}轮)...")
    report = run(llm, bed, args.scenario, c, args.turns)

    print("\n[3/3] 评估...")
    report.analysis = eval_report(llm, report)

    md = gen_report(report)
    out = Path(args.output) if args.output else PROJECT_ROOT / "tools" / f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"  ✅ {report.turns_total}轮 {report.sec}s {report.violations_total}违规")
    print(f"  📄 {out}")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
