from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


class FakeDeepSeekClient:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def parse_json(self, prompt):
        self.prompts.append(prompt)
        return self.payload


def home_record() -> QueryRecord:
    return QueryRecord(
        query_key="home",
        func_num=108,
        description="回到 Home 位",
        keywords="home 回home",
        params={
            "target_x": 1400.0,
            "target_y": 0.0,
            "target_z": 1270.0,
            "target_rx": 0.0,
            "target_ry": 90.0,
            "target_rz": 0.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "move_type": 0,
        },
    )


def complex_flow_payload():
    return {
        "intent": "create_flow",
        "flowName": "打招呼",
        "positions": [
            {"name": "home", "pose": [1475, 0, 1545, 0, 0, 0]},
        ],
        "steps": [
            {"type": "move_position", "position": "home"},
            {"type": "gesture_repeat", "gesture": "小臂上下点头", "angleDeg": 15, "repeat": 3},
        ],
        "reason": "用户描述了一个需要创建的小流程",
    }


def test_complex_flow_with_unknown_gesture_asks_clarification_instead_of_home_execution():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=(), flow_phrase_aliases={})
    client = FakeDeepSeekClient(complex_flow_payload())
    adapter.set_deepseek_client(client)

    plan = adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再小臂上下点头15度，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=True,
    )

    assert plan.actions[0].action_type == "clarification"
    assert plan.actions[0].target == "gesture_mapping:小臂上下点头"
    assert "小臂上下点头" in plan.reason
    assert plan.atomic_records == {}
    assert plan.flow_draft["flow_name"] == "打招呼"
    assert client.prompts
    assert "create_flow" in client.prompts[0]


def test_complex_flow_default_alias_expands_nod_gesture_without_deepseek():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=())

    plan = adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再小臂上下点头15度，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=False,
    )

    assert plan.actions[0].action_type == "flow_draft"
    draft = plan.flow_draft
    assert len(draft["expanded_steps"]) == 7
    assert [step["func_id"] for step in draft["expanded_steps"]] == [108, 107, 107, 107, 107, 107, 107]
    assert [step["params"]["axis_no"] for step in draft["expanded_steps"][1:]] == [10, 10, 10, 10, 10, 10]
    assert [step["params"]["pos_val"] for step in draft["expanded_steps"][1:]] == [
        15.0,
        -15.0,
        15.0,
        -15.0,
        15.0,
        -15.0,
    ]


def test_complex_flow_default_alias_matches_short_nod_phrase_without_prefix_capture():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=())

    plan = adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再点头10度，2次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=False,
    )

    assert plan.actions[0].action_type == "flow_draft"
    draft = plan.flow_draft
    assert len(draft["expanded_steps"]) == 5
    assert draft["expanded_steps"][1]["description"] == "点头:Ry正转"
    assert draft["expanded_steps"][2]["description"] == "点头:Ry反转"


def test_complex_flow_with_configured_gesture_returns_non_executable_flow_draft():
    adapter = VoiceNlpAdapter(
        table={"home": home_record()},
        flow_names=(),
        flow_phrase_aliases={
            "小臂上下点头": [
                {"command": "Ry正转", "axis_no": 10, "direction": 1},
                {"command": "Ry反转", "axis_no": 10, "direction": -1},
            ]
        },
    )
    client = FakeDeepSeekClient(complex_flow_payload())
    adapter.set_deepseek_client(client)

    plan = adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再小臂上下点头15度，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=True,
    )

    assert plan.actions[0].action_type == "flow_draft"
    assert plan.actions[0].target == "打招呼"
    assert plan.atomic_records == {}
    assert plan.requires_confirmation is True
    draft = plan.flow_draft
    assert draft["flow_name"] == "打招呼"
    assert draft["positions"][0]["pose"] == [1475.0, 0.0, 1545.0, 0.0, 0.0, 0.0]
    assert [step["func_id"] for step in draft["expanded_steps"]] == [108, 107, 107, 107, 107, 107, 107]
    assert draft["expanded_steps"][1]["params"]["axis_no"] == 10
    assert draft["expanded_steps"][1]["params"]["pos_val"] == 15.0
    assert draft["expanded_steps"][2]["params"]["pos_val"] == -15.0


