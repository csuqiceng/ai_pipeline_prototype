"""模拟控制器六轴并行、互斥和恢复场景回归工具。"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mock_controller.client import MockZMotionVrClient
from robot_modbus_lite.models import SixAxisCommand, SixAxisStatus, VrReadRequest


STATUS_READ = VrReadRequest(34, 1)
ALARM_READ = VrReadRequest(38, 1)


@dataclass
class ScenarioResult:
    """模拟控制器回归场景的一项结果。"""
    name: str
    ok: bool
    detail: str
    status_raw: int
    alarm_raw: int


def connect_client() -> MockZMotionVrClient:
    """连接客户端。"""
    client = MockZMotionVrClient("mock", connect_delay=0)
    client.connect()
    return client


def read_status(client: MockZMotionVrClient) -> int:
    """读取状态。"""
    return int(client.read_modbus_long(STATUS_READ)[0])


def read_alarm(client: MockZMotionVrClient) -> int:
    """读取报警。"""
    return int(client.read_modbus_long(ALARM_READ)[0])


def func_state(client: MockZMotionVrClient, func_num: int) -> int:
    """处理函数状态。"""
    return SixAxisStatus.from_value(read_status(client), func_num=func_num).function_state()


def trigger(client: MockZMotionVrClient, command: SixAxisCommand) -> None:
    """触发相关数据。"""
    for request in command.to_func_writes():
        client.write_modbus_float(request)
    client.write_modbus_float(command.to_trigger_write())
    time.sleep(0.01)


def wait_until(predicate: Callable[[], bool], timeout_sec: float = 1.0, interval_sec: float = 0.01) -> bool:
    """等待相关数据。"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_sec)
    return predicate()


def wait_done(client: MockZMotionVrClient, func_num: int, timeout_sec: float = 1.0) -> bool:
    """等待完成。"""
    return wait_until(
        lambda: func_state(client, func_num) in (SixAxisStatus.STATE_DONE, SixAxisStatus.STATE_ERR),
        timeout_sec=timeout_sec,
    )


def reset(client: MockZMotionVrClient) -> None:
    """复位相关数据。"""
    trigger(client, SixAxisCommand(func_num=104, desc="reset", reset_ctrl=1))
    wait_done(client, 104)


def line_move() -> SixAxisCommand:
    """处理直线移动。"""
    return SixAxisCommand(
        func_num=108,
        desc="mock line move",
        target_x=100.0,
        target_y=20.0,
        target_z=80.0,
        target_rx=0.0,
        target_ry=0.0,
        target_rz=0.0,
        spd_pct=30.0,
        acc_pct=30.0,
        dec_pct=30.0,
        move_type=0,
    )


def delay(seconds: float = 0.5) -> SixAxisCommand:
    """处理延时。"""
    return SixAxisCommand(func_num=110, desc="mock delay", delay_sec=seconds)


def io_ctrl() -> SixAxisCommand:
    """处理输入输出。"""
    return SixAxisCommand(func_num=120, desc="mock io", io_no=1, io_action=1)


def joint_jog() -> SixAxisCommand:
    """处理关节点动。"""
    return SixAxisCommand(
        func_num=106,
        desc="mock joint jog",
        axis_no=0,
        pos_val=10.0,
        spd_pct=20.0,
        acc_pct=20.0,
        dec_pct=20.0,
    )


def timer_check(seconds: float = 0.5) -> SixAxisCommand:
    """检查定时器。"""
    return SixAxisCommand(func_num=109, desc="mock timer", delay_sec=seconds)


def scenario_result(name: str, client: MockZMotionVrClient, ok: bool, detail: str) -> ScenarioResult:
    """处理场景结果。"""
    return ScenarioResult(name, ok, detail, read_status(client), read_alarm(client))


def run_scenario(name: str, body: Callable[[MockZMotionVrClient], tuple[bool, str]]) -> ScenarioResult:
    """运行场景。"""
    client = connect_client()
    try:
        ok, detail = body(client)
        return scenario_result(name, client, ok, detail)
    except Exception as exc:
        return scenario_result(name, client, False, f"异常: {exc}")
    finally:
        client.disconnect()


def scenario_motion_program_parallel(client: MockZMotionVrClient) -> tuple[bool, str]:
    """处理场景运动程序并行。"""
    trigger(client, line_move())
    trigger(client, delay(0.4))
    motion_seen = wait_until(lambda: func_state(client, 108) == SixAxisStatus.STATE_EXEC, timeout_sec=0.05)
    program_seen = wait_until(lambda: func_state(client, 110) == SixAxisStatus.STATE_EXEC, timeout_sec=0.1)
    no_busy_alarm = (read_alarm(client) & (1 << 2)) == 0
    wait_done(client, 108)
    wait_done(client, 110)
    ok = motion_seen and program_seen and no_busy_alarm
    return ok, f"motion_exec={motion_seen}, program_exec={program_seen}, cmd_busy={not no_busy_alarm}"


