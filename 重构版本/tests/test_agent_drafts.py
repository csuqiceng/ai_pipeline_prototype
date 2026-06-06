import pytest

from robot_modbus_lite.agent.drafts import CommandDraft, draft_to_query_record


def test_draft_to_query_record_deepcopies_params():
    params = {
        "target_x": 1000.0,
        "target_y": 0.0,
        "target_z": 0.0,
        "target_rx": 0.0,
        "target_ry": 0.0,
        "target_rz": 0.0,
        "spd_pct": 50.0,
        "acc_pct": 50.0,
        "dec_pct": 50.0,
        "stop_cmd": 0,
        "fuzzy_pos": 0,
        "fuzzy_spd": 0,
        "fuzzy_acc": 0,
        "fuzzy_dec": 0,
        "move_type": 0,
    }
    draft = CommandDraft(
        draft_id="abc123",
        func_id=108,
        intent="move_linear",
        params=params,
        param_sources={key: "specified" for key in params},
        raw_text="走到 X1000",
        confidence=1.0,
    )

    record = draft_to_query_record(draft)
    record.params["target_x"] = 2000.0

    assert draft.params["target_x"] == 1000.0
    assert record.query_key == "agent:abc123"


def test_draft_to_query_record_maps_position_increment_to_fuzzy_pos_for_execution_copy():
    params = {
        "target_x": 210.0,
        "target_y": 20.0,
        "target_z": 30.0,
        "target_rx": 1.0,
        "target_ry": 2.0,
        "target_rz": 3.0,
        "spd_pct": 40.0,
        "acc_pct": 45.0,
        "dec_pct": 50.0,
        "stop_cmd": 0,
        "fuzzy_pos": 0,
        "fuzzy_spd": 0,
        "fuzzy_acc": 0,
        "fuzzy_dec": 0,
        "move_type": 0,
        "position_increment": 1,
    }
    draft = CommandDraft(
        draft_id="inc1",
        func_id=108,
        intent="move_linear",
        params=params,
        param_sources={key: "specified" for key in params},
        raw_text="向左移动200",
        confidence=0.95,
    )

    record = draft_to_query_record(draft)

    assert record.params["fuzzy_pos"] == 1
    assert record.params["position_increment"] == 1
    assert draft.params["fuzzy_pos"] == 0


def test_draft_to_query_record_rejects_missing_required_keys():
    draft = CommandDraft(
        draft_id="missing",
        func_id=108,
        intent="move_linear",
        params={"target_x": 100.0},
        param_sources={"target_x": "specified"},
        raw_text="走到 X100",
        confidence=1.0,
    )

    with pytest.raises(ValueError, match="missing required params"):
        draft_to_query_record(draft)
