# Restricted Agent Phase 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Qt-facing bridge that converts backend `RestrictedAgentResult` objects into existing `VoiceNlpPlan` containers without creating executable `QueryRecord` objects before confirmation.

**Architecture:** `AgentPlanAdapter` keeps existing semantic policy mapping and adds `to_voice_plan()`. Waiting Agent drafts use action type `agent_draft` and store metadata in `VoiceNlpPlan.flow_draft`; this avoids old `flow_draft` handling and avoids old atomic execution paths.

**Tech Stack:** Python dataclasses, pytest, existing `VoiceNlpPlan`.

---

### Task 1: Agent Result to VoiceNlpPlan

**Files:**
- Modify: `robot_modbus_lite/agent/plan_adapter.py`
- Test: `tests/test_agent_plan_adapter.py`

- [ ] Write failing tests for waiting confirmation, clarification, blocked, precheck_failed, and bypass conversion.
- [ ] Run `pytest tests/test_agent_plan_adapter.py -q` and verify it fails.
- [ ] Implement conversion without populating `atomic_records`.
- [ ] Run the tests and verify they pass.

---

### Task 2: Regression

- [ ] Run all Agent tests:

```powershell
pytest tests/test_agent_axis_status.py tests/test_agent_alarm_explanation.py tests/test_agent_drafts.py tests/test_agent_plan_adapter.py tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py tests/test_agent_confirmation.py tests/test_agent_safety_review.py tests/test_restricted_agent_service.py -q
```

- [ ] Run related existing tests:

```powershell
pytest tests/test_semantic_response_policy.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_atomic.py -q
```

- [ ] Compile touched package:

```powershell
python -m compileall -q robot_modbus_lite tests/test_agent_plan_adapter.py
```

## Execution Notes

- Do not commit.
- Do not execute Modbus writes.
- Do not add Web integration.
- Do not create `QueryRecord` for waiting Agent drafts.
