from robot_modbus_lite.agent.axis_status import AxisStatusBitDecomposer


def test_axis_status_decomposer_reports_known_bits_by_axis():
    decomposer = AxisStatusBitDecomposer()

    detail = decomposer.decompose([0, 0b10, 0, 0x100, 0, 0x4000])

    assert detail["axes"][1]["active_bits"] == [1]
    assert detail["axes"][1]["messages"][0]["code"] == "following_error_warning"
    assert detail["axes"][3]["active_bits"] == [8]
    assert detail["axes"][3]["messages"][0]["code"] == "following_error_error"
    assert detail["axes"][5]["active_bits"] == [14]
    assert detail["has_error"] is True


def test_axis_status_decomposer_formats_hard_limit_direction():
    decomposer = AxisStatusBitDecomposer()

    detail = decomposer.decompose([1 << 4, 1 << 5])

    assert "J1轴碰到正向硬限位" in detail["axes"][0]["messages"][0]["message"]
    assert "向负方向" in detail["axes"][0]["messages"][0]["suggestion"]
    assert "J2轴碰到负向硬限位" in detail["axes"][1]["messages"][0]["message"]
    assert "向正方向" in detail["axes"][1]["messages"][0]["suggestion"]
