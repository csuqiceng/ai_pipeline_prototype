# Restricted Agent Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first safe, testable slice of the restricted upper-computer Agent without changing the existing MODBUS execution path.

**Architecture:** Add small deterministic modules under `robot_modbus_lite/agent/`. Phase 1 implements protocol-safe status/alarm explanation, draft data models, and integration boundaries only. Execution still goes through `QueryRecord -> RobotModbusService -> SixAxisCommand -> six_axis_command_mixin.py`.

**Tech Stack:** Python dataclasses, existing `robot_modbus_lite` models/services, pytest.

---

## Scope

Phase 1 includes:

- P0 protocol confirmation checklist as an engineering artifact.
- `AxisStatusBitDecomposer` for AXISSTATUS bit-level structure.
- `AlarmExplanationAgent` for `LONG(34)`, `LONG(38)`, `AXISSTATUS`, current func, and safety-limit summaries.
- `CommandDraft` and safe `QueryRecord` conversion with deep-copy protection.
- `AgentPlanAdapter` semantic-policy mapping shell.

Phase 1 explicitly excludes:

- Real Func112 / continuous path generation.
- Changing `fuzzy_pos` semantics.
- Replacing `AtomicParser` / `AtomicResolver`.
- Letting Agent output execute before confirmation.
- Any git commit. The user requested not to submit to git.

## P0 Protocol Confirmation Checklist

Before enabling movement execution from Agent-generated drafts, confirm these with the controller/firmware owner:

| Item | Default for Phase 1 | Confirmation Needed |
| --- | --- | --- |
| Natural-language absolute movement function | Generate Func108 only | Whether Func8 needs upper-computer writes |
| Continuous path function | Do not generate | Func11 / Func111 / Func112 final ID |
| Cartesian inheritance source | Read existing DPOS/feedback path only in dry run | IEEE(1500~1510), 1512~1522, or 1612~1622 |
| Any-axis moving state | Use `SixAxisStatus.can_send` first | Whether BIT(252) must be read |
| `IEEE(22)` / `fuzzy_pos` | Preserve current field meaning | Whether it is position increment |
| Pose angle limits | Read only after parser extension | IEEE(1732~1738) stable semantics |

## File Structure

- Create `robot_modbus_lite/agent/__init__.py`: package exports.
- Create `robot_modbus_lite/agent/axis_status.py`: AXISSTATUS bit decomposition.
- Create `robot_modbus_lite/agent/alarm_explanation.py`: deterministic alarm/status explanation.
- Create `robot_modbus_lite/agent/drafts.py`: `CommandDraft`, `DraftState`, conversion to `QueryRecord`.
- Create `robot_modbus_lite/agent/plan_adapter.py`: semantic policy mapping and no-execute plan shell.
- Modify `robot_modbus_lite/service.py`: add explicit pose angle keys in `parse_six_safety_limits()`.
- Create `tests/test_agent_axis_status.py`.
- Create `tests/test_agent_alarm_explanation.py`.
- Create `tests/test_agent_drafts.py`.
- Create `tests/test_agent_plan_adapter.py`.

---

### Task 1: AXISSTATUS Bit Decomposer

**Files:**
- Create: `robot_modbus_lite/agent/__init__.py`
- Create: `robot_modbus_lite/agent/axis_status.py`
- Test: `tests/test_agent_axis_status.py`

- [ ] **Step 1: Write the failing test**

