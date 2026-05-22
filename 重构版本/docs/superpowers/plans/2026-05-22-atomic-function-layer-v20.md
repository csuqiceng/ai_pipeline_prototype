# Atomic Function Layer V2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the offline Qt-side “二次原子函数封装表 V2.0” layer so natural-language commands such as “小正，上升3毫米20%速度”, “小正，J1正转”, “小正，等待2秒”, and “小正，夹爪开” can be parsed into safe, reviewable Func104/106/107/108/109/110/120 command records without relying on pre-created query-table templates.

**Architecture:** Add a small atomic layer below `VoiceNlpAdapter` and above `RobotModbusService`: classifier -> element parser -> resolver -> confirmation/execution bridge. The atomic layer returns deterministic `QueryRecord` objects plus metadata, while existing Qt safety precheck, confirmation, logging, and execution paths continue to handle final approval and controller writes.

**Tech Stack:** Python dataclasses, regex parsing, existing `QueryRecord`, `VoiceNlpPlan`, `SafetyPrecheckService`, pytest.

---

## Current Implementation Analysis

Already implemented:

- Wake-word isolation exists in `robot_modbus_lite/voice_nlp_adapter.py`: normal production commands require `小正/小郑/校正`.
- Three-part emergency coding exists through `EmergencyChannel` and `VoiceNlpAdapter._is_coded_emergency()`.
- System actions `pause/resume/cancel/alarm_reset` already map to Func104 through existing `system` actions.
- Existing template commands can map fixed `query_table.json` records to Func106/107/108/109/110/120 through `RobotModbusService.build_six_command_from_record()`.
- L1/L2/L3 precheck, confirmation page, interaction archiving, response building, and dashboard query answering already exist.

Missing against `docs/二次原子函数封装表_V2.0.md`:

- No dynamic atomic parser for J/V/C/SP/IO/D/S instruction families.
- No memory parameter object for `current_speed/current_step_mm/current_step_deg/current_acc/current_dec/confirm_mode`.
- No deterministic element extraction for step, speed, acceleration, deceleration, axis, IO, delay, coordinates, and move type.
- No dynamic `QueryRecord` generation from parsed atomic commands.
- No risk scoring for low/mid/high and no confirm-mode policy for beginner/skilled/expert.
- No position name library operations for “保存当前位置为位置A / 删除位置A / 位置A坐标是多少”.
- No motion history for “继续前进 / 返回 / 再走一次”.
- Continuous interpolation Func11 is present in low-level service, but the V2.0 doc marks it as reserved; it should stay deferred unless the welding path format is defined.

---

## File Structure

- Create `robot_modbus_lite/atomic_models.py`: dataclasses and enums for atomic memory, parsed elements, resolved commands, risk policy, and temporary template keys.
- Create `robot_modbus_lite/atomic_parser.py`: wake-word classification, danger-word warning, synonym normalization, regex element extraction.
- Create `robot_modbus_lite/atomic_resolver.py`: map parsed elements into `QueryRecord` objects for Func106/107/108/109/110/120 and memory/system/query results.
- Create `robot_modbus_lite/atomic_memory.py`: runtime memory defaults, update methods, and position/motion history storage.
- Modify `robot_modbus_lite/voice_nlp_adapter.py`: call atomic parser before legacy table matching, preserve emergency and dashboard query fast paths.
- Modify `robot_modbus_lite/operator_ui_mixin.py`: execute atomic temporary records through existing confirmation/precheck path and update atomic memory after successful parse/execute.
- Modify `robot_modbus_lite/command_intent_adapter.py`: include atomic params and fuzzy metadata in type-A `command_intent`.
- Add tests:
  - `tests/test_atomic_parser.py`
  - `tests/test_atomic_resolver.py`
  - `tests/test_voice_nlp_atomic.py`
  - `tests/test_operator_atomic_integration.py`

---

### Task 1: Atomic Models and Memory Defaults

