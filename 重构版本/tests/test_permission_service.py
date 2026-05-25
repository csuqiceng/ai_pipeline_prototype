import pytest

from robot_modbus_lite.permission_service import PermissionDenied, PermissionService


def test_operator_can_read_and_save_flow_drafts_but_cannot_delete_registry():
    svc = PermissionService(actor="operator")

    assert svc.allowed("position.read")
    for action in ["position.create", "position.update", "flow.create", "flow.update"]:
        svc.require(action)
    assert not svc.allowed("position.delete")

    with pytest.raises(PermissionDenied) as exc:
        svc.require("position.delete")
    assert "operator" in str(exc.value)
    assert "position.delete" in str(exc.value)


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
