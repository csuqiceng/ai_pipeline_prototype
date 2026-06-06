# Restricted Agent Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic command understanding and parameter completion for Func108 drafts without wiring to live execution.

**Architecture:** `CommandUnderstandingAgent` parses text into intent, func_id, extracted params, confidence, and model-need flags. `ParameterCompletionAgent` turns a move intent into a `CommandDraft` by combining explicit params with controller snapshot values; it refuses when the controller is moving or live values are unavailable.

**Tech Stack:** Python dataclasses, pytest, existing `AtomicParser`, existing `CommandDraft`.

---

### Task 1: CommandUnderstandingAgent

**Files:**
- Create: `robot_modbus_lite/agent/command_understanding.py`
- Test: `tests/test_agent_command_understanding.py`

- [ ] **Step 1: Write failing tests**

Cover full Cartesian move, partial Cartesian move, emergency fast-path intent, alarm query, and unclear control text requiring model fallback.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_agent_command_understanding.py -q`

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement minimal deterministic parser**

Use `AtomicParser.parse()` for Cartesian extraction. Do not call DeepSeek. Include `func_id` in output.

- [ ] **Step 4: Verify**

Run: `pytest tests/test_agent_command_understanding.py -q`

Expected: PASS.

---

### Task 2: ParameterCompletionAgent

**Files:**
- Create: `robot_modbus_lite/agent/parameter_completion.py`
- Test: `tests/test_agent_parameter_completion.py`

- [ ] **Step 1: Write failing tests**

Cover explicit + inherited pose values, safe speed inheritance, moving-state blocking, and live-read failure blocking.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_agent_parameter_completion.py -q`

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement minimal completion**

Only support Func108 in this phase. Do not use `AtomicResolver`, because it fills missing coordinates with zero.

- [ ] **Step 4: Verify**

Run: `pytest tests/test_agent_parameter_completion.py -q`

Expected: PASS.

---

### Task 3: Regression

- [ ] Run new Agent tests:

```powershell
pytest tests/test_agent_axis_status.py tests/test_agent_alarm_explanation.py tests/test_agent_drafts.py tests/test_agent_plan_adapter.py tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py -q
```

- [ ] Run related existing tests:

```powershell
pytest tests/test_semantic_response_policy.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_atomic.py -q
```

- [ ] Compile touched package and tests:

```powershell
python -m compileall -q robot_modbus_lite tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py
```

## Execution Notes

- Do not commit.
- Do not wire Agent drafts into Modbus writes in Phase 2.
- Do not let LLM output set numeric motion parameters.
