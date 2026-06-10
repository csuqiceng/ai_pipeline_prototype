from types import SimpleNamespace

from robot_modbus_lite.agent_runtime.deepseek_tool_decider import DeepSeekToolDecider
from robot_modbus_lite.agent_runtime.operator_bridge_factory import (
    build_operator_bridge_cache_signature,
    build_operator_runtime_bridge,
    build_operator_tool_decider,
)
from robot_modbus_lite.agent_runtime.tool_calling_agent import ToolCallingAgentRuntime


class Host:
    def __init__(self):
        self.service = object()
        self._deepseek_client = object()
        self._atomic_memory = object()
        self.nlp_use_deepseek_check = SimpleNamespace(isChecked=lambda: True)
        self._operator_tool_calling_agent_runtime = None

    def _append_log(self, *_args):
        pass

    def _operator_restricted_agent_service(self):
        return "restricted"

    def _operator_execution_plan_service(self):
        return "execution-plan"

    def _operator_controller_snapshot_provider(self):
        return "snapshot"

    def _operator_safety_review_agent(self):
        return "safety"

    def _operator_dashboard_snapshot_dict(self, *, refresh):
        return {"refresh": refresh}

    def _operator_current_pose_tuple(self):
        return (1, 2, 3, 4, 5, 6)

    def _operator_confirmation_agent(self):
        return "confirmation"

    def _operator_now_seconds(self):
        return 10.0

    def _operator_restricted_agent_status_signature(self):
        return "status"

    def _operator_restricted_agent_safety_signature(self):
        return "safety"

    def _operator_flow_draft_parse_func(self):
        return lambda text: text

    def _position_registry(self):
        return "position-registry"

    def _operator_agent_llm_fallback_enabled(self):
        return bool(self.nlp_use_deepseek_check.isChecked()) and self._deepseek_client is not None


def test_build_operator_bridge_cache_signature_tracks_deepseek_client_and_overrides(tmp_path):
    host = Host()

    first = build_operator_bridge_cache_signature(host, tmp_path)
    host._deepseek_client = object()
    second = build_operator_bridge_cache_signature(host, tmp_path)
    host.__dict__["_operator_agent_memory_store"] = lambda: None
    third = build_operator_bridge_cache_signature(host, tmp_path)

    assert first != second
    assert second != third


def test_build_operator_tool_decider_uses_deepseek_client_when_enabled():
    host = Host()

    decider = build_operator_tool_decider(host)

    assert isinstance(decider, DeepSeekToolDecider)
    assert decider.client is host._deepseek_client


def test_build_operator_tool_decider_returns_none_when_disabled():
    host = Host()
    host.nlp_use_deepseek_check = SimpleNamespace(isChecked=lambda: False)

    assert build_operator_tool_decider(host) is None


def test_build_operator_runtime_bridge_wires_providers_and_runtime_override(tmp_path):
    host = Host()
    runtime = ToolCallingAgentRuntime(langchain_available=False)
    host.__dict__["_operator_tool_calling_agent_runtime"] = lambda: runtime

    bridge = build_operator_runtime_bridge(host, runtime_root=tmp_path)

    assert bridge.runtime_root == tmp_path
    assert bridge.restricted_service_provider() == "restricted"
    assert bridge.flow_service_provider() is host.service
    assert bridge.execution_plan_service_provider() == "execution-plan"
    assert bridge.controller_snapshot_provider() == "snapshot"
    assert bridge.atomic_memory_provider() is host._atomic_memory
    assert bridge.position_registry_provider() == "position-registry"
    assert bridge.runtime_snapshot_provider() == {"refresh": True}
    assert bridge.start_pose_provider() == (1, 2, 3, 4, 5, 6)
    assert bridge._tool_calling_runtime is runtime