**Files:**
- Create: `robot_modbus_lite/atomic_models.py`
- Create: `robot_modbus_lite/atomic_memory.py`
- Test: `tests/test_atomic_memory.py`

- [ ] **Step 1: Write failing memory tests**

Create `tests/test_atomic_memory.py`:

```python
from robot_modbus_lite.atomic_memory import AtomicMemory


def test_atomic_memory_defaults_match_v20_doc():
    memory = AtomicMemory()

    assert memory.current_speed == 50.0
    assert memory.current_step_mm == 10.0
    assert memory.current_step_deg == 5.0
    assert memory.current_acc == 100.0
    assert memory.current_dec == 100.0
    assert memory.confirm_mode == "beginner"


def test_atomic_memory_clamps_speed_and_updates_steps():
    memory = AtomicMemory()

    memory.set_speed(200)
    memory.set_step_mm(3)
    memory.set_step_deg(2)

    assert memory.current_speed == 150.0
    assert memory.current_step_mm == 3.0
    assert memory.current_step_deg == 2.0
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pytest tests/test_atomic_memory.py -q
```

Expected: fails because modules do not exist.

- [ ] **Step 3: Implement models and memory**

Create `robot_modbus_lite/atomic_models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AtomicKind = Literal["chat", "warning", "system", "memory", "template", "query", "unsupported"]
RiskLevel = Literal["low", "medium", "high", "emergency"]


@dataclass(frozen=True)
class AtomicElements:
    raw_text: str
    command_text: str
    family: str = ""
    axis_no: int | None = None
    direction: int | None = None
    step: float | None = None
    target: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    rx: float | None = None
    ry: float | None = None
    rz: float | None = None
    spd_pct: float | None = None
    acc_pct: float | None = None
    dec_pct: float | None = None
    io_no: int | None = None
    io_action: int | None = None
    delay_sec: float | None = None
    move_type: int = 0
    fuzzy_pos: int = 1
    name: str | None = None


@dataclass(frozen=True)
class AtomicResolved:
    kind: AtomicKind
    action_type: str
    target: str | None
    reason: str
    params: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = "low"
    requires_confirmation: bool = True
```

Create `robot_modbus_lite/atomic_memory.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AtomicMemory:
    current_speed: float = 50.0
    current_step_mm: float = 10.0
    current_step_deg: float = 5.0
    current_acc: float = 100.0
    current_dec: float = 100.0
    confirm_mode: str = "beginner"
    positions: dict[str, tuple[float, float, float, float, float, float]] = field(default_factory=dict)
    last_direction: tuple[float, float, float] | None = None
    last_step: float | None = None
    last_command_params: dict[str, Any] | None = None
    position_stack: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)

    def set_speed(self, value: float) -> None:
        self.current_speed = min(150.0, max(5.0, float(value)))

    def speed_up(self, delta: float = 10.0) -> None:
        self.set_speed(self.current_speed + delta)

    def speed_down(self, delta: float = 10.0) -> None:
        self.set_speed(self.current_speed - delta)

    def set_step_mm(self, value: float) -> None:
        self.current_step_mm = max(0.1, float(value))

    def set_step_deg(self, value: float) -> None:
        self.current_step_deg = max(0.1, float(value))

    def set_confirm_mode(self, mode: str) -> None:
        if mode not in {"beginner", "skilled", "expert"}:
            raise ValueError(f"unsupported confirm mode: {mode}")
        self.confirm_mode = mode
```

- [ ] **Step 4: Run memory tests**

Run:

```powershell
pytest tests/test_atomic_memory.py -q
```

Expected: pass.

---

### Task 2: Input Classification and Element Extraction

**Files:**
- Create: `robot_modbus_lite/atomic_parser.py`
- Test: `tests/test_atomic_parser.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_atomic_parser.py`:

