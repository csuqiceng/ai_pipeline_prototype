from pathlib import Path

from robot_modbus_lite.memory_params import MemoryManager


def test_memory_manager_tracks_action_specific_speeds(tmp_path: Path):
    manager = MemoryManager(tmp_path / "memory_params.json")

    manager.update_after_command("移动", {"spd_pct": 60})
    manager.update_after_command("点动", {"spd_pct": 20})
    manager.update_after_command("回零", {"spd_pct": 30})
    manager.update_after_command("标定", {"spd_pct": 10})

    loaded = MemoryManager(tmp_path / "memory_params.json")
    assert loaded.memory.last_motion_speed_pct == 60
    assert loaded.memory.last_jog_speed_pct == 20
    assert loaded.memory.last_home_speed_pct == 30
    assert loaded.memory.last_calib_speed_pct == 10
    assert loaded.memory.total_commands == 4
    assert loaded.memory.last_command_time


def test_memory_manager_ignores_invalid_speed():
    manager = MemoryManager()

    manager.update_after_command("移动", {"spd_pct": 150})

    assert manager.memory.last_motion_speed_pct == 50
    assert manager.memory.total_commands == 1
