# Unified Agent Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a unified Agent entrypoint that routes all user input while keeping safety and execution deterministic.

**Architecture:** Introduce `AgentOrchestrator` as the single backend entrypoint. It wraps the existing restricted-agent modules, adds chat explanation and compound planning as separate phases, and keeps completion/safety/confirmation as shared modules.

**Tech Stack:** Python dataclasses, pytest, existing `robot_modbus_lite.agent` modules, existing Qt `operator_ui_mixin.py` integration.

---

## File Structure

- Create: `robot_modbus_lite/agent/orchestrator.py`
  - Unified entrypoint and result model.
- Create: `robot_modbus_lite/agent/chat_explanation.py`
  - Deterministic explanation responses for system concepts and current Agent state.
- Create: `robot_modbus_lite/agent/compound.py`
  - Sequential compound command splitting and draft coordination.
- Create: `robot_modbus_lite/agent/memory_setting.py`
  - Deterministic memory-parameter setting for speed, step length, and confirmation mode.
- Create: `robot_modbus_lite/agent/position_memory.py`
  - Deterministic local position-library save/delete action routing.
- Create: `robot_modbus_lite/agent/atomic_template.py`
  - Deterministic old atomic-template routing for position move and rest pose.
- Create: `robot_modbus_lite/agent/dashboard_query.py`
  - Deterministic seven-board dashboard query routing.
- Create: `robot_modbus_lite/agent/flow_draft.py`
  - Delegates existing flow-draft parsing into the Orchestrator.
- Create: `robot_modbus_lite/agent/registered_flow.py`
  - Delegates existing registered-flow parsing into the Orchestrator.
- Modify: `robot_modbus_lite/agent/service.py`
  - Keep `RestrictedAgentService` as motion/utility service used by Orchestrator.
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
  - Adapt new Orchestrator results to `VoiceNlpPlan`.
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
  - Replace white-list gate with Orchestrator call in a later phase.
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_chat_explanation.py`
- Test: `tests/test_agent_compound.py`
- Test: `tests/test_agent_memory_setting.py`
- Test: `tests/test_agent_position_memory.py`
- Test: `tests/test_agent_atomic_template.py`
- Test: `tests/test_agent_dashboard_query.py`
- Test: `tests/test_agent_flow_draft.py`
- Test: `tests/test_agent_registered_flow.py`
- Test: `tests/test_operator_precheck_helpers.py`

---

## Task 1: Add Orchestrator Result Model

**Files:**
- Create: `robot_modbus_lite/agent/orchestrator.py`
- Test: `tests/test_agent_orchestrator.py`

- [x] **Step 1: Write failing tests**

```python
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator, AgentOrchestratorResult


def test_orchestrator_returns_unknown_for_plain_chat_when_chat_agent_disabled():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=None)

    result = orchestrator.handle("你好")

    assert isinstance(result, AgentOrchestratorResult)
    assert result.kind == "fallback_legacy"
    assert result.message == "交回旧 NLP 路径。"


def test_orchestrator_routes_supported_motion_to_restricted_service():
    class FakeRestrictedService:
        def parse(self, text):
            self.text = text
            return "restricted-result"

    service = FakeRestrictedService()
    orchestrator = AgentOrchestrator(restricted_service=service, chat_agent=None)

    result = orchestrator.handle("走到 X1000 Z300")

    assert result.kind == "restricted_agent"
    assert result.payload == "restricted-result"
    assert service.text == "走到 X1000 Z300"
```

- [x] **Step 2: Run failing tests**

Run:

```powershell
pytest tests/test_agent_orchestrator.py -q
```

Expected: fail because `robot_modbus_lite.agent.orchestrator` does not exist.

- [x] **Step 3: Implement minimal Orchestrator**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent


@dataclass(frozen=True)
class AgentOrchestratorResult:
    kind: str
    message: str = ""
    payload: Any = None


class AgentOrchestrator:
    def __init__(self, *, restricted_service: Any, chat_agent: Any = None, understanding_agent: CommandUnderstandingAgent | None = None) -> None:
        self.restricted_service = restricted_service
        self.chat_agent = chat_agent
        self.understanding_agent = understanding_agent or CommandUnderstandingAgent()

    def handle(self, text: str) -> AgentOrchestratorResult:
        understanding = self.understanding_agent.understand(text)
        if self._should_route_to_restricted_agent(understanding) and self.restricted_service is not None:
            return AgentOrchestratorResult(
                kind="restricted_agent",
                payload=self.restricted_service.parse(text),
            )
        return AgentOrchestratorResult(kind="fallback_legacy", message="交回旧 NLP 路径。")

    @staticmethod
    def _should_route_to_restricted_agent(understanding: Any) -> bool:
        intent = str(getattr(understanding, "intent", "") or "")
        if intent == "unknown":
            return False
        return intent in {
            "alarm_query",
            "status_query",
            "move_linear",
            "delay_blocking",
            "delay_parallel",
            "io",
            "sys_estop",
            "sys_pause",
            "sys_resume",
            "sys_cancel",
            "alarm_reset",
        }
```

- [x] **Step 4: Verify tests pass**

Run:

```powershell
pytest tests/test_agent_orchestrator.py -q
```

Expected: pass.

---

## Task 2: Add ChatExplanationAgent

**Files:**
- Create: `robot_modbus_lite/agent/chat_explanation.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Test: `tests/test_agent_chat_explanation.py`
- Test: `tests/test_agent_orchestrator.py`

- [x] **Step 1: Write failing tests**

```python
from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent
from robot_modbus_lite.agent.orchestrator import AgentOrchestrator


def test_chat_explains_l2_without_generating_command():
    result = ChatExplanationAgent().answer("L2是什么")

    assert result is not None
    assert result["kind"] == "chat_answer"
    assert "运动规划预演" in result["text"]
    assert result["generates_command"] is False


