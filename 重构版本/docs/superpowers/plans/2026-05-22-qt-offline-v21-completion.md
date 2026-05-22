# Qt Offline V2.1 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining V2.1 requirements that can be completed in the Qt GUI and offline logic scope, excluding Web API, HTML pages, true machine commissioning, VAD hardware behavior, deployed TTS validation, and hardware-independent emergency channels.

**Architecture:** Keep the existing module split. Strengthen the local NLP pipeline, dashboard query layer, operator scene state, and GUI verification without changing the low-level controller contract. Each change must be covered by focused pytest tests and must preserve the current `306 passed` baseline.

**Tech Stack:** Python, PyQt, pytest, optional jieba, existing `robot_modbus_lite` modules.

---

## Scope

This plan covers only work that can be completed locally:

- Qt GUI behavior and layout checks.
- Offline NLP parsing and classification.
- Local dashboard query answer quality.
- Operator scene state hardening.
- Unit tests and docs updates.

This plan intentionally does not include:

- Web API or HTML pages.
- Real `FRAME_TRANS2` controller commissioning.
- True 50ms controller bus pressure testing.
- VAD-driven microphone event integration.
- Deployed TTS acceptance testing.
- Hardware/thread-level independent emergency channel.

---

## File Structure

- Modify `robot_modbus_lite/voice_nlp_adapter.py`: promote optional jieba tokens from metadata into candidate scoring and semantic classification.
- Modify or create `robot_modbus_lite/semantic_candidates.py`: keep candidate scoring small and testable if the adapter grows too large.
- Modify `robot_modbus_lite/dashboard_query.py`: expand L2/L3 query responses for safety boundary, motion planning, process preview, and error recovery.
- Modify `robot_modbus_lite/dashboard_query_specs.py`: add aliases and query specs for remaining operator questions.
- Modify `robot_modbus_lite/operator_ui_mixin.py`: reduce implicit scene inference, improve scene transition edge cases, and add GUI diagnostic helpers if needed.
- Modify `docs/P0用户页安全交互闭环实施计划.md`: mark completed items and record remaining deferred true-machine items.
- Modify `docs/用户页面实现现状与缺口总结.md`: align completion percentages with the current scope.
- Add or modify tests under `tests/`: keep each feature covered by focused unit tests.

---

### Task 1: Make Jieba Tokens Drive Rule Candidate Scoring

**Files:**
- Modify: `robot_modbus_lite/voice_nlp_adapter.py`
- Optional Create: `robot_modbus_lite/semantic_candidates.py`
- Test: `tests/test_voice_nlp_semantic_candidates.py`

- [ ] **Step 1: Write failing tests for token-driven candidate scoring**

Create `tests/test_voice_nlp_semantic_candidates.py` with tests that verify:

```python
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


def test_jieba_tokens_promote_dashboard_query_intent():
    adapter = VoiceNlpAdapter(tokenizer=lambda text: ["查询", "安全", "边界"])

    plan = adapter.parse("小正 查询安全边界")

    assert plan.action_type == "query"
    assert plan.semantic_level == "L2"
    assert plan.tokens == ("查询", "安全", "边界")
    assert plan.nlp_engine == "jieba_rule"


def test_tokens_do_not_override_emergency_fast_path():
    adapter = VoiceNlpAdapter(tokenizer=lambda text: ["急停", "ABC123", "急停"])

    plan = adapter.parse("小正 急停 ABC123 急停")

    assert plan.semantic_level == "L5"
    assert plan.action_type == "system"
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
pytest tests/test_voice_nlp_semantic_candidates.py -q
```

Expected: at least one test fails because token-driven scoring is not yet used for classification.

- [ ] **Step 3: Implement minimal candidate scoring**

Keep emergency/system/template direct matches higher priority. Use tokens only to improve query/template/flow recognition when keyword text is ambiguous.

Suggested shape:

