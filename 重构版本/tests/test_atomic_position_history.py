from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver
from robot_modbus_lite.models import QueryRecord


def resolve(text: str, memory: AtomicMemory | None = None):
    return AtomicResolver(memory or AtomicMemory()).resolve(AtomicParser().parse(text))


def record_from(text: str, memory: AtomicMemory | None = None) -> QueryRecord:
    result = resolve(text, memory)
    record = result.params["record"]
    assert isinstance(record, QueryRecord)
    return record


def test_memory_saves_queries_and_deletes_named_position():
    memory = AtomicMemory()
    pose = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    memory.save_position("A", pose)

    assert memory.get_position("A") == pose
    memory.delete_position("A")
    assert memory.get_position("A") is None


def test_resolver_moves_to_named_position():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    record = record_from("小正，移动到位置A", memory)

    assert record.func_num == 108
    assert record.params["target_x"] == 350.0
    assert record.params["target_y"] == 200.0
    assert record.params["target_z"] == 500.0
    assert record.params["target_ry"] == 90.0
    assert record.params["fuzzy_pos"] == 0


def test_resolver_reports_named_position_coordinate():
    memory = AtomicMemory()
    memory.save_position("A", (350.0, 200.0, 500.0, 0.0, 90.0, 0.0))

    result = resolve("小正，位置A的坐标是多少", memory)

    assert result.kind == "query"
    assert result.params["position_name"] == "A"
    assert result.params["pose"] == (350.0, 200.0, 500.0, 0.0, 90.0, 0.0)


def test_resolver_returns_save_position_request_without_pose_side_effect():
    memory = AtomicMemory()

    result = resolve("小正，保存当前位置为位置A", memory)

    assert result.kind == "memory"
    assert result.action_type == "memory"
    assert result.target == "position_save"
    assert result.params["position_name"] == "A"
    assert memory.get_position("A") is None


def test_repeat_last_reuses_last_command_record():
    memory = AtomicMemory()
    first = record_from("小正，上升3毫米", memory)

    repeated = record_from("小正，再走一次", memory)

    assert repeated.func_num == first.func_num
    assert repeated.params == first.params
    assert repeated.query_key.startswith("atomic:repeat:")
    assert memory.last_record.query_key == first.query_key


def test_step_back_returns_previous_pose_from_stack():
    memory = AtomicMemory()
    memory.push_position((10.0, 20.0, 30.0, 0.0, 90.0, 0.0))

    record = record_from("小正，返回", memory)

    assert record.func_num == 108
    assert record.params["target_x"] == 10.0
    assert record.params["target_y"] == 20.0
    assert record.params["target_z"] == 30.0
    assert record.params["fuzzy_pos"] == 0


def test_continue_forward_uses_memory_default_step():
    memory = AtomicMemory()
    memory.set_step_mm(12)

    record = record_from("小正，继续前进", memory)

    assert record.func_num == 107
    assert record.params["axis_no"] == 6
    assert record.params["pos_val"] == 12.0
    assert record.params["fuzzy_pos"] == 1
