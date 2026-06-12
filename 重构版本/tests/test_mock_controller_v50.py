import time

from mock_controller.client import MockZMotionVrClient
from robot_modbus_lite.models import SixAxisCommand, SixAxisStatus, VrReadRequest


def _wait_for_modbus_float(client: MockZMotionVrClient, start_vr: int, expected: float) -> float:
    deadline = time.monotonic() + 0.5
    value = 0.0
    while time.monotonic() < deadline:
        value = client.read_modbus_float(VrReadRequest(start_vr, 1))[0]
        if value == expected:
            return value
        time.sleep(0.01)
    return value


def test_mock_controller_sets_ieee312_zero_after_six_axis_command_acceptance():
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    command = SixAxisCommand(
        func_num=108,
        target_x=10.0,
        target_y=0.0,
        target_z=10.0,
        target_rx=0.0,
        target_ry=0.0,
        target_rz=0.0,
        spd_pct=20.0,
        acc_pct=20.0,
        dec_pct=20.0,
    )

    for request in command.to_func_writes():
        client.write_modbus_float(request)
    client.write_modbus_float(command.to_trigger_write())

    assert client.read_modbus_float(VrReadRequest(312, 1))[0] == 0.0


def test_func112_writes_use_linear_parameter_layout_with_func112_id():
    command = SixAxisCommand(
        func_num=112,
        target_x=1000.0,
        target_y=200.0,
        target_z=800.0,
        target_rx=0.0,
        target_ry=45.0,
        target_rz=0.0,
        spd_pct=60.0,
        acc_pct=50.0,
        dec_pct=50.0,
        fuzzy_pos=0,
        move_type=0,
    )

    writes = command.to_func_writes()

    assert writes[0].start_vr == 0
    assert writes[0].values == (112.0,)
    assert writes[1].start_vr == 2
    assert writes[1].values == (1000.0,)
    assert writes[6].start_vr == 12
    assert writes[6].values == (0.0,)
    assert writes[15].start_vr == 30
    assert writes[15].values == (0.0,)


def test_func8_and_102_write_use_linear_parameter_layout_with_their_func_id():
    for func_id in (8, 102):
        command = SixAxisCommand(
            func_num=func_id,
            target_x=1000.0,
            target_y=200.0,
            target_z=800.0,
            target_rx=0.0,
            target_ry=45.0,
            target_rz=0.0,
            spd_pct=60.0,
            acc_pct=50.0,
            dec_pct=50.0,
        )

        writes = command.to_func_writes()

        assert writes[0].start_vr == 0
        assert writes[0].values == (float(func_id),)
        assert writes[1].start_vr == 2
        assert writes[1].values == (1000.0,)
        assert writes[8].start_vr == 16
        assert writes[8].values == (50.0,)


def test_func109_delay_uses_zbasic_hmi_parameter_slot():
    command = SixAxisCommand(func_num=109, delay_sec=1.5)

    writes = command.to_func_writes()

    assert writes[1].start_vr == 4
    assert writes[1].values == (1.5,)
    assert (284, 1.5) in command.expected_echo_points()
    assert (282, 1.5) not in command.expected_echo_points()


def test_func110_delay_uses_zbasic_hmi_parameter_slot():
    command = SixAxisCommand(func_num=110, delay_sec=2.5)

    writes = command.to_func_writes()

    assert writes[1].start_vr == 6
    assert writes[1].values == (2.5,)
    assert (286, 2.5) in command.expected_echo_points()
    assert (282, 2.5) not in command.expected_echo_points()


def test_status_parses_func112_zbasic_mask():
    assert SixAxisStatus(0x00010000, 112).is_executing
    assert SixAxisStatus(0x00020000, 112).is_complete
    assert SixAxisStatus(0x00030000, 112).has_error


def test_status_parses_func8_and_102_zbasic_mask():
    for func_id in (8, 102):
        assert SixAxisStatus(0x00400000, func_id).is_executing
        assert SixAxisStatus(0x00800000, func_id).is_complete
        assert SixAxisStatus(0x00C00000, func_id).has_error


def test_mock_func109_reads_delay_from_ieee4():
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    command = SixAxisCommand(func_num=109, delay_sec=1.0)

    for request in command.to_func_writes():
        client.write_modbus_float(request)
    client.write_modbus_float(command.to_trigger_write())

    assert _wait_for_modbus_float(client, 330, 1.0) == 1.0


def test_mock_func110_reads_delay_from_ieee6():
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    command = SixAxisCommand(func_num=110, delay_sec=1.0)

    for request in command.to_func_writes():
        client.write_modbus_float(request)
    client.write_modbus_float(command.to_trigger_write())

    assert _wait_for_modbus_float(client, 332, 1.0) == 1.0