```python
from robot_modbus_lite.agent.axis_status import AxisStatusBitDecomposer


def test_axis_status_decomposer_reports_known_bits_by_axis():
    decomposer = AxisStatusBitDecomposer()

    detail = decomposer.decompose([0, 0b10, 0, 0x100, 0, 0x4000])

    assert detail["axes"][1]["active_bits"] == [1]
    assert detail["axes"][1]["messages"][0]["code"] == "following_error_warning"
    assert detail["axes"][3]["active_bits"] == [8]
    assert detail["axes"][3]["messages"][0]["code"] == "following_error_error"
    assert detail["axes"][5]["active_bits"] == [14]
    assert detail["has_error"] is True


def test_axis_status_decomposer_formats_hard_limit_direction():
    decomposer = AxisStatusBitDecomposer()

    detail = decomposer.decompose([1 << 4, 1 << 5])

    assert "J1轴碰到正向硬限位" in detail["axes"][0]["messages"][0]["message"]
    assert "向负方向" in detail["axes"][0]["messages"][0]["suggestion"]
    assert "J2轴碰到负向硬限位" in detail["axes"][1]["messages"][0]["message"]
    assert "向正方向" in detail["axes"][1]["messages"][0]["suggestion"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_axis_status.py -q`

Expected: FAIL because `robot_modbus_lite.agent.axis_status` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# robot_modbus_lite/agent/axis_status.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


AXIS_STATUS_BITS: dict[int, tuple[str, str, str]] = {
    1: ("following_error_warning", "J{axis}轴随动误差偏大", "请注意J{axis}轴负载变化。"),
    2: ("ethercat_lost", "J{axis}轴（驱动器{driver}号）通讯丢失", "请检查{driver}号驱动器网线连接和供电。"),
    3: ("drive_alarm", "J{axis}轴（驱动器{driver}号）驱动器故障", "建议断电重启{driver}号驱动器，并检查J{axis}轴电机接线。"),
    4: ("positive_hard_limit", "J{axis}轴碰到正向硬限位", "请点动J{axis}轴向负方向运动，移出限位区。"),
    5: ("negative_hard_limit", "J{axis}轴碰到负向硬限位", "请点动J{axis}轴向正方向运动，移出限位区。"),
    8: ("following_error_error", "J{axis}轴（驱动器{driver}号）随动误差超限出错", "检查J{axis}轴负载、降低运动加速度，并检查{driver}号驱动器编码器接线。"),
    9: ("positive_soft_limit", "J{axis}轴超过正向软限位", "请调整目标位置，使J{axis}轴不超过正向软限位。"),
    10: ("negative_soft_limit", "J{axis}轴超过负向软限位", "请调整目标位置，使J{axis}轴不低于负向软限位。"),
    12: ("max_speed_pulse", "J{axis}轴脉冲频率超MAX_SPEED", "请降低运动速度。"),
    14: ("command_coordinate_error", "J{axis}轴坐标错误", "请检查FRAME配置。"),
    18: ("power_error", "J{axis}轴（驱动器{driver}号）电源异常", "请工程师检查供电系统。"),
    20: ("axis_speed_protect", "J{axis}轴速度超限保护", "请降低速度。"),
}


@dataclass(frozen=True)
class AxisStatusBitDecomposer:
    max_axes: int = 6

    def decompose(self, values: list[int] | tuple[int, ...]) -> dict[str, Any]:
        axes = []
        has_error = False
        for index, raw_value in enumerate(list(values)[: self.max_axes]):
            axis = index + 1
            raw = int(raw_value)
            messages = []
            active_bits = []
            for bit, (code, text, suggestion) in AXIS_STATUS_BITS.items():
                if raw & (1 << bit):
                    active_bits.append(bit)
                    messages.append({
                        "bit": bit,
                        "code": code,
                        "message": text.format(axis=axis, driver=axis),
                        "suggestion": suggestion.format(axis=axis, driver=axis),
                    })
            has_error = has_error or bool(messages)
            axes.append({"axis": axis, "raw": raw, "active_bits": active_bits, "messages": messages})
        return {"axes": axes, "has_error": has_error}
