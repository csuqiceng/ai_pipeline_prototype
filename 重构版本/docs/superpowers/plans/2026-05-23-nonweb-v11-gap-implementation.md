# Non-Web V1.1 Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the non-Web implementation gaps listed in `docs/当前实现与编程手册V1.1需求对照_非Web.md` without changing the current V5.0 controller contract or adding Web scope.

**Architecture:** Keep the existing Qt/operator execution path and V5.0 six-axis command chain. Add small backend services for permission, position registry, structured flow registry, memory parameters, alarm advice, daily dialog logs, and NLP normalization, then adapt existing mixins to call those services. Do not implement the manual's V4.3 seven-step handshake or direct emergency MODBUS write; update documentation and acceptance criteria to the current auditable V5.0 behavior.

**Tech Stack:** Python dataclasses, JSON persistence, PySide6 integration points, existing `robot_modbus_lite` modules, pytest.

---

## Scope

This plan implements the non-Web gaps from the comparison document:

- P0 documentation baseline: V4.3 -> V5.0, emergency channel acceptance.
- P1 safety and data consistency: permission service, position registry, structured flow registry, L1 sphere/speed clamps, alarm lifecycle, memory parameters, daily dialog logs.
- P2 NLP usability: standard words, homophone map, pinyin normalization, Sichuan dialect normalization, and a deterministic normalization pipeline before existing atomic/template/DeepSeek parsing.

Out of scope:

- Web API, Web UI, FastAPI endpoints, browser verification.
- Reverting the six-axis command chain to V4.3.
- Emergency direct write that bypasses all audit and status confirmation.
- Large UI redesign.

---

## File Map

Create:

- `robot_modbus_lite/permission_service.py`  
  Unified backend permission checks for operator/engineer/system actors.
- `robot_modbus_lite/position_registry.py`  
  Structured position entries, locked/system protection, JSON persistence, compatibility import from `AtomicMemory`.
- `robot_modbus_lite/flow_registry.py`  
  Structured flow entries and steps, status transitions, confirmed/draft/rehearsal state, JSON persistence, compatibility import from current `FlowDefinition`.
- `robot_modbus_lite/memory_params.py`  
  Action-type speed preferences and command statistics persisted to `memory_params.json`.
- `robot_modbus_lite/dialog_logger.py`  
  Daily `dialog_YYYY-MM-DD.jsonl` writer.
- `robot_modbus_lite/alarm_advice.py`  
  Alarm-code advice table, severity, recoverability, auto-clear rules.
- `robot_modbus_lite/nlp_standard_words.py`  
  Standard word definitions and category/function indexes.
- `robot_modbus_lite/nlp_normalization.py`  
  Pinyin/homophone/dialect/unit candidate normalization without bypassing safety checks.
- `tests/test_permission_service.py`
- `tests/test_position_registry.py`
- `tests/test_flow_registry.py`
- `tests/test_memory_params.py`
- `tests/test_dialog_logger.py`
- `tests/test_alarm_advice.py`
- `tests/test_nlp_normalization.py`

Modify:

- `docs/机械手自然语言交互系统_编程手册_V1.1.md`  
  Retire V4.3 acceptance language where it conflicts with current V5.0 behavior.
- `docs/当前实现与编程手册V1.1需求对照_非Web.md`  
  Mark implemented gaps after tasks land.
- `robot_modbus_lite/models.py`  
  Either keep `FlowDefinition` as compatibility type or extend it to wrap `FlowEntry`.
- `robot_modbus_lite/service.py`  
  Load both legacy `flows.json` and new `flow_registry.json` during migration.
- `robot_modbus_lite/flow_store.py`  
  Add migration helpers; preserve existing legacy read/write for compatibility tests.
- `robot_modbus_lite/flow_management_mixin.py`  
  Enforce backend permissions, structured step editing, confirm/rehearsal fields.
- `robot_modbus_lite/flow_execution_mixin.py`  
  Execute `FlowEntry.steps` directly while continuing to support legacy string steps.
- `robot_modbus_lite/process_precheck.py`  
  Accept structured flow steps and check `rehearsal_spd`.
- `robot_modbus_lite/atomic_memory.py`  
  Keep existing memory behavior but bridge named positions to `PositionRegistry`.
- `robot_modbus_lite/atomic_resolver.py`  
  Read named positions from `PositionRegistry` when present; respect locked/delete permissions.
- `robot_modbus_lite/nlp_mixin.py`  
  Load/save `PositionRegistry` and `MemoryManager`.
- `robot_modbus_lite/safety_precheck.py`  
  Add sphere radius and per-action speed clamp checks.
- `robot_modbus_lite/safety_suggestion.py`  
  Consume `AlarmAdvice`.
- `robot_modbus_lite/response_builder.py`  
  Include alarm advice and daily dialog ids when available.
- `robot_modbus_lite/operator_ui_mixin.py`  
  Inject permission actor, daily dialog logging, and registry-backed save/delete operations.
- `robot_modbus_lite/gui_logging.py` and/or `robot_modbus_lite/interaction_archiver.py`  
  Mirror interaction records to daily dialog files.
- `robot_modbus_lite/voice_nlp_adapter.py`  
  Run deterministic normalization candidates before existing local rules and DeepSeek.
- `requirements.txt`  
  Add `pypinyin` if pinyin normalization is enabled as a hard dependency; otherwise keep optional import and document fallback.

---

## Task 1: Update Protocol Baseline Documentation

**Files:**

- Modify: `docs/机械手自然语言交互系统_编程手册_V1.1.md`
- Modify: `docs/当前实现与编程手册V1.1需求对照_非Web.md`

- [ ] **Step 1: Make a documentation-only diff**

Update the manual acceptance language:

```markdown
协议基线：当前非 Web 主链路以 V5.0 六轴协议为准。

验收口径：
- 命令写入必须经过前置状态门、回显比对、触发前复查、控制器接受确认和完成/错误轮询。
- V4.3 七步握手仅作为历史设计背景，不作为当前代码验收标准。
- Func110/120 并行能力以 `six_func_slot()` 的 motion/program/system 槽互斥为准。
- 应急命令绕过普通 NLP、普通确认和普通预检，但必须走可审计 Func104 快速通道，并保留授权码、执行状态确认和日志。
- 30ms 应急指标限定为“文本进入本地应急解析到本地决策完成”，不包含 ASR、网络、控制器响应和机械动作时间。
```

- [ ] **Step 2: Verify references remain consistent**

Run:

```powershell
rg -n "V4\\.3|V5\\.0|七步|应急直接|30ms|Func104 快速通道" docs
```

Expected:

- V4.3 appears only as historical context or "not current acceptance".
- V5.0 appears in the current acceptance section.
- "应急直接写 MODBUS" is replaced or explicitly marked "不建议照搬".

- [ ] **Step 3: Commit**

```powershell
git add docs\机械手自然语言交互系统_编程手册_V1.1.md docs\当前实现与编程手册V1.1需求对照_非Web.md
git commit -m "docs: align non-web manual with v5 protocol baseline"
```

---

## Task 2: Add Unified Backend Permission Service

**Files:**

- Create: `robot_modbus_lite/permission_service.py`
- Create: `tests/test_permission_service.py`
- Modify: `robot_modbus_lite/flow_management_mixin.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Modify later tasks to accept `PermissionService`

- [ ] **Step 1: Write failing permission tests**

Create `tests/test_permission_service.py`:

```python
import pytest

from robot_modbus_lite.permission_service import PermissionDenied, PermissionService


def test_operator_can_read_but_cannot_modify_registry():
    svc = PermissionService(actor="operator")

    assert svc.allowed("position.read")
    assert not svc.allowed("position.update")

    with pytest.raises(PermissionDenied) as exc:
        svc.require("position.update")
    assert "operator" in str(exc.value)
    assert "position.update" in str(exc.value)


