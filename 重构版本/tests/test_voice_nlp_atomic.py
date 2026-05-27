from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.assistant_knowledge_base import AssistantKnowledgeBase, KnowledgeEntry
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.voice_nlp_adapter import VoiceNlpAdapter


class FakeChatDeepSeekClient:
    def __init__(self, text: str):
        self.text = text
        self.prompts = []

    def generate_chat(self, prompt: str, *, system_prompt: str | None = None) -> str:
        self.prompts.append((system_prompt, prompt))
        return self.text


class FakeStreamingChatDeepSeekClient:
    def __init__(self, chunks: tuple[str, ...]):
        self.chunks = chunks
        self.prompts = []

    def generate_chat_stream(self, prompt: str, *, system_prompt: str | None = None):
        self.prompts.append((system_prompt, prompt))
        yield from self.chunks

    def generate_chat(self, prompt: str, *, system_prompt: str | None = None) -> str:
        self.prompts.append((system_prompt, prompt))
        return "".join(self.chunks)


def make_adapter(memory: AtomicMemory | None = None) -> VoiceNlpAdapter:
    return VoiceNlpAdapter(table={}, flow_names=(), atomic_memory=memory or AtomicMemory())


def make_knowledge_base() -> AssistantKnowledgeBase:
    return AssistantKnowledgeBase(
        entries=(
            KnowledgeEntry(
                entry_id="identity",
                category="identity",
                keywords=("你是谁", "能做什么"),
                content="我是机械手自然语言交互系统的问答助手，可以查询状态、解释功能和协助创建流程草案。",
                priority="high",
                source="test",
            ),
        )
    )


def test_voice_nlp_adapter_parses_atomic_virtual_command():
    plan = make_adapter().parse("小正，上升3毫米")

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.semantic_level == 3
    assert plan.requires_confirmation is True
    record = plan.atomic_records[plan.actions[0].target]
    assert isinstance(record, QueryRecord)
    assert record.func_num == 107
    assert record.params["axis_no"] == 8
    assert record.params["pos_val"] == 3.0


def test_voice_nlp_adapter_accepts_configured_asr_wake_alias():
    plan = make_adapter().parse("小郭，上升3毫米")

    assert plan.actions[0].action_type == "atomic_template"
    assert plan.requires_confirmation is True
    record = plan.atomic_records[plan.actions[0].target]
    assert record.func_num == 107


def test_voice_nlp_adapter_keeps_dashboard_query_before_atomic():
    plan = make_adapter().parse("小正，查一下安全范围")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "safety_boundary"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_marks_atomic_capability_query():
    plan = make_adapter().parse("小正，现在支持哪些原子命令")

    assert plan.actions[0].action_type == "query"
    assert plan.actions[0].target == "atomic_capabilities"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_answers_non_control_identity_question_with_deepseek_chat():
    adapter = VoiceNlpAdapter(
        table={},
        flow_names=(),
        knowledge_base=AssistantKnowledgeBase(
            entries=(
                KnowledgeEntry(
                    entry_id="feature_context",
                    category="usage",
                    keywords=("资料",),
                    content="系统资料包含功能、流程和安全边界。",
                    priority="low",
                    source="test",
                ),
            )
        ),
    )
    client = FakeChatDeepSeekClient("我是机械手自然语言交互助手，可以解释功能、查询状态并协助创建流程草案。")
    adapter.set_deepseek_client(client)

    plan = adapter.parse("我想看下功能资料", use_deepseek=True)

    assert plan.actions[0].action_type == "chat"
    assert plan.source == "deepseek_chat"
    assert plan.reason == "我是机械手自然语言交互助手，可以解释功能、查询状态并协助创建流程草案。"
    assert plan.requires_precheck is False
    assert plan.requires_confirmation is False
    assert client.prompts
    assert "本地资料" in client.prompts[0][1]
    assert "本地知识库命中资料" in client.prompts[0][1]
    assert "系统资料包含功能" in client.prompts[0][1]


def test_voice_nlp_adapter_answers_known_question_from_local_knowledge_without_deepseek():
    adapter = VoiceNlpAdapter(table={}, flow_names=(), knowledge_base=make_knowledge_base())

    plan = adapter.parse("你是谁", use_deepseek=False)

    assert plan.actions[0].action_type == "chat"
    assert plan.source == "knowledge_base"
    assert "问答助手" in plan.reason


def test_voice_nlp_adapter_prefers_high_confidence_knowledge_before_deepseek_chat():
    adapter = VoiceNlpAdapter(table={}, flow_names=(), knowledge_base=make_knowledge_base())
    client = FakeChatDeepSeekClient("DeepSeek回答")
    adapter.set_deepseek_client(client)

    plan = adapter.parse("你是谁", use_deepseek=True)

    assert plan.actions[0].action_type == "chat"
    assert plan.source == "knowledge_base"
    assert "问答助手" in plan.reason
    assert client.prompts == []