def test_complex_flow_local_rules_expand_relative_virtual_repeat_after_home():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=())

    plan = adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再上移50mm，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=False,
    )

    assert plan.actions[0].action_type == "flow_draft"
    draft = plan.flow_draft
    assert draft["flow_name"] == "打个招呼的小"
    assert [step["func_id"] for step in draft["expanded_steps"]] == [108, 107, 107, 107]
    assert [step["params"]["axis_no"] for step in draft["expanded_steps"][1:]] == [8, 8, 8]
    assert [step["params"]["pos_val"] for step in draft["expanded_steps"][1:]] == [50.0, 50.0, 50.0]


def test_complex_flow_with_leading_instruction_before_wake_word_uses_flow_parser():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=())

    plan = adapter.parse(
        "你直接编写一下，小正，打个招呼的小流程，让机械手先回到home位再上移50mm，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=False,
    )

    assert plan.actions[0].action_type == "flow_draft"
    assert plan.flow_draft["flow_name"] == "打个招呼的小"
    assert [step["func_id"] for step in plan.flow_draft["expanded_steps"]] == [108, 107, 107, 107]


def test_rest_phrase_returns_confirmable_atomic_motion_plan():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=())

    plan = adapter.parse("小正，休息了")

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.actions[0].target == "atomic:rest_pose"
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True
    record = plan.atomic_records["atomic:rest_pose"]
    assert record.func_num == 108
    assert record.params["target_x"] == 900.0
    assert record.params["target_z"] == 1000.0


def test_rest_phrase_without_wake_word_returns_atomic_motion_plan():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=())

    plan = adapter.parse("休息")

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.actions[0].target == "atomic:rest_pose"
    assert plan.requires_confirmation is True
    assert plan.requires_precheck is True


def test_complex_flow_clarification_followup_completes_pending_draft_with_ry_mapping():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=(), flow_phrase_aliases={})
    client = FakeDeepSeekClient(complex_flow_payload())
    adapter.set_deepseek_client(client)

    first = adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再小臂上下点头15度，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=True,
    )
    assert first.actions[0].action_type == "clarification"

    followup = adapter.parse("用 Ry 正反转")

    assert followup.actions[0].action_type == "flow_draft"
    assert followup.actions[0].target == "打招呼"
    assert "小臂上下点头" in adapter.flow_phrase_aliases
    draft = followup.flow_draft
    assert len(draft["expanded_steps"]) == 7
    assert draft["expanded_steps"][1]["params"]["axis_no"] == 10
    assert draft["expanded_steps"][1]["params"]["pos_val"] == 15.0
    assert draft["expanded_steps"][2]["params"]["axis_no"] == 10
    assert draft["expanded_steps"][2]["params"]["pos_val"] == -15.0


def test_complex_flow_clarification_followup_completes_pending_draft_with_joint_mapping():
    adapter = VoiceNlpAdapter(table={"home": home_record()}, flow_names=(), flow_phrase_aliases={})
    client = FakeDeepSeekClient(complex_flow_payload())
    adapter.set_deepseek_client(client)

    adapter.parse(
        "小正，打个招呼的小流程，让机械手先回到home位再小臂上下点头15度，3次。"
        "这样一个小流程，home位，xyzrxryrz，1475，0，1545，0，0，0",
        use_deepseek=True,
    )
    followup = adapter.parse("用 J2 正反转")

    assert followup.actions[0].action_type == "flow_draft"
    draft = followup.flow_draft
    assert draft["expanded_steps"][1]["func_id"] == 106
    assert draft["expanded_steps"][1]["params"]["axis_no"] == 1
    assert draft["expanded_steps"][1]["params"]["pos_val"] == 15.0
    assert draft["expanded_steps"][2]["params"]["pos_val"] == -15.0
