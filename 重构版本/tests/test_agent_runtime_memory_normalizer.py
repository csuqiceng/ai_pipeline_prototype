from robot_modbus_lite.agent_runtime.memory_normalizer import apply_active_memory_to_text
from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore


def test_active_memory_alias_normalizes_text_and_records_audit(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="vote",
    )
    store.approve_memory(candidate["memory_id"], reviewer="engineer")

    result = apply_active_memory_to_text(store, "移动到位置诶")

    assert result.text == "移动到位置A"
    assert result.applied[0]["memory_id"] == candidate["memory_id"]
    assert store.list_audit_events(memory_id=candidate["memory_id"])[-1]["event"] == "memory_applied"


def test_candidate_memory_does_not_normalize_text(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})

    result = apply_active_memory_to_text(store, "移动到位置诶")

    assert result.text == "移动到位置诶"
    assert result.applied == ()


def test_active_memory_replacement_supports_longest_alias_first(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    short = store.create_candidate(kind="asr_alias", key="位置", value={"normalized": "点位"})
    long = store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})
    store.approve_memory(short["memory_id"], reviewer="engineer")
    store.approve_memory(long["memory_id"], reviewer="engineer")

    result = apply_active_memory_to_text(store, "移动到位置诶")

    assert result.text == "移动到位置A"
