# zbasic HMI Protocol Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the current Python upper-computer Modbus mapping and mock controller with the verified `zbasic-GLM + HMI` communication protocol.

**Architecture:** Keep the existing command model and service flow. Change only the protocol mapping layer, status bit definitions, mock-controller behavior, and focused regression tests so command generation, echo expectations, and local simulation match the real controller/HMI pair.

**Tech Stack:** Python, pytest, current `robot_modbus_lite` models, current `mock_controller` in-memory simulator.

---

## File Structure

- Modify: `robot_modbus_lite/models.py`
  - Change `Func109` and `Func110` register write addresses.
  - Add `Func8/102/112` status bit definitions.
- Modify: `mock_controller/controller.py`
  - Change mock `Func109` delay read from `IEEE(2)` to `IEEE(4)`.
  - Change mock `Func110` delay read and running update from `IEEE(2)` to `IEEE(6)`.
  - Add `Func8/102/112` status bit definitions and include `Func8/102/112` where supported by the mock motion slot.
- Modify: `tests/test_mock_controller_v50.py`
  - Add protocol regression tests for `Func109`, `Func110`, `Func112`, and `Func8/102` status parsing.
  - Add mock tests proving timer/delay behavior reads the zbasic/HMI addresses.
- Created: `docs/zbasic_hmi_protocol_diff.md`
  - Local saved record of the analyzed differences.

---

### Task 1: Add Failing Protocol Mapping Tests

**Files:**
- Modify: `tests/test_mock_controller_v50.py`

- [ ] **Step 1: Write failing tests for command write addresses and echo points**

Add tests asserting:

```python
def test_func109_delay_uses_zbasic_hmi_parameter_slot():
    command = SixAxisCommand(func_num=109, delay_sec=1.5)

    writes = command.to_func_writes()

    assert writes[1].start_vr == 4
    assert writes[1].values == (1.5,)
    assert (284, 1.5) in command.expected_echo_points()
    assert (282, 1.5) not in command.expected_echo_points()


def test_func110_delay_uses_zbasic_hmi_parameter_slot():
    command = SixAxisCommand(func_num=110, delay_sec=2.5)

    writes = command.to_func_writes()

    assert writes[1].start_vr == 6
    assert writes[1].values == (2.5,)
    assert (286, 2.5) in command.expected_echo_points()
    assert (282, 2.5) not in command.expected_echo_points()
```

- [ ] **Step 2: Write failing tests for status bit parsing**

Add tests asserting:

```python
def test_status_parses_func112_zbasic_mask():
    assert SixAxisStatus(0x00010000, 112).is_executing
    assert SixAxisStatus(0x00020000, 112).is_complete
    assert SixAxisStatus(0x00030000, 112).has_error


def test_status_parses_func8_and_102_zbasic_mask():
    for func_id in (8, 102):
        assert SixAxisStatus(0x00400000, func_id).is_executing
        assert SixAxisStatus(0x00800000, func_id).is_complete
        assert SixAxisStatus(0x00C00000, func_id).has_error
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_mock_controller_v50.py -q
```

Expected:

- `Func109` expects `4` but gets `2`.
- `Func110` expects `6` but gets `2`.
- `Func112/8/102` status tests fail because fields are missing.

---

### Task 2: Implement Command and Status Mapping

**Files:**
- Modify: `robot_modbus_lite/models.py`

- [ ] **Step 1: Change Func109 and Func110 write addresses**

Change:

```python
if self.func_num == 109:
    return [
        VrWriteRequest(start_vr=0, values=(109.0,)),
        VrWriteRequest(start_vr=4, values=(self.delay_sec,)),
    ]
if self.func_num == 110:
    return [
        VrWriteRequest(start_vr=0, values=(110.0,)),
        VrWriteRequest(start_vr=6, values=(self.delay_sec,)),
    ]
```

- [ ] **Step 2: Add zbasic status fields**

Change `SixAxisStatus.FUNC_STATE_FIELDS` to include:

```python
8: (22, 0x00C00000),
102: (22, 0x00C00000),
112: (16, 0x00030000),
```

- [ ] **Step 3: Run protocol tests**

Run:

```powershell
pytest tests/test_mock_controller_v50.py -q
```

Expected:

- New command/status mapping tests pass.
- Any remaining failures should be mock-behavior related.

---

### Task 3: Add Failing Mock Address Tests

**Files:**
- Modify: `tests/test_mock_controller_v50.py`

- [ ] **Step 1: Write failing mock tests**

Add tests that write the zbasic/HMI parameter slots directly:

```python
def test_mock_func109_reads_delay_from_ieee4():
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    client.write_modbus_float(SixAxisCommand(func_num=109, delay_sec=1.0).to_func_writes()[0])
    client.write_modbus_float(SixAxisCommand(func_num=109, delay_sec=1.0).to_func_writes()[1])
    client.write_modbus_float(SixAxisCommand(func_num=109, delay_sec=1.0).to_trigger_write())

    assert client.read_modbus_float(VrReadRequest(330, 1))[0] == 1.0


def test_mock_func110_reads_delay_from_ieee6():
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    command = SixAxisCommand(func_num=110, delay_sec=1.0)
    for request in command.to_func_writes():
        client.write_modbus_float(request)
    client.write_modbus_float(command.to_trigger_write())

    assert client.read_modbus_float(VrReadRequest(332, 1))[0] == 1.0
```

- [ ] **Step 2: Run tests and verify mock failures**

Run:

```powershell
pytest tests/test_mock_controller_v50.py -q
```

Expected:

- Mock tests fail before `mock_controller/controller.py` is updated because mock still reads `IEEE(2)`.

---

### Task 4: Implement Mock Alignment

**Files:**
- Modify: `mock_controller/controller.py`

- [ ] **Step 1: Add zbasic status fields and motion funcs**

Add fields:

```python
FuncSixAxis.ABSOLUTE_MOVE: (22, 0x00C00000),
FuncSixAxis.ABSOLUTE_MOVE_102: (22, 0x00C00000),
FuncSixAxis.CONTINUOUS_PATH: (16, 0x00030000),
```

Use the exact enum names present in `mock_controller/protocol.py`.

- [ ] **Step 2: Change Func110 running update address**

Change:

```python
delay_sec = float(self._modbus_ieee[6])
```

inside the running `FuncSixAxis.DELAY` update branch.

- [ ] **Step 3: Change Func109 timer read address**

Change `_do_six_timer_check()`:

```python
delay_sec = float(self._modbus_ieee[4])
```

- [ ] **Step 4: Change Func110 delay read address**

Change `_do_six_delay()`:

```python
delay_sec = float(self._modbus_ieee[6])
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_mock_controller_v50.py -q
```

Expected: all tests in this file pass.

---

### Task 5: Regression Test Broader Surface

**Files:**
- No planned production changes unless failures identify a direct protocol dependency.

- [ ] **Step 1: Run focused nearby tests**

Run:

```powershell
pytest tests/test_mock_controller_v50.py tests/test_zmotion_client.py tests/test_template_policy.py -q
```

Expected: pass.

- [ ] **Step 2: Run agent delay semantic tests**

Run:

```powershell
pytest tests/test_agent_command_understanding.py tests/test_agent_parameter_completion.py tests/test_atomic_resolver.py -q
```

Expected: pass. These tests should continue asserting `delay_sec` semantics, not low-level addresses.

- [ ] **Step 3: Run full suite if focused tests pass**

Run:

```powershell
pytest -q
```

Expected: pass, or only unrelated pre-existing failures. Any failure involving `Func109`, `Func110`, `Func112`, `Func8`, or `Func102` must be investigated before completion.

