from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore
from robot_modbus_lite.agent_tools import memory_tools
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionEntry, PositionRegistry


def test_memory_tools_create_candidate_returns_structured_result(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    result = memory_tools.create_memory_candidate(
        store,
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
        source="vote",
    )

    assert result.ok is True
    assert result.state == "memory_candidate_created"
    assert result.data["memory"]["status"] == "candidate"


def test_memory_tools_reject_forbidden_memory_candidate(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    result = memory_tools.create_memory_candidate(
        store,
        kind="safety_boundary",
        key="x_min",
        value={"value": -9999},
        source="vote",
    )

    assert result.ok is False
    assert result.state == "forbidden_memory_candidate"
    assert result.errors[0]["code"] == "FORBIDDEN_MEMORY_CANDIDATE"
    assert result.data["kind"] == "safety_boundary"
    assert store.list_memories() == []


def test_memory_tools_reject_execution_mode_memory_candidate(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    result = memory_tools.create_memory_candidate(
        store,
        kind="preference",
        key="专家模式",
        value={"normalized": "低风险命令自动执行"},
        source="vote",
    )

    assert result.ok is False
    assert result.state == "forbidden_memory_candidate"
    assert result.errors[0]["code"] == "FORBIDDEN_MEMORY_CANDIDATE"
    assert result.data["reason"] == "专家模式"
    assert store.list_memories() == []


def test_memory_tools_approve_and_lookup_active_memory(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = memory_tools.create_memory_candidate(
        store,
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    ).data["memory"]

    approved = memory_tools.approve_memory_candidate(store, candidate["memory_id"], reviewer="engineer")
    lookup = memory_tools.lookup_active_memory(store, kind="asr_alias", key="位置诶")

    assert approved.state == "memory_approved"
    assert lookup.ok is True
    assert lookup.data["memories"][0]["value"]["normalized"] == "位置A"


def test_memory_tools_reject_approval_of_forbidden_imported_candidate(tmp_path):
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

    result = memory_tools.approve_memory_candidate(store, "mem-imported-forbidden", reviewer="engineer")

    assert result.ok is False
    assert result.state == "forbidden_memory_candidate"
    assert result.errors[0]["code"] == "FORBIDDEN_MEMORY_CANDIDATE"
    assert store.list_memories()[0]["status"] == "candidate"


def test_memory_tools_rollback_memory_returns_structured_result(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = memory_tools.create_memory_candidate(
        store,
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    ).data["memory"]
    memory_tools.approve_memory_candidate(store, candidate["memory_id"], reviewer="engineer")

    result = memory_tools.rollback_memory(store, candidate["memory_id"], reviewer="engineer", reason="用户点踩")

    assert result.ok is True
    assert result.state == "memory_rolled_back"
    assert result.data["memory"]["status"] == "rolled_back"
    assert memory_tools.lookup_active_memory(store, kind="asr_alias", key="位置诶").data["memories"] == []


def test_memory_tools_query_memory_candidates_filters_by_kind(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    memory_tools.create_memory_candidate(
        store,
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    )
    memory_tools.create_memory_candidate(
        store,
        kind="flow_alias",
        key="点头",
        value={"flow_name": "点头流程"},
    )

    result = memory_tools.query_memory_candidates(store, kind="asr_alias")

    assert result.ok is True
    assert result.state == "memory_candidates_listed"
    assert len(result.data["memories"]) == 1
    assert result.data["memories"][0]["kind"] == "asr_alias"


def test_memory_tools_query_memory_review_includes_audit_events(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    candidate = memory_tools.create_memory_candidate(
        store,
        kind="asr_alias",
        key="位置诶",
        value={"normalized": "位置A"},
    ).data["memory"]
    memory_tools.approve_memory_candidate(store, candidate["memory_id"], reviewer="engineer")
    memory_tools.record_memory_applied(store, candidate["memory_id"], context={"interaction_id": "record-1"})

    result = memory_tools.query_memory_review(store, status="active", kind="asr_alias")

    assert result.ok is True
    assert result.state == "memory_review_listed"
    assert result.data["count"] == 1
    memory = result.data["memories"][0]
    assert memory["memory_id"] == candidate["memory_id"]
    assert memory["status"] == "active"
    assert [event["event"] for event in memory["audit_events"]] == [
        "candidate_created",
        "memory_approved",
        "memory_applied",
    ]


def test_memory_tools_record_feedback_vote(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")

    result = memory_tools.record_feedback_vote(
        store,
        interaction_id="record-1",
        target_type="answer",
        target_id="record-1",
        vote="down",
        note="回答没有按我的坐标",
    )

    assert result.state == "feedback_vote_recorded"
    assert result.data["vote"]["vote"] == "down"


def test_memory_tools_save_position_alias_uses_position_registry(tmp_path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))

    result = memory_tools.save_position_alias(
        registry,
        name="A",
        pose=(1, 2, 3, 4, 5, 6),
        created_by="operator",
    )

    saved = registry.get("A")
    assert result.ok is True
    assert result.state == "position_alias_saved"
    assert result.data["position"]["name"] == "A"
    assert result.data["generates_command"] is False
    assert saved is not None
    assert saved.pose == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_memory_tools_delete_position_alias_uses_position_registry_lock_rules(tmp_path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))
    registry.add(PositionEntry(name="HOME", pose=(1, 2, 3, 4, 5, 6), locked=True))

    result = memory_tools.delete_position_alias(registry, name="HOME")

    assert result.ok is False
    assert result.state == "position_alias_delete_failed"
    assert result.errors[0]["code"] == "POSITION_ALIAS_DELETE_FAILED"
    assert registry.get("HOME") is not None
