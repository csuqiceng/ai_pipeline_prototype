from robot_modbus_lite.kinematics_engine import FrameTrans2KinematicsEngine
from robot_modbus_lite.web_control_bridge import WebControlBridge


def test_web_control_bridge_exposes_mock_controller_kinematics_engine():
    bridge = WebControlBridge.from_runtime_files(mode="mock_controller")

    engine = bridge.kinematics_engine()

    assert isinstance(engine, FrameTrans2KinematicsEngine)
    result = engine.inverse((300.0, 0.0, 500.0, 0.0, 0.0, 0.0), fstatus=0)
    assert result.success is True
    assert len(result.joints) == 6
