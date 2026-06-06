from mock_controller.client import MockZMotionVrClient
from robot_modbus_lite.models import SixAxisCommand, VrReadRequest


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
