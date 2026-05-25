from pathlib import Path

from robot_modbus_lite.flow_registry import FlowEntry, FlowRegistry, FlowStep
from robot_modbus_lite.flow_store import save_flows_json
from robot_modbus_lite.models import FlowDefinition
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.service import RobotModbusService


def _entry(name: str, query_key: str) -> FlowEntry:
    return FlowEntry(
        name=name,
        steps=[
            FlowStep(
                step_id=1,
                action=query_key,
                func_id=108,
                params={"query_key": query_key},
                description=query_key,
            )
        ],
        step_delay_ms=250,
    )


def test_service_loads_legacy_and_structured_flow_registry(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    save_flows_json(legacy_path, {"legacy": FlowDefinition(name="legacy", steps=("move_a",), step_delay_ms=500)})
    registry = FlowRegistry(registry_path, permission=PermissionService("engineer"))
    ok, msg = registry.add(_entry("structured", "move_b"))
    assert ok, msg

    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})

    assert service.list_flow_names() == ["legacy", "structured"]
    assert service.get_flow("legacy").steps == ("move_a",)
    assert service.get_flow("structured").steps == ("move_b",)


def test_service_save_flow_writes_legacy_and_structured_registry(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})

    service.save_flow(FlowDefinition(name="F", steps=("move_a", "wait_1"), step_delay_ms=750))

    reloaded = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    assert reloaded.get_flow("F").steps == ("move_a", "wait_1")
    entry = reloaded.get_flow_entry("F")
    assert entry is not None
    assert entry.steps[0].params["query_key"] == "move_a"


def test_service_updates_confirmed_flow_as_draft_in_registry(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    service.save_flow(FlowDefinition(name="F", steps=("move_a",), step_delay_ms=500))
    ok, msg = service.confirm_flow("F")
    assert ok, msg

    service.save_flow(FlowDefinition(name="F", steps=("move_b",), step_delay_ms=600))

    entry = service.get_flow_entry("F")
    assert entry is not None
    assert entry.confirmed is False
    assert entry.version == 2
    assert entry.steps[0].params["query_key"] == "move_b"


def test_service_delete_flow_removes_legacy_and_registry_entry(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    service.save_flow(FlowDefinition(name="F", steps=("move_a",), step_delay_ms=500))

    service.delete_flow("F")

    assert service.list_flow_names() == []
    assert service.get_flow_entry("F") is None
    reloaded = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    assert reloaded.list_flow_names() == []


def test_service_start_flow_rehearsal_updates_structured_registry(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    service.save_flow(FlowDefinition(name="F", steps=("move_a",), step_delay_ms=500))

    ok, msg = service.start_flow_rehearsal("F")

    assert ok, msg
    assert service.get_flow_entry("F").state == "rehearsal"
    assert "演练" in msg


def test_service_get_effective_flow_prefers_structured_entry_with_native_steps(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    registry = FlowRegistry(registry_path, permission=PermissionService("engineer"))
    ok, msg = registry.add(
        FlowEntry(
            name="native",
            steps=[
                FlowStep(
                    step_id=1,
                    action="移动",
                    func_id=108,
                    params={"target_x": 1.0, "target_y": 2.0, "target_z": 3.0},
                    description="结构化移动",
                )
            ],
            step_delay_ms=300,
        )
    )
    assert ok, msg
    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})

    flow = service.get_effective_flow("native")

    assert isinstance(flow, FlowEntry)
    assert flow.steps[0].func_id == 108
    assert flow.steps[0].params["target_x"] == 1.0


def test_service_save_flow_entry_writes_registry_and_legacy_view(tmp_path: Path):
    legacy_path = tmp_path / "flows.json"
    registry_path = tmp_path / "flow_registry.json"
    service = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    entry = FlowEntry(
        name="native",
        steps=[
            FlowStep(
                step_id=1,
                action="移动",
                func_id=108,
                params={"target_x": 1.0},
                description="结构化移动",
            )
        ],
        step_delay_ms=300,
        rehearsal_spd=25,
    )

    service.save_flow_entry(entry)

    reloaded = RobotModbusService("unused.csv", flows_path=legacy_path, flow_registry_path=registry_path, table={})
    saved = reloaded.get_flow_entry("native")
    assert saved is not None
    assert saved.steps[0].func_id == 108
    assert saved.rehearsal_spd == 25
    assert reloaded.get_flow("native").steps == ("结构化移动",)