def test_orchestrator_routes_l2_question_to_chat_agent():
    orchestrator = AgentOrchestrator(restricted_service=None, chat_agent=ChatExplanationAgent())

    result = orchestrator.handle("这个L2是什么")

    assert result.kind == "chat_answer"
    assert "运动规划预演" in result.message


def test_chat_does_not_intercept_control_text_with_confirmation_keyword():
    result = ChatExplanationAgent().answer("确认执行走到X1000")

    assert result is None
```

- [x] **Step 2: Run failing tests**

```powershell
pytest tests/test_agent_chat_explanation.py tests/test_agent_orchestrator.py -q
```

Expected: fail because `ChatExplanationAgent` is missing.

- [x] **Step 3: Implement ChatExplanationAgent**

```python
from __future__ import annotations


class ChatExplanationAgent:
    CONTROL_KEYWORDS = (
        "走到",
        "移动",
        "向左",
        "向右",
        "向前",
        "向后",
        "升高",
        "下降",
        "急停",
        "暂停",
        "复位",
        "IO",
        "io",
        "等待",
        "延时",
    )

    def answer(self, text: str) -> dict | None:
        compact = str(text or "").replace(" ", "")
        if any(keyword in compact for keyword in self.CONTROL_KEYWORDS):
            return None
        if "L2" in compact or "运动规划" in compact or "预演" in compact:
            return {
                "kind": "chat_answer",
                "text": "L2是运动规划预演，用来检查从当前位置到目标点的路径、逆解、关节限位和奇异风险。它不是执行指令，也不会直接驱动机械手。",
                "generates_command": False,
            }
        if "为什么要确认" in compact or "确认执行" in compact:
            return {
                "kind": "chat_answer",
                "text": "确认执行用于让操作者核对补全后的完整参数和安全预检结果。未确认前，Agent 不会生成实际执行请求。",
                "generates_command": False,
            }
        return None
```

- [x] **Step 4: Update Orchestrator**

Add before restricted routing:

```python
if self.chat_agent is not None:
    answer = self.chat_agent.answer(text)
    if answer is not None:
        return AgentOrchestratorResult(kind="chat_answer", message=str(answer["text"]), payload=answer)
```

- [x] **Step 5: Verify tests pass**

```powershell
pytest tests/test_agent_chat_explanation.py tests/test_agent_orchestrator.py -q
```

Expected: pass.

---

## Task 3: Adapt Orchestrator Results To VoiceNlpPlan

**Files:**
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Test: `tests/test_agent_plan_adapter.py`

- [x] **Step 1: Write failing test**

```python
from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult
from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter


def test_plan_adapter_converts_chat_answer():
    result = AgentOrchestratorResult(kind="chat_answer", message="L2是运动规划预演。")

    plan = AgentPlanAdapter().to_voice_plan(result)

    assert plan.actions[0].action_type == "chat"
    assert plan.actions[0].reason == "L2是运动规划预演。"
    assert plan.requires_confirmation is False
    assert plan.source == "agent_orchestrator"
```

- [x] **Step 2: Run failing test**

```powershell
pytest tests/test_agent_plan_adapter.py::test_plan_adapter_converts_chat_answer -q
```

Expected: fail because adapter only accepts `RestrictedAgentResult`.

- [x] **Step 3: Extend AgentPlanAdapter**

Change `to_voice_plan` parameter annotation from `RestrictedAgentResult` to `Any`, then add a branch before existing restricted-result handling:

```python
from typing import Any


def to_voice_plan(self, result: Any) -> VoiceNlpPlan:
    if not hasattr(result, "kind"):
        raise TypeError(f"unsupported agent result: {type(result).__name__}")

if getattr(result, "kind", "") == "chat_answer":
    text = str(getattr(result, "message", "") or "")
    return VoiceNlpPlan(
        actions=(VoiceNlpAction("chat", None, "agent_orchestrator", text, text),),
        source="agent_orchestrator",
        raw_text=text,
        reason=text,
        semantic_level=1,
        semantic_label="闲聊解释层",
        requires_precheck=False,
        requires_confirmation=False,
        priority="normal",
        nlp_engine="agent_orchestrator",
    )
```

- [x] **Step 4: Verify adapter tests**

```powershell
pytest tests/test_agent_plan_adapter.py -q
```

Expected: pass.

---

## Task 4: Add CompoundCommandCoordinator Skeleton

**Files:**
- Create: `robot_modbus_lite/agent/compound.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Test: `tests/test_agent_compound.py`

- [x] **Step 1: Write failing tests**

```python
from robot_modbus_lite.agent.compound import CompoundCommandCoordinator


def test_compound_splits_simple_sequential_command():
    result = CompoundCommandCoordinator().split("走到X1000，然后等待2秒，再IO1开")

    assert result.kind == "compound_sequence"
    assert result.steps == ("走到X1000", "等待2秒", "IO1开")


def test_compound_rejects_parallel_or_conditional_commands():
    coordinator = CompoundCommandCoordinator()

    assert coordinator.split("同时走到X1000并且IO1开").kind == "unsupported_compound"
    assert coordinator.split("如果没有报警就走到X1000").kind == "unsupported_compound"


def test_compound_does_not_split_when_any_part_is_not_actionable():
    class FakeUnderstandingAgent:
        def understand(self, text):
            intent = "move_linear" if "X1000" in text else "unknown"
            return type("Result", (), {"intent": intent})()

    coordinator = CompoundCommandCoordinator(understanding_agent=FakeUnderstandingAgent())

    result = coordinator.split("走到X1000然后告诉我结果")

    assert result.kind == "not_compound"
```

- [x] **Step 2: Run failing tests**

```powershell
pytest tests/test_agent_compound.py -q
```

Expected: fail because module is missing.

- [x] **Step 3: Implement safe splitter**