def test_voice_nlp_adapter_injects_runtime_context_into_deepseek_chat_prompt():
    adapter = make_adapter()
    adapter.set_runtime_context_provider(lambda: "当前待确认流程草案：打招呼，步骤：移动到home。")
    client = FakeChatDeepSeekClient("当前草案会先移动到home。")
    adapter.set_deepseek_client(client)

    plan = adapter.parse("这个流程是什么样的", use_deepseek=True)

    assert plan.actions[0].action_type == "chat"
    assert "当前待确认流程草案：打招呼" in client.prompts[0][1]


def test_voice_nlp_adapter_answers_wake_word_capability_question_with_deepseek_chat():
    adapter = make_adapter()
    client = FakeChatDeepSeekClient("我能处理位置示教、流程草案、状态查询和安全确认。")
    adapter.set_deepseek_client(client)

    plan = adapter.parse("小正，你能做什么", use_deepseek=True)

    assert plan.actions[0].action_type == "chat"
    assert plan.source == "knowledge_base"
    assert "当前系统支持" in plan.reason
    assert client.prompts == []


def test_voice_nlp_adapter_streams_deepseek_chat_deltas_without_thinking_text():
    adapter = VoiceNlpAdapter(table={}, flow_names=(), knowledge_base=AssistantKnowledgeBase(entries=()))
    client = FakeStreamingChatDeepSeekClient(("<think>先判断身份</think>", "我是", "问答助手。"))
    adapter.set_deepseek_client(client)
    deltas = []

    plan = adapter.parse("你是谁", use_deepseek=True, chat_delta_callback=deltas.append)

    assert plan.actions[0].action_type == "chat"
    assert plan.reason == "我是问答助手。"
    assert deltas == ["我是", "问答助手。"]
    assert client.prompts


def test_voice_nlp_adapter_treats_command_explanation_without_wake_word_as_chat():
    adapter = make_adapter()
    client = FakeStreamingChatDeepSeekClient(("位置A的命令可以说：小正，去位置A。",))
    adapter.set_deepseek_client(client)

    plan = adapter.parse("我想看下位置A的命令", use_deepseek=True)

    assert plan.actions[0].action_type == "chat"
    assert plan.reason == "位置A的命令可以说：小正，去位置A。"


def test_voice_nlp_adapter_treats_position_parameter_question_as_chat():
    adapter = make_adapter()
    client = FakeStreamingChatDeepSeekClient(("位置A参数可在位置库中查看，包括 XYZ/RX/RY/RZ。",))
    adapter.set_deepseek_client(client)

    plan = adapter.parse("位置A的参数是什么样的", use_deepseek=True)

    assert plan.actions[0].action_type == "chat"
    assert "位置A参数" in plan.reason


def test_voice_nlp_adapter_updates_atomic_memory():
    memory = AtomicMemory()
    plan = make_adapter(memory).parse("小正，速度60%")

    assert plan.actions[0].action_type == "memory"
    assert memory.current_speed == 60.0
    assert plan.requires_confirmation is False


def test_voice_nlp_adapter_ignores_unsupported_atomic_and_falls_back_unknown():
    plan = make_adapter().parse("小正，画个圆")

    assert plan.actions[0].action_type == "unknown"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_parses_named_position_move():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    plan = make_adapter(memory).parse("小正，移动到位置A")

    assert plan.actions[0].action_type == "atomic_template"
    record = plan.atomic_records[plan.actions[0].target]
    assert record.func_num == 108
    assert record.params["target_x"] == 350.0
    assert record.params["target_z"] == 500.0


def test_voice_nlp_adapter_parses_save_position_request():
    plan = make_adapter().parse("小正，保存当前位置为位置A")

    assert plan.actions[0].action_type == "memory"
    assert plan.actions[0].target == "position_save:A"
    assert plan.requires_confirmation is False


def test_voice_nlp_adapter_repeats_last_atomic_command_from_memory():
    memory = AtomicMemory()
    adapter = make_adapter(memory)
    first = adapter.parse("小正，上升3毫米")

    repeated = adapter.parse("小正，再走一次")

    assert first.actions[0].action_type == "atomic_template"
    assert repeated.actions[0].action_type == "atomic_template"
    record = repeated.atomic_records[repeated.actions[0].target]
    assert record.func_num == 107
    assert record.params["axis_no"] == 8
    assert record.params["pos_val"] == 3.0


