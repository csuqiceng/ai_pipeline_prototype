# Restricted Agent Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-only confirmation lifecycle for Agent `CommandDraft` objects.

**Architecture:** `ConfirmationAgent` renders deterministic confirmation text, stores pending draft sessions, and converts a draft to `QueryRecord` only after a valid confirmation. Draft IDs are one-use; expired, rejected, precheck-failed, or changed-status sessions cannot be confirmed.

**Tech Stack:** Python dataclasses, pytest, existing `CommandDraft`, existing `draft_to_query_record`.

---

### Task 1: Confirmation Text

**Files:**
- Create: `robot_modbus_lite/agent/confirmation.py`
- Test: `tests/test_agent_confirmation.py`

- [ ] Write failing tests for Func108 confirmation text with parameter source labels and precheck result.
- [ ] Run `pytest tests/test_agent_confirmation.py -q` and verify it fails.
- [ ] Implement deterministic text rendering.
- [ ] Run the test and verify it passes.

---

### Task 2: Draft Lifecycle

**Files:**
- Modify: `robot_modbus_lite/agent/confirmation.py`
- Test: `tests/test_agent_confirmation.py`

- [ ] Write failing tests for begin, confirm, reject, duplicate confirm, timeout, status signature change, and precheck_failed.
- [ ] Run `pytest tests/test_agent_confirmation.py -q` and verify failure.
- [ ] Implement in-memory lifecycle state.
- [ ] Run the test and verify it passes.

---

### Task 3: Regression

- [ ] Run all Agent tests:

```powershell
pytest tests/test_agent_axis_status.py tests/test_agent_alarm_explanation.py tests/test_agent_drafts.py tests/test_agent_plan_adapter.py tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py tests/test_agent_confirmation.py -q
```

- [ ] Run related existing tests:

```powershell
pytest tests/test_semantic_response_policy.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_atomic.py -q
```

- [ ] Compile touched package:

```powershell
python -m compileall -q robot_modbus_lite tests/test_agent_confirmation.py
```

## Execution Notes

- Do not commit.
- Do not add Web endpoints or UI changes.
- Do not execute Modbus writes from this module.