```python
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CompoundSplitResult:
    kind: str
    steps: tuple[str, ...] = ()
    message: str = ""


class CompoundCommandCoordinator:
    def __init__(self, understanding_agent=None):
        from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent

        self.understanding_agent = understanding_agent or CommandUnderstandingAgent()

    def split(self, text: str) -> CompoundSplitResult:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return CompoundSplitResult(kind="not_compound")
        if any(word in compact for word in ("同时", "并行", "如果", "循环", "重复")):
            return CompoundSplitResult(kind="unsupported_compound", message="暂不支持并行、条件、循环类复合指令。")
        if not any(word in compact for word in ("然后", "再", "接着")):
            return CompoundSplitResult(kind="not_compound")
        parts = tuple(part for part in re.split(r"然后|再|接着", compact) if part)
        if len(parts) < 2:
            return CompoundSplitResult(kind="not_compound")
        if not all(self._is_actionable(part) for part in parts):
            return CompoundSplitResult(kind="not_compound", message="复合指令中包含非动作子句。")
        return CompoundSplitResult(kind="compound_sequence", steps=parts)

    def _is_actionable(self, text: str) -> bool:
        result = self.understanding_agent.understand(text)
        return str(getattr(result, "intent", "") or "") != "unknown"
```

- [x] **Step 4: Verify splitter tests**

```powershell
pytest tests/test_agent_compound.py -q
```

Expected: pass.

---

## Task 5: Build Compound Draft Plan Without Execution

**Files:**
- Modify: `robot_modbus_lite/agent/compound.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Test: `tests/test_agent_compound.py`
- Test: `tests/test_agent_orchestrator.py`

Implementation note: first release returns an auditable `compound_plan_draft` only. It must not enter GUI "confirm execution" flow, because multi-step confirmation UI has not been reviewed.

- [x] **Step 1: Write failing test**

```python
def test_compound_builds_step_results_without_executing():
    class FakeRestrictedService:
        def parse(self, text):
            return {"kind": "waiting_confirmation", "text": text}

    coordinator = CompoundCommandCoordinator(
        restricted_service=FakeRestrictedService(),
        clock=lambda: 100.0,
        id_factory=lambda: "compound:test",
    )

    result = coordinator.plan("走到X1000，然后等待2秒")

    assert result.kind == "compound_plan_draft"
    assert result.plan_id.startswith("compound:")
    assert result.raw_text == "走到X1000，然后等待2秒"
    assert result.created_at == 100.0
    assert len(result.step_results) == 2
    assert result.step_results[0]["text"] == "走到X1000"
    assert result.step_results[1]["text"] == "等待2秒"
```

- [x] **Step 2: Run failing test**

```powershell
pytest tests/test_agent_compound.py::test_compound_builds_step_results_without_executing -q
```

Expected: fail because `plan()` does not exist.

- [x] **Step 3: Implement plan method**

```python
from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent


@dataclass(frozen=True)
class CompoundPlanResult:
    kind: str
    plan_id: str
    raw_text: str
    created_at: float
    steps: tuple[str, ...]
    step_results: tuple[object, ...]
    message: str = ""


class CompoundCommandCoordinator:
    def __init__(self, restricted_service=None, understanding_agent=None, clock=None, id_factory=None):
        self.restricted_service = restricted_service
        self.understanding_agent = understanding_agent or CommandUnderstandingAgent()
        self.clock = clock or (lambda: 0.0)
        self.id_factory = id_factory or (lambda: "compound:manual")

    def plan(self, text: str) -> CompoundPlanResult:
        split = self.split(text)
        if split.kind != "compound_sequence":
            return CompoundPlanResult(kind=split.kind, plan_id="", raw_text=text, created_at=float(self.clock()), steps=(), step_results=(), message=split.message)
        if self.restricted_service is None:
            return CompoundPlanResult(kind="unsupported_compound", plan_id="", raw_text=text, created_at=float(self.clock()), steps=split.steps, step_results=(), message="复合指令服务未配置。")
        results = tuple(self.restricted_service.parse(step) for step in split.steps)
        return CompoundPlanResult(kind="compound_plan_draft", plan_id=str(self.id_factory()), raw_text=text, created_at=float(self.clock()), steps=split.steps, step_results=results)
```

- [x] **Step 4: Verify tests**

```powershell
pytest tests/test_agent_compound.py tests/test_agent_orchestrator.py -q
```

Expected: pass.

---

## Task 6: Switch GUI To Orchestrator In Shadow Mode

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

```python
def test_operator_try_agent_orchestrator_returns_chat_plan():
    dummy = DummyOperator()
    dummy._operator_restricted_agent_service = lambda: None

    plan = dummy._operator_try_agent_orchestrator_plan("L2是什么")

    assert plan.actions[0].action_type == "chat"
    assert "运动规划预演" in plan.reason


def test_operator_legacy_fallback_when_orchestrator_declines():
    dummy = DummyOperator()
    dummy._operator_restricted_agent_service = lambda: None

    plan = dummy._operator_try_agent_orchestrator_plan("小正，J1转到45度")

    assert plan is None
```

- [x] **Step 2: Run failing tests**

```powershell
pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_chat_plan tests/test_operator_precheck_helpers.py::test_operator_legacy_fallback_when_orchestrator_declines -q
```

Expected: fail because `_operator_try_agent_orchestrator_plan` is missing.

- [x] **Step 3: Add GUI helper**

Add helper that calls Orchestrator and converts results:

```python
def _operator_try_agent_orchestrator_plan(self, text: str):
    from .agent.chat_explanation import ChatExplanationAgent
    from .agent.orchestrator import AgentOrchestrator
    from .agent.plan_adapter import AgentPlanAdapter

    orchestrator = AgentOrchestrator(
        restricted_service=self._operator_restricted_agent_service(),
        chat_agent=ChatExplanationAgent(),
    )
    result = orchestrator.handle(text)
    if result.kind == "fallback_legacy":
        return None
    if result.kind == "restricted_agent":
        return AgentPlanAdapter().to_voice_plan(result.payload)
    return AgentPlanAdapter().to_voice_plan(result)