def test_engineer_can_manage_positions_and_flows():
    svc = PermissionService(actor="engineer")

    for action in [
        "position.create",
        "position.update",
        "position.delete",
        "flow.create",
        "flow.update",
        "flow.delete",
        "flow.confirm",
        "flow.rehearsal",
    ]:
        svc.require(action)


def test_system_can_write_system_config_but_operator_cannot():
    PermissionService(actor="system").require("system_config.update")

    with pytest.raises(PermissionDenied):
        PermissionService(actor="operator").require("system_config.update")


def test_unknown_action_is_denied_by_default():
    svc = PermissionService(actor="engineer")

    assert not svc.allowed("unknown.action")
    with pytest.raises(PermissionDenied):
        svc.require("unknown.action")
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
pytest tests/test_permission_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'robot_modbus_lite.permission_service'`.

- [ ] **Step 3: Implement permission service**

Create `robot_modbus_lite/permission_service.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


class PermissionDenied(PermissionError):
    """Raised when a backend operation is not allowed for the active actor."""


_ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "operator": frozenset(
        {
            "position.read",
            "flow.read",
            "flow.execute",
            "alarm.ack",
            "dashboard.read",
        }
    ),
    "engineer": frozenset(
        {
            "position.read",
            "position.create",
            "position.update",
            "position.delete",
            "flow.read",
            "flow.create",
            "flow.update",
            "flow.delete",
            "flow.confirm",
            "flow.rehearsal",
            "flow.execute",
            "alarm.ack",
            "alarm.reset",
            "dashboard.read",
            "system_config.update",
        }
    ),
    "system": frozenset(
        {
            "position.read",
            "position.create",
            "position.update",
            "position.delete",
            "flow.read",
            "flow.create",
            "flow.update",
            "flow.delete",
            "flow.confirm",
            "flow.rehearsal",
            "flow.execute",
            "alarm.ack",
            "alarm.reset",
            "dashboard.read",
            "system_config.update",
        }
    ),
}


@dataclass(frozen=True)
class PermissionService:
    actor: str = "operator"

    def normalized_actor(self) -> str:
        actor = (self.actor or "operator").strip().lower()
        return actor if actor in _ROLE_ACTIONS else "operator"

    def allowed(self, action: str) -> bool:
        action_key = str(action or "").strip()
        return action_key in _ROLE_ACTIONS[self.normalized_actor()]

    def require(self, action: str) -> None:
        action_key = str(action or "").strip()
        if not self.allowed(action_key):
            raise PermissionDenied(f"{self.normalized_actor()} is not allowed to perform {action_key}")
```

- [ ] **Step 4: Run permission tests**

Run:

```powershell
pytest tests/test_permission_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Add a small UI bridge**

In `operator_ui_mixin.py`, add a helper near existing role/login helpers:

```python
def _current_permission_actor(self) -> str:
    role = getattr(self, "current_user_role", None) or getattr(self, "user_role", None)
    return str(role or "operator").strip().lower()


def _permission_service(self):
    from .permission_service import PermissionService

    return PermissionService(actor=self._current_permission_actor())
```

In save/delete handlers that already require engineer context, call `self._permission_service().require(...)` before mutating data.

- [ ] **Step 6: Run focused regression**

Run:

```powershell
pytest tests/test_permission_service.py tests/test_operator_voice_commands.py tests/test_engineer_voice_commands.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add robot_modbus_lite\permission_service.py robot_modbus_lite\operator_ui_mixin.py robot_modbus_lite\flow_management_mixin.py tests\test_permission_service.py
git commit -m "feat: add backend permission service"
```

---

## Task 3: Add Structured Position Registry

**Files:**

- Create: `robot_modbus_lite/position_registry.py`
- Create: `tests/test_position_registry.py`
- Modify: `robot_modbus_lite/atomic_memory.py`
- Modify: `robot_modbus_lite/atomic_resolver.py`
- Modify: `robot_modbus_lite/nlp_mixin.py`

- [ ] **Step 1: Write failing position registry tests**

Create `tests/test_position_registry.py`:

```python
from pathlib import Path

import pytest

from robot_modbus_lite.permission_service import PermissionDenied, PermissionService
from robot_modbus_lite.position_registry import PositionEntry, PositionRegistry


def test_position_registry_persists_full_entry(tmp_path: Path):
    path = tmp_path / "position_registry.json"
    registry = PositionRegistry(path, permission=PermissionService("engineer"))

    ok, msg = registry.add(PositionEntry(name="焊接位A", pose=(1, 2, 3, 4, 5, 6), spd=30, move_type=1))

    assert ok, msg
    loaded = PositionRegistry(path, permission=PermissionService("engineer")).get("焊接位A")
    assert loaded is not None
    assert loaded.pose == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert loaded.spd == 30
    assert loaded.move_type == 1
    assert not loaded.locked


def test_locked_position_cannot_update_or_delete(tmp_path: Path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))
    registry.add(PositionEntry(name="home", pose=(0, 0, 0, 0, 0, 0), locked=True, is_system=True))

    ok, msg = registry.update("home", spd=20)
    assert not ok
    assert "锁定" in msg

    ok, msg = registry.remove("home")
    assert not ok
    assert "不可删除" in msg


def test_operator_cannot_modify_positions(tmp_path: Path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("operator"))

    with pytest.raises(PermissionDenied):
        registry.add(PositionEntry(name="A", pose=(1, 2, 3, 4, 5, 6)))


def test_position_entry_exports_func108_params():
    entry = PositionEntry(name="A", pose=(10, 20, 30, 1, 2, 3), spd=40, move_type=2)

    params = entry.to_func108_params()

    assert params["target_x"] == 10.0
    assert params["target_y"] == 20.0
    assert params["target_z"] == 30.0
    assert params["target_rx"] == 1.0
    assert params["target_ry"] == 2.0
    assert params["target_rz"] == 3.0
    assert params["spd_pct"] == 40
    assert params["move_type"] == 2
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
pytest tests/test_position_registry.py -q
```

Expected: FAIL because `position_registry.py` does not exist.

- [ ] **Step 3: Implement registry**