def scenario_program_mutex(client: MockZMotionVrClient) -> tuple[bool, str]:
    """处理场景程序。"""
    trigger(client, delay(0.5))
    trigger(client, io_ctrl())
    program_busy = wait_until(lambda: func_state(client, 120) == SixAxisStatus.STATE_ERR, timeout_sec=0.1)
    alarm_busy = (read_alarm(client) & (1 << 2)) != 0
    ok = program_busy and alarm_busy
    return ok, f"func120_err={program_busy}, cmd_busy_alarm={alarm_busy}"


def scenario_delay_update(client: MockZMotionVrClient) -> tuple[bool, str]:
    """处理场景延时。"""
    trigger(client, delay(0.8))
    first_exec = wait_until(lambda: func_state(client, 110) == SixAxisStatus.STATE_EXEC, timeout_sec=0.1)
    trigger(client, delay(0.2))
    no_busy_alarm = (read_alarm(client) & (1 << 2)) == 0
    still_exec = func_state(client, 110) == SixAxisStatus.STATE_EXEC
    done = wait_done(client, 110, timeout_sec=0.5)
    ok = first_exec and still_exec and done and no_busy_alarm
    return ok, f"first_exec={first_exec}, still_exec={still_exec}, done={done}, cmd_busy={not no_busy_alarm}"


def scenario_motion_mutex_joint(client: MockZMotionVrClient) -> tuple[bool, str]:
    """处理场景运动关节。"""
    trigger(client, line_move())
    trigger(client, joint_jog())
    motion_busy = wait_until(lambda: func_state(client, 106) == SixAxisStatus.STATE_ERR, timeout_sec=0.1)
    alarm_busy = (read_alarm(client) & (1 << 2)) != 0
    ok = motion_busy and alarm_busy
    return ok, f"func106_err={motion_busy}, cmd_busy_alarm={alarm_busy}"


def scenario_motion_mutex_timer(client: MockZMotionVrClient) -> tuple[bool, str]:
    """处理场景运动定时器。"""
    trigger(client, line_move())
    trigger(client, timer_check(0.5))
    motion_busy = wait_until(lambda: func_state(client, 109) == SixAxisStatus.STATE_ERR, timeout_sec=0.1)
    alarm_busy = (read_alarm(client) & (1 << 2)) != 0
    ok = motion_busy and alarm_busy
    return ok, f"func109_err={motion_busy}, cmd_busy_alarm={alarm_busy}"


def scenario_func104_during_motion(client: MockZMotionVrClient) -> tuple[bool, str]:
    """处理场景函数运动。"""
    trigger(client, line_move())
    trigger(client, SixAxisCommand(func_num=104, desc="pause", pause_ctrl=1))
    system_done = wait_until(lambda: func_state(client, 104) == SixAxisStatus.STATE_DONE, timeout_sec=0.2)
    motion_state = func_state(client, 108)
    ok = system_done and motion_state in (
        SixAxisStatus.STATE_EXEC,
        SixAxisStatus.STATE_DONE,
        SixAxisStatus.STATE_IDLE,
    )
    return ok, f"func104_done={system_done}, func108_state={motion_state}"


def scenario_reset_clears_fake_busy(client: MockZMotionVrClient) -> tuple[bool, str]:
    """复位场景忙。"""
    trigger(client, delay(0.8))
    wait_until(lambda: func_state(client, 110) == SixAxisStatus.STATE_EXEC, timeout_sec=0.1)
    reset(client)
    trigger(client, io_ctrl())
    io_done = wait_done(client, 120, timeout_sec=0.3)
    no_busy_alarm = (read_alarm(client) & (1 << 2)) == 0
    ok = io_done and no_busy_alarm and func_state(client, 120) == SixAxisStatus.STATE_DONE
    return ok, f"func120_done={io_done}, cmd_busy_after_reset={not no_busy_alarm}"


def main() -> int:
    """执行命令行入口逻辑。"""
    scenarios: list[tuple[str, Callable[[MockZMotionVrClient], tuple[bool, str]]]] = [
        ("Func108 + Func110 parallel", scenario_motion_program_parallel),
        ("Func110 + Func120 mutex", scenario_program_mutex),
        ("Func110 update while running", scenario_delay_update),
        ("Func108 + Func106 mutex", scenario_motion_mutex_joint),
        ("Func108 + Func109 mutex", scenario_motion_mutex_timer),
        ("Func104 during motion", scenario_func104_during_motion),
        ("Func104 reset clears fake busy", scenario_reset_clears_fake_busy),
    ]

    results = [run_scenario(name, body) for name, body in scenarios]
    for result in results:
        prefix = "PASS" if result.ok else "FAIL"
        print(f"{prefix} {result.name}")
        print(f"     {result.detail}")
        if not result.ok:
            print(f"     LONG(34)={result.status_raw}, LONG(38)={result.alarm_raw}")

    failed = [result for result in results if not result.ok]
    print(f"\nsummary: PASS={len(results) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