```

- [x] **Step 4: Verify GUI helper tests**

```powershell
pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_chat_plan tests/test_operator_precheck_helpers.py::test_operator_legacy_fallback_when_orchestrator_declines -q
```

Expected: pass.

---

## Task 7: Replace White-List Entry With Orchestrator

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing regression tests**

```python
def test_operator_parse_uses_orchestrator_before_legacy_path():
    dummy = DummyOperator()
    calls = []
    plan = VoiceNlpPlan(actions=(VoiceNlpAction("chat", None, "agent_orchestrator", "L2是什么", "L2是运动规划预演。"),), source="agent_orchestrator", raw_text="L2是什么", reason="L2是运动规划预演。")
    dummy.nlp_input_edit = SimpleNamespace(toPlainText=lambda: "L2是什么")
    dummy._operator_try_agent_orchestrator_plan = lambda text: plan
    dummy._set_nlp_parse_busy = lambda busy: calls.append(("busy", busy))
    dummy._set_nlp_result_plan = lambda parsed: calls.append(("plan", parsed))
    dummy.status_label = SimpleNamespace(setText=lambda text: calls.append(("status", text)))
    dummy._append_log = lambda *args, **kwargs: calls.append(("log", args))
    dummy._operator_prepare_pending_flow_creation_followup_text = lambda text: text
    dummy._handle_operator_ui_command = lambda text: False
    dummy._operator_reject_new_action_while_busy = lambda text: False

    dummy._parse_nlp_text()

    assert ("plan", plan) in calls
```

- [x] **Step 2: Run failing test**

```powershell
pytest tests/test_operator_precheck_helpers.py::test_operator_parse_uses_orchestrator_before_legacy_path -q
```

Expected: fail until `_parse_nlp_text` uses Orchestrator helper.

- [x] **Step 3: Update `_parse_nlp_text` and `_execute_nlp_text`**

Replace calls to `_operator_try_restricted_agent_plan(text)` with `_operator_try_agent_orchestrator_plan(text)`.

- [x] **Step 4: Verify operator tests**

```powershell
pytest tests/test_operator_precheck_helpers.py -q
```

Expected: pass.

---

## Task 8: Migrate Memory Settings Into Orchestrator

**Files:**
- Create: `robot_modbus_lite/agent/memory_setting.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_agent_memory_setting.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- `小正，速度60%` updates `AtomicMemory.current_speed/current_acc/current_dec`.
- `小正，步长10毫米` updates `current_step_mm`.
- `小正，专家模式` updates `confirm_mode`.
- Motion text such as `小正，上升3毫米` and `让机械手走到X1000速度60%` is not intercepted as memory setting.
- Orchestrator returns `memory_setting_answer`.
- GUI helper returns a chat plan and saves atomic memory.

- [x] **Step 2: Implement `MemorySettingAgent`**

Reuse `AtomicParser.parse()` for `family == "memory"` and call existing `AtomicMemory` setters. The result is read-only from the execution perspective:

```text
kind = memory_setting_answer
generates_command = false
```

- [x] **Step 3: Wire Orchestrator and GUI**

Inject `MemorySettingAgent(memory=self._atomic_memory, save_callback=...)` from `operator_ui_mixin.py`. Convert `memory_setting_answer` through `AgentPlanAdapter` as a chat action.

- [x] **Step 4: Verify**

Executed:

```powershell
python -m pytest tests/test_agent_memory_setting.py -q
python -m pytest tests/test_agent_orchestrator.py::test_orchestrator_routes_memory_setting_before_chat tests/test_agent_plan_adapter.py::test_plan_adapter_converts_memory_setting_answer -q
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_memory_setting_chat_plan -q
```

Expected: pass.

## Task 9: Migrate Position Save/Delete Into Orchestrator

**Files:**
- Create: `robot_modbus_lite/agent/position_memory.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Modify: `robot_modbus_lite/nlp_mixin.py`
- Modify: `robot_modbus_lite/atomic_resolver.py`
- Test: `tests/test_agent_position_memory.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`
- Test: `tests/test_voice_nlp_atomic.py`

- [x] **Step 1: Write failing tests**

Cover:

- `小正，保存当前位置为位置A` returns `position_memory_action` with target `position_save:A`.
- `小正，删除位置A` returns `position_memory_action` with target `position_delete:A`.
- Position query and position move are not intercepted.
- Orchestrator routes position memory before chat fallback.
- PlanAdapter converts the result to existing `VoiceNlpAction("memory", ...)`.
- GUI helper returns a memory plan.
- `position_delete:A` removes from memory and registry during execution.

- [x] **Step 2: Implement `PositionMemoryAgent`**

Reuse `AtomicParser.parse()` for `family == "position"` and only accept `save:*` / `delete:*`. The Agent does not mutate local state during parsing.

- [x] **Step 3: Wire Orchestrator, Adapter, and GUI**

Inject `PositionMemoryAgent()` from `operator_ui_mixin.py`. Convert `position_memory_action` through `AgentPlanAdapter` to the existing memory action path.

- [x] **Step 4: Complete execution-stage delete**

Extend `_nlp_apply_memory_action()` so `position_delete:<name>` deletes from `PositionRegistry` and `AtomicMemory` during execution. This keeps parse-only flows side-effect free.

- [x] **Step 5: Fix confirm-mode zero target bug**

Fix `AtomicResolver._resolve_memory()` so `target=0` maps to `expert` instead of falling back to `beginner`.

- [x] **Step 6: Verify**

Executed:

```powershell
python -m pytest tests/test_agent_position_memory.py tests/test_agent_orchestrator.py::test_orchestrator_routes_position_memory_before_chat tests/test_agent_plan_adapter.py::test_plan_adapter_converts_position_memory_action -q
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_position_save_memory_plan tests/test_operator_precheck_helpers.py::test_nlp_apply_memory_action_deletes_position_from_memory_and_registry tests/test_voice_nlp_atomic.py::test_voice_nlp_adapter_sets_expert_confirm_mode -q
```

Expected: pass.

## Task 10: Migrate Position Move, Rest Pose, And History Atomic Templates

**Files:**
- Create: `robot_modbus_lite/agent/atomic_template.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_agent_atomic_template.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- `小正，移动到位置A` returns an atomic template Func108 record from local memory/position registry.
- `小正，去休息` returns the default rest-pose Func108 record.
- `小正，再走一次` repeats `AtomicMemory.last_record`.
- `小正，继续` uses `AtomicMemory.last_direction`.
- `小正，返回上一步` uses `AtomicMemory.position_stack`.
- Position save/delete are not intercepted by this agent.
- Parsing history commands restores the atomic memory snapshot and does not mutate last record/direction/position stack.
- Orchestrator and PlanAdapter preserve `atomic_records`.
- GUI helper returns an executable `atomic_template` plan.