```python
def _token_set(self, text: str) -> set[str]:
    return {token.strip().lower() for token in self._tokenize(text) if token.strip()}

def _looks_like_dashboard_query(self, text: str, tokens: set[str]) -> bool:
    query_words = {"查", "查询", "看看", "状态", "看板"}
    board_words = {"安全", "边界", "报警", "通讯", "流程", "进度", "工艺", "适配", "运动", "极限"}
    return bool(tokens & query_words) and bool(tokens & board_words)
```

Then route this to the existing query action without changing emergency behavior.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
pytest tests/test_voice_nlp_semantic_candidates.py tests/test_voice_nlp_system_actions.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run full tests**

Run:

```powershell
pytest -q
```

Expected: baseline passes with one more test file.

---

### Task 2: Expand Dashboard Query Coverage for V2.1 Operator Questions

**Files:**
- Modify: `robot_modbus_lite/dashboard_query.py`
- Modify: `robot_modbus_lite/dashboard_query_specs.py`
- Test: `tests/test_dashboard_query.py`

- [ ] **Step 1: Add failing tests for remaining natural questions**

Add tests for these user questions:

```python
def test_dashboard_query_answers_why_motion_not_allowed():
    answer = answer_dashboard_query("为什么现在不能动", snapshot_with_action_blocked())
    assert "不能执行" in answer.text
    assert "急停" in answer.text or "报警" in answer.text or "通道" in answer.text


def test_dashboard_query_answers_process_preview_progress():
    answer = answer_dashboard_query("流程预演到哪一步了", snapshot_with_l3_progress())
    assert "流程预演" in answer.text
    assert "%" in answer.text or "步骤" in answer.text


def test_dashboard_query_answers_recovery_after_alarm():
    answer = answer_dashboard_query("报警后我该怎么处理", snapshot_with_alarm())
    assert "报警" in answer.text
    assert "复位" in answer.text or "确认" in answer.text
```

Use existing test helper style in `tests/test_dashboard_query.py`; do not introduce GUI dependencies.

- [ ] **Step 2: Run dashboard query tests and verify failure**

Run:

```powershell
pytest tests/test_dashboard_query.py -q
```

Expected: new tests fail on wording or routing.

- [ ] **Step 3: Add specs and answer builders**

Update `dashboard_query_specs.py` aliases for:

- `为什么不能动`
- `能不能执行`
- `流程预演`
- `报警处理`
- `怎么恢复`

Update `dashboard_query.py` to answer from existing board fields, not from DeepSeek.

- [ ] **Step 4: Run query tests**

Run:

```powershell
pytest tests/test_dashboard_query.py -q
```

Expected: pass.

- [ ] **Step 5: Run full tests**

Run:

```powershell
pytest -q
```

Expected: all tests pass.

---

### Task 3: Harden Operator Scene State Without Reworking Layout

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_operator_scene_state.py` or existing operator helper tests

- [ ] **Step 1: Add tests for scene transition edge cases**

Cover:

```python
def test_alarm_restore_does_not_restore_stale_confirm_scene():
    state = OperatorSceneState(current="alarm", before_alarm="confirm")
    state.clear_pending_confirm()
    restored = state.restore_after_alarm()
    assert restored == "idle"


def test_execute_completion_broadcasts_once():
    # Use existing broadcast queue test style.
    # Simulate execute -> idle twice and assert only one completion message.
    ...
```

If current tests already use a fake window object, extend that pattern.

- [ ] **Step 2: Run targeted operator tests**

Run:

```powershell
pytest tests/test_operator_scene_state.py tests/test_operator_precheck_helpers.py -q
```

Expected: new tests expose remaining implicit-scene issues or pass if already handled.

- [ ] **Step 3: Move remaining scene decisions behind one method**

In `operator_ui_mixin.py`, make scene changes pass through a single method such as:

```python
def _operator_request_scene(self, scene: str, reason: str = "") -> None:
    self._operator_scene_state.request(scene, reason=reason)
    self._operator_apply_scene(scene, reason=reason)
