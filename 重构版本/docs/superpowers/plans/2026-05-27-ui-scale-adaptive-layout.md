# UI Scale Adaptive Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Implemented and verified on 2026-05-27.

**Goal:** Make both engineer and operator pages fit reliably on high-DPI or low-available-size screens such as 2560x1080 with Windows scaling.

**Architecture:** Add a global UI scale setting to system configuration, calculate an effective scale from the current screen available geometry, and apply it to window size plus key fixed-width panels. Keep scroll areas as the fallback for content that still exceeds the visible area.

**DPI note:** Qt `screen().availableGeometry()` returns logical pixels, not physical pixels. For example, a 2560x1080 monitor at Windows 150% scaling is reported at roughly 1706 logical pixels wide. The auto-scale calculation intentionally uses this logical available size.

**Tech Stack:** PySide6 Qt Widgets, existing JSON system config, pytest.

---

### Task 1: Add UI Scale To System Config

**Files:**
- Modify: `robot_modbus_lite/system_config.py`
- Modify: `data/system_config.json`
- Test: `tests/test_system_config_ui_scale.py`

- [x] **Step 1: Write failing tests**

```python
from robot_modbus_lite.system_config import AxisRangeConfig, DEFAULT_SYSTEM_CONFIG, validate_system_config


def test_default_system_config_contains_ui_scale_auto():
    assert DEFAULT_SYSTEM_CONFIG["ui_scale"] == "auto"
    config = AxisRangeConfig.from_dict({"x": [-1, 1], "y": [-1, 1], "z": [0, 1]})
    assert config.ui_scale == "auto"
    assert config.to_dict()["ui_scale"] == "auto"


def test_system_config_accepts_numeric_ui_scale():
    config = AxisRangeConfig.from_dict({"x": [-1, 1], "y": [-1, 1], "z": [0, 1], "ui_scale": 0.9})
    assert config.ui_scale == 0.9
    assert config.to_dict()["ui_scale"] == 0.9


def test_validate_system_config_rejects_invalid_ui_scale():
    assert validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale=0.4)) == "UI 缩放比例必须在 0.6 到 1.2 之间，或使用 auto。"
    assert validate_system_config(AxisRangeConfig(x=(-1, 1), y=(-1, 1), z=(0, 1), ui_scale="bad")) == "UI 缩放比例必须在 0.6 到 1.2 之间，或使用 auto。"
```

- [x] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_system_config_ui_scale.py`

- [x] **Step 3: Implement config field**

Add `ui_scale` to `DEFAULT_SYSTEM_CONFIG`, `AxisRangeConfig`, `from_dict`, `to_dict`, and validation.

- [x] **Step 4: Update runtime config**

Add `"ui_scale": "auto"` to `data/system_config.json`.

- [x] **Step 5: Verify**

Run: `pytest -q tests/test_system_config_ui_scale.py tests/test_settings_config.py`

---

### Task 2: Add Screen-Based Scale Calculation

**Files:**
- Modify: `robot_modbus_lite/gui_ui_mixin.py`
- Test: `tests/test_engineer_layout.py`

- [x] **Step 1: Write failing tests**

```python
from robot_modbus_lite.gui_ui_mixin import GuiUiMixin


class Dummy(GuiUiMixin):
    pass


def test_ui_scale_auto_uses_screen_constraints():
    dummy = Dummy()
    scale = dummy._calculate_ui_scale("auto", available_width=1706, available_height=680)
    assert 0.6 <= scale <= 1.0
    assert scale < 1.0


def test_ui_scale_numeric_is_clamped():
    dummy = Dummy()
    assert dummy._calculate_ui_scale(0.5, available_width=9999, available_height=9999) == 0.6
    assert dummy._calculate_ui_scale(1.5, available_width=9999, available_height=9999) == 1.2
```

- [x] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_engineer_layout.py::test_ui_scale_auto_uses_screen_constraints tests/test_engineer_layout.py::test_ui_scale_numeric_is_clamped`

- [x] **Step 3: Implement helper methods**

Implement:
- `_screen_available_size() -> tuple[int, int]`
- `_calculate_ui_scale(config_value, available_width, available_height) -> float`
- `_scaled(value: int | float) -> int`
- `_scaled_min(value: int | float, minimum: int) -> int`
- `_target_window_size(width: int, height: int) -> tuple[int, int]`

- [x] **Step 4: Verify**

Run the two tests above.

---

### Task 3: Apply Scale To Engineer And Operator Shell

**Files:**
- Modify: `robot_modbus_lite/gui_ui_mixin.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_engineer_layout.py`
- Test: `tests/test_operator_precheck_helpers.py`

- [x] **Step 1: Write failing layout tests**

Add tests that assert:
- engineer nav width uses scaled `96`
- engineer right panel width uses scaled `150`
- login/main window target size does not exceed available size
- operator left sidebar and right sidebar use scaled `268` and `336`
- operator compact window uses scaled target size and does not exceed available screen
- workspace minimum size fits a scaled high-DPI logical screen

- [x] **Step 2: Run tests and confirm failure**

Run targeted tests.

- [x] **Step 3: Implement scale usage**

Apply `_scaled()` to:
- engineer nav fixed width
- engineer right panel fixed width
- operator left sidebar fixed width
- operator right sidebar fixed width
- key fixed button widths on engineer run page
- minimum button width fallback for readable labels

- [x] **Step 4: Replace fixed resize calls**

Replace:
- login `resize(900, 620)`
- post-login `resize(1380, 860)`
- operator page restore `resize(1380, 860)`
- operator compact mode `resize(620, 820)`

with `_resize_to_fit_screen(target_width, target_height)`.

- [x] **Step 5: Verify**

Run:
`pytest -q tests/test_system_config_ui_scale.py tests/test_engineer_layout.py tests/test_operator_precheck_helpers.py::test_operator_sidebars_use_ui_scale`

---

### Task 4: Runtime Verification

**Files:**
- Runtime only

- [x] **Step 1: Run focused tests**

Run:
`pytest -q tests/test_system_config_ui_scale.py tests/test_engineer_layout.py tests/test_operator_precheck_helpers.py::test_operator_sidebars_use_ui_scale`

- [x] **Step 2: Launch GUI**

Run:
Use the project Python environment to run `gui_main.py`.

- [x] **Step 3: Inspect manually**

Check:
- 2560x1080 high DPI does not crop right side.
- Bottom footer remains visible.
- Engineer log page can show full controls.
- Operator page still shows left/sidebar/chat/right panel.

Automated offscreen verification also simulates a 1706x680 logical available area and checks the window minimum size stays below that screen.