- [x] **Step 2: Implement `AtomicTemplateAgent`**

Reuse `AtomicParser` and `AtomicResolver`, but only allow:

```text
family == position and name starts with move:
family == rest_pose
family == history and name in {repeat, continue, back}
```

- [x] **Step 3: Wire Orchestrator, Adapter, and GUI**

Inject `AtomicTemplateAgent(memory=self._atomic_memory)` from `operator_ui_mixin.py`, synchronizing `memory.position_registry` when available. Convert `atomic_template_action` into existing `VoiceNlpAction("atomic_template", ...)`.

- [x] **Step 4: Verify**

Executed:

```powershell
python -m pytest tests/test_agent_atomic_template.py tests/test_agent_orchestrator.py::test_orchestrator_routes_atomic_template_before_chat tests/test_agent_plan_adapter.py::test_plan_adapter_converts_atomic_template_action_with_record -q
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_position_move_atomic_template tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_rest_pose_atomic_template tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_repeats_last_atomic_template tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_continues_last_direction_atomic_template tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_back_history_atomic_template -q
```

Expected: pass.

## Task 11: Migrate Dashboard Query Routing Into Orchestrator

**Files:**
- Create: `robot_modbus_lite/agent/dashboard_query.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_agent_dashboard_query.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- `通讯正常吗` maps to `communication_faults`.
- `当前位置安全吗` maps to `safety_boundary`.
- Cartesian motion text is not intercepted.
- Orchestrator routes dashboard query before chat fallback.
- PlanAdapter converts it into existing `VoiceNlpAction("query", ...)`.
- GUI helper returns a query plan.

- [x] **Step 2: Implement `DashboardQueryAgent`**

Reuse `dashboard_query_specs.match_dashboard_query_spec()` and only generate:

```text
kind = dashboard_query_action
action_type = query
target = board_key
```

Do not generate answer text from Agent. Existing `DashboardQueryService` remains the single answer generator.

- [x] **Step 3: Wire Orchestrator, Adapter, and GUI**

Inject `DashboardQueryAgent()` from `operator_ui_mixin.py`. Convert `dashboard_query_action` to existing query plan.

- [x] **Step 4: Verify**

Executed:

```powershell
python -m pytest tests/test_agent_dashboard_query.py tests/test_agent_orchestrator.py::test_orchestrator_routes_dashboard_query_before_chat tests/test_agent_plan_adapter.py::test_plan_adapter_converts_dashboard_query_action tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_dashboard_query_plan -q
```

Expected: pass.

## Task 12: Delegate Flow Draft Plans Through Orchestrator

**Files:**
- Create: `robot_modbus_lite/agent/flow_draft.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_agent_flow_draft.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- Existing `VoiceNlpPlan(source="flow_draft")` is accepted.
- Flow-draft clarification plans are accepted.
- Non-flow plans are ignored.
- Orchestrator routes `flow_draft_plan`.
- PlanAdapter returns the original `VoiceNlpPlan` unchanged.
- GUI helper returns the existing flow draft plan.

- [x] **Step 2: Implement `FlowDraftAgent`**

Delegate to an injected parse function and only accept `source == "flow_draft"`.

- [x] **Step 3: Wire Orchestrator, Adapter, and GUI**

Inject `FlowDraftAgent(parse_func=...)` from `operator_ui_mixin.py`. The GUI parse function uses the existing `VoiceNlpAdapter` and returns the old `VoiceNlpPlan`; PlanAdapter passes it through unchanged.

- [x] **Step 4: Verify**

Executed:

```powershell
python -m pytest tests/test_agent_flow_draft.py tests/test_agent_orchestrator.py::test_orchestrator_routes_flow_draft_before_chat tests/test_agent_plan_adapter.py::test_plan_adapter_passes_through_flow_draft_plan tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_flow_draft_plan -q
```

Expected: pass.

## Task 13: Delegate Registered Flow Plans Through Orchestrator

