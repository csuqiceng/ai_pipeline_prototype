from robot_modbus_lite.kinematics_engine import FrameTrans2KinematicsEngine


class FakeFrameTrans2Transport:
    def __init__(self):
        self.tables = {}
        self.commands = []

    def set_table(self, index: int, value: float) -> None:
        self.tables[index] = float(value)

    def get_table(self, index: int) -> float:
        return float(self.tables.get(index, 1000 + index))

    def execute(self, command: str) -> None:
        self.commands.append(command)
        for offset in range(6):
            self.tables[560 + offset] = 10.0 + offset


def test_frame_trans2_inverse_writes_pose_fstatus_and_reads_joints():
    transport = FakeFrameTrans2Transport()
    engine = FrameTrans2KinematicsEngine(transport)

    result = engine.inverse((1, 2, 3, 4, 5, 6), fstatus=7)

    assert result.success is True
    assert result.fstatus == 7
    assert result.joints == (10.0, 11.0, 12.0, 13.0, 14.0, 15.0)
    assert [transport.tables[550 + idx] for idx in range(6)] == [1, 2, 3, 4, 5, 6]
    assert transport.tables[556] == 7
    assert transport.commands == ["FRAME_TRANS2(550,560,2)"]


def test_frame_trans2_inverse_returns_failure_when_transport_raises():
    class BrokenTransport(FakeFrameTrans2Transport):
        def execute(self, command: str) -> None:
            raise RuntimeError("controller failed")

    engine = FrameTrans2KinematicsEngine(BrokenTransport())

    result = engine.inverse((1, 2, 3, 4, 5, 6), fstatus=1)

    assert result.success is False
    assert result.joints == ()
    assert result.fstatus == 1
    assert "controller failed" in result.message
