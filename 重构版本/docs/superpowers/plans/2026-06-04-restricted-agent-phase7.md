# Restricted Agent Phase 7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the restricted Agent into Qt text/voice NLP entry points with a conservative feature gate.

**Architecture:** Operator UI attempts the restricted Agent only for supported text classes: Cartesian coordinate motion, alarm query, and explicit system aliases. Other text continues through the existing `VoiceNlpAdapter`.

**Tech Stack:** Python, pytest, existing Qt mixins, existing restricted Agent modules.

---

### Task 1: Service Construction

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [ ] Add tests for conservative Agent gate and injected Agent service conversion.
- [ ] Implement service construction providers for controller snapshot, runtime snapshot, signatures, and clock.

---

### Task 2: NLP Entry Hook

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [ ] Add tests for `_parse_nlp_text()` and `_execute_nlp_text()` using Agent when supported.
- [ ] Implement entry hook before falling back to `super()`.

---

### Task 3: Regression

- [ ] Run Agent and operator tests.
- [ ] Run voice NLP regression.
- [ ] Compile package.

## Execution Notes

- Do not commit.
- Do not add Web integration.
- Do not route unsupported text through Agent.