**Files:**
- Create: `robot_modbus_lite/agent/registered_flow.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_agent_registered_flow.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- Existing `VoiceNlpPlan` whose actions are all `flow` is accepted.
- Non-flow plans are ignored.
- Orchestrator routes `registered_flow_plan`.
- PlanAdapter returns the original `VoiceNlpPlan` unchanged.
- GUI helper returns the existing registered flow plan.

- [x] **Step 2: Implement `RegisteredFlowAgent`**

Delegate to an injected parse function and only accept plans whose actions are all `flow`.

- [x] **Step 3: Wire Orchestrator, Adapter, and GUI**

Inject `RegisteredFlowAgent(parse_func=...)` from `operator_ui_mixin.py`. The GUI parse function uses the existing `VoiceNlpAdapter`; PlanAdapter passes the old plan through unchanged.

- [x] **Step 4: Verify**

Executed:

```powershell
python -m pytest tests/test_agent_registered_flow.py tests/test_agent_orchestrator.py::test_orchestrator_routes_registered_flow_before_chat tests/test_agent_plan_adapter.py::test_plan_adapter_passes_through_registered_flow_plan tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_registered_flow_plan -q
```

Expected: pass.

## Task 14: Add Fallback Audit Payload

**Files:**
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- Plain chat with no chat agent returns `fallback_legacy` and includes `reason`.
- Legacy-only unknown text returns `fallback_legacy` and preserves a deterministic understanding summary.
- GUI helper logs the fallback audit summary, then still returns `None` so old NLP can handle the input.

- [x] **Step 2: Run failing tests**

Executed:

```powershell
python -m pytest tests/test_agent_orchestrator.py::test_orchestrator_returns_unknown_for_plain_chat_when_chat_agent_disabled tests/test_agent_orchestrator.py::test_orchestrator_fallback_payload_marks_model_candidate_control_text -q
```

Expected failure: `result.payload` is `None`.

- [x] **Step 3: Implement audit payload**

Add stable payload fields to `fallback_legacy`:

```text
reason
needs_model
understanding.raw_text
understanding.intent
understanding.func_id
understanding.confidence
understanding.clarification
understanding.bypass_completion
```

This does not change GUI behavior. `_operator_try_agent_orchestrator_plan()` still returns `None` for `fallback_legacy`, so the old NLP path remains the fallback executor/parser.

Add GUI log detail:

```text
Agent / 统一Agent交回旧路径 / 提示
reason=...; intent=...; func_id=...; confidence=...; needs_model=...; clarification=...
```

- [x] **Step 4: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_agent_orchestrator.py::test_orchestrator_returns_unknown_for_plain_chat_when_chat_agent_disabled tests/test_agent_orchestrator.py::test_orchestrator_fallback_payload_marks_model_candidate_control_text -q
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_logs_fallback_audit_payload -q
```

Expected: pass.

## Task 15: Route Ambiguous Control Text To Deterministic Clarification

**Files:**
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- `CommandUnderstandingAgent` model-candidate control text such as `往安全一点的位置挪一下` returns Orchestrator `clarification`.
- `AgentPlanAdapter` converts Orchestrator `clarification` into existing `VoiceNlpAction("clarification", ...)`.
- GUI helper returns a clarification plan instead of falling back to old NLP.
- Existing restricted-agent `kind="clarification"` adapter behavior remains unchanged.

- [x] **Step 2: Run failing tests**

Executed:

```powershell
python -m pytest tests/test_agent_orchestrator.py::test_orchestrator_routes_model_candidate_control_text_to_clarification tests/test_agent_plan_adapter.py::test_adapter_converts_orchestrator_clarification_to_clarification_action tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_clarification_for_ambiguous_control_text -q
```

Expected: fail because model-candidate control text still returns `fallback_legacy`, and PlanAdapter has no Orchestrator clarification branch.

- [x] **Step 3: Implement deterministic clarification routing**

Add an Orchestrator branch before chat/fallback:

```text
if understanding.needs_model:
    return clarification with serialized understanding payload
```

Add a PlanAdapter branch for Orchestrator-only `clarification`; keep old `RestrictedAgentResult(kind="clarification")` behavior unchanged.

- [x] **Step 4: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_agent_orchestrator.py tests/test_agent_plan_adapter.py tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_falls_back_for_unknown_legacy_text tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_logs_fallback_audit_payload tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_returns_clarification_for_ambiguous_control_text tests/test_operator_precheck_helpers.py::test_operator_execute_nlp_plan_handles_clarification_without_running_sequence -q
```

Expected: pass.

## Task 16: Remove Old Whitelist From Restricted Service Injection

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- Orchestrator-supported actions still receive `RestrictedAgentService` when `_operator_should_try_restricted_agent()` returns `False`.
- `restricted_agent_enabled=False` still prevents service creation.

- [x] **Step 2: Run failing test**

Executed:

```powershell
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_does_not_depend_on_old_whitelist_for_supported_actions -q
```

Expected: fail because GUI helper only injected restricted service when the old whitelist matched.

- [x] **Step 3: Implement feature-switch based service injection**

Add `_operator_restricted_agent_enabled()` and change `_operator_try_agent_orchestrator_plan()`:

```text
restricted_service = _operator_restricted_agent_service()
only when axis_ranges.restricted_agent_enabled is true
```

Do not change legacy `_operator_try_restricted_agent_plan()`; it can keep the old whitelist for compatibility.

- [x] **Step 4: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_does_not_depend_on_old_whitelist_for_supported_actions tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_respects_restricted_agent_disabled -q
```

Expected: pass.

## Task 17: Add Position Increment Protocol Alias

**Files:**
- Modify: `robot_modbus_lite/agent/command_understanding.py`
- Modify: `robot_modbus_lite/agent/parameter_completion.py`
- Test: `tests/test_agent_command_understanding.py`
- Test: `tests/test_agent_parameter_completion.py`

- [x] **Step 1: Write failing tests**

Cover:

- Absolute Cartesian text such as `走到X1000` exposes `position_increment=0`.
- Incremental Cartesian text such as `向左移动200` exposes `position_increment=1`.
- Completed linear drafts preserve `position_increment` in `params` and `param_sources`.
- Existing execution field `fuzzy_pos` remains unchanged in this phase.

- [x] **Step 2: Run failing tests**

Executed:

```powershell
python -m pytest tests/test_agent_command_understanding.py::test_understands_incremental_cartesian_move_without_model tests/test_agent_command_understanding.py::test_understands_absolute_cartesian_move_marks_position_increment_zero -q
python -m pytest tests/test_agent_parameter_completion.py::test_completion_applies_incremental_cartesian_offsets_from_current_pose tests/test_agent_parameter_completion.py::test_completion_marks_absolute_cartesian_position_increment_zero -q
```

