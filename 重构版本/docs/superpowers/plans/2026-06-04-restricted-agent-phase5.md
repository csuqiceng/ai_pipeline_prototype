# Restricted Agent Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-only `RestrictedAgentService` that orchestrates understanding, completion, safety review, confirmation, and confirmed `QueryRecord` conversion.

**Architecture:** The service owns the deterministic Agent modules and exposes `parse()` plus `confirm()` / `reject()`. `parse()` never executes; it returns structured outcomes for bypass, clarification, blocked precheck, or waiting confirmation.

**Tech Stack:** Python dataclasses, pytest, existing Agent modules.

---

### Task 1: Parse Orchestration

**Files:**
- Create: `robot_modbus_lite/agent/service.py`
- Test: `tests/test_restricted_agent_service.py`

- [ ] Write failing tests for motion parse generating waiting confirmation, alarm query bypass, emergency bypass, unclear clarification, completion blocking, and precheck blocking.
- [ ] Run `pytest tests/test_restricted_agent_service.py -q` and verify it fails.
- [ ] Implement parse orchestration and structured result dataclasses.
- [ ] Run the tests and verify they pass.

---

### Task 2: Confirm and Reject Orchestration

**Files:**
- Modify: `robot_modbus_lite/agent/service.py`
- Test: `tests/test_restricted_agent_service.py`

- [ ] Write failing tests for confirm returning `QueryRecord`, duplicate confirm rejection, and reject blocking later confirm.
- [ ] Run `pytest tests/test_restricted_agent_service.py -q` and verify it fails.
- [ ] Implement confirm and reject wrappers around `ConfirmationAgent`.
- [ ] Run the tests and verify they pass.

---

### Task 3: Regression

- [ ] Run all Agent tests:

```powershell
pytest tests/test_agent_axis_status.py tests/test_agent_alarm_explanation.py tests/test_agent_drafts.py tests/test_agent_plan_adapter.py tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py tests/test_agent_confirmation.py tests/test_agent_safety_review.py tests/test_restricted_agent_service.py -q
```

- [ ] Run related existing tests:

```powershell
pytest tests/test_safety_precheck.py tests/test_motion_plan.py tests/test_semantic_response_policy.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_atomic.py -q
```

- [ ] Compile touched package:

```powershell
python -m compileall -q robot_modbus_lite tests/test_restricted_agent_service.py
```

## Execution Notes

- Do not commit.
- Do not execute Modbus writes.
- Do not add Web or UI integration.