```python
from robot_modbus_lite.atomic_parser import AtomicParser


def test_parser_blocks_production_command_without_wake_word():
    parsed = AtomicParser().classify("上升3毫米")

    assert parsed.kind == "chat"
    assert parsed.command_text == "上升3毫米"


def test_parser_warns_single_emergency_word_without_code():
    parsed = AtomicParser().classify("急停")

    assert parsed.kind == "warning"
    assert "标准格式" in parsed.reason


def test_parser_extracts_virtual_axis_full_params():
    parsed = AtomicParser().parse("小正，20%速度上升3毫米加速度50%减速度30%")

    assert parsed.family == "virtual"
    assert parsed.axis_no == 8
    assert parsed.direction == 1
    assert parsed.step == 3.0
    assert parsed.spd_pct == 20.0
    assert parsed.acc_pct == 50.0
    assert parsed.dec_pct == 30.0


def test_parser_extracts_joint_absolute_target():
    parsed = AtomicParser().parse("小正，J1转到45度30%速度")

    assert parsed.family == "joint"
    assert parsed.axis_no == 0
    assert parsed.target == 45.0
    assert parsed.fuzzy_pos == 0
    assert parsed.spd_pct == 30.0
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```powershell
pytest tests/test_atomic_parser.py -q
```

Expected: fails because parser does not exist.

- [ ] **Step 3: Implement parser**

Create `robot_modbus_lite/atomic_parser.py` with:

- `AtomicParser.classify(text)` returning `AtomicResolved(kind="chat"|"warning", ...)` for no wake-word cases.
- `AtomicParser.parse(text)` returning `AtomicElements`.
- Regex extraction for:
  - `J\s*([1-6])`
  - `正转|正向|正走`
  - `反转|反向|反走`
  - `前进|后退|左移|右移|上升|下降`
  - `(\d+\.?\d*)\s*度`
  - `(\d+\.?\d*)\s*毫?米`
  - `(\d+\.?\d*)\s*%?\s*速度|速度.*?(\d+\.?\d*)%`
  - `加速度.*?(\d+\.?\d*)%`
  - `减速度.*?(\d+\.?\d*)%`
  - `(\d+\.?\d*)\s*秒`
  - `IO\s*(\d+)`
  - coordinate triple

- [ ] **Step 4: Run parser tests**

Run:

```powershell
pytest tests/test_atomic_parser.py -q
```

Expected: pass.

---

### Task 3: Resolve Atomic Commands to QueryRecord

**Files:**
- Create: `robot_modbus_lite/atomic_resolver.py`
- Test: `tests/test_atomic_resolver.py`

- [ ] **Step 1: Write failing resolver tests**

Create `tests/test_atomic_resolver.py`:

```python
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver


def resolve(text, memory=None):
    elements = AtomicParser().parse(text)
    return AtomicResolver(memory or AtomicMemory()).resolve(elements)


def test_resolves_virtual_axis_nudge_to_func107():
    result = resolve("小正，上升3毫米20%速度")

    assert result.kind == "template"
    assert result.record.func_num == 107
    assert result.record.params["axis_no"] == 8
    assert result.record.params["pos_val"] == 3.0
    assert result.record.params["spd_pct"] == 20.0
    assert result.record.params["fuzzy_pos"] == 1


def test_resolves_joint_abs_to_func106():
    result = resolve("小正，J1转到45度")

    assert result.record.func_num == 106
    assert result.record.params["axis_no"] == 0
    assert result.record.params["pos_val"] == 45.0
    assert result.record.params["fuzzy_pos"] == 0


def test_resolves_delay_and_io():
    delay = resolve("小正，等待2秒")
    io = resolve("小正，夹爪开")

    assert delay.record.func_num == 109
    assert delay.record.params["delay_sec"] == 2.0
    assert io.record.func_num == 120
    assert io.record.params["io_no"] == 0
    assert io.record.params["io_action"] == 1