Expected: fail because no `position_increment` alias exists.

- [x] **Step 3: Implement alias without changing MODBUS write semantics**

Add `position_increment` to understanding and completed draft params:

```text
absolute Cartesian: position_increment=0
delta Cartesian: position_increment=1
```

Keep existing `fuzzy_pos` behavior unchanged. The alias is for protocol audit and future para(10) mapping.

- [x] **Step 4: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_agent_command_understanding.py::test_understands_incremental_cartesian_move_without_model tests/test_agent_command_understanding.py::test_understands_absolute_cartesian_move_marks_position_increment_zero -q
python -m pytest tests/test_agent_parameter_completion.py::test_completion_applies_incremental_cartesian_offsets_from_current_pose tests/test_agent_parameter_completion.py::test_completion_marks_absolute_cartesian_position_increment_zero -q
```

Expected: pass.

## Task 18: Add Func112 Continuous Path Execution Through Existing Chain

**Files:**
- Modify: `robot_modbus_lite/agent/command_understanding.py`
- Modify: `robot_modbus_lite/agent/parameter_completion.py`
- Modify: `robot_modbus_lite/agent/safety_review.py`
- Modify: `robot_modbus_lite/agent/service.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Modify: `robot_modbus_lite/agent/orchestrator.py`
- Test: `tests/test_agent_command_understanding.py`
- Test: `tests/test_agent_parameter_completion.py`
- Test: `tests/test_restricted_agent_service.py`
- Test: `tests/test_agent_plan_adapter.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- `规划路径/规避/绕行` motion text maps to `intent=continuous_path`, `func_id=112`.
- Func112 reuses Func108 linear parameters and completion.
- Restricted service returns `waiting_confirmation` for Func112.
- Func112 draft is inserted into the same confirmation queue as Func108.
- Confirmed Func112 drafts generate `QueryRecord(func_num=112)`.
- `SixAxisCommand` writes IEEE(0)=112 and reuses Func108 parameter layout.

- [x] **Step 2: Run failing tests**

Executed:

```powershell
python -m pytest tests/test_agent_command_understanding.py::test_understands_continuous_path_motion_as_func112_executable_candidate tests/test_agent_parameter_completion.py::test_completion_builds_func112_executable_draft_with_linear_parameters tests/test_restricted_agent_service.py::test_parse_continuous_path_returns_waiting_confirmation_and_query_record tests/test_mock_controller_v50.py::test_func112_writes_use_linear_parameter_layout_with_func112_id -q
```

Expected: fail because Func112 is not routed or executable yet.

- [x] **Step 3: Implement executable Func112**

Add:

```text
CommandUnderstandingAgent: continuous_path / Func112
ParameterCompletionAgent: Func112 uses linear parameter completion
SafetyReviewAgent: Func112 uses the same L1/L2 move target checks as Func108
RestrictedAgentService: returns waiting_confirmation and uses confirmation_agent.begin()
AgentPlanAdapter: converts Func112 waiting_confirmation to agent_draft plan
AgentOrchestrator: routes continuous_path to restricted service
SixAxisCommand: writes Func112 with the Func108 parameter layout
```

- [x] **Step 4: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_agent_command_understanding.py::test_understands_continuous_path_motion_as_func112_executable_candidate tests/test_agent_parameter_completion.py::test_completion_builds_func112_executable_draft_with_linear_parameters tests/test_restricted_agent_service.py::test_parse_continuous_path_returns_waiting_confirmation_and_query_record tests/test_mock_controller_v50.py::test_func112_writes_use_linear_parameter_layout_with_func112_id tests/test_operator_precheck_helpers.py::test_operator_try_agent_orchestrator_routes_continuous_path_to_agent_draft -q
```

Expected: pass.

## Task 19: Add Compound Step State Machine Metadata

**Files:**
- Modify: `robot_modbus_lite/agent/compound.py`
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Test: `tests/test_agent_compound.py`
- Test: `tests/test_agent_plan_adapter.py`

- [x] **Step 1: Write failing tests**

Cover:

- Compound plans start at step 0 with `waiting_step_confirmation`.
- Confirming a step changes status to `step_confirmed`.
- Marking a confirmed step completed advances to the next waiting step.
- Completing the last step marks the compound plan `completed`.
- Any step with `precheck_failed` / `blocked` stops the compound plan.
- PlanAdapter exposes state-machine metadata in `flow_draft.step_machine`.

- [x] **Step 2: Run failing tests**

Executed:

```powershell
python -m pytest tests/test_agent_compound.py -q
python -m pytest tests/test_agent_plan_adapter.py::test_plan_adapter_converts_compound_plan_draft_to_nonexecutable_summary -q
```

Expected: fail because no compound step state machine exists.

- [x] **Step 3: Implement pure state machine**

Add `CompoundStepMachine` and `CompoundStepState` in `compound.py`.

The state machine is pure metadata:

```text
from_plan()
confirm_current()
mark_current_completed()
mark_current_failed()
```

It does not call restricted service, confirmation service, or MODBUS.

- [x] **Step 4: Add adapter metadata**

Add `flow_draft.step_machine` summary to compound display plans.

