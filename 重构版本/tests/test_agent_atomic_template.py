from robot_modbus_lite.agent.atomic_template import AtomicTemplateAgent
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.models import QueryRecord


def test_atomic_template_agent_builds_named_position_move_record():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    result = AtomicTemplateAgent(memory=memory).apply("小正，移动到位置A")

    assert result is not None
    assert result["kind"] == "atomic_template_action"
    assert result["action_type"] == "atomic_template"
    assert result["target"] == "atomic:position:A"
    record = result["record"]
    assert record.func_num == 108
    assert record.params["target_x"] == 350.0
    assert record.params["target_z"] == 500.0
    assert result["requires_confirmation"] is True


def test_atomic_template_query_table_position_lookup_ignores_flowdraft_name_collisions():
    table = {
        "flowdraft:home点头流程:06": QueryRecord(
            query_key="flowdraft:home点头流程:06",
            func_num=107,
            description="小臂上下点头:Ry正转",
            params={"axis_no": 10, "pos_val": 15.0},
        ),
        "home": QueryRecord(
            query_key="home",
            func_num=108,
            description="移动到home",
            keywords="home 位置home",
            params={"target_x": 1400.0, "target_y": 0.0, "target_z": 1270.0},
        ),
    }
    agent = AtomicTemplateAgent(
        memory=AtomicMemory(),
        template_lookup=AtomicTemplateAgent.query_table_position_template_lookup(table),
    )

    result = agent.apply("小正，移动到位置home")

    assert result is not None
    record = result["record"]
    assert record.query_key == "home"
    assert record.func_num == 108


def test_atomic_template_agent_builds_rest_pose_record():
    memory = AtomicMemory(default_rest_pose=(900.0, 0.0, 1000.0, 0.0, 0.0, 0.0))

    result = AtomicTemplateAgent(memory=memory).apply("小正，去休息")

    assert result is not None
    record = result["record"]
    assert record.func_num == 108
    assert record.query_key == "atomic:rest_pose"
    assert record.params["target_x"] == 900.0
    assert record.params["target_z"] == 1000.0


def test_atomic_template_agent_repeats_last_record_without_mutating_memory():
    memory = AtomicMemory()
    last_record = QueryRecord(
        query_key="atomic:virtual:8:1:3",
        func_num=108,
        description="原子函数：Func108相对位移/姿态",
        params={
            "target_x": 0.0,
            "target_y": 0.0,
            "target_z": 3.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "fuzzy_pos": 1,
            "position_increment": 1,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
        },
    )
    memory.remember_record(last_record)

    result = AtomicTemplateAgent(memory=memory).apply("小正，再走一次")

    assert result is not None
    record = result["record"]
    assert record.query_key == "atomic:repeat:atomic:virtual:8:1:3"
    assert record.func_num == 108
    assert record.params["target_z"] == 3.0
    assert memory.last_record is last_record


def test_atomic_template_agent_continues_last_direction_without_mutating_memory():
    memory = AtomicMemory()
    memory.record_direction(func_num=108, axis_no=6, direction=1, step=3.0)
    before_direction = memory.last_direction
    before_record = memory.last_record

    result = AtomicTemplateAgent(memory=memory).apply("小正，继续")

    assert result is not None
    record = result["record"]
    assert record.func_num == 108
    assert record.params["target_y"] == 3.0
    assert memory.last_direction == before_direction
    assert memory.last_record is before_record


def test_atomic_template_agent_returns_back_history_without_popping_stack():
    memory = AtomicMemory()
    memory.push_position((100.0, 200.0, 300.0, 0.0, 45.0, 0.0))
    before_stack = list(memory.position_stack)

    result = AtomicTemplateAgent(memory=memory).apply("小正，返回上一步")

    assert result is not None
    record = result["record"]
    assert record.query_key == "atomic:history:back"
    assert record.func_num == 108
    assert record.params["target_x"] == 100.0
    assert record.params["target_z"] == 300.0
    assert memory.position_stack == before_stack


def test_atomic_template_agent_does_not_intercept_position_memory():
    memory = AtomicMemory()
    agent = AtomicTemplateAgent(memory=memory)

    assert agent.apply("小正，保存当前位置为位置A") is None
    assert agent.apply("小正，删除位置A") is None
