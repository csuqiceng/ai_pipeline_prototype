import threading

from robot_modbus_lite.zmotion_client import ZMotionVrClient


class FakeDevice:
    def __init__(self):
        self.calls = []

    def ZAux_Direct_FrameTrans2(self, axis_list, table_in, table_out, mode):
        self.calls.append((tuple(axis_list), table_in, table_out, mode))
        return 0


class FakeCountDevice:
    def __init__(self):
        self.calls = []

    def ZAux_Direct_FrameTrans2(self, axis_list, axis_count, table_in, table_out, mode):
        self.calls.append((tuple(axis_list), axis_count, table_in, table_out, mode))
        return 0


def test_zmotion_client_frame_trans2_wraps_direct_sdk_call():
    client = ZMotionVrClient.__new__(ZMotionVrClient)
    client._device = FakeDevice()
    client._lock = threading.Lock()
    client.connected = True

    client.frame_trans2((6, 7, 8, 9, 10, 11), 550, 560, 2)

    assert client._device.calls == [((6, 7, 8, 9, 10, 11), 550, 560, 2)]


def test_zmotion_client_frame_trans2_supports_axis_count_signature():
    client = ZMotionVrClient.__new__(ZMotionVrClient)
    client._device = FakeCountDevice()
    client._lock = threading.Lock()
    client.connected = True

    client.frame_trans2((6, 7, 8, 9, 10, 11), 550, 560, 2)

    assert client._device.calls == [((6, 7, 8, 9, 10, 11), 6, 550, 560, 2)]
