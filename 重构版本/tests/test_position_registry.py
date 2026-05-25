from pathlib import Path

import pytest

from robot_modbus_lite.permission_service import PermissionDenied, PermissionService
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.position_registry import PositionEntry, PositionRegistry, migrate_atomic_positions


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


def test_operator_can_create_and_update_positions_but_cannot_delete(tmp_path: Path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("operator"))

    ok, msg = registry.add(PositionEntry(name="A", pose=(1, 2, 3, 4, 5, 6)))
    assert ok, msg
    ok, msg = registry.update("A", spd=30)
    assert ok, msg

    with pytest.raises(PermissionDenied):
        registry.remove("A")


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


def test_migrate_atomic_positions_imports_legacy_positions(tmp_path: Path):
    atomic_path = tmp_path / "atomic_state.json"
    registry_path = tmp_path / "position_registry.json"
    memory = AtomicMemory()
    memory.save_position("A", (1, 2, 3, 4, 5, 6))
    memory.save(atomic_path)

    result = migrate_atomic_positions(atomic_path, registry_path)

    loaded = PositionRegistry(registry_path, permission=PermissionService("engineer"))
    assert result == {"created": 1, "skipped": 0, "failed": 0}
    assert loaded.get("A").pose == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_migrate_atomic_positions_does_not_override_locked_or_system_positions(tmp_path: Path):
    atomic_path = tmp_path / "atomic_state.json"
    registry_path = tmp_path / "position_registry.json"
    memory = AtomicMemory()
    memory.save_position("HOME", (9, 9, 9, 9, 9, 9))
    memory.save(atomic_path)
    registry = PositionRegistry(registry_path, permission=PermissionService("engineer"))
    registry.add(PositionEntry(name="HOME", pose=(0, 0, 0, 0, 0, 0), locked=True, is_system=True))

    result = migrate_atomic_positions(atomic_path, registry_path)

    loaded = PositionRegistry(registry_path, permission=PermissionService("engineer"))
    assert result == {"created": 0, "skipped": 1, "failed": 0}
    assert loaded.get("HOME").pose == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_migrate_atomic_positions_is_idempotent(tmp_path: Path):
    atomic_path = tmp_path / "atomic_state.json"
    registry_path = tmp_path / "position_registry.json"
    memory = AtomicMemory()
    memory.save_position("A", (1, 2, 3, 4, 5, 6))
    memory.save(atomic_path)

    first = migrate_atomic_positions(atomic_path, registry_path)
    second = migrate_atomic_positions(atomic_path, registry_path)

    loaded = PositionRegistry(registry_path, permission=PermissionService("engineer"))
    assert first == {"created": 1, "skipped": 0, "failed": 0}
    assert second == {"created": 0, "skipped": 1, "failed": 0}
    assert len(loaded.list_all()) == 1


def test_migrate_atomic_positions_ignores_missing_atomic_state(tmp_path: Path):
    result = migrate_atomic_positions(tmp_path / "missing.json", tmp_path / "position_registry.json")

    assert result == {"created": 0, "skipped": 0, "failed": 0}
