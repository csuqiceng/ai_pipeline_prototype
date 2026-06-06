# Restricted Agent Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-only `SafetyReviewAgent` that adapts existing L1 and L2 precheck services to Agent drafts.

**Architecture:** The Agent converts a `CommandDraft` into the plan shape expected by `SafetyPrecheckService.run_l1()`, then optionally calls `MotionPlanService.plan()` for Func108. It returns a normalized `precheck_result` dictionary for confirmation and blocking decisions.

**Tech Stack:** Python dataclasses, pytest, existing `SafetyPrecheckService`, existing `MotionPlanService`.

---

### Task 1: L1 Adapter

**Files:**
- Create: `robot_modbus_lite/agent/safety_review.py`
- Test: `tests/test_agent_safety_review.py`

- [ ] Write failing tests for passing and failing L1 review from a Func108 `CommandDraft`.
- [ ] Run `pytest tests/test_agent_safety_review.py -q` and verify it fails.
- [ ] Implement draft-to-L1-plan conversion and normalized result.
- [ ] Run the test and verify it passes.

---

### Task 2: Optional L2 Adapter

**Files:**
- Modify: `robot_modbus_lite/agent/safety_review.py`
- Test: `tests/test_agent_safety_review.py`

- [ ] Write failing tests for L2 pass, fail, and unavailable behavior.
- [ ] Run `pytest tests/test_agent_safety_review.py -q` and verify it fails.
- [ ] Implement optional motion-plan adapter for Func108.
- [ ] Run the test and verify it passes.

---

### Task 3: Regression

- [ ] Run all Agent tests:

```powershell
pytest tests/test_agent_axis_status.py tests/test_agent_alarm_explanation.py tests/test_agent_drafts.py tests/test_agent_plan_adapter.py tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py tests/test_agent_confirmation.py tests/test_agent_safety_review.py -q
```

- [ ] Run related existing tests:

```powershell
pytest tests/test_safety_precheck.py tests/test_motion_plan.py tests/test_semantic_response_policy.py -q
```

- [ ] Compile touched package:

```powershell
python -m compileall -q robot_modbus_lite tests/test_agent_safety_review.py
```

## Execution Notes

- Do not commit.
- Do not execute Modbus writes.
- Do not add Web or UI integration.
- L2 `unavailable` must not be treated as a hard failure in this phase unless strict mode is explicitly enabled.
