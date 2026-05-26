# 对话式执行方案助手 Phase 1 Flow Draft Editing Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Qt 用户页的 `_operator_pending_flow_draft` 支持自然语言步骤级编辑：修改步骤速度、修改延时、删除步骤、整体速度、撤销。

**Architecture:** 只接入流程草案 Path B，不改原子动作确认 Path A。GUI 层识别 pending flow draft 后，把草案转换为 `ExecutionPlan`，交给 `ExecutionPlanService` / `DraftEditor` 编辑，再转换回 flow draft dict，刷新对话预览。

---

## Task 1: Flow Draft Edit Command Tests

- [ ] Add tests in `tests/test_operator_precheck_helpers.py` for:
  - “第 2 步速度改成 30%”
  - “删除第 3 步”
  - “整体速度改成 20%”
  - “撤销上一次修改”
- [ ] Verify tests fail before implementation.

## Task 2: Service Editing Helpers

- [ ] Add `ExecutionPlanService.set_pending_flow_draft(draft)`.
- [ ] Add `ExecutionPlanService.pending_flow_draft()`.
- [ ] Add `ExecutionPlanService.edit_step_params(step_id, params)`.
- [ ] Add `ExecutionPlanService.delete_step(step_id)`.
- [ ] Add `ExecutionPlanService.undo()`.

## Task 3: Operator Flow Draft Editing

- [ ] Add `_operator_execution_plan_service()` lazy helper.
- [ ] Add `_operator_handle_pending_flow_draft_edit(text)` before save/query handling.
- [ ] Parse minimum regex commands locally.
- [ ] On edit success, update `_operator_pending_flow_draft`.
- [ ] Add assistant chat message and log entry.
- [ ] Do not save or execute.

## Task 4: Regression

- [ ] Run `pytest tests/test_operator_precheck_helpers.py -q`.
- [ ] Run Phase 0 tests.
- [ ] Run `pytest tests/test_complex_flow_draft.py -q`.