```

- [ ] **Step 2: Run resolver tests and verify failure**

Run:

```powershell
pytest tests/test_atomic_resolver.py -q
```

Expected: fails because resolver does not exist.

- [ ] **Step 3: Implement resolver**

Create `robot_modbus_lite/atomic_resolver.py` with:

- `AtomicResolvedTemplate` dataclass containing `record: QueryRecord`.
- `AtomicResolver.resolve(elements)`.
- Default fill rules:
  - `spd_pct = explicit or memory.current_speed`
  - `acc_pct = explicit or memory.current_acc`
  - `dec_pct = explicit or memory.current_dec`
  - virtual translation step default = `memory.current_step_mm`
  - joint/rotation step default = `memory.current_step_deg`
- QueryRecord keys like:
  - `atomic:virt:8:+:3.0`
  - `atomic:joint:0:abs:45.0`
  - `atomic:delay:109:2.0`

- [ ] **Step 4: Run resolver and service conversion tests**

Run:

```powershell
pytest tests/test_atomic_resolver.py tests/test_system_cancel_command.py -q
```

Expected: pass and existing service tests unaffected.

---

### Task 4: Integrate Atomic Layer Into VoiceNlpAdapter

**Files:**
- Modify: `robot_modbus_lite/voice_nlp_adapter.py`
- Test: `tests/test_voice_nlp_atomic.py`

- [ ] **Step 1: Write failing voice adapter tests**

Create `tests/test_voice_nlp_atomic.py`:

```python
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


def test_voice_nlp_adapter_parses_atomic_virtual_command():
    adapter = VoiceNlpAdapter(table={}, flow_names=(), atomic_memory=AtomicMemory())

    plan = adapter.parse("小正，上升3毫米20%速度")

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.semantic_level == 3
    assert plan.requires_precheck is True
    assert plan.requires_confirmation is True
    assert plan.atomic_records[plan.actions[0].target].func_num == 107


def test_voice_nlp_adapter_does_not_execute_single_emergency_word():
    adapter = VoiceNlpAdapter(table={}, flow_names=(), atomic_memory=AtomicMemory())

    plan = adapter.parse("急停")

    assert plan.actions[0].action_type == "unknown"
    assert "标准格式" in plan.reason
```

- [ ] **Step 2: Run voice atomic tests and verify failure**

Run:

```powershell
pytest tests/test_voice_nlp_atomic.py -q
```

Expected: fails because `atomic_memory` and `atomic_records` are absent.

- [ ] **Step 3: Extend VoiceNlpPlan and adapter**

Modify `VoiceNlpPlan`:

```python
atomic_records: dict[str, QueryRecord] = field(default_factory=dict)
```

Because frozen dataclasses cannot use mutable defaults, use `field(default_factory=dict)`.

Modify `VoiceNlpAdapter.__init__`:

```python
atomic_memory: AtomicMemory | None = None
```

Routing order:

1. Empty input.
2. Three-part emergency.
3. Dashboard query.
4. Wake-word check.
5. Atomic parse/resolve.
6. DeepSeek fallback if enabled.
7. Legacy template/flow/system rules.

- [ ] **Step 4: Run NLP tests**

Run:

```powershell
pytest tests/test_voice_nlp_atomic.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_semantic_candidates.py -q
```

Expected: pass.

---

### Task 5: Execute Atomic Records Through Existing Qt Confirmation Path

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Modify: `robot_modbus_lite/command_intent_adapter.py`
- Test: `tests/test_operator_atomic_integration.py`

- [ ] **Step 1: Write failing operator integration tests**

Create `tests/test_operator_atomic_integration.py`:

```python
from types import SimpleNamespace

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.operator_ui_mixin import OperatorUiMixin
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan


class DummyOperator(OperatorUiMixin):
    pass


def test_operator_plan_is_executable_accepts_atomic_template():
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "atomic:virt:8", "rule", "小正，上升", "atomic"),),
        source="rule",
        raw_text="小正，上升",
        reason="atomic",
        atomic_records={
            "atomic:virt:8": QueryRecord(
                query_key="atomic:virt:8",
                func_num=107,
                params={"axis_no": 8, "pos_val": 10, "spd_pct": 50, "acc_pct": 100, "dec_pct": 100, "fuzzy_pos": 1},
            )
        },
    )

    assert DummyOperator._operator_plan_is_executable(plan) is True


