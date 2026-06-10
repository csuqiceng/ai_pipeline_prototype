from robot_modbus_lite.agent_runtime.feedback_learner import learn_memory_candidates_from_feedback
from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore


def test_feedback_learner_creates_asr_alias_candidate_from_explicit_correction(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="把 位置诶 识别为 位置A",
    )

    result = learn_memory_candidates_from_feedback(store)

    candidates = store.list_memories(status="candidate", kind="asr_alias")
    assert result.created_count == 1
    assert candidates[0]["key"] == "位置诶"
    assert candidates[0]["value"]["normalized"] == "位置A"
    assert candidates[0]["source"] == "feedback:record-1"


def test_feedback_learner_supports_equals_style_alias_feedback(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="位置诶=位置A",
    )

    result = learn_memory_candidates_from_feedback(store)

    assert result.created_count == 1
    assert store.list_memories(status="candidate", kind="asr_alias")[0]["key"] == "位置诶"


def test_feedback_learner_ignores_non_correction_feedback(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="回答没有按我的坐标",
    )

    result = learn_memory_candidates_from_feedback(store)

    assert result.created_count == 0
    assert store.list_memories(status="candidate") == []


def test_feedback_learner_does_not_duplicate_existing_candidate(tmp_path):
    store = AgentMemoryStore(tmp_path / "agent_memory.sqlite3")
    store.create_candidate(kind="asr_alias", key="位置诶", value={"normalized": "位置A"})
    store.record_feedback_vote(
        interaction_id="record-1",
        target_type="interaction",
        target_id="record-1",
        vote="down",
        note="把 位置诶 识别为 位置A",
    )

    result = learn_memory_candidates_from_feedback(store)

    assert result.created_count == 0
    assert len(store.list_memories(status="candidate", kind="asr_alias")) == 1
