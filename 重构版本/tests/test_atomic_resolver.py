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


def test_resolver_maps_virtual_nudge_to_func107_with_defaults():
    record = record_from("小正，上升3毫米")

    assert record.func_num == 107
    assert record.params["axis_no"] == 8
    assert record.params["pos_val"] == 3.0
    assert record.params["spd_pct"] == 50.0
    assert record.params["acc_pct"] == 100.0
    assert record.params["dec_pct"] == 100.0
    assert record.params["fuzzy_pos"] == 1


def test_resolver_maps_joint_absolute_to_func106():
    record = record_from("小正，J1转到45度30%速度")

    assert record.func_num == 106
    assert record.params["axis_no"] == 0
    assert record.params["pos_val"] == 45.0
    assert record.params["spd_pct"] == 30.0
    assert record.params["fuzzy_pos"] == 0


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


def test_resolver_rejects_unknown_atomic_command():
    result = resolve("小正，画个圆")

    assert result.kind == "unsupported"
    assert result.action_type == "unknown"


def test_resolver_grades_small_low_speed_virtual_motion_as_low_risk():
    memory = AtomicMemory(confirm_mode="skilled")

    result = resolve("小正，10%速度上升1毫米", memory)
    record = result.params["record"]

    assert result.risk_level == "low"
    assert result.requires_confirmation is False
    assert record.params["atomic_risk_level"] == "low"
    assert "小步长低速" in record.params["atomic_risk_reason"]


def test_resolver_keeps_fast_or_absolute_motion_high_risk():
    memory = AtomicMemory(confirm_mode="skilled")

    fast = resolve("小正，100%速度上升1毫米", memory)
    absolute = resolve("小正，J1转到45度", memory)

    assert fast.risk_level == "high"
    assert fast.requires_confirmation is True
    assert absolute.risk_level == "high"
    assert absolute.requires_confirmation is True