def test_operator_can_resolve_atomic_record_for_l1_plan():
    dummy = DummyOperator()
    dummy.table = {}
    plan = VoiceNlpPlan(
        actions=(VoiceNlpAction("atomic_template", "atomic:virt:8", "rule", "小正，上升", "atomic"),),
        source="rule",
        raw_text="小正，上升",
        reason="atomic",
        atomic_records={
            "atomic:virt:8": QueryRecord(
                query_key="atomic:virt:8",
                func_num=107,
                params={"axis_no": 8, "pos_val": 10, "spd_pct": 50, "acc_pct": 100, "dec_pct": 100, "fuzzy_pos": 1},
            )
        },
    )

    record = dummy._operator_record_for_action(plan.actions[0], plan)

    assert record.func_num == 107
```

- [ ] **Step 2: Run operator atomic tests and verify failure**

Run:

```powershell
pytest tests/test_operator_atomic_integration.py -q
```

Expected: fails because operator does not know `atomic_template`.

- [ ] **Step 3: Add atomic record lookup helper**

Modify `operator_ui_mixin.py`:

```python
def _operator_record_for_action(self, action, plan=None):
    target = getattr(action, "target", None)
    if getattr(action, "action_type", "") == "atomic_template" and plan is not None:
        return getattr(plan, "atomic_records", {}).get(target)
    return getattr(self, "table", {}).get(target)
```

Replace direct `self.table.get(first.target)` reads in L1/L2/execute helper paths with this helper.

Treat `atomic_template` as executable and precheck-required wherever `template` is currently accepted.

- [ ] **Step 4: Run operator tests**

Run:

```powershell
pytest tests/test_operator_atomic_integration.py tests/test_operator_precheck_helpers.py -q
```

Expected: pass.

---

### Task 6: Memory Commands, Confirm Modes, and Risk Policy

**Files:**
- Modify: `robot_modbus_lite/atomic_parser.py`
- Modify: `robot_modbus_lite/atomic_resolver.py`
- Modify: `robot_modbus_lite/atomic_memory.py`
- Test: `tests/test_atomic_confirm_policy.py`

- [ ] **Step 1: Write failing risk policy tests**

Create `tests/test_atomic_confirm_policy.py`:

```python
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver


def resolve(text, memory):
    return AtomicResolver(memory).resolve(AtomicParser().parse(text))


def test_speed_memory_commands_update_memory():
    memory = AtomicMemory()
    result = resolve("小正，速度60%", memory)

    assert result.kind == "memory"
    assert result.params["current_speed"] == 60.0


def test_confirm_policy_expert_still_confirms_high_risk():
    memory = AtomicMemory(confirm_mode="expert")
    result = resolve("小正，快速上升50毫米", memory)

    assert result.risk_level == "high"
    assert result.requires_confirmation is True


def test_confirm_policy_skilled_skips_low_risk():
    memory = AtomicMemory(confirm_mode="skilled")
    result = resolve("小正，上升")

    assert result.risk_level == "low"
    assert result.requires_confirmation is False
```

- [ ] **Step 2: Implement memory actions and risk policy**

Rules:

- `速度XX%` -> memory update.
- `加速` -> speed +10, max 150.
- `减速` -> speed -10, min 5.
- `慢速` -> speed 5.
- `正常速度` -> speed 50.
- `全速` -> speed 100.
- `步长XX毫米` -> `current_step_mm`.
- `步长XX度` -> `current_step_deg`.
- `切换新手模式/熟练模式/专家模式` -> `confirm_mode`.
- Risk:
  - low: default step and speed <= 50.
  - medium: step > default*2 or speed > 50.
  - high: speed > 80 or parser/resolver marks limit/unknown target risk.
  - emergency: system emergency.

- [ ] **Step 3: Run policy tests**

Run:

```powershell
pytest tests/test_atomic_confirm_policy.py -q
```

Expected: pass.

---

### Task 7: Position Library and Motion History MVP

**Files:**
- Modify: `robot_modbus_lite/atomic_memory.py`
- Modify: `robot_modbus_lite/atomic_parser.py`
- Modify: `robot_modbus_lite/atomic_resolver.py`
- Test: `tests/test_atomic_position_history.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_atomic_position_history.py`:

```python
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver


