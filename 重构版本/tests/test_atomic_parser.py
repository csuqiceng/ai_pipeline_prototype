from robot_modbus_lite.atomic_parser import AtomicParser


def test_parser_blocks_production_command_without_wake_word():
    parsed = AtomicParser().classify("上升3毫米")

    assert parsed.kind == "chat"
    assert parsed.params["command_text"] == "上升3毫米"


def test_parser_accepts_configured_asr_wake_word_aliases():
    parsed = AtomicParser().parse("小镇，移动到位置A")

    assert parsed.family == "position"
    assert parsed.name == "move:A"


def test_parser_accepts_xiaobing_wake_word_alias():
    parsed = AtomicParser().parse("小兵，上升3毫米")

    assert parsed.family == "virtual"
    assert parsed.axis_no == 8
    assert parsed.step == 3.0


def test_parser_warns_single_emergency_word_without_code():
    parsed = AtomicParser().classify("急停")

    assert parsed.kind == "warning"
    assert "标准格式" in parsed.reason


def test_parser_extracts_virtual_axis_full_params():
    parsed = AtomicParser().parse("小正，20%速度上升3毫米加速度50%减速度30%")

    assert parsed.family == "virtual"
    assert parsed.axis_no == 8
    assert parsed.direction == 1
    assert parsed.step == 3.0
    assert parsed.spd_pct == 20.0
    assert parsed.acc_pct == 50.0
    assert parsed.dec_pct == 30.0


def test_parser_extracts_joint_absolute_target():
    parsed = AtomicParser().parse("小正，J1转到45度30%速度")

    assert parsed.family == "joint"
    assert parsed.axis_no == 0
    assert parsed.target == 45.0
    assert parsed.fuzzy_pos == 0
    assert parsed.spd_pct == 30.0


def test_parser_extracts_delay_and_io_commands():
    delay = AtomicParser().parse("小正，等待2秒")
    io_on = AtomicParser().parse("小正，IO1开")

    assert delay.family == "delay"
    assert delay.delay_sec == 2.0
    assert io_on.family == "io"
    assert io_on.io_no == 1
    assert io_on.io_action == 1


def test_parser_extracts_memory_commands():
    speed = AtomicParser().parse("小正，速度60%")
    step = AtomicParser().parse("小正，步长10毫米")

    assert speed.family == "memory"
    assert speed.name == "speed"
    assert speed.spd_pct == 60.0
    assert step.family == "memory"
    assert step.name == "step_mm"
    assert step.step == 10.0
