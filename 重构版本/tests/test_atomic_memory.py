from robot_modbus_lite.atomic_memory import AtomicMemory


def test_atomic_memory_defaults_match_v11_doc():
    memory = AtomicMemory()

    assert memory.current_speed == 50.0
    assert memory.current_step_mm == 10.0
    assert memory.current_step_deg == 5.0
    assert memory.current_acc == 50.0
    assert memory.current_dec == 50.0
    assert memory.confirm_mode == "beginner"


def test_atomic_memory_clamps_speed_acc_dec_and_updates_steps():
    memory = AtomicMemory()

    memory.set_speed(200)
    memory.set_acc(0)
    memory.set_dec(180)
    memory.set_step_mm(3)
    memory.set_step_deg(2)

    assert memory.current_speed == 150.0
    assert memory.current_acc == 5.0
    assert memory.current_dec == 150.0
    assert memory.current_step_mm == 3.0
    assert memory.current_step_deg == 2.0


def test_atomic_memory_set_speed_syncs_default_acc_dec():
    memory = AtomicMemory()

    memory.set_speed(80)

    assert memory.current_speed == 80.0
    assert memory.current_acc == 80.0
    assert memory.current_dec == 80.0


def test_atomic_memory_updates_confirm_mode_with_validation():
    memory = AtomicMemory()

    memory.set_confirm_mode("expert")

    assert memory.confirm_mode == "expert"

    try:
        memory.set_confirm_mode("unsafe")
    except ValueError as exc:
        assert "unsupported confirm mode" in str(exc)
    else:
        raise AssertionError("expected unsupported confirm mode to raise")


def test_atomic_memory_round_trips_to_json_file(tmp_path):
    path = tmp_path / "atomic_state.json"
    memory = AtomicMemory()
    memory.set_speed(80)
    memory.set_step_mm(12)
    memory.set_confirm_mode("expert")
    memory.save_position("A", (1, 2, 3, 4, 5, 6))
    memory.push_position((10, 20, 30, 0, 90, 0))

    memory.save(path)
    loaded = AtomicMemory.load(path)

    assert loaded.current_speed == 80.0
    assert loaded.current_step_mm == 12.0
    assert loaded.confirm_mode == "expert"
    assert loaded.get_position("A") == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert loaded.pop_position() == (10.0, 20.0, 30.0, 0.0, 90.0, 0.0)


def test_atomic_memory_loads_defaults_from_missing_or_broken_file(tmp_path):
    missing = tmp_path / "missing.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")

    assert AtomicMemory.load(missing).current_speed == 50.0
    assert AtomicMemory.load(broken).confirm_mode == "beginner"
