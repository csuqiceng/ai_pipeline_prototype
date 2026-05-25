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


def test_operator_can_create_and_update_flow_but_cannot_confirm_or_delete(tmp_path: Path):
    registry = FlowRegistry(tmp_path / "flows.json", permission=PermissionService("operator"))

    ok, msg = registry.add(FlowEntry(name="F", steps=[_step()]))
    assert ok, msg
    ok, msg = registry.update("F", step_delay_ms=300)
    assert ok, msg

    with pytest.raises(PermissionDenied):
        registry.confirm("F")
    with pytest.raises(PermissionDenied):
        registry.remove("F")


def test_legacy_flow_definition_converts_to_structured_entry():
    from robot_modbus_lite.flow_store import flow_definition_to_entry
    from robot_modbus_lite.models import FlowDefinition

    legacy = FlowDefinition(name="legacy", steps=("move_a", "wait_1"), step_delay_ms=500)

    entry = flow_definition_to_entry(legacy)

    assert entry.name == "legacy"
    assert entry.step_delay_ms == 500
    assert entry.steps[0].params["query_key"] == "move_a"


def test_structured_entry_converts_to_legacy_definition_for_old_callers():
    from robot_modbus_lite.flow_store import flow_entry_to_definition

    entry = FlowEntry(name="structured", steps=[_step()], step_delay_ms=250)

    legacy = flow_entry_to_definition(entry)

    assert legacy.name == "structured"
    assert legacy.step_delay_ms == 250
    assert legacy.steps == ("移动到A",)
