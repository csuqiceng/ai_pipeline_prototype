from fastapi.testclient import TestClient

from robot_modbus_lite.json_schema import validate_command_intent
from robot_modbus_lite.web_nlp_service import WebNlpService
from robot_modbus_lite.web_server import app


def test_web_nlp_service_parse_intent_returns_command_intent():
    service = WebNlpService()

    intent = service.parse_intent("急停 A1B2 急停")

    assert intent["msg_type"] == "command_intent"
    assert intent["is_emergency"] is True
    assert intent["priority"] == "high"
    assert validate_command_intent(intent) is None


def test_web_nlp_parse_v21_endpoint_returns_command_intent():
    client = TestClient(app)

    response = client.post("/api/nlp/parse/v21", json={"text": "急停 A1B2 急停"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["msg_type"] == "command_intent"
    assert payload["func_id"] == 104
    assert validate_command_intent(payload) is None
