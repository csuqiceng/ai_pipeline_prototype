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