def resolve(text, memory):
    return AtomicResolver(memory).resolve(AtomicParser().parse(text))


def test_move_to_named_position_uses_position_library():
    memory = AtomicMemory()
    memory.positions["位置A"] = (350.0, 200.0, 450.0, 0.0, -90.0, 0.0)

    result = resolve("小正，移动到位置A", memory)

    assert result.record.func_num == 108
    assert result.record.params["target_x"] == 350.0
    assert result.record.params["target_y"] == 200.0
    assert result.record.params["target_z"] == 450.0


def test_repeat_last_without_history_returns_warning():
    memory = AtomicMemory()

    result = resolve("小正，再走一次", memory)

    assert result.kind == "warning"
    assert "没有上次" in result.reason
```

- [ ] **Step 2: Implement MVP**

Implement only:

- `移动到位置X` using `AtomicMemory.positions`.
- `位置X的坐标是多少` as `query`.
- `再走一次` if `last_command_params` exists.
- `返回` if `position_stack` contains a pose.
- Leave “保存当前位置为位置A” for operator integration because it needs live DPOS.

- [ ] **Step 3: Run position tests**

Run:

```powershell
pytest tests/test_atomic_position_history.py -q
```

Expected: pass.

---

### Task 8: Documentation and Final Verification

**Files:**
- Modify: `docs/P0用户页安全交互闭环实施计划.md`
- Modify: `docs/用户页面实现现状与缺口总结.md`
- Optional Create: `docs/二次原子函数封装表_V2.0_实现对照.md`

- [ ] **Step 1: Create implementation coverage doc**

Create `docs/二次原子函数封装表_V2.0_实现对照.md` with this structure:

```markdown
# 二次原子函数封装表 V2.0 实现对照

## 已实现
- 唤醒词门禁。
- 三段式应急编码。
- J/V/C/SP/IO/D 基础解析。
- 动态 QueryRecord 生成。
- 记忆参数。
- 风险分级和确认模式。

## 暂缓
- 连续插补 Func11：等待焊接路径格式。
- 位置保存：需要 Qt 当前 DPOS 接入。
- 真机安全验证：等待现场。

## 验收
- `pytest -q`
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
pytest tests/test_atomic_memory.py tests/test_atomic_parser.py tests/test_atomic_resolver.py tests/test_voice_nlp_atomic.py tests/test_operator_atomic_integration.py tests/test_atomic_confirm_policy.py tests/test_atomic_position_history.py -q
```

Expected: pass.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run compile check**

Run:

```powershell
python -m compileall robot_modbus_lite tests
```

Expected: no syntax errors.

---

## Completion Criteria

The document scope is complete for offline Qt logic when:

- Commands in E/J/V/C/SP/IO/D/Q categories parse locally without requiring pre-created templates.
- The parser rejects no-wake production commands and single emergency-word mentions.
- Dynamic atomic commands become `QueryRecord` objects compatible with existing `RobotModbusService.build_six_command_from_record()`.
- Existing L1/L2/confirmation/execution path can consume atomic records.
- Memory parameters and confirm modes affect generated params and confirmation policy.
- Full tests pass.

Deferred by design:

- Func11 continuous interpolation path library.
- Live “保存当前位置为位置A” until current DPOS persistence UX is finalized.
- True machine validation, VAD, TTS deployment, and hardware-independent emergency channel.