```

```python
# robot_modbus_lite/agent/__init__.py
"""Restricted upper-computer Agent helpers."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_axis_status.py -q`

Expected: PASS.

---

### Task 2: Safety Limit Pose Angle Parsing

**Files:**
- Modify: `robot_modbus_lite/service.py`
- Test: `tests/test_agent_alarm_explanation.py`

- [ ] **Step 1: Write the failing test**

```python
from robot_modbus_lite.service import RobotModbusService


def test_parse_six_safety_limits_exposes_pose_angles():
    values = [0.0] * 22
    values[16] = 11.0
    values[17] = 22.0
    values[18] = 33.0
    values[19] = 44.0

    parsed = RobotModbusService().parse_six_safety_limits(values)

    assert parsed["pose_upper_angle"] == 11.0
    assert parsed["pose_lower_angle"] == 22.0
    assert parsed["pose_cw_angle"] == 33.0
    assert parsed["pose_ccw_angle"] == 44.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_alarm_explanation.py::test_parse_six_safety_limits_exposes_pose_angles -q`

Expected: FAIL with missing `pose_upper_angle`.

- [ ] **Step 3: Write minimal implementation**

Modify `RobotModbusService.parse_six_safety_limits()` return dict:

```python
"pose_upper_angle": float(padded[16]),
"pose_lower_angle": float(padded[17]),
"pose_cw_angle": float(padded[18]),
"pose_ccw_angle": float(padded[19]),
```

Keep `reserved` for backward compatibility, but remove `padded[16:20]` from `reserved` if tests show callers expect only unknown values.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_alarm_explanation.py::test_parse_six_safety_limits_exposes_pose_angles -q`

Expected: PASS.

---

### Task 3: AlarmExplanationAgent Core

**Files:**
- Create: `robot_modbus_lite/agent/alarm_explanation.py`
- Test: `tests/test_agent_alarm_explanation.py`

- [ ] **Step 1: Write the failing test**

```python
from robot_modbus_lite.agent.alarm_explanation import AlarmExplanationAgent


def test_alarm_explanation_reports_estop_before_normal_status():
    result = AlarmExplanationAgent().explain(long34=1 << 25, long36=0, long38=0, axis_status=[], current_func=108)

    assert result["severity"] == "critical"
    assert "急停" in result["summary"]
    assert result["can_move"] is False
    assert result["func_name_zh"] == "直线插补"


def test_alarm_explanation_uses_axis_detail_for_drive_alarm():
    result = AlarmExplanationAgent().explain(
        long34=1 << 28,
        long36=0,
        long38=1 << 7,
        axis_status=[0, 1 << 3, 0, 0, 0, 0],
        current_func=108,
    )

    assert result["severity"] == "critical"
    assert result["affected_axes"] == [2]
    assert "J2" in result["summary"]
    assert result["can_move"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_alarm_explanation.py -q`

Expected: FAIL because `AlarmExplanationAgent` does not exist.

- [ ] **Step 3: Write minimal implementation**

Use existing `SixAxisStatus.from_value()` and `FUNC_NAME_MAP` from `robot_modbus_lite.models`. Use `AxisStatusBitDecomposer` for axis details. Priority order:

1. `SixAxisStatus.is_estop`
2. `SixAxisStatus.has_alarm`
3. `long38` bit7 / bit6 axis details
4. `SixAxisStatus.is_paused`
5. ready / executing / ok

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_alarm_explanation.py -q`

Expected: PASS.

---

### Task 4: CommandDraft Safe Conversion

**Files:**
- Create: `robot_modbus_lite/agent/drafts.py`
- Test: `tests/test_agent_drafts.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from robot_modbus_lite.agent.drafts import CommandDraft, draft_to_query_record


def test_draft_to_query_record_deepcopies_params():
    params = {
        "target_x": 1000.0,
        "target_y": 0.0,
        "target_z": 0.0,
        "target_rx": 0.0,
        "target_ry": 0.0,
        "target_rz": 0.0,
        "spd_pct": 50.0,
        "acc_pct": 50.0,
        "dec_pct": 50.0,
        "stop_cmd": 0,
        "fuzzy_pos": 0,
        "fuzzy_spd": 0,
        "fuzzy_acc": 0,
        "fuzzy_dec": 0,
        "move_type": 0,
    }
    draft = CommandDraft(
        draft_id="abc123",
        func_id=108,
        intent="move_linear",
        params=params,
        param_sources={key: "specified" for key in params},
        raw_text="走到 X1000",
        confidence=1.0,
    )

    record = draft_to_query_record(draft)
    record.params["target_x"] = 2000.0

    assert draft.params["target_x"] == 1000.0
    assert record.query_key == "agent:abc123"


def test_draft_to_query_record_rejects_missing_required_keys():
    draft = CommandDraft(
        draft_id="missing",
        func_id=108,
        intent="move_linear",
        params={"target_x": 100.0},
        param_sources={"target_x": "specified"},
        raw_text="走到 X100",
        confidence=1.0,
    )

    with pytest.raises(ValueError, match="missing required params"):
        draft_to_query_record(draft)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_drafts.py -q`

Expected: FAIL because draft module does not exist.

- [ ] **Step 3: Write minimal implementation**

Create frozen `CommandDraft`, required-key validation for Func108/104/109/110/120, and `draft_to_query_record()` that uses `copy.deepcopy()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_drafts.py -q`

Expected: PASS.

---

### Task 5: AgentPlanAdapter Policy Mapping Shell

**Files:**
- Create: `robot_modbus_lite/agent/plan_adapter.py`
- Test: `tests/test_agent_plan_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
from robot_modbus_lite.agent.plan_adapter import AgentPlanAdapter


def test_agent_policy_mapping_requires_confirmation_for_motion():
    policy = AgentPlanAdapter().policy_for_agent_result("move_linear")

    assert policy.semantic_level == 3
    assert policy.requires_precheck is True
    assert policy.requires_confirmation is True
    assert policy.emergency_fast_path is False


def test_agent_policy_mapping_fast_paths_estop():
    policy = AgentPlanAdapter().policy_for_agent_result("sys_estop")

    assert policy.semantic_level == 5
    assert policy.requires_precheck is False
    assert policy.requires_confirmation is False
    assert policy.emergency_fast_path is True


def test_agent_policy_mapping_query_is_read_only():
    policy = AgentPlanAdapter().policy_for_agent_result("alarm_query")

    assert policy.semantic_level == 2
    assert policy.requires_precheck is False
    assert policy.requires_confirmation is False
    assert policy.emergency_fast_path is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_plan_adapter.py -q`

Expected: FAIL because `AgentPlanAdapter` does not exist.

- [ ] **Step 3: Write minimal implementation**

Map Agent intents to the existing semantic policy meanings without constructing `VoiceNlpPlan` yet:

```python
INTENT_LEVELS = {
    "alarm_query": 2,
    "status_query": 2,
    "move_linear": 3,
    "sys_estop": 5,
    "sys_pause": 4,
    "sys_resume": 4,
    "alarm_reset": 4,
}
```

Use `policy_for_level()` from `robot_modbus_lite.semantic_response_policy`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_plan_adapter.py -q`

Expected: PASS.

---

### Task 6: Focused Regression

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
pytest tests/test_agent_axis_status.py tests/test_agent_alarm_explanation.py tests/test_agent_drafts.py tests/test_agent_plan_adapter.py -q
```

Expected: PASS.

- [ ] **Step 2: Run related existing regression tests**

Run:

```powershell
pytest tests/test_semantic_response_policy.py tests/test_safety_precheck.py tests/test_motion_plan.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_atomic.py -q
```

Expected: PASS.

- [ ] **Step 3: Compile package**

Run:

```powershell
python -m compileall -q robot_modbus_lite tests
```

Expected: exit code 0.

## Execution Notes

- Do not commit in this workspace unless the user explicitly asks.
- Keep all new Agent code deterministic; no model calls in Phase 1.
- Do not wire Agent output into live execution in Phase 1.
- If P0 protocol answers contradict this plan, update the plan before enabling movement execution.