Create `robot_modbus_lite/position_registry.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .permission_service import PermissionService


Pose6 = tuple[float, float, float, float, float, float]


def _pose6(values: Any) -> Pose6:
    seq = list(values)
    if len(seq) < 6:
        raise ValueError("position pose requires 6 numeric values")
    return tuple(float(v) for v in seq[:6])  # type: ignore[return-value]


@dataclass
class PositionEntry:
    name: str
    pose: Pose6
    spd: int = 50
    move_type: int = 0
    locked: bool = False
    is_system: bool = False
    created_by: str = "operator"
    created_at: str = ""
    updated_at: str = ""

    def normalized_name(self) -> str:
        return self.name.strip().lower()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pose"] = list(self.pose)
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionEntry":
        return cls(
            name=str(payload.get("name", "")).strip(),
            pose=_pose6(payload.get("pose", (0, 0, 0, 0, 0, 0))),
            spd=int(payload.get("spd", 50)),
            move_type=int(payload.get("move_type", 0)),
            locked=bool(payload.get("locked", False)),
            is_system=bool(payload.get("is_system", False)),
            created_by=str(payload.get("created_by", "operator")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    def to_func108_params(self, spd_override: int | None = None) -> dict[str, float | int | str]:
        x, y, z, rx, ry, rz = self.pose
        return {
            "target_x": x,
            "target_y": y,
            "target_z": z,
            "target_rx": rx,
            "target_ry": ry,
            "target_rz": rz,
            "spd_pct": int(spd_override if spd_override is not None else self.spd),
            "move_type": int(self.move_type),
            "fuzzy_pos": 0,
            "position_name": self.name,
        }


class PositionRegistry:
    def __init__(self, path: str | Path, *, permission: PermissionService | None = None):
        self.path = Path(path)
        self.permission = permission or PermissionService("operator")
        self._positions: dict[str, PositionEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        items = payload.get("positions", [])
        if isinstance(items, dict):
            items = items.values()
        for item in items:
            entry = PositionEntry.from_dict(dict(item))
            if entry.name:
                self._positions[entry.normalized_name()] = entry

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.1",
            "updated_at": datetime.now().isoformat(),
            "positions": [entry.to_dict() for entry in sorted(self._positions.values(), key=lambda e: e.name)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, entry: PositionEntry) -> tuple[bool, str]:
        self.permission.require("position.create")
        key = entry.normalized_name()
        if not key:
            return False, "位置名称不能为空"
        if key in self._positions:
            return False, f"位置'{entry.name}'已存在"
        now = datetime.now().isoformat()
        entry.created_at = entry.created_at or now
        entry.updated_at = now
        entry.created_by = entry.created_by or self.permission.normalized_actor()
        self._positions[key] = entry
        self._save()
        return True, f"位置'{entry.name}'已保存"

    def update(self, name: str, **kwargs: Any) -> tuple[bool, str]:
        self.permission.require("position.update")
        entry = self.get(name)
        if entry is None:
            return False, f"位置'{name}'不存在"
        if entry.locked and not kwargs.pop("unlock", False):
            return False, f"位置'{entry.name}'已锁定，不可修改"
        for key in ("pose", "spd", "move_type", "locked", "is_system"):
            if key in kwargs:
                value = _pose6(kwargs[key]) if key == "pose" else kwargs[key]
                setattr(entry, key, value)
        entry.updated_at = datetime.now().isoformat()
        self._save()
        return True, f"位置'{entry.name}'已更新"

    def set_position(self, name: str, pose: Pose6, **kwargs: Any) -> tuple[bool, str]:
        existing = self.get(name)
        if existing is None:
            return self.add(PositionEntry(name=name, pose=_pose6(pose), **kwargs))
        return self.update(name, pose=pose, **kwargs)

    def remove(self, name: str) -> tuple[bool, str]:
        self.permission.require("position.delete")
        entry = self.get(name)
        if entry is None:
            return False, f"位置'{name}'不存在"
        if entry.locked or entry.is_system:
            return False, f"位置'{entry.name}'已锁定，不可删除"
        del self._positions[entry.normalized_name()]
        self._save()
        return True, f"位置'{entry.name}'已删除"

    def get(self, name: str) -> PositionEntry | None:
        return self._positions.get(str(name or "").strip().lower())

    def list_all(self) -> list[PositionEntry]:
        return list(sorted(self._positions.values(), key=lambda entry: entry.name))
```

- [ ] **Step 4: Run registry tests**

Run:

```powershell
pytest tests/test_position_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Bridge named-position reads**

Modify `AtomicResolver` so position movement reads registry first when `self.position_registry` exists:

```python
registry = getattr(self.memory, "position_registry", None)
if registry is not None:
    entry = registry.get(normalized_name)
    if entry is not None:
        params = entry.to_func108_params()
        pose = entry.pose
```

Keep the existing `AtomicMemory.positions` path as fallback for backward compatibility.

- [ ] **Step 6: Add bridge regression**

Extend `tests/test_atomic_resolver.py`:

```python
from pathlib import Path

from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionEntry, PositionRegistry


def test_resolver_prefers_structured_position_registry(tmp_path: Path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))
    registry.add(PositionEntry(name="A", pose=(1, 2, 3, 4, 5, 6), spd=35, move_type=1))
    memory = AtomicMemory()
    memory.position_registry = registry  # compatibility bridge

    elements = AtomicParser().parse("小正，移动到位置A")
    resolved = AtomicResolver(memory).resolve(elements)

    assert resolved.kind == "command"
    assert resolved.record is not None
    assert resolved.record.float_param("target_x") == 1
    assert resolved.record.float_param("spd_pct") == 35
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
pytest tests/test_position_registry.py tests/test_atomic_resolver.py tests/test_atomic_memory.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add robot_modbus_lite\position_registry.py robot_modbus_lite\atomic_memory.py robot_modbus_lite\atomic_resolver.py robot_modbus_lite\nlp_mixin.py tests\test_position_registry.py tests\test_atomic_resolver.py
git commit -m "feat: add structured position registry"
```

---

## Task 4: Add Structured Flow Registry and Migration

**Files:**

- Create: `robot_modbus_lite/flow_registry.py`
- Create: `tests/test_flow_registry.py`
- Modify: `robot_modbus_lite/models.py`
- Modify: `robot_modbus_lite/flow_store.py`
- Modify: `robot_modbus_lite/service.py`
- Modify: `robot_modbus_lite/flow_management_mixin.py`
- Modify: `robot_modbus_lite/flow_execution_mixin.py`
- Modify: `robot_modbus_lite/process_precheck.py`

- [ ] **Step 1: Write failing flow registry tests**

Create `tests/test_flow_registry.py`:

```python
from pathlib import Path

import pytest

from robot_modbus_lite.flow_registry import FlowEntry, FlowRegistry, FlowState, FlowStep
from robot_modbus_lite.permission_service import PermissionDenied, PermissionService


def _step(step_id: int = 1) -> FlowStep:
    return FlowStep(
        step_id=step_id,
        action="移动",
        func_id=108,
        params={"target_position": "A"},
        position_name="A",
        spd_pct=30,
        description="移动到A",
    )


def test_flow_registry_add_rejects_duplicate_and_persists(tmp_path: Path):
    registry = FlowRegistry(tmp_path / "flow_registry.json", permission=PermissionService("engineer"))

    ok, msg = registry.add(FlowEntry(name="焊接流程", steps=[_step()]))
    assert ok, msg

    ok, msg = registry.add(FlowEntry(name="焊接流程", steps=[_step()]))
    assert not ok
    assert "已存在" in msg

    loaded = FlowRegistry(tmp_path / "flow_registry.json", permission=PermissionService("engineer"))
    flow = loaded.get("焊接流程")
    assert flow is not None
    assert flow.steps[0].func_id == 108
    assert flow.steps[0].params["target_position"] == "A"


def test_confirmed_flow_cannot_update_without_draft(tmp_path: Path):
    registry = FlowRegistry(tmp_path / "flows.json", permission=PermissionService("engineer"))
    registry.add(FlowEntry(name="F", steps=[_step()]))
    ok, msg = registry.confirm("F")
    assert ok, msg

    ok, msg = registry.update("F", description="changed")
    assert not ok
    assert "已确认" in msg

    ok, msg = registry.update("F", description="changed", create_draft=True)
    assert ok, msg
    assert registry.get("F").description == "changed"
    assert registry.get("F").confirmed is False
    assert registry.get("F").version == 2


def test_unconfirmed_flow_can_update_directly(tmp_path: Path):
    registry = FlowRegistry(tmp_path / "flows.json", permission=PermissionService("engineer"))
    registry.add(FlowEntry(name="F", steps=[_step()]))

    ok, msg = registry.update("F", description="normal edit")

    assert ok, msg
    assert registry.get("F").description == "normal edit"
    assert registry.get("F").confirmed is False
    assert registry.get("F").version == 1


def test_rehearsal_transition_uses_configured_speed(tmp_path: Path):
    registry = FlowRegistry(tmp_path / "flows.json", permission=PermissionService("engineer"))
    registry.add(FlowEntry(name="F", steps=[_step()], rehearsal_spd=20))

    ok, msg = registry.start_rehearsal("F")

    assert ok, msg
    assert registry.get("F").state == FlowState.REHEARSAL.value
    assert "20%" in msg


def test_operator_cannot_create_flow(tmp_path: Path):
    registry = FlowRegistry(tmp_path / "flows.json", permission=PermissionService("operator"))

    with pytest.raises(PermissionDenied):
        registry.add(FlowEntry(name="F", steps=[_step()]))
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
pytest tests/test_flow_registry.py -q
```

Expected: FAIL because `flow_registry.py` does not exist.

- [ ] **Step 3: Implement flow registry**

Create `robot_modbus_lite/flow_registry.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .permission_service import PermissionService


