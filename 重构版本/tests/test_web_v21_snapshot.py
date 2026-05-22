from fastapi.testclient import TestClient

from robot_modbus_lite.json_schema import validate_device_snapshot
from robot_modbus_lite.web_server import app


def test_web_snapshot_v21_returns_device_snapshot_schema():
    client = TestClient(app)

    response = client.get("/api/snapshot/v21")

    assert response.status_code == 200
    payload = response.json()
    assert payload["msg_type"] == "device_snapshot"
    assert payload["dashboard_type"] == "status"
    assert payload["refresh_ms"] == 500
    assert "system_state" in payload["data"]
    assert validate_device_snapshot(payload) is None