- [x] **Step 5: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_agent_compound.py tests/test_agent_plan_adapter.py::test_plan_adapter_converts_compound_plan_draft_to_nonexecutable_summary -q
```

Expected: pass.

## Task 20: Show Compound Step Machine Summary In GUI

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing tests**

Cover:

- Compound display text includes current waiting step, such as `当前等待确认第 1/2 步：走到X1000`.
- Blocked step machine displays the blocked reason.
- The compound plan still does not start an execution sequence.

- [x] **Step 2: Run failing test**

Executed:

```powershell
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_execute_nlp_plan_handles_compound_plan_without_running_sequence -q
```

Expected: fail because GUI only listed raw steps and did not display `step_machine`.

- [x] **Step 3: Implement GUI summary helper**

Add `_operator_compound_step_machine_text()` and include its output in compound plan display text.

This helper is read-only and does not call confirmation or execution paths.

- [x] **Step 4: Verify targeted tests**

Executed:

```powershell
python -m pytest tests/test_operator_precheck_helpers.py::test_operator_execute_nlp_plan_handles_compound_plan_without_running_sequence tests/test_operator_precheck_helpers.py::test_operator_compound_step_machine_text_shows_blocked_reason -q
```

Expected: pass.

## Verification

Run after all tasks:

```powershell
$files = rg --files tests | Where-Object { $_ -match 'test_agent_.*\.py$' }
pytest @($files + 'tests/test_restricted_agent_service.py') -q
pytest tests/test_operator_precheck_helpers.py -q
pytest tests/test_semantic_response_policy.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_atomic.py tests/test_safety_precheck.py tests/test_motion_plan.py -q
python -m compileall -q robot_modbus_lite
```

Expected:

- Agent tests pass.
- Operator tests pass.
- Existing voice/atomic/safety tests pass.
- Compile check exits successfully.

## Rollout Notes

- Keep old `VoiceNlpAdapter` and `AtomicParser` paths as fallback.
- Current implementation has migrated explicit Func106/Func107 jog commands such as `小正，J1转到45度`, `小正，RY反转15度`, and wake-word virtual-axis jogs such as `小正，上升3毫米` into the Orchestrator restricted-agent path. They are auxiliary jog/teaching/micro-adjustment capabilities, not replacements for the Func108/Func112 Cartesian parameter-motion main path. They still generate confirmable drafts and then convert to the existing `QueryRecord` execution chain after confirmation.
- Current implementation also routes read-only capability questions such as `支持哪些原子命令` through `ChatExplanationAgent`, reusing the existing atomic capability table and never generating an execution draft.
- Identity and usage questions such as `你是谁` and `怎么使用` are handled by `ChatExplanationAgent` as read-only explanations and never generate execution drafts.
- Read-only position-library questions such as `位置A坐标是多少` are handled by `PositionQueryAgent` through the Orchestrator. The agent reads the local `PositionRegistry` via an injected lookup and never generates move/save/delete drafts.
- Memory-parameter settings such as `小正，速度60%`, `小正，步长10毫米`, and `小正，专家模式` are handled by `MemorySettingAgent` through the Orchestrator. They update `AtomicMemory`, save it through the existing GUI save hook, return a chat response, and never generate execution drafts.
- Position save/delete requests such as `小正，保存当前位置为位置A` and `小正，删除位置A` are handled by `PositionMemoryAgent` through the Orchestrator. They generate existing `memory` actions and only mutate the position registry during execution, not during parsing.
- Position move/rest-pose/history requests such as `小正，移动到位置A`, `小正，去休息`, `小正，再走一次`, `小正，继续`, and `小正，返回上一步` are handled by `AtomicTemplateAgent` through the Orchestrator. They generate existing `atomic_template` plans and continue through existing confirmation/precheck/execution.
- Dashboard query requests such as `通讯正常吗` and `当前位置安全吗` are handled by `DashboardQueryAgent` through the Orchestrator. They generate existing `query` plans and continue to use `DashboardQueryService` for the final answer.
- Flow draft requests are handled by `FlowDraftAgent` through the Orchestrator. It delegates to the existing `VoiceNlpAdapter` and passes through `flow_draft` plans unchanged.
- Registered flow requests are handled by `RegisteredFlowAgent` through the Orchestrator. It delegates to the existing `VoiceNlpAdapter` and passes through `flow` plans unchanged.
- Inputs not handled by connected Orchestrator modules return `fallback_legacy` with an audit payload containing reason, needs_model, and the deterministic understanding summary. GUI still hands these inputs to the old NLP fallback path.
- GUI logs `fallback_legacy` audit summaries before handing inputs to the old NLP fallback path.
- Ambiguous control text marked `needs_model=True` returns deterministic `clarification` and does not fall back to old NLP guessing.
- Orchestrator restricted-service injection is controlled by `restricted_agent_enabled`, not by the old GUI whitelist.
- `AddressResolver` / `AddressConfig` has been added as the protocol difference abstraction layer. The default configuration keeps current Func108/Func112 behavior, while injected configurations can change values such as `continuous_path_func` without editing business Agent modules.
- `absolute_motion_func` can be configured to 8 or 102 for controllers that require the document's absolute-motion function IDs; Func8/102 reuse the Func108 parameter layout through `CommandDraft`, `RobotModbusService`, and `SixAxisCommand`.
- Linear motion understanding and drafts expose `position_increment` as the document-facing alias for para(10); confirmed execution copies map it to `fuzzy_pos`.
- Func112 continuous path requests now enter the normal confirmation queue and confirmed QueryRecords write IEEE(0)=112 with the Func108 parameter layout.
- Compound plans include step state-machine metadata and, when all steps are confirmable, an executable flow draft. GUI waits for explicit “确认执行” before starting the existing flow execution chain.
- LLM fallback is available behind the existing DeepSeek checkbox. It only accepts `candidate_text` or `clarification`; candidate text is re-parsed by local rules before any restricted Agent path is used, and can route to either a single restricted Agent command or a local compound plan.
- FrameTrans2 L2 precheck is wired to the real controller client: `ZMotionVrClient.frame_trans2()` wraps `ZAux_Direct_FrameTrans2`, `FrameTrans2KinematicsEngine` prefers the direct SDK method with mode 2, and `ControllerRuntimeMixin` installs the engine after controller connection.
- Do not commit automatically; user requested no git submission in this workstream.