class FlowState(Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    REHEARSAL = "rehearsal"


VALID_TRANSITIONS: dict[FlowState, frozenset[FlowState]] = {
    FlowState.IDLE: frozenset({FlowState.READY, FlowState.REHEARSAL}),
    FlowState.READY: frozenset({FlowState.RUNNING, FlowState.IDLE, FlowState.REHEARSAL}),
    FlowState.RUNNING: frozenset({FlowState.PAUSED, FlowState.COMPLETED, FlowState.ERROR}),
    FlowState.PAUSED: frozenset({FlowState.RUNNING, FlowState.IDLE}),
    FlowState.COMPLETED: frozenset({FlowState.IDLE}),
    FlowState.ERROR: frozenset({FlowState.IDLE}),
    FlowState.REHEARSAL: frozenset({FlowState.IDLE, FlowState.READY}),
}


@dataclass
class FlowStep:
    step_id: int
    action: str
    func_id: int
    params: dict[str, Any] = field(default_factory=dict)
    position_name: str | None = None
    spd_pct: int = 50
    description: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FlowStep":
        return cls(
            step_id=int(payload.get("step_id", 0)),
            action=str(payload.get("action", "")),
            func_id=int(payload.get("func_id", 0)),
            params=dict(payload.get("params", {})),
            position_name=payload.get("position_name"),
            spd_pct=int(payload.get("spd_pct", payload.get("speed_pct", 50))),
            description=str(payload.get("description", "")),
        )


@dataclass
class FlowEntry:
    name: str
    description: str = ""
    steps: list[FlowStep] = field(default_factory=list)
    step_delay_ms: int = 1000
    rehearsal_spd: int = 20
    confirmed: bool = False
    created_by: str = "operator"
    version: int = 1
    state: str = FlowState.IDLE.value
    current_step: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FlowEntry":
        steps = [FlowStep.from_dict(dict(item)) for item in payload.get("steps", [])]
        return cls(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            steps=steps,
            step_delay_ms=int(payload.get("step_delay_ms", 1000)),
            rehearsal_spd=int(payload.get("rehearsal_spd", 20)),
            confirmed=bool(payload.get("confirmed", False)),
            created_by=str(payload.get("created_by", "operator")),
            version=int(payload.get("version", 1)),
            state=str(payload.get("state", FlowState.IDLE.value)),
            current_step=int(payload.get("current_step", 0)),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


class FlowRegistry:
    def __init__(self, path: str | Path, *, permission: PermissionService | None = None):
        self.path = Path(path)
        self.permission = permission or PermissionService("operator")
        self._flows: dict[str, FlowEntry] = {}
        self._load()

    def _key(self, name: str) -> str:
        return str(name or "").strip().lower()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for item in payload.get("flows", []):
            flow = FlowEntry.from_dict(dict(item))
            if flow.name:
                self._flows[self._key(flow.name)] = flow

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.1",
            "updated_at": datetime.now().isoformat(),
            "flows": [asdict(flow) for flow in sorted(self._flows.values(), key=lambda item: item.name)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, entry: FlowEntry) -> tuple[bool, str]:
        self.permission.require("flow.create")
        key = self._key(entry.name)
        if not key:
            return False, "流程名称不能为空"
        if key in self._flows:
            return False, f"流程'{entry.name}'已存在"
        now = datetime.now().isoformat()
        entry.created_at = entry.created_at or now
        entry.updated_at = now
        entry.created_by = entry.created_by or self.permission.normalized_actor()
        self._flows[key] = entry
        self._save()
        return True, f"流程'{entry.name}'已保存"

    def update(self, name: str, *, create_draft: bool = False, **kwargs: Any) -> tuple[bool, str]:
        self.permission.require("flow.update")
        flow = self.get(name)
        if flow is None:
            return False, f"流程'{name}'不存在"
        if flow.confirmed and not create_draft:
            return False, f"流程'{flow.name}'已确认，修改需生成草稿版本"
        if flow.confirmed and create_draft:
            flow.confirmed = False
            flow.version += 1
            flow.state = FlowState.IDLE.value
        for key in ("description", "steps", "step_delay_ms", "rehearsal_spd"):
            if key in kwargs:
                setattr(flow, key, kwargs[key])
        flow.updated_at = datetime.now().isoformat()
        self._save()
        return True, f"流程'{flow.name}'已更新"

    def remove(self, name: str) -> tuple[bool, str]:
        self.permission.require("flow.delete")
        key = self._key(name)
        if key not in self._flows:
            return False, f"流程'{name}'不存在"
        del self._flows[key]
        self._save()
        return True, f"流程'{name}'已删除"

    def confirm(self, name: str) -> tuple[bool, str]:
        self.permission.require("flow.confirm")
        flow = self.get(name)
        if flow is None:
            return False, f"流程'{name}'不存在"
        flow.confirmed = True
        flow.state = FlowState.READY.value
        flow.updated_at = datetime.now().isoformat()
        self._save()
        return True, f"流程'{flow.name}'已确认"

    def transition(self, name: str, target: FlowState) -> bool:
        flow = self.get(name)
        if flow is None:
            return False
        current = FlowState(flow.state)
        if target not in VALID_TRANSITIONS[current]:
            return False
        flow.state = target.value
        flow.updated_at = datetime.now().isoformat()
        self._save()
        return True

    def start_rehearsal(self, name: str) -> tuple[bool, str]:
        self.permission.require("flow.rehearsal")
        flow = self.get(name)
        if flow is None:
            return False, f"流程'{name}'不存在"
        if not self.transition(name, FlowState.REHEARSAL):
            return False, f"流程'{name}'当前状态不可演练"
        return True, f"流程'{name}'演练模式启动，速度{flow.rehearsal_spd}%"

    def get(self, name: str) -> FlowEntry | None:
        return self._flows.get(self._key(name))

    def list_all(self) -> list[FlowEntry]:
        return list(sorted(self._flows.values(), key=lambda item: item.name))
```

- [ ] **Step 4: Run flow registry tests**

Run:

```powershell
pytest tests/test_flow_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Add compatibility conversion**

In `flow_store.py`, add helpers:

```python
from .flow_registry import FlowEntry, FlowStep


def flow_definition_to_entry(flow: FlowDefinition) -> FlowEntry:
    steps = [
        FlowStep(step_id=index + 1, action=str(step), func_id=0, params={"query_key": str(step)}, description=str(step))
        for index, step in enumerate(flow.steps)
    ]
    return FlowEntry(name=flow.name, steps=steps, step_delay_ms=flow.step_delay_ms)


def flow_entry_to_definition(entry: FlowEntry) -> FlowDefinition:
    steps = tuple(str(step.params.get("query_key") or step.description or step.action) for step in entry.steps)
    return FlowDefinition(name=entry.name, steps=steps, step_delay_ms=entry.step_delay_ms)
```

- [ ] **Step 6: Add migration tests**

Extend `tests/test_flow_registry.py`:

```python
from robot_modbus_lite.flow_store import flow_definition_to_entry, flow_entry_to_definition
from robot_modbus_lite.models import FlowDefinition


def test_legacy_flow_definition_converts_to_structured_entry():
    legacy = FlowDefinition(name="legacy", steps=("move_a", "wait_1"), step_delay_ms=500)

    entry = flow_definition_to_entry(legacy)

    assert entry.name == "legacy"
    assert entry.step_delay_ms == 500
    assert entry.steps[0].params["query_key"] == "move_a"


def test_structured_entry_converts_to_legacy_definition_for_old_callers():
    entry = FlowEntry(name="structured", steps=[_step()], step_delay_ms=250)

    legacy = flow_entry_to_definition(entry)

    assert legacy.name == "structured"
    assert legacy.step_delay_ms == 250
    assert legacy.steps == ("移动到A",)
```

- [ ] **Step 7: Run flow and process regressions**

Run:

```powershell
pytest tests/test_flow_registry.py tests/test_flow_pause.py tests/test_process_precheck.py -q
```

Expected: PASS.

- [ ] **Step 8: Integrate registry in management and execution**

Implementation rules:

- `flow_management_mixin.py` must call `PermissionService.require("flow.create")`, `flow.update`, `flow.delete`, `flow.confirm`, or `flow.rehearsal` before mutations.
- Existing UI flow lists must still display legacy flows loaded from `data/flows.json`.
- `flow_execution_mixin.py` must accept both:
  - legacy `FlowDefinition.steps: tuple[str, ...]`
  - structured `FlowEntry.steps: list[FlowStep]`
- For structured steps, prefer `step.params["query_key"]` if present; otherwise execute by `func_id` and `params` through existing command building path.

- [ ] **Step 9: Run broader flow tests**

Run:

```powershell
pytest tests/test_flow_registry.py tests/test_flow_pause.py tests/test_process_precheck.py tests/test_operator_precheck_helpers.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
git add robot_modbus_lite\flow_registry.py robot_modbus_lite\flow_store.py robot_modbus_lite\models.py robot_modbus_lite\service.py robot_modbus_lite\flow_management_mixin.py robot_modbus_lite\flow_execution_mixin.py robot_modbus_lite\process_precheck.py tests\test_flow_registry.py
git commit -m "feat: add structured flow registry"
```

---

## Task 5: Complete L1 Safety Subchecks

**Files:**

- Modify: `robot_modbus_lite/safety_precheck.py`
- Modify: `tests/test_safety_precheck.py`

- [ ] **Step 1: Add failing tests for sphere radius and speed clamps**

Append to `tests/test_safety_precheck.py`:

```python
from robot_modbus_lite.safety_precheck import SafetyPrecheckService


def test_l1_blocks_target_outside_max_sphere_radius():
    service = SafetyPrecheckService(max_sphere_radius=1200.0)

    result = service.run_l1({"target_x": 1201.0, "target_y": 0.0, "target_z": 0.0}, {})

    assert not result.ok
    assert any("球面半径" in item.message for item in result.items)


def test_l1_clamps_joint_motion_speed():
    service = SafetyPrecheckService(speed_clamps={"joint": 50})

    result = service.run_l1({"action_type": "joint", "spd_pct": 80}, {})

    assert not result.ok
    assert any("速度钳位" in item.message for item in result.items)


def test_l1_allows_motion_inside_sphere_and_clamp():
    service = SafetyPrecheckService(max_sphere_radius=1200.0, speed_clamps={"joint": 50})

    result = service.run_l1({"action_type": "joint", "target_x": 100, "target_y": 100, "target_z": 100, "spd_pct": 50}, {})

    assert result.ok
```

- [ ] **Step 2: Run failing safety tests**

Run:

```powershell
pytest tests/test_safety_precheck.py -q
```

Expected: FAIL because constructor and checks do not support these parameters.

- [ ] **Step 3: Implement checks**

In `safety_precheck.py`, add defaults:

```python
DEFAULT_MAX_SPHERE_RADIUS = 1200.0
DEFAULT_SPEED_CLAMPS = {
    "joint": 50,
    "home": 50,
    "calibration": 30,
}
```

In `SafetyPrecheckService.__init__`, accept:

```python
max_sphere_radius: float = DEFAULT_MAX_SPHERE_RADIUS
speed_clamps: dict[str, int] | None = None
```

In `run_l1`, after target coordinate extraction:

```python
radius = math.sqrt(target_x * target_x + target_y * target_y + target_z * target_z)
if radius > self.max_sphere_radius:
    items.append(L1CheckItem(False, f"目标球面半径{radius:.1f}mm超过上限{self.max_sphere_radius:.1f}mm"))
```

After speed extraction:

```python
action_type = str(params.get("action_type") or params.get("motion_type") or params.get("action") or "").lower()
limit = self.speed_clamps.get(action_type)
if limit is not None and speed > limit:
    items.append(L1CheckItem(False, f"{action_type}速度钳位{limit}%，当前{speed}%"))
```

Preserve existing global speed/acc/dec checks.

- [ ] **Step 4: Add action-type mapping at L1 call sites**

Before calling `SafetyPrecheckService.run_l1()`, callers must provide an action type that matches the clamp table. Add a small helper in `safety_precheck.py` so `operator_ui_mixin.py` and `process_precheck.py` do not duplicate mapping logic:

```python
def infer_l1_action_type(params: dict) -> str:
    explicit = params.get("action_type") or params.get("motion_type")
    if explicit:
        return str(explicit).strip().lower()
    func_id = int(params.get("func_id") or params.get("func") or 0)
    action = str(params.get("action") or params.get("action_name") or "")
    if func_id == 106 or "关节" in action:
        return "joint"
    if func_id == 107:
        return "virtual"
    if func_id == 108 or "移动" in action:
        return "move"
    if "回零" in action:
        return "home"
    if "标定" in action:
        return "calibration"
    return ""
```

Use this helper inside `run_l1()` before the clamp lookup:

```python
action_type = infer_l1_action_type(params)
limit = self.speed_clamps.get(action_type)
```

Mapping rules:

- Func106 or action text containing `关节` -> `action_type="joint"`
- Func107 -> `action_type="virtual"`
- Func108 or action text containing `移动` -> `action_type="move"`
- action text containing `回零` -> `action_type="home"`
- action text containing `标定` -> `action_type="calibration"`

- [ ] **Step 5: Run safety tests**

Run:

```powershell
pytest tests/test_safety_precheck.py tests/test_operator_precheck_helpers.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add robot_modbus_lite\safety_precheck.py tests\test_safety_precheck.py
git commit -m "feat: complete l1 sphere and speed clamp checks"
```

---

## Task 6: Add Memory Params and Daily Dialog Logger

**Files:**

- Create: `robot_modbus_lite/memory_params.py`
- Create: `robot_modbus_lite/dialog_logger.py`
- Create: `tests/test_memory_params.py`
- Create: `tests/test_dialog_logger.py`
- Modify: `robot_modbus_lite/nlp_mixin.py`
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Modify: `robot_modbus_lite/interaction_archiver.py`

- [ ] **Step 1: Write memory parameter tests**

Create `tests/test_memory_params.py`:

```python
from pathlib import Path

from robot_modbus_lite.memory_params import MemoryManager


def test_memory_manager_tracks_action_specific_speeds(tmp_path: Path):
    manager = MemoryManager(tmp_path / "memory_params.json")

    manager.update_after_command("移动", {"spd_pct": 60})
    manager.update_after_command("点动", {"spd_pct": 20})
    manager.update_after_command("回零", {"spd_pct": 30})
    manager.update_after_command("标定", {"spd_pct": 10})

    loaded = MemoryManager(tmp_path / "memory_params.json")
    assert loaded.memory.last_motion_speed_pct == 60
    assert loaded.memory.last_jog_speed_pct == 20
    assert loaded.memory.last_home_speed_pct == 30
    assert loaded.memory.last_calib_speed_pct == 10
    assert loaded.memory.total_commands == 4
    assert loaded.memory.last_command_time


def test_memory_manager_ignores_invalid_speed():
    manager = MemoryManager()

    manager.update_after_command("移动", {"spd_pct": 150})

    assert manager.memory.last_motion_speed_pct == 50
    assert manager.memory.total_commands == 1
```

- [ ] **Step 2: Write daily dialog tests**

Create `tests/test_dialog_logger.py`:

```python
import json
from datetime import datetime
from pathlib import Path

from robot_modbus_lite.dialog_logger import DialogLogger


def test_dialog_logger_writes_daily_jsonl(tmp_path: Path):
    logger = DialogLogger(tmp_path, clock=lambda: datetime(2026, 5, 23, 10, 30, 0))

    logger.append(role="user", text="小正，移动到位置A", result="received", extra={"ack_delay_ms": 12})

    path = tmp_path / "dialog_2026-05-23.jsonl"
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["role"] == "user"
    assert rows[0]["text"] == "小正，移动到位置A"
    assert rows[0]["extra"]["ack_delay_ms"] == 12
```

- [ ] **Step 3: Run failing tests**

Run:

```powershell
pytest tests/test_memory_params.py tests/test_dialog_logger.py -q
```

Expected: FAIL because modules do not exist.

- [ ] **Step 4: Implement `memory_params.py`**

Create `robot_modbus_lite/memory_params.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class MemoryParams:
    last_motion_speed_pct: int = 50
    last_jog_speed_pct: int = 30
    last_home_speed_pct: int = 30
    last_calib_speed_pct: int = 20
    preferred_rehearsal_spd: int = 20
    total_commands: int = 0
    last_command_time: str = ""


class MemoryManager:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.memory = MemoryParams()
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        memory_payload = payload.get("memory", payload)
        self.memory = MemoryParams(**{**asdict(MemoryParams()), **memory_payload})

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": "1.1", "updated_at": datetime.now().isoformat(), "memory": asdict(self.memory)}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_after_command(self, action: str, params: dict[str, Any]) -> None:
        speed = params.get("spd_pct", params.get("speed_pct"))
        if isinstance(speed, (int, float)) and 0 <= int(speed) <= 100:
            if action == "移动":
                self.memory.last_motion_speed_pct = int(speed)
            elif action in {"点动", "关节运动", "虚拟轴运动"}:
                self.memory.last_jog_speed_pct = int(speed)
            elif action == "回零":
                self.memory.last_home_speed_pct = int(speed)
            elif action == "标定":
                self.memory.last_calib_speed_pct = int(speed)
        self.memory.total_commands += 1
        self.memory.last_command_time = datetime.now().isoformat()
        self._save()
```

- [ ] **Step 5: Implement `dialog_logger.py`**

Create `robot_modbus_lite/dialog_logger.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class DialogLogger:
    def __init__(self, directory: str | Path, *, clock: Callable[[], datetime] | None = None):
        self.directory = Path(directory)
        self.clock = clock or datetime.now

    def _path(self) -> Path:
        now = self.clock()
        return self.directory / f"dialog_{now:%Y-%m-%d}.jsonl"

    def append(self, *, role: str, text: str, result: str, extra: dict[str, Any] | None = None) -> Path:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": self.clock().isoformat(),
            "role": role,
            "text": text,
            "result": result,
            "extra": extra or {},
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
```

- [ ] **Step 6: Run new tests**

Run:

```powershell
pytest tests/test_memory_params.py tests/test_dialog_logger.py -q
```

Expected: PASS.

- [ ] **Step 7: Integrate daily logger and memory manager**

Rules:

- Instantiate `MemoryManager(resolve_runtime_data_file("memory_params.json"))` near existing `AtomicMemory` load.
- Call `memory_manager.update_after_command(action, params)` only after a command reaches an accepted terminal result or a local memory command succeeds.
- Instantiate `DialogLogger(resolve_runtime_data_file("dialog_logs"))` and append records when text input/voice input is archived.
- Keep existing session JSONL and interaction archive outputs unchanged.

- [ ] **Step 8: Run archive regressions**

Run:

```powershell
pytest tests/test_memory_params.py tests/test_dialog_logger.py tests/test_interaction_archiver.py tests/test_operator_precheck_helpers.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add robot_modbus_lite\memory_params.py robot_modbus_lite\dialog_logger.py robot_modbus_lite\nlp_mixin.py robot_modbus_lite\operator_ui_mixin.py robot_modbus_lite\interaction_archiver.py tests\test_memory_params.py tests\test_dialog_logger.py
git commit -m "feat: add memory params and daily dialog logs"
```

---

## Task 7: Add Alarm Advice Table and Lifecycle Rules

**Files:**

- Create: `robot_modbus_lite/alarm_advice.py`
- Create: `tests/test_alarm_advice.py`
- Modify: `robot_modbus_lite/safety_suggestion.py`
- Modify: `robot_modbus_lite/dashboard_query.py`
- Modify: `robot_modbus_lite/response_builder.py`

- [ ] **Step 1: Write failing alarm advice tests**

Create `tests/test_alarm_advice.py`:

```python
from robot_modbus_lite.alarm_advice import AlarmAdviceBook


def test_alarm_advice_book_contains_ten_codes():
    book = AlarmAdviceBook.default()

    assert len(book.codes()) >= 10
    assert book.get("E_STOP").severity == "critical"
    assert book.get("OVER_SPEED").auto_clear is False


def test_unknown_alarm_returns_safe_fallback():
    advice = AlarmAdviceBook.default().get("UNKNOWN_CODE")

    assert advice.code == "UNKNOWN_CODE"
    assert advice.severity == "unknown"
    assert "工程师" in advice.operator_hint


def test_auto_clear_policy_is_explicit():
    book = AlarmAdviceBook.default()

    assert isinstance(book.get("COMM_STALE").auto_clear, bool)
    assert isinstance(book.get("CONTROLLER_NOT_READY").auto_clear, bool)
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
pytest tests/test_alarm_advice.py -q
```

Expected: FAIL because `alarm_advice.py` does not exist.

- [ ] **Step 3: Implement alarm advice book**

Create `robot_modbus_lite/alarm_advice.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlarmAdvice:
    code: str
    title: str
    severity: str
    operator_hint: str
    engineer_hint: str
    auto_clear: bool = False


class AlarmAdviceBook:
    def __init__(self, entries: dict[str, AlarmAdvice]):
        self._entries = dict(entries)

    @classmethod
    def default(cls) -> "AlarmAdviceBook":
        entries = {
            "E_STOP": AlarmAdvice("E_STOP", "急停触发", "critical", "确认人员安全后联系工程师复位。", "检查急停回路和 Func104 状态。"),
            "PAUSED": AlarmAdvice("PAUSED", "设备暂停", "warning", "确认现场安全后执行继续。", "检查暂停来源和暂停输入。", True),
            "OVER_SPEED": AlarmAdvice("OVER_SPEED", "速度超限", "critical", "降低速度后重新确认。", "检查模板速度和动作类型钳位。"),
            "OVER_ACCEL": AlarmAdvice("OVER_ACCEL", "加速度超限", "critical", "降低加速度后重新确认。", "检查 acc_pct 和控制器限制。"),
            "OVER_DECEL": AlarmAdvice("OVER_DECEL", "减速度超限", "critical", "降低减速度后重新确认。", "检查 dec_pct 和控制器限制。"),
            "JOINT_LIMIT": AlarmAdvice("JOINT_LIMIT", "关节限位", "critical", "停止当前动作，选择安全点或中点绕行。", "检查关节软限位和目标位姿。"),
            "CART_LIMIT": AlarmAdvice("CART_LIMIT", "笛卡尔软限位", "critical", "选择安全点或调整目标位置。", "检查 R/Z/XYZ 边界。"),
            "SINGULARITY": AlarmAdvice("SINGULARITY", "奇异区风险", "warning", "采纳中点绕行建议。", "检查逆解 FSTATUS 和中点建议。"),
            "COMM_STALE": AlarmAdvice("COMM_STALE", "通讯反馈过期", "warning", "等待通讯恢复或刷新连接。", "检查 Modbus 连接和实时反馈时间戳。", True),
            "CONTROLLER_NOT_READY": AlarmAdvice("CONTROLLER_NOT_READY", "控制器未就绪", "warning", "等待控制器就绪后重试。", "检查控制器状态字和通道状态。", True),
        }
        return cls(entries)

    def codes(self) -> list[str]:
        return sorted(self._entries)

    def get(self, code: str) -> AlarmAdvice:
        key = str(code or "").strip().upper()
        if key in self._entries:
            return self._entries[key]
        return AlarmAdvice(key or "UNKNOWN", "未知报警", "unknown", "保持停止状态并联系工程师确认。", "读取 LONG(38) 和控制器日志。")
```

- [ ] **Step 4: Run alarm tests**

Run:

```powershell
pytest tests/test_alarm_advice.py -q
```

Expected: PASS.

- [ ] **Step 5: Integrate advice into existing suggestions**

Rules:

- Map `SixAxisAlarmDetail` bits to stable advice codes.
- `safety_suggestion.py` should include `operator_hint` and `engineer_hint` when alarm code is known.
- `dashboard_query.py` should answer "报警怎么处理" using `AlarmAdviceBook`.
- `response_builder.py` should include advice for final alarm/failure responses.

- [ ] **Step 6: Run alarm and dashboard regressions**

Run:

```powershell
pytest tests/test_alarm_advice.py tests/test_safety_suggestion.py tests/test_dashboard_query.py tests/test_response_builder.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add robot_modbus_lite\alarm_advice.py robot_modbus_lite\safety_suggestion.py robot_modbus_lite\dashboard_query.py robot_modbus_lite\response_builder.py tests\test_alarm_advice.py
git commit -m "feat: add alarm advice lifecycle table"
```

---

## Task 8: Add Deterministic NLP Normalization Layer

**Files:**

- Create: `robot_modbus_lite/nlp_standard_words.py`
- Create: `robot_modbus_lite/nlp_normalization.py`
- Create: `tests/test_nlp_normalization.py`
- Modify: `robot_modbus_lite/voice_nlp_adapter.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write failing NLP normalization tests**

Create `tests/test_nlp_normalization.py`:

```python
from robot_modbus_lite.nlp_normalization import NlpNormalizer
from robot_modbus_lite.nlp_standard_words import STANDARD_WORDS, get_words_by_category


def test_standard_word_catalog_has_required_core_words():
    assert len(STANDARD_WORDS) >= 50
    for word in ["执行", "保存", "删除", "流程", "位置", "急停", "复位", "上升", "下降"]:
        assert word in STANDARD_WORDS
    assert get_words_by_category("flow")


def test_homophone_normalization_maps_common_errors():
    normalizer = NlpNormalizer()

    result = normalizer.normalize("小正，保村位置A")

    assert result.text == "小正，保存位置A"
    assert any(step.kind == "homophone" for step in result.steps)


def test_dialect_normalization_maps_sichuan_phrase():
    normalizer = NlpNormalizer()

    result = normalizer.normalize("小正，往上头走十毫米")

    assert "上升" in result.text or "向上" in result.text
    assert any(step.kind == "dialect" for step in result.steps)


def test_pinyin_normalization_is_safe_when_dependency_missing():
    normalizer = NlpNormalizer(enable_pinyin=True)

    result = normalizer.normalize("小正，zhixing流程A")

    assert result.text
    assert result.original == "小正，zhixing流程A"
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
pytest tests/test_nlp_normalization.py -q
```

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement standard words**

Create `robot_modbus_lite/nlp_standard_words.py` with at least 50 entries:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardWord:
    standard: str
    pinyin: str
    category: str
    homophones: tuple[str, ...] = ()
    sichuan_variants: tuple[str, ...] = ()
    func_id: int | None = None


_WORDS = [
    ("执行", "zhixing", "flow", ("直行", "执形"), (), None),
    ("保存", "baocun", "flow", ("保村", "宝存", "报存"), (), None),
    ("删除", "shanchu", "flow", ("山除", "善除"), (), None),
    ("流程", "liucheng", "flow", (), (), None),
    ("步骤", "buzhou", "flow", (), (), None),
    ("循环", "xunhuan", "flow", (), (), None),
    ("单步", "danbu", "flow", (), (), None),
    ("位置", "weizhi", "position", (), (), 108),
    ("示教", "shijiao", "position", (), (), None),
    ("移动", "yidong", "motion", (), (), 108),
    ("上升", "shangsheng", "motion", (), ("上头走", "往上头走"), 107),
    ("下降", "xiajiang", "motion", (), ("下切", "往下头走"), 107),
    ("左移", "zuoyi", "motion", (), (), 107),
    ("右移", "youyi", "motion", (), (), 107),
    ("前进", "qianjin", "motion", (), (), 107),
    ("后退", "houtui", "motion", (), (), 107),
    ("关节", "guanjie", "motion", (), (), 106),
    ("虚拟轴", "xunixhou", "motion", (), (), 107),
    ("回零", "huiling", "system", (), (), 104),
    ("复位", "fuwei", "system", (), (), 104),
    ("暂停", "zanting", "system", (), (), 104),
    ("继续", "jixu", "system", (), (), 110),
    ("停止", "tingzhi", "system", (), (), 104),
    ("急停", "jiting", "emergency", (), (), 104),
    ("报警", "baojing", "alarm", (), (), None),
    ("确认", "queren", "confirm", (), (), None),
    ("取消", "quxiao", "confirm", (), (), None),
    ("速度", "sudu", "param", (), (), None),
    ("加速度", "jiasudu", "param", (), (), None),
    ("减速度", "jiansudu", "param", (), (), None),
    ("慢速", "mansu", "param", (), (), None),
    ("快速", "kuaisu", "param", (), (), None),
    ("毫米", "haomi", "unit", (), (), None),
    ("厘米", "limi", "unit", (), (), None),
    ("度", "du", "unit", (), (), None),
    ("秒", "miao", "unit", (), (), None),
    ("打开", "dakai", "io", (), (), 120),
    ("关闭", "guanbi", "io", (), (), 120),
    ("IO", "io", "io", (), (), 120),
    ("看板", "kanban", "query", (), (), None),
    ("状态", "zhuangtai", "query", (), (), None),
    ("边界", "bianjie", "query", (), (), None),
    ("极限", "jixian", "query", (), (), None),
    ("通讯", "tongxun", "query", (), (), None),
    ("故障", "guzhang", "query", (), (), None),
    ("演练", "yanlian", "flow", (), (), None),
    ("锁定", "suoding", "flow", (), (), None),
    ("工程师", "gongchengshi", "permission", (), (), None),
    ("操作员", "caozuoyuan", "permission", (), (), None),
    ("系统", "xitong", "system", (), (), None),
]

STANDARD_WORDS = {item[0]: StandardWord(*item) for item in _WORDS}


def get_words_by_category(category: str) -> list[StandardWord]:
    return [word for word in STANDARD_WORDS.values() if word.category == category]


def get_words_by_func(func_id: int) -> list[StandardWord]:
    return [word for word in STANDARD_WORDS.values() if word.func_id == func_id]
```

- [ ] **Step 4: Implement normalizer**

Create `robot_modbus_lite/nlp_normalization.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from .nlp_standard_words import STANDARD_WORDS


@dataclass(frozen=True)
class NormalizationStep:
    kind: str
    source: str
    target: str


@dataclass(frozen=True)
class NormalizationResult:
    original: str
    text: str
    steps: tuple[NormalizationStep, ...] = field(default_factory=tuple)


class NlpNormalizer:
    def __init__(self, *, enable_pinyin: bool = False):
        self.enable_pinyin = enable_pinyin
        self.homophones = {
            variant: word.standard
            for word in STANDARD_WORDS.values()
            for variant in word.homophones
        }
        self.dialect = {
            variant: word.standard
            for word in STANDARD_WORDS.values()
            for variant in word.sichuan_variants
        }

    def normalize(self, text: str) -> NormalizationResult:
        original = str(text or "")
        current = original
        steps: list[NormalizationStep] = []

        for source, target in sorted(self.homophones.items(), key=lambda item: len(item[0]), reverse=True):
            if source in current:
                current = current.replace(source, target)
                steps.append(NormalizationStep("homophone", source, target))

        for source, target in sorted(self.dialect.items(), key=lambda item: len(item[0]), reverse=True):
            if source in current:
                current = current.replace(source, target)
                steps.append(NormalizationStep("dialect", source, target))

        if self.enable_pinyin:
            current, pinyin_steps = self._pinyin_normalize(current)
            steps.extend(pinyin_steps)

        return NormalizationResult(original=original, text=current, steps=tuple(steps))

    def _pinyin_normalize(self, text: str) -> tuple[str, list[NormalizationStep]]:
        try:
            from pypinyin import Style, pinyin  # type: ignore
        except Exception:
            return text, []
        pinyin_index = {word.pinyin: word.standard for word in STANDARD_WORDS.values()}
        compact = "".join(item[0] for item in pinyin(text, style=Style.NORMAL))
        if compact in pinyin_index and pinyin_index[compact] != text:
            return pinyin_index[compact], [NormalizationStep("pinyin", text, pinyin_index[compact])]
        return text, []
```

- [ ] **Step 5: Run NLP tests**

Run:

```powershell
pytest tests/test_nlp_normalization.py -q
```

Expected: PASS.

- [ ] **Step 6: Integrate before existing local parsing**

In `voice_nlp_adapter.py`:

```python
from .nlp_normalization import NlpNormalizer
```

In `VoiceNlpAdapter.__init__`:

```python
self.normalizer = normalizer or NlpNormalizer(enable_pinyin=False)
```

At the start of `parse()` after raw text extraction:

```python
normalization = self.normalizer.normalize(raw_text)
normalized_text = normalization.text
```

Use `normalized_text` for emergency/dashboard/wake/atomic/rule parsing. Preserve `raw_text` in diagnostics and archives. Do not let normalization create an executable action without the existing wake-word, confirmation, and safety checks.

- [ ] **Step 7: Add adapter regression**

Append to `tests/test_voice_nlp_atomic.py`:

```python
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


def test_voice_nlp_uses_homophone_normalization_without_skipping_wake_gate():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("保村位置A")

    assert plan.action_type in {"unknown", "chat"}


def test_voice_nlp_uses_homophone_normalization_with_wake_word():
    adapter = VoiceNlpAdapter(table={}, flow_names=())

    plan = adapter.parse("小正，保村位置A")

    assert plan.actions
    assert "position_save" in str(plan.actions[0].target)
```

- [ ] **Step 8: Run NLP regressions**

Run:

```powershell
pytest tests/test_nlp_normalization.py tests/test_voice_nlp_atomic.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_semantic_candidates.py -q
```

Expected: PASS.

- [ ] **Step 9: Decide dependency mode**

If pinyin must be enabled by default, add to `requirements.txt`:

```text
pypinyin>=0.49
```

If pinyin remains optional, do not add the dependency and keep `enable_pinyin=False` by default. Record that choice in `docs/当前实现与编程手册V1.1需求对照_非Web.md`.

- [ ] **Step 10: Commit**

```powershell
git add robot_modbus_lite\nlp_standard_words.py robot_modbus_lite\nlp_normalization.py robot_modbus_lite\voice_nlp_adapter.py requirements.txt tests\test_nlp_normalization.py tests\test_voice_nlp_atomic.py
git commit -m "feat: add deterministic nlp normalization"
```

---

## Task 9: Final Documentation, Migration Notes, and Full Verification

**Files:**

- Modify: `docs/当前实现与编程手册V1.1需求对照_非Web.md`
- Modify: `docs/机械手自然语言交互系统_编程手册_V1.1.md`
- Modify or create: `docs/非Web_V1.1差异收敛验收记录.md`

- [ ] **Step 1: Update comparison statuses**

In `docs/当前实现与编程手册V1.1需求对照_非Web.md`, update each row after implementation:

```markdown
| 位置 locked 保护 | 已实现 | `position_registry.py` | 锁定和系统位置禁止修改/删除。 |
| 流程步骤结构化模型 | 已实现 | `flow_registry.py` 的 `FlowStep` | 支持结构化字段并兼容旧 `FlowDefinition.steps`。 |
| 流程确认后锁定 | 已实现 | `FlowRegistry.confirm/update(create_draft=True)` | 已确认流程修改需生成草稿版本。 |
| 对话日志按日分割 | 已实现 | `dialog_logger.py` | 写入 `dialog_YYYY-MM-DD.jsonl`。 |
```

Do not mark NLP as fully implemented unless pinyin, homophone, dialect, standard words, and pipeline integration are all enabled and covered by tests.

- [ ] **Step 2: Write migration notes**

Create `docs/非Web_V1.1差异收敛验收记录.md`:

```markdown
# 非 Web V1.1 差异收敛验收记录

日期：2026-05-23

## 保留的工程决策

- 协议主链路保持 V5.0，不回退到 V4.3 七步握手。
- 应急命令保持可审计 Func104 快速通道，不实现完全绕过网关的直接写入。
- Web 相关实现不在本轮验收范围内。

## 数据迁移

- 旧 `data/flows.json` 可迁移为 `flow_registry.json`。
- 旧 `AtomicMemory.positions` 可迁移为 `position_registry.json`。
- 旧 `data/atomic_state.json` 继续保留，用于原子命令基础记忆。
- 新 `memory_params.json` 保存动作分类速度偏好和统计。

## 验收命令

```powershell
pytest tests/test_permission_service.py tests/test_position_registry.py tests/test_flow_registry.py tests/test_safety_precheck.py tests/test_memory_params.py tests/test_dialog_logger.py tests/test_alarm_advice.py tests/test_nlp_normalization.py -q
pytest -q
```
```

- [ ] **Step 3: Run focused verification**

Run:

```powershell
pytest tests/test_permission_service.py tests/test_position_registry.py tests/test_flow_registry.py tests/test_safety_precheck.py tests/test_memory_params.py tests/test_dialog_logger.py tests/test_alarm_advice.py tests/test_nlp_normalization.py -q
```

Expected: PASS.

- [ ] **Step 4: Run non-Web regression set**

Run:

```powershell
pytest tests/test_atomic_memory.py tests/test_atomic_parser.py tests/test_atomic_resolver.py tests/test_voice_nlp_atomic.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_semantic_candidates.py tests/test_process_precheck.py tests/test_flow_pause.py tests/test_operator_precheck_helpers.py tests/test_safety_suggestion.py tests/test_dashboard.py tests/test_dashboard_query.py tests/test_response_builder.py tests/test_interaction_archiver.py -q
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

Run:

```powershell
pytest -q
```

Expected: PASS. If Web tests fail because Web scope was not touched, record the failing test names and rerun the non-Web regression set above before claiming this plan complete.

- [ ] **Step 6: Commit**

```powershell
git add docs\当前实现与编程手册V1.1需求对照_非Web.md docs\机械手自然语言交互系统_编程手册_V1.1.md docs\非Web_V1.1差异收敛验收记录.md
git commit -m "docs: record non-web v11 gap closure"
```

---

## Implementation Order

Recommended sequence:

1. Task 1: documentation baseline.
2. Task 2: permission service.
3. Task 3: position registry.
4. Task 4: flow registry.
5. Task 5: L1 subchecks.
6. Task 6: memory params and daily dialog logs.
7. Task 7: alarm advice.
8. Task 8: NLP normalization.
9. Task 9: final documentation and verification.

This order keeps backend security primitives ahead of data mutations and keeps NLP normalization last so it cannot accidentally bypass confirmation or safety checks.

---

## Acceptance Criteria

- Current V5.0 command path remains the only non-Web controller protocol baseline.
- Emergency remains audited through Func104 fast path with authorization code and state confirmation.
- Operators cannot mutate protected registries through backend APIs.
- Locked/system positions cannot be modified or deleted.
- Confirmed flows cannot be overwritten; modification creates a draft/new version path.
- Structured flow steps exist and legacy string-step flows still execute.
- L1 precheck blocks out-of-sphere targets and action-specific speed clamp violations.
- Alarm advice covers at least 10 stable codes with operator and engineer hints.
- Dialog logs are written by date as `dialog_YYYY-MM-DD.jsonl`.
- Memory parameters track action-specific speed preferences and command stats.
- NLP normalization improves candidates but does not bypass wake-word gates, confirmation, permissions, or safety prechecks.
- Focused tests and full `pytest -q` pass, or any unrelated Web-only failures are documented with the non-Web regression set passing.
