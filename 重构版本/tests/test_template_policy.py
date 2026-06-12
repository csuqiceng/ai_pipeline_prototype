from types import SimpleNamespace

import pytest

from robot_modbus_lite.models import QueryRecord
from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.system_config import AxisRangeConfig
from robot_modbus_lite.template_mixin import TemplateMixin
from robot_modbus_lite.web_server import _validate_template_record


def _func107_record() -> QueryRecord:
    return QueryRecord(
        query_key="forbidden_107",
        func_num=107,
        params={
            "axis_no": 8,
            "pos_val": 50.0,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "fuzzy_pos": 1,
            "fuzzy_spd": 1,
            "fuzzy_acc": 1,
            "fuzzy_dec": 1,
            "stop_cmd": 0,
        },
        safety_level=5,
    )


def _func11_record() -> QueryRecord:
    return QueryRecord(
        query_key="legacy_func11",
        func_num=11,
        params={
            "point_count": 2,
            "spd_pct": 50.0,
            "acc_pct": 50.0,
            "dec_pct": 50.0,
            "points": [
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ],
        },
        safety_level=5,
    )


def test_qt_template_validation_rejects_func106_107_current_policy():
    host = SimpleNamespace(axis_ranges=AxisRangeConfig(x=(-100, 100), y=(-100, 100), z=(-100, 100)))

    assert "当前阶段不支持 Func106/Func107" in TemplateMixin._validate_record(host, _func107_record())


def test_web_template_validation_rejects_func106_107_current_policy():
    assert "当前阶段不支持 Func106/Func107" in _validate_template_record(_func107_record())


def test_qt_template_validation_rejects_func11_for_zbasic_glm_protocol():
    host = SimpleNamespace(axis_ranges=AxisRangeConfig(x=(-100, 100), y=(-100, 100), z=(-100, 100)))

    assert "当前 zbasic-GLM 协议不支持 Func11" in TemplateMixin._validate_record(host, _func11_record())


def test_web_template_validation_rejects_func11_for_zbasic_glm_protocol():
    assert "当前 zbasic-GLM 协议不支持 Func11" in _validate_template_record(_func11_record())


def test_service_rejects_func11_before_real_six_axis_command_build():
    service = RobotModbusService("unused.json", table={"legacy_func11": _func11_record()})

    with pytest.raises(ValueError, match="当前 zbasic-GLM 协议不支持 Func11"):
        service.build_six_command_from_record(_func11_record())
