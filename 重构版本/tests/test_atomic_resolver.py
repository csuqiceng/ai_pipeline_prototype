from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.atomic_resolver import AtomicResolver
from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.permission_service import PermissionService
from robot_modbus_lite.position_registry import PositionEntry, PositionRegistry


def resolve(text: str, memory: AtomicMemory | None = None):
    return AtomicResolver(memory or AtomicMemory()).resolve(AtomicParser().parse(text))


def record_from(text: str, memory: AtomicMemory | None = None) -> QueryRecord:
    result = resolve(text, memory)
    record = result.params["record"]
    assert isinstance(record, QueryRecord)
    return record


def test_resolver_maps_virtual_nudge_to_func108_relative_defaults():
    record = record_from("小正，上升3毫米")

    assert record.func_num == 108
    assert record.params["target_z"] == 3.0
    assert record.params["target_x"] == 0.0
    assert record.params["spd_pct"] == 50.0
    assert record.params["acc_pct"] == 50.0
    assert record.params["dec_pct"] == 50.0
    assert record.params["fuzzy_pos"] == 1
    assert record.params["position_increment"] == 1


def test_resolver_rejects_joint_absolute_current_policy():
    result = resolve("小正，J1转到45度30%速度")

    assert result.kind == "unsupported"
    assert "当前阶段不支持 Func106" in result.reason


def test_resolver_maps_delay_and_io_to_func110_and_120():
    delay = record_from("小正，等待2秒")
    io_on = record_from("小正，IO1开")

    assert delay.func_num == 110
    assert delay.params["delay_sec"] == 2.0
    assert io_on.func_num == 120
    assert io_on.params["io_no"] == 1
    assert io_on.params["io_action"] == 1


def test_resolver_updates_memory_without_record():
    memory = AtomicMemory()
    result = resolve("小正，速度60%", memory)

    assert result.kind == "memory"
    assert "record" not in result.params
    assert memory.current_speed == 60.0
    assert memory.current_acc == 60.0
    assert memory.current_dec == 60.0


def test_resolver_uses_speed_for_default_acc_dec_when_speed_is_given_inline():
    record = record_from("小正，30%速度上升3毫米")

    assert record.params["spd_pct"] == 30.0
    assert record.params["acc_pct"] == 30.0
    assert record.params["dec_pct"] == 30.0


def test_resolver_records_last_direction_for_virtual_jog():
    memory = AtomicMemory()

    record = record_from("小正，前进3毫米", memory)

    assert record.func_num == 108
    assert record.params["target_y"] == 3.0
    assert memory.last_direction == (108.0, 6.0, 1.0)
    assert memory.last_step == 3.0


def test_resolver_continues_last_direction_with_last_step():
    memory = AtomicMemory()
    record_from("小正，前进3毫米", memory)

    record = record_from("小正，继续", memory)

    assert record.func_num == 108
    assert record.params["target_y"] == 3.0


def test_resolver_rejects_continuing_last_joint_direction_with_explicit_step():
    memory = AtomicMemory()
    memory.record_direction(func_num=106, axis_no=1, direction=-1, step=15.0)

    result = resolve("小正，继续5度", memory)

    assert result.kind == "unsupported"
    assert "当前阶段不支持继续 Func106" in result.reason


def test_resolver_rejects_unknown_atomic_command():
    result = resolve("小正，画个圆")

    assert result.kind == "unsupported"
    assert result.action_type == "unknown"


def test_resolver_keeps_relative_func108_motion_high_risk():
    memory = AtomicMemory(confirm_mode="skilled")

    result = resolve("小正，10%速度上升1毫米", memory)
    record = result.params["record"]

    assert result.risk_level == "high"
    assert result.requires_confirmation is True
    assert record.params["atomic_risk_level"] == "high"
    assert "完整预检确认" in record.params["atomic_risk_reason"]


def test_resolver_keeps_fast_or_absolute_motion_high_risk():
    memory = AtomicMemory(confirm_mode="skilled")

    fast = resolve("小正，100%速度上升1毫米", memory)
    joint = resolve("小正，J1转到45度", memory)

    assert fast.risk_level == "high"
    assert fast.requires_confirmation is True
    assert joint.kind == "unsupported"


def test_resolver_prefers_structured_position_registry(tmp_path):
    registry = PositionRegistry(tmp_path / "positions.json", permission=PermissionService("engineer"))
    registry.add(PositionEntry(name="A", pose=(1, 2, 3, 4, 5, 6), spd=35, move_type=1))
    memory = AtomicMemory()
    memory.position_registry = registry

    result = resolve("小正，移动到位置A", memory)
    record = result.params["record"]

    assert result.kind == "template"
    assert isinstance(record, QueryRecord)
    assert record.float_param("target_x") == 1
    assert record.float_param("spd_pct") == 35
    assert record.int_param("move_type") == 1


def test_resolver_maps_rest_phrase_to_default_rest_pose():
    result = resolve("小正，休息了")
    record = result.params["record"]

    assert result.kind == "template"
    assert result.requires_confirmation is True
    assert isinstance(record, QueryRecord)
    assert record.query_key == "atomic:rest_pose"
    assert record.func_num == 108
    assert record.params["target_x"] == 900.0
    assert record.params["target_y"] == 0.0
    assert record.params["target_z"] == 1000.0
    assert record.params["target_rx"] == 0.0
    assert record.params["target_ry"] == 0.0
    assert record.params["target_rz"] == 0.0
    assert record.params["fuzzy_pos"] == 0


def test_resolver_allows_configured_rest_pose_from_memory():
    memory = AtomicMemory()
    memory.default_rest_pose = (901.0, 2.0, 1003.0, 4.0, 5.0, 6.0)

    record = record_from("小正，回休息姿态", memory)

    assert record.params["target_x"] == 901.0
    assert record.params["target_y"] == 2.0
    assert record.params["target_z"] == 1003.0
    assert record.params["target_rx"] == 4.0
    assert record.params["target_ry"] == 5.0
    assert record.params["target_rz"] == 6.0
