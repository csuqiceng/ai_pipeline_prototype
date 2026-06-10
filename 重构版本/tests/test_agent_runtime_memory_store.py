import pytest
import sqlite3

from robot_modbus_lite.agent_runtime.memory_store import (
    AgentMemoryStore,
    ForbiddenMemoryCandidateError,
    default_agent_memory_path,
)


def test_default_agent_memory_path_uses_runtime_data_dir(tmp_path):
    path = default_agent_memory_path(tmp_path)

    assert path == tmp_path / "data" / "agent_memory.sqlite3"


def test_memory_store_creates_candidate_without_activation(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    candidate = store.create_candidate(
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="user_feedback",
        confidence=0.7,
    )

    assert candidate["status"] == "candidate"
    assert store.lookup_active(kind="asr_alias", key="位置诶") == []
    assert store.list_memories(status="candidate")[0]["value"]["normalized"] == "位置A"


def test_memory_store_approval_makes_memory_active_and_audited(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(kind="preference", key="default_speed", value={"spd_pct": 30})

    active = store.approve_memory(candidate["memory_id"], reviewer="engineer")

    assert active["status"] == "active"
    assert store.lookup_active(kind="preference", key="default_speed")[0]["value"]["spd_pct"] == 30
    audit_events = store.list_audit_events(memory_id=candidate["memory_id"])
    assert [event["event"] for event in audit_events] == ["candidate_created", "memory_approved"]


def test_memory_store_rejects_forbidden_execution_fact_candidate(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    with pytest.raises(ForbiddenMemoryCandidateError) as exc_info:
        store.create_candidate(
            kind="register_address",
            key="FUNC108_ADDR",
            value={"address": 40001},
            source="vote",
        )

    assert exc_info.value.reason == "register_address"
    assert store.list_memories() == []
    assert store.list_audit_events() == []


def test_memory_store_rejects_confirmation_mode_policy_candidate(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    with pytest.raises(ForbiddenMemoryCandidateError) as exc_info:
        store.create_candidate(
            kind="preference",
            key="专家模式",
            value={"normalized": "低风险命令自动执行"},
            source="vote",
        )

    assert exc_info.value.reason == "专家模式"
    assert store.list_memories() == []
    assert store.list_audit_events() == []


def test_memory_store_rejects_approval_of_imported_forbidden_candidate(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    now = store._now()
    with store._connect() as conn:
        conn.execute(
            """
            insert into memory_items
            (memory_id, kind, key, value_json, status, source, confidence, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mem-imported-forbidden",
                "preference",
                "跳过确认",
                '{"normalized": "低风险命令直接执行"}',
                "candidate",
                "legacy_import",
                0.9,
                now,
                now,
            ),
        )

    with pytest.raises(ForbiddenMemoryCandidateError) as exc_info:
        store.approve_memory("mem-imported-forbidden", reviewer="engineer")

    assert exc_info.value.reason in {"跳过确认", "直接执行"}
    assert store.list_memories()[0]["status"] == "candidate"
    assert store.lookup_active(kind="preference", key="跳过确认") == []


def test_memory_store_disable_active_memory_removes_from_lookup(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})
    store.approve_memory(candidate["memory_id"], reviewer="engineer")

    disabled = store.disable_memory(candidate["memory_id"], reviewer="engineer", reason="误识别样本不足")

    assert disabled["status"] == "disabled"
    assert store.lookup_active(kind="asr_alias", key="位置诶") == []
    assert store.list_audit_events(memory_id=candidate["memory_id"])[-1]["payload"]["reason"] == "误识别样本不足"


def test_memory_store_rolls_back_active_memory_with_audit_reason(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})
    store.approve_memory(candidate["memory_id"], reviewer="engineer")
    store.record_memory_applied(candidate["memory_id"], context={"interaction_id": "record-1"})

    rolled_back = store.rollback_memory(candidate["memory_id"], reviewer="engineer", reason="用户点踩")

    assert rolled_back["status"] == "rolled_back"
    assert store.lookup_active(kind="asr_alias", key="位置诶") == []
    events = store.list_audit_events(memory_id=candidate["memory_id"])
    assert [event["event"] for event in events] == [
        "candidate_created",
        "memory_approved",
        "memory_applied",
        "memory_rolled_back",
    ]
    assert events[-1]["payload"] == {"reviewer": "engineer", "reason": "用户点踩"}


def test_memory_store_records_feedback_vote(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    vote = store.record_feedback_vote(
        interaction_id="record-1",
        target_type="candidate_memory",
        target_id="memory-1",
        vote="up",
        note="这次识别正确",
    )

    votes = store.list_feedback_votes(interaction_id="record-1")
    assert votes == [vote]
    assert votes[0]["note"] == "这次识别正确"


def test_memory_store_schema_is_sqlite_file(tmp_path):
    db_path = tmp_path / "agent_memory.sqlite3"
    AgentMemoryStore(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'").fetchall()
        }

    assert {"memory_items", "feedback_votes", "memory_audit"} <= tables


def test_memory_store_records_schema_version_for_migrations(tmp_path):
    db_path = tmp_path / "agent_memory.sqlite3"
    AgentMemoryStore(db_path)

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("pragma user_version").fetchone()[0]

    assert version == 1
