"""V5.0 六轴协议关键差异的本地 mock 验证工具。"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mock_controller.client import MockZMotionVrClient
from robot_modbus_lite.models import (
    QueryRecord,
    SixAxisAlarmDetail,
    SixAxisCommand,
    SixAxisStatus,
    SixAxisSystemState,
    VrReadRequest,
    VrWriteRequest,
)
from robot_modbus_lite.service import RobotModbusService
from robot_modbus_lite.six_axis_command_mixin import SixAxisCommandMixin


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


class Harness(SixAxisCommandMixin):
    """只暴露六轴 mixin 内部校验方法的轻量对象。"""

    def __init__(self) -> None:
        self.service = RobotModbusService("data/query_table.json")
        self.axis_ranges = SimpleNamespace(
            echo_retry_interval_sec=0.005,
            echo_retry_count=3,
            echo_write_rounds=1,
            echo_compare_epsilon=0.001,
            motion_timeout_sec=1.0,
        )
        self.logs: list[tuple[str, str, str, str]] = []

    def _append_log(self, category: str, action: str, result: str, detail: str = "", **_) -> None:
        self.logs.append((category, action, result, detail))

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{float(value):g}"


def connect_client() -> MockZMotionVrClient:
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    return client


def trigger(client: MockZMotionVrClient, command: SixAxisCommand) -> None:
    for request in command.to_func_writes():
        client.write_modbus_float(request)
    client.write_modbus_float(command.to_trigger_write())
    time.sleep(0.02)


def wait_until(predicate: Callable[[], bool], timeout_sec: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def func_state(client: MockZMotionVrClient, func_num: int) -> int:
    raw = int(client.read_modbus_long(VrReadRequest(34, 1))[0])
    return SixAxisStatus.from_value(raw, func_num=func_num).function_state()


def reset(client: MockZMotionVrClient) -> None:
    trigger(client, SixAxisCommand(func_num=104, reset_ctrl=1))
    wait_until(lambda: func_state(client, 104) == SixAxisStatus.STATE_DONE)


def check_command_mapping() -> CheckResult:
    cmd109 = SixAxisCommand(func_num=109, delay_sec=1.25).to_func_writes()
    cmd110 = SixAxisCommand(func_num=110, delay_sec=2.5).to_func_writes()
    got109 = [(item.start_vr, item.values) for item in cmd109]
    got110 = [(item.start_vr, item.values) for item in cmd110]
    ok = got109 == [(0, (109.0,)), (2, (1.25,))] and got110 == [(0, (110.0,)), (2, (2.5,))]
    return CheckResult("Func109/110 参数地址", ok, f"109={got109}, 110={got110}")


def check_status_parsers() -> CheckResult:
    alarm = SixAxisAlarmDetail.from_value((1 << 8) | (1 << 9))
    system = SixAxisSystemState.from_value((1 << 8) | (1 << 9))
    ok = str(alarm) == "无报警详情" and system.func_id_invalid and system.func120_param_invalid
    return CheckResult("LONG36/38 bit8-bit9 归属", ok, f"alarm={alarm}, system={system}")


def check_service_addresses() -> CheckResult:
    service = RobotModbusService("data/query_table.json")
    ok = (
        service.build_six_current_func_read().start_vr == 324
        and service.build_six_accept_confirm_read().start_vr == 312
        and service.build_six_safety_limits_read().count == 22
    )
    return CheckResult("service 312/324/1700~1742 地址", ok, "checked")


def check_mock_runtime() -> CheckResult:
    client = connect_client()
    try:
        trigger(client, SixAxisCommand(func_num=110, delay_sec=0.05))
        delay_running = wait_until(lambda: func_state(client, 110) == SixAxisStatus.STATE_EXEC, timeout_sec=0.05)
        delay_remaining_during = float(client.read_modbus_float(VrReadRequest(328, 1))[0])
        long40_during = int(client.read_modbus_long(VrReadRequest(40, 1))[0])
        done = wait_until(lambda: func_state(client, 110) == SixAxisStatus.STATE_DONE)
        delay_remaining_done = float(client.read_modbus_float(VrReadRequest(328, 1))[0])
        long40_done = int(client.read_modbus_long(VrReadRequest(40, 1))[0])
        ack = int(client.read_modbus_float(VrReadRequest(312, 1))[0])
        current_func = int(client.read_modbus_float(VrReadRequest(324, 1))[0])

        reset(client)
        reset_states = {
            func_num: func_state(client, func_num)
            for func_num in (11, 104, 106, 107, 108, 109, 110, 120)
        }
        reset_all_done = all(state == SixAxisStatus.STATE_DONE for state in reset_states.values())
        trigger(client, SixAxisCommand(func_num=120, io_no=1, io_action=1))
        io_done = wait_until(lambda: func_state(client, 120) == SixAxisStatus.STATE_DONE)
        y_state = int(client.read_modbus_long(VrReadRequest(42, 1))[0])
        y_echo = int(client.read_modbus_long(VrReadRequest(44, 1))[0])
        x_state = int(client.read_modbus_long(VrReadRequest(46, 1))[0])

        reset(client)
        trigger(client, SixAxisCommand(func_num=109, delay_sec=0.2))
        timer_running = wait_until(lambda: func_state(client, 109) == SixAxisStatus.STATE_EXEC, timeout_sec=0.1)
        timer_remaining_during = float(client.read_modbus_float(VrReadRequest(326, 1))[0])
        timer_done = wait_until(lambda: func_state(client, 109) == SixAxisStatus.STATE_DONE)
        timer_remaining_done = float(client.read_modbus_float(VrReadRequest(326, 1))[0])
        client.write_modbus_float(VrWriteRequest(8, (1.0,)))
        timer_cleared = func_state(client, 109) == SixAxisStatus.STATE_IDLE

        reset(client)
        client.write_modbus_float(VrWriteRequest(0, (999.0,)))
        client.write_modbus_float(VrWriteRequest(32, (1.0,)))
        time.sleep(0.02)
        system_invalid_func = int(client.read_modbus_long(VrReadRequest(36, 1))[0])
        alarm_after_invalid_func = int(client.read_modbus_long(VrReadRequest(38, 1))[0])

        reset(client)
        trigger(client, SixAxisCommand(func_num=120, io_no=99, io_action=1))
        wait_until(lambda: func_state(client, 120) == SixAxisStatus.STATE_ERR)
        system_invalid_param = int(client.read_modbus_long(VrReadRequest(36, 1))[0])
        alarm_after_invalid_param = int(client.read_modbus_long(VrReadRequest(38, 1))[0])

        reset(client)
        trigger(
            client,
            SixAxisCommand(
                func_num=108,
                target_x=3.0,
                target_y=4.0,
                target_z=12.0,
                spd_pct=20.0,
                acc_pct=20.0,
                dec_pct=20.0,
            ),
        )
        wait_until(lambda: func_state(client, 108) == SixAxisStatus.STATE_DONE)
        current_r3d = float(client.read_modbus_float(VrReadRequest(1740, 1))[0])
        current_z = float(client.read_modbus_float(VrReadRequest(1742, 1))[0])
        joint_dpos = client.read_modbus_float(VrReadRequest(1500, 6))
        pose_dpos = client.read_modbus_float(VrReadRequest(1512, 6))
        joint_mpos = client.read_modbus_float(VrReadRequest(1600, 6))
        pose_mpos = client.read_modbus_float(VrReadRequest(1612, 6))
        axis_status = [int(value) for value in client.read_modbus_float(VrReadRequest(200, 12))]
        motion_type = [int(value) for value in client.read_modbus_float(VrReadRequest(240, 12))]
        diag316 = float(client.read_modbus_float(VrReadRequest(316, 1))[0])
        long36 = float(client.read_modbus_long(VrReadRequest(36, 1))[0])

        ok = (
            delay_running
            and done
            and delay_remaining_during > 0
            and long40_during > 0
            and delay_remaining_done == 0
            and long40_done == 0
            and ack == 1
            and current_func == 110
            and reset_all_done
            and io_done
            and y_state == y_echo == 2
            and x_state == 0
            and timer_running
            and timer_done
            and timer_remaining_during > 0
            and timer_remaining_done == 0
            and timer_cleared
            and (system_invalid_func & (1 << 8)) != 0
            and alarm_after_invalid_func == 0
            and (system_invalid_param & (1 << 9)) != 0
            and (alarm_after_invalid_param & ((1 << 8) | (1 << 9))) == 0
            and abs(current_r3d - 13.0) < 0.001
            and abs(current_z - 12.0) < 0.001
            and joint_dpos == joint_mpos
            and pose_dpos == pose_mpos
            and len(axis_status) == 12
            and axis_status[:6] == [1] * 6
            and len(motion_type) == 12
            and motion_type[:6] == [1] * 6
            and motion_type[6:] == [2] * 6
            and diag316 == long36
        )
        detail = (
            f"ack={ack}, current_func={current_func}, delay={delay_remaining_during:.3f}->{delay_remaining_done:.3f}, "
            f"LONG40={long40_during}->{long40_done}, reset_states={reset_states}, Y={y_state}/{y_echo}, X={x_state}, "
            f"timer={timer_remaining_during:.3f}->{timer_remaining_done:.3f}, timer_done={timer_done}, timer_cleared={timer_cleared}, "
            f"LONG36_invalid_func={system_invalid_func}, LONG38_invalid_func={alarm_after_invalid_func}, "
            f"LONG36_invalid_param={system_invalid_param}, LONG38_invalid_param={alarm_after_invalid_param}, "
            f"R3D={current_r3d:.3f}, Z={current_z:.3f}, DPOS=MPOS={joint_dpos == joint_mpos and pose_dpos == pose_mpos}, "
            f"AXISSTATUS={axis_status[:2]}..., MTYPE={motion_type[:2]}..., IEEE316={diag316}, LONG36={long36}"
        )
        return CheckResult("mock V5.0 运行语义", ok, detail)
    finally:
        client.disconnect()


def check_third_gate_pause_recovery() -> CheckResult:
    client = connect_client()
    try:
        class PauseAfterEchoHarness(Harness):
            def __init__(self) -> None:
                super().__init__()
                self.pause_injected = False

            def _wait_six_command_echo_ready(self, *args, **kwargs) -> None:
                super()._wait_six_command_echo_ready(*args, **kwargs)
                if self.pause_injected:
                    return
                self.pause_injected = True
                raw = int(client.read_modbus_long(VrReadRequest(34, 1))[0])
                client.write_modbus_long(VrWriteRequest(34, (raw | (1 << 26),)))
                wait_until(lambda: SixAxisStatus.from_value(client.read_modbus_long(VrReadRequest(34, 1))[0]).is_paused)

                def resume() -> None:
                    time.sleep(0.05)
                    paused_raw = int(client.read_modbus_long(VrReadRequest(34, 1))[0])
                    client.write_modbus_long(VrWriteRequest(34, (paused_raw & ~(1 << 26),)))

                threading.Thread(target=resume, daemon=True).start()

        harness = PauseAfterEchoHarness()
        record = QueryRecord("io_gate", 120, {"io_no": 0, "io_action": 1})
        harness._write_six_command(client, SixAxisCommand(func_num=120, io_no=0, io_action=1), record)
        done = wait_until(lambda: func_state(client, 120) == SixAxisStatus.STATE_DONE)
        paused_after = SixAxisStatus.from_value(client.read_modbus_long(VrReadRequest(34, 1))[0]).is_paused
        gate_wait_logged = any("第三道门等待暂停解除" in item[1] for item in harness.logs)
        ok = harness.pause_injected and gate_wait_logged and done and not paused_after
        return CheckResult(
            "第三道门暂停自动恢复",
            ok,
            f"pause_injected={harness.pause_injected}, gate_wait_logged={gate_wait_logged}, done={done}, paused_after={paused_after}",
        )
    finally:
        client.disconnect()


def main() -> int:
    checks = [
        check_command_mapping(),
        check_status_parsers(),
        check_service_addresses(),
        check_mock_runtime(),
        check_third_gate_pause_recovery(),
    ]
    for check in checks:
        prefix = "PASS" if check.ok else "FAIL"
        print(f"{prefix} {check.name}")
        print(f"     {check.detail}")
    failed = [check for check in checks if not check.ok]
    print(f"\nsummary: PASS={len(checks) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
