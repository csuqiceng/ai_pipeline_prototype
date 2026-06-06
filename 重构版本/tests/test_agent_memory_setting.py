from robot_modbus_lite.agent.memory_setting import MemorySettingAgent
from robot_modbus_lite.atomic_memory import AtomicMemory


def test_memory_setting_updates_speed_without_command():
    memory = AtomicMemory()

    result = MemorySettingAgent(memory=memory).apply("小正，速度60%")

    assert result is not None
    assert result["kind"] == "memory_setting_answer"
    assert memory.current_speed == 60.0
    assert memory.current_acc == 60.0
    assert memory.current_dec == 60.0
    assert "速度=60.0%" in result["text"]
    assert result["generates_command"] is False


def test_memory_setting_updates_step_and_confirm_mode():
    memory = AtomicMemory()
    agent = MemorySettingAgent(memory=memory)

    step = agent.apply("小正，步长10毫米")
    mode = agent.apply("小正，专家模式")

    assert step is not None
    assert memory.current_step_mm == 10.0
    assert mode is not None
    assert memory.confirm_mode == "expert"


def test_memory_setting_does_not_intercept_motion_commands():
    memory = AtomicMemory()
    agent = MemorySettingAgent(memory=memory)

    assert agent.apply("小正，上升3毫米") is None
    assert agent.apply("让机械手走到X1000速度60%") is None
