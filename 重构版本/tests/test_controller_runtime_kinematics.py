import threading

from robot_modbus_lite.controller_runtime_mixin import ControllerRuntimeMixin
from robot_modbus_lite.kinematics_engine import FrameTrans2KinematicsEngine


class FakeClient:
    def __init__(self):
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def set_table(self, index, value):
        pass

    def get_table(self, index):
        return 0.0

    def frame_trans2(self, axis_list, table_in, table_out, mode):
        pass


class DummyRuntime(ControllerRuntimeMixin):
    def __init__(self):
        self._client_cache_lock = threading.Lock()
        self._cached_client = None
        self._cached_client_host = ""
        self.resource_root = "."
        self.client = FakeClient()

    def _make_client(self, host):
        return self.client


def test_get_client_installs_frame_trans2_kinematics_engine():
    runtime = DummyRuntime()

    client = runtime._get_client("127.0.0.1")

    assert client is runtime.client
    assert isinstance(runtime.operator_kinematics_engine, FrameTrans2KinematicsEngine)
    assert runtime.operator_kinematics_engine.transport is client


def test_disconnect_clears_operator_kinematics_engine():
    runtime = DummyRuntime()
    runtime._get_client("127.0.0.1")

    runtime._disconnect_client()

    assert runtime._cached_client is None
    assert runtime.operator_kinematics_engine is None
