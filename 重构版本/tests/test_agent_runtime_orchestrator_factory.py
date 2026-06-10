from types import SimpleNamespace

from robot_modbus_lite.agent_runtime.orchestrator_factory import (
    build_legacy_orchestrator_cache_signature,
    build_legacy_orchestrator,
)


class Host:
    def __init__(self):
        self._deepseek_client = object()
        self._atomic_memory = object()
        self._operator_agent_flow_draft_parse = object()
        self._operator_agent_registered_flow_parse = object()
        self.axis_ranges = SimpleNamespace(restricted_agent_enabled=True)
        self.position_lookup = object()

    def _operator_restricted_agent_enabled(self):
        return bool(self.axis_ranges.restricted_agent_enabled)

    def _operator_restricted_agent_service(self):
        return "restricted-service"

    def _operator_agent_llm_fallback_enabled(self):
        return True

    def _operator_agent_position_lookup(self, *args, **kwargs):
        return None

    def _operator_agent_memory_setting_agent(self, agent_cls):
        return ("memory", agent_cls)

    def _operator_agent_atomic_template_agent(self, agent_cls):
        return ("atomic", agent_cls)

    def _operator_agent_flow_draft_agent(self, agent_cls):
        return ("flow", agent_cls)

    def _operator_agent_registered_flow_agent(self, agent_cls):
        return ("registered", agent_cls)

    def _operator_agent_llm_fallback_agent(self, agent_cls):
        return ("llm", agent_cls)


def test_build_legacy_orchestrator_cache_signature_tracks_stateful_dependencies():
    host = Host()

    first = build_legacy_orchestrator_cache_signature(host)
    host._deepseek_client = object()
    second = build_legacy_orchestrator_cache_signature(host)
    host.axis_ranges.restricted_agent_enabled = False
    third = build_legacy_orchestrator_cache_signature(host)

    assert first != second
    assert second != third


def test_build_legacy_orchestrator_wires_agents_and_restricted_service(monkeypatch):
    captured = []

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr("robot_modbus_lite.agent.orchestrator.AgentOrchestrator", FakeOrchestrator)

    host = Host()
    orchestrator = build_legacy_orchestrator(host)

    assert isinstance(orchestrator, FakeOrchestrator)
    kwargs = captured[0]
    assert kwargs["restricted_service"] == "restricted-service"
    assert kwargs["position_query_agent"]._lookup == host._operator_agent_position_lookup
    assert kwargs["memory_setting_agent"][0] == "memory"
    assert kwargs["atomic_template_agent"][0] == "atomic"
    assert kwargs["flow_draft_agent"][0] == "flow"
    assert kwargs["registered_flow_agent"][0] == "registered"
    assert kwargs["llm_fallback_agent"][0] == "llm"
    assert kwargs["llm_fallback_enabled"] is True


def test_build_legacy_orchestrator_omits_restricted_service_when_disabled(monkeypatch):
    captured = []

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr("robot_modbus_lite.agent.orchestrator.AgentOrchestrator", FakeOrchestrator)

    host = Host()
    host.axis_ranges.restricted_agent_enabled = False

    build_legacy_orchestrator(host)

    assert captured[0]["restricted_service"] is None
