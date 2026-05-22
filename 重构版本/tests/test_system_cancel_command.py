from robot_modbus_lite.gui_constants import SYSTEM_COMMAND_CODES
from robot_modbus_lite.gui_system_mixin import GuiSystemMixin
from robot_modbus_lite.service import RobotModbusService


def test_system_cancel_builds_func104_cancel_press_command():
    service = RobotModbusService("unused.csv", table={})

    command = service.build_six_system_command(SYSTEM_COMMAND_CODES["sys_cancel"])

    assert command.func_num == 104
    assert command.cancel_ctrl == 1
    assert command.desc == "SYS_CANCEL"


class DummySystem(GuiSystemMixin):
    pass


def test_safety_system_actions_are_not_blocked_while_flow_is_running():
    dummy = DummySystem()
    calls = []
    dummy.flow_running = True
    dummy._handle_system_action_six = lambda action_key, on_done=None: calls.append(action_key)
    dummy._show_warning = lambda *_args, **_kwargs: None
    dummy._append_log = lambda *_args, **_kwargs: None

    for action in ("sys_estop", "sys_pause", "sys_resume", "sys_cancel"):
        dummy._handle_system_action(action)

    assert calls == ["sys_estop", "sys_pause", "sys_resume", "sys_cancel"]


def test_alarm_reset_remains_blocked_while_flow_is_running():
    dummy = DummySystem()
    calls = []
    done = []
    logs = []
    warnings = []
    dummy.flow_running = True
    dummy._handle_system_action_six = lambda action_key, on_done=None: calls.append(action_key)
    dummy._show_warning = lambda *args, **_kwargs: warnings.append(args)
    dummy._append_log = lambda *args, **_kwargs: logs.append(args)

    dummy._handle_system_action("alarm_reset", on_done=done.append)

    assert calls == []
    assert done == [False]
    assert warnings[-1][0] == "流程运行中"
    assert logs[-1][0:3] == ("系统", "alarm_reset", "失败")


def test_pause_and_resume_update_local_flow_pause_state():
    dummy = DummySystem()
    resumed = []
    dummy.flow_running = True
    dummy.flow_paused = False
    dummy.flow_status = "已暂停"
    dummy.mode_label = type("Label", (), {"text": lambda self: "自动"})()
    dummy.status_label = type("Status", (), {"setText": lambda self, _text: None})()
    dummy._refresh_status_labels = lambda: None
    dummy._run_next_flow_step = lambda: resumed.append(True)

    dummy._apply_legacy_system_action("sys_pause")

    assert dummy.flow_paused is True
    assert dummy.busy == "暂停"

    dummy._apply_legacy_system_action("sys_resume")

    assert dummy.flow_paused is False
    assert resumed == [True]