```

Do not redesign the layout. Keep this as a routing cleanup.

- [ ] **Step 4: Run targeted and full tests**

Run:

```powershell
pytest tests/test_operator_scene_state.py tests/test_operator_precheck_helpers.py -q
pytest -q
```

Expected: pass.

---

### Task 4: Add Qt GUI Layout Smoke Diagnostics

**Files:**
- Modify or Create: `tests/test_qt_gui_layout_smoke.py`
- Modify only if needed: `robot_modbus_lite/operator_ui_mixin.py`, `robot_modbus_lite/settings_mixin.py`, `robot_modbus_lite/gui_ui_mixin.py`

- [ ] **Step 1: Add a non-invasive smoke test**

Test that the main window can build the engineer and operator pages without changing default geometry.

```python
def test_operator_page_has_expected_core_panels(qtbot):
    window = build_test_main_window()
    qtbot.addWidget(window)
    window._set_workspace_mode("operator")
    assert window.operator_page is not None
    assert window.operator_stack.count() >= 6
```

Use existing Qt test helpers if present. If no Qt test helper exists, skip this task and document manual GUI verification instead.

- [ ] **Step 2: Verify whether Qt test support exists**

Run:

```powershell
pytest --fixtures -q | Select-String qtbot
```

Expected: if `qtbot` exists, continue; if not, avoid adding a new pytest dependency.

- [ ] **Step 3: Fix only confirmed layout regressions**

Do not resize the whole app globally. Fix narrow issues only:

- Long engineer system parameter panel must stay inside a scroll area.
- Operator page should preserve the restored compact/default size.
- Full status panel should scroll internally.

- [ ] **Step 4: Run GUI-related tests and manual launch**

Run:

```powershell
pytest tests/test_operator_precheck_helpers.py -q
python -m robot_modbus_lite.qt_gui
```

Expected: tests pass; GUI opens with restored layout.

---

### Task 5: Update Completion Docs With Correct Scope

**Files:**
- Modify: `docs/P0用户页安全交互闭环实施计划.md`
- Modify: `docs/用户页面实现现状与缺口总结.md`
- Optional Modify: `docs/开发任务清单.md`

- [ ] **Step 1: Update completion percentages**

Use this wording:

```markdown
当前口径：
- Qt GUI + 离线逻辑 + 不含 Web + 不含真机/部署验收：约 98%。
- Qt GUI + 含真机/VAD/TTS/50ms 数据源验收：约 82%-85%。
- 完整 V2.1 全量口径：约 75%-80%。
```

- [ ] **Step 2: Separate deferred true-machine items**

List deferred items explicitly:

- `FRAME_TRANS2` 真机联调。
- 50ms 控制器数据源压测。
- VAD 事件驱动语音。
- TTS 部署验收。
- 硬件/线程级独立应急通道。

- [ ] **Step 3: Run documentation sanity search**

Run:

```powershell
rg "97%|98%|82%-85%|FRAME_TRANS2|VAD|TTS|Web" docs
```

Expected: docs consistently distinguish local completion from full V2.1 completion.

---

### Task 6: Final Verification

**Files:**
- No source edits unless verification exposes a failure.

- [ ] **Step 1: Run full unit tests**

Run:

```powershell
pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run static import check for touched modules**

Run:

```powershell
python -m compileall robot_modbus_lite tests
```

Expected: compile completes without syntax errors.

- [ ] **Step 3: Launch Qt GUI**

Run:

```powershell
python -m robot_modbus_lite.qt_gui
```

Expected: GUI opens. Operator and engineer pages are usable, long settings content scrolls, and the app does not force an oversized default layout.

- [ ] **Step 4: Record final status**

Update `docs/P0用户页安全交互闭环实施计划.md` with:

- Test count.
- Remaining deferred true-machine/deployment items.
- Current completion percentage by scope.

---

## Completion Criteria

The plan is complete when:

- Full pytest suite passes.
- No Web API or HTML page work was required.
- Qt GUI opens normally.
- Local NLP classification is stronger than token logging only.
- Dashboard query answers cover common V2.1 operator questions.
- Scene state has fewer implicit transition paths.
- Docs clearly separate local completion from full V2.1 deployment completion.