def test_voice_nlp_adapter_continues_last_direction_from_memory():
    memory = AtomicMemory()
    adapter = make_adapter(memory)
    adapter.parse("小正，前进3毫米")

    continued = adapter.parse("小正，继续")

    assert continued.actions[0].action_type == "atomic_template"
    record = continued.atomic_records[continued.actions[0].target]
    assert record.func_num == 107
    assert record.params["axis_no"] == 6
    assert record.params["pos_val"] == 3.0


def test_voice_nlp_adapter_parses_multiple_atomic_templates_in_order():
    plan = make_adapter().parse("小正，上升3毫米然后IO1开")

    assert [action.action_type for action in plan.actions] == ["atomic_template", "atomic_template"]
    assert len(plan.atomic_records) == 2
    first = plan.atomic_records[plan.actions[0].target]
    second = plan.atomic_records[plan.actions[1].target]
    assert first.func_num == 107
    assert first.params["axis_no"] == 8
    assert first.params["pos_val"] == 3.0
    assert second.func_num == 120
    assert second.params["io_no"] == 1
    assert second.params["io_action"] == 1


def test_voice_nlp_adapter_parses_three_step_atomic_sequence():
    plan = make_adapter().parse("小正，J1转到45度然后等待2秒然后IO1关")

    assert [action.action_type for action in plan.actions] == [
        "atomic_template",
        "atomic_template",
        "atomic_template",
    ]
    records = [plan.atomic_records[action.target] for action in plan.actions]
    assert [record.func_num for record in records] == [106, 110, 120]
    assert records[0].params["pos_val"] == 45.0
    assert records[1].params["delay_sec"] == 2.0
    assert records[2].params["io_action"] == 0


def test_voice_nlp_adapter_rejects_partial_atomic_sequence():
    plan = make_adapter().parse("小正，上升3毫米然后画个圆")

    assert plan.actions[0].action_type == "unknown"
    assert plan.atomic_records == {}


def test_voice_nlp_adapter_rejects_unsupported_complex_atomic_commands():
    adapter = make_adapter()

    loop_plan = adapter.parse("小正，上升3毫米重复3次")
    parallel_plan = adapter.parse("小正，同时上升3毫米并且IO1开")
    conditional_plan = adapter.parse("小正，如果没有报警就上升3毫米")

    for plan in (loop_plan, parallel_plan, conditional_plan):
        assert plan.actions[0].action_type == "unknown"
        assert plan.atomic_records == {}
        assert "暂不支持" in plan.reason


def test_voice_nlp_adapter_rejects_func11_continuous_interpolation_commands():
    adapter = make_adapter()

    path_plan = adapter.parse("小正，连续路径经过位置A和位置B")
    interpolation_plan = adapter.parse("小正，插补到X100Y200Z300")
    trajectory_plan = adapter.parse("小正，执行轨迹A")

    for plan in (path_plan, interpolation_plan, trajectory_plan):
        assert plan.actions[0].action_type == "unknown"
        assert plan.atomic_records == {}
        assert "Func11" in plan.reason
        assert "暂不支持" in plan.reason


def test_atomic_confirm_mode_beginner_confirms_all_atomic_templates():
    memory = AtomicMemory(confirm_mode="beginner")

    delay_plan = make_adapter(memory).parse("小正，等待2秒")

    assert delay_plan.actions[0].action_type == "atomic_template"
    assert delay_plan.requires_confirmation is True


def test_atomic_confirm_mode_skilled_only_confirms_high_risk_motion():
    memory = AtomicMemory(confirm_mode="skilled")
    adapter = make_adapter(memory)

    delay_plan = adapter.parse("小正，等待2秒")
    io_plan = adapter.parse("小正，IO1开")
    motion_plan = adapter.parse("小正，上升3毫米")

    assert delay_plan.requires_confirmation is False
    assert io_plan.requires_confirmation is False
    assert motion_plan.requires_confirmation is True


def test_atomic_confirm_mode_expert_still_confirms_high_risk_motion():
    memory = AtomicMemory(confirm_mode="expert")
    adapter = make_adapter(memory)

    io_plan = adapter.parse("小正，IO1开")
    motion_plan = adapter.parse("小正，上升3毫米")

    assert io_plan.requires_confirmation is False
    assert motion_plan.requires_confirmation is True


def test_voice_nlp_uses_homophone_normalization_without_skipping_wake_gate():
    plan = make_adapter().parse("保村当前位置为位置A")

    assert plan.actions[0].action_type in {"unknown", "chat"}
    assert plan.atomic_records == {}


def test_voice_nlp_uses_homophone_normalization_with_wake_word():
    plan = make_adapter().parse("小正，保村当前位置为位置A")

    assert plan.actions[0].action_type == "memory"
    assert plan.actions[0].target == "position_save:A"
