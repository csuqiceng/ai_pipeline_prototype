"""六轴协议的写参数、验回显、触发、等待和恢复链路。"""

from __future__ import annotations

import time

from .exceptions import SixAxisCommandRuntimeError
from .gui_constants import (
    SIX_CMD_BUSY_RECOVERY_MAX_RETRIES,
    SIX_CMD_BUSY_SLOT_WAIT_TIMEOUT_SEC,
    SIX_ECHO_COMPARE_EPSILON,
    SIX_ECHO_CONSECUTIVE_FAIL_THRESHOLD,
    SIX_ECHO_MAX_RETRY_COUNT,
    SIX_ECHO_RETRY_INTERVAL_SEC,
    SIX_ECHO_WRITE_ROUNDS,
    SIX_POST_TRIGGER_SETTLE_SEC,
    SIX_READY_RECOVERY_TIMEOUT_SEC,
)
from .models import ControllerClient, QueryRecord, SixAxisCommand, SixAxisStatus, VrReadRequest, six_func_slot


class SixAxisCommandMixin:
    """执行六轴协议完整事务的主窗口能力。"""

    def _execute_send_six(self, client: ControllerClient, record: QueryRecord) -> list[float]:
        """执行六轴。"""
        recovery_attempts = 0
        while True:
            six_cmd = self._trigger_six_no_wait(client, record)
            if six_cmd.func_num == 104:
                self._wait_func104_done(client, six_cmd, record.query_key)
                return []
            try:
                self._wait_six_command_done(client, six_cmd, record)
                return self._read_six_command_feedback(client, six_cmd, record)
            except SixAxisCommandRuntimeError as exc:
                if not exc.is_cmd_busy or recovery_attempts >= SIX_CMD_BUSY_RECOVERY_MAX_RETRIES:
                    raise
                recovery_attempts += 1
                self._append_log(
                    "六轴",
                    f"指令忙自动恢复 {record.query_key}",
                    "警告",
                    (
                        f"attempt={recovery_attempts}/{SIX_CMD_BUSY_RECOVERY_MAX_RETRIES}, "
                        f"LONG(34)={exc.status_raw}, LONG(38)={exc.alarm_raw}"
                    ),
                )
                self._recover_six_cmd_busy(client, six_cmd, record, recovery_attempts)

    def _trigger_six_no_wait(self, client: ControllerClient, record: QueryRecord) -> SixAxisCommand:
        """触发六轴。"""
        six_cmd = self.service.build_six_command_from_record(record)
        self._write_six_command(client, six_cmd, record)
        return six_cmd

    def _recover_six_cmd_busy(
        self,
        client: ControllerClient,
        six_cmd: SixAxisCommand,
        record: QueryRecord,
        attempt: int,
    ) -> None:
        """处理六轴忙。"""
        # 指令忙只做有限自动恢复：先等当前槽释放，再发一零四函数复位，
        # 最后等待就绪状态回来。这里不无限重试，避免现场控制器异常时卡死界面流程。
        self._wait_six_slot_not_busy(client, six_func_slot(six_cmd.func_num), record.query_key)
        reset_cmd = SixAxisCommand(func_num=104, desc="AUTO_CMD_BUSY_RESET", reset_ctrl=1)
        reset_record = QueryRecord(
            query_key=f"{record.query_key}__auto_cmd_busy_reset_{attempt}",
            func_num=104,
            params={"estop_ctrl": 0, "pause_ctrl": 0, "cancel_ctrl": 0, "reset_ctrl": 1},
            description="指令忙自动报警复位",
        )
        try:
            self._write_six_command(client, reset_cmd, reset_record)
            self._wait_func104_done(client, reset_cmd, reset_record.query_key)
            self._wait_six_ready_after_reset(client, reset_record.query_key)
        except Exception as exc:
            raise RuntimeError(
                f"指令忙恢复失败: reset 失败 attempt={attempt} | {record.query_key} | reason={exc}"
            ) from exc

    def _wait_six_slot_not_busy(self, client: ControllerClient, slot: str, label: str) -> None:
        """等待六轴槽忙。"""
        if slot in ("system", "unknown"):
            return
        status_read = self.service.build_six_status_read()
        poll_interval_sec = 0.05
        max_wait_sec = max(SIX_CMD_BUSY_SLOT_WAIT_TIMEOUT_SEC, poll_interval_sec)
        deadline = time.monotonic() + max_wait_sec
        while time.monotonic() < deadline:
            vals = client.read_modbus_long(status_read)
            six_status = self.service.parse_six_status(vals)
            if not six_status.slot_busy(slot):
                return
            time.sleep(poll_interval_sec)
        raise RuntimeError(f"指令忙恢复失败: 等待 {slot} slot 空闲超时 | {label} | timeout={self._fmt(max_wait_sec)}s")

    def _wait_six_ready_after_reset(self, client: ControllerClient, label: str) -> None:
        """等待六轴就绪复位。"""
        status_read = self.service.build_six_status_read()
        poll_interval_sec = 0.05
        deadline = time.monotonic() + SIX_READY_RECOVERY_TIMEOUT_SEC
        last_status = 0
        while time.monotonic() < deadline:
            vals = client.read_modbus_long(status_read)
            six_status = self.service.parse_six_status(vals)
            last_status = six_status.raw
            if six_status.is_ready and not six_status.has_alarm:
                self._append_log("六轴", f"指令忙恢复就绪 {label}", "成功", f"LONG(34)={six_status.raw}")
                return
            time.sleep(poll_interval_sec)
        raise RuntimeError(
            f"指令忙恢复失败: 等待就绪超时 | {label} | timeout={self._fmt(SIX_READY_RECOVERY_TIMEOUT_SEC)}s | LONG(34)={last_status}"
        )

    def _precheck_six_command(self, client: ControllerClient, six_cmd: SixAxisCommand) -> None:
        """处理六轴命令。"""
        if six_cmd.func_num < 0:
            raise RuntimeError(f"V4.3 不支持本地负函数号命令: func={six_cmd.func_num}")
        if six_cmd.func_num == 104:
            return
        status_read = self.service.build_six_status_read()
        status_vals = client.read_modbus_long(status_read)
        six_status = self.service.parse_six_status(status_vals, six_cmd.func_num)
        if six_status.function_state(six_cmd.func_num) == SixAxisStatus.STATE_ERR:
            raise RuntimeError(f"六轴前置检查失败: Func{six_cmd.func_num} 处于 ERR，需先复位 | LONG(34)={six_status.raw}")
        if six_cmd.func_num == 110 and self._can_update_func110_delay(six_status):
            return
        if not six_status.can_send_for(six_cmd.func_num):
            raise RuntimeError(f"六轴前置检查失败: LONG(34)={six_status.raw} 目标 slot 未就绪")

    @staticmethod
    def _can_update_func110_delay(six_status: SixAxisStatus) -> bool:
        """更新函数延时。"""
        if six_status.has_alarm or six_status.is_estop or not six_status.is_ready:
            return False
        return (
            six_status.function_state(110) == SixAxisStatus.STATE_EXEC
            and six_status.function_state(120) != SixAxisStatus.STATE_EXEC
        )

    @staticmethod
    def _expected_echo_map(six_cmd: SixAxisCommand) -> dict[int, float]:
        """处理期望回显。"""
        return {addr: float(expected) for addr, expected in six_cmd.expected_echo_points()}

    def _read_six_echo_snapshot(self, client: ControllerClient) -> dict[int, float]:
        """读取六轴回显快照。"""
        snapshot: dict[int, float] = {}
        for addr in range(280, 314, 2):
            vals = client.read_modbus_float(VrReadRequest(start_vr=addr, count=1))
            snapshot[addr] = float(vals[0]) if vals else 0.0
        return snapshot

    def _six_echo_settings(self) -> tuple[float, int, int, float]:
        """处理六轴回显。"""
        retry_interval_sec = max(float(getattr(self.axis_ranges, "echo_retry_interval_sec", SIX_ECHO_RETRY_INTERVAL_SEC)), 0.0)
        retry_count = max(1, int(getattr(self.axis_ranges, "echo_retry_count", SIX_ECHO_MAX_RETRY_COUNT)))
        write_rounds = max(1, int(getattr(self.axis_ranges, "echo_write_rounds", SIX_ECHO_WRITE_ROUNDS)))
        compare_epsilon = max(float(getattr(self.axis_ranges, "echo_compare_epsilon", SIX_ECHO_COMPARE_EPSILON)), 0.0)
        return retry_interval_sec, retry_count, write_rounds, compare_epsilon

    def _collect_six_echo_mismatches(
        self,
        expected_map: dict[int, float],
        snapshot: dict[int, float],
        *,
        epsilon: float,
    ) -> list[str]:
        """收集六轴回显。"""
        mismatches: list[str] = []
        for addr, expected in expected_map.items():
            actual = float(snapshot.get(addr, 0.0))
            if abs(actual - expected) > epsilon:
                mismatches.append(f"IEEE({addr})期望={self._fmt(expected)} 实际={self._fmt(actual)}")
        return mismatches

    def _format_six_echo_snapshot(self, snapshot: dict[int, float], addrs: list[int] | None = None) -> str:
        """格式化六轴回显快照。"""
        target_addrs = addrs if addrs is not None else sorted(snapshot)
        return ", ".join(f"{addr}={self._fmt(float(snapshot.get(addr, 0.0)))}" for addr in target_addrs)

    def _verify_six_echo_once(
        self,
        client: ControllerClient,
        expected_map: dict[int, float],
        *,
        epsilon: float,
    ) -> tuple[dict[int, float], list[str]]:
        """处理六轴回显。"""
        snapshot = self._read_six_echo_snapshot(client)
        return snapshot, self._collect_six_echo_mismatches(expected_map, snapshot, epsilon=epsilon)

    def _write_six_params_only(self, client: ControllerClient, six_cmd: SixAxisCommand) -> None:
        """写入六轴。"""
        for wr in six_cmd.to_func_writes():
            client.write_modbus_float(wr)
            self._append_log("六轴", f"写入IEEE({wr.start_vr})", "成功", f"values={list(wr.values)}")

    def _wait_six_command_echo_ready(
        self,
        client: ControllerClient,
        expected_map: dict[int, float],
        record: QueryRecord,
        *,
        write_round: int,
        retry_interval_sec: float,
        retry_count: int,
        compare_epsilon: float,
    ) -> None:
        """等待六轴命令回显就绪。"""
        if not expected_map:
            return

        fail_threshold = max(1, min(int(SIX_ECHO_CONSECUTIVE_FAIL_THRESHOLD), retry_count))
        derived_wait_sec = retry_interval_sec * retry_count
        expected_addrs = sorted(expected_map)
        start_monotonic = time.monotonic()
        last_snapshot = {addr: 0.0 for addr in range(280, 314, 2)}
        last_echo_failures: list[str] = []
        consecutive_echo_fail = 0
        total_echo_fail_count = 0

        self._append_log(
            "六轴",
            f"回显等待开始 {record.query_key}",
            "进行中",
            (
                f"round={write_round}, retry_interval={retry_interval_sec:.3f}s, "
                f"max_retries={retry_count}, fail_threshold={fail_threshold}, "
                f"epsilon={compare_epsilon:g}, derived_wait={derived_wait_sec:.3f}s, "
                f"expected={self._format_six_echo_snapshot(expected_map, expected_addrs)}"
            ),
        )

        for attempt in range(1, retry_count + 1):
            read_error = ""
            try:
                last_snapshot, last_echo_failures = self._verify_six_echo_once(
                    client,
                    expected_map,
                    epsilon=compare_epsilon,
                )
            except Exception as exc:
                read_error = f"{type(exc).__name__}: {exc}"
                last_echo_failures = [f"回显读取异常 {read_error}"]

            if not read_error and not last_echo_failures:
                elapsed_sec = time.monotonic() - start_monotonic
                self._append_log(
                    "六轴",
                    f"回显等待成功 {record.query_key}",
                    "成功",
                    (
                        f"round={write_round}, elapsed={elapsed_sec:.3f}s, attempts={attempt}, "
                        f"snapshot={self._format_six_echo_snapshot(last_snapshot, expected_addrs)}"
                    ),
                )
                return
            else:
                consecutive_echo_fail += 1
                total_echo_fail_count += 1
                if consecutive_echo_fail == fail_threshold:
                    elapsed_sec = time.monotonic() - start_monotonic
                    detail = (
                        f"round={write_round}, count={consecutive_echo_fail}, elapsed={elapsed_sec:.3f}s, "
                        f"reason={last_echo_failures[0] if last_echo_failures else '未知'}, "
                        f"snapshot={self._format_six_echo_snapshot(last_snapshot, expected_addrs)}"
                    )
                    self._append_log("六轴", f"回显连续错误 {record.query_key}", "警告", detail)

            if attempt < retry_count:
                time.sleep(retry_interval_sec)

        elapsed_sec = time.monotonic() - start_monotonic
        reason = "; ".join(last_echo_failures[:6]) if last_echo_failures else "回显未收敛"
        detail_parts = [
            f"round={write_round}",
            f"retry_interval={retry_interval_sec:.3f}s",
            f"max_retries={retry_count}",
            f"attempts={retry_count}",
            f"total_echo_fail={total_echo_fail_count}",
            f"consecutive_echo_fail={consecutive_echo_fail}",
            f"threshold={fail_threshold}",
            f"epsilon={compare_epsilon:g}",
            f"reason={reason}",
            f"elapsed={elapsed_sec:.3f}s",
            f"snapshot={self._format_six_echo_snapshot(last_snapshot, expected_addrs)}",
        ]
        raise RuntimeError("六轴回显通讯失败: " + " | ".join(detail_parts))

    def _write_six_command(self, client: ControllerClient, six_cmd: SixAxisCommand, record: QueryRecord) -> None:
        """写入六轴命令。"""
        if six_cmd.func_num in (106, 107, 108):
            self._append_log(
                "六轴",
                f"Func{six_cmd.func_num} 参数说明 {record.query_key}",
                "成功",
                self._describe_six_motion_options(six_cmd),
            )

        self._precheck_six_command(client, six_cmd)

        expected_map = self._expected_echo_map(six_cmd)
        retry_interval_sec, retry_count, write_rounds, compare_epsilon = self._six_echo_settings()
        last_error: Exception | None = None
        # 四点三协议真机偶发回显滞后时，不能直接触发三十二号浮点寄存器。
        # 每轮都重新写一遍参数，再按配置轮询回显区；全部失败才报错。
        for write_round in range(1, write_rounds + 1):
            self._write_six_params_only(client, six_cmd)
            try:
                self._wait_six_command_echo_ready(
                    client,
                    expected_map,
                    record,
                    write_round=write_round,
                    retry_interval_sec=retry_interval_sec,
                    retry_count=retry_count,
                    compare_epsilon=compare_epsilon,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if write_round < write_rounds:
                    self._append_log(
                        "六轴",
                        f"回显失败重写参数 {record.query_key}",
                        "警告",
                        f"round={write_round}/{write_rounds}, reason={exc}",
                    )
                    continue
                raise RuntimeError(
                    f"六轴回显通讯失败: write_rounds={write_rounds}, "
                    f"retry_count={retry_count}, retry_interval={retry_interval_sec:.3f}s, "
                    f"epsilon={compare_epsilon:g}, reason={exc}"
                ) from exc

        if last_error is not None:
            raise last_error

        # 第七步：写触发寄存器。
        trigger = six_cmd.to_trigger_write()
        client.write_modbus_float(trigger)
        self._append_log("六轴", f"写入触发 {record.query_key}", "成功", "IEEE(32)=1")
        time.sleep(SIX_POST_TRIGGER_SETTLE_SEC)

    def _wait_six_command_done(self, client: ControllerClient, six_cmd: SixAxisCommand, record: QueryRecord) -> None:
        """等待六轴命令完成。"""
        status_read = self.service.build_six_status_read()
        system_state_read = self.service.build_six_system_state_read()
        curr_func_read = self.service.build_six_current_func_read()
        motion_state_read = self.service.build_six_motion_state_read()
        alarm_read = self.service.build_six_alarm_detail_read()
        poll_interval_sec = 0.05
        max_wait_sec = max(float(self.axis_ranges.motion_timeout_sec), poll_interval_sec)
        max_attempts = max(1, int(max_wait_sec / poll_interval_sec))
        saw_received = False
        saw_executing = False
        last_motion_state = 0
        # 三十四号长整型寄存器是主状态来源；其它寄存器作为诊断信息。
        # 只作为诊断信息同步读取，方便现场复盘卡在收到/执行/报警的哪一步。
        for _ in range(max_attempts):
            time.sleep(poll_interval_sec)
            vals = client.read_modbus_long(status_read)
            st = self.service.parse_six_status(vals, six_cmd.func_num)
            system_state_vals = client.read_modbus_long(system_state_read)
            system_state = self.service.parse_six_system_state(system_state_vals)
            curr_func_vals = client.read_modbus_float(curr_func_read)
            curr_func = self.service.parse_six_current_func(curr_func_vals)
            motion_state_vals = client.read_modbus_float(motion_state_read)
            motion_state = self.service.parse_six_motion_state(motion_state_vals)
            last_motion_state = motion_state

            if st.is_received or st.is_executing or st.is_complete:
                if st.is_received and not saw_received:
                    saw_received = True
                    self._append_log(
                        "六轴",
                        f"收到确认 {record.query_key}",
                        "成功",
                        f"LONG(34)={st.raw}, LONG(36)={system_state}, IEEE(322)={curr_func}",
                    )
                if st.is_executing and not saw_executing:
                    saw_executing = True
                    self._append_log(
                        "六轴",
                        f"执行中确认 {record.query_key}",
                        "成功",
                        f"LONG(34)={st.raw}, LONG(36)={system_state}, IEEE(322)={curr_func}, IEEE(56)={motion_state}",
                    )

            if st.has_error:
                alarm_vals = client.read_modbus_long(alarm_read)
                alarm_raw = alarm_vals[0] if alarm_vals else 0
                alarm_detail = self.service.parse_six_alarm_detail(alarm_vals)
                message = (
                    f"六轴执行错误: LONG(34)={st.raw}, LONG(36)={system_state}, LONG(38)={alarm_vals[0] if alarm_vals else 0}, "
                    f"IEEE(322)={curr_func}, IEEE(56)={motion_state}, 详情={alarm_detail}"
                )
                raise SixAxisCommandRuntimeError(
                    message,
                    status_raw=st.raw,
                    system_state=system_state,
                    alarm_raw=alarm_raw,
                    func_num=six_cmd.func_num,
                    curr_func=curr_func,
                    motion_state=motion_state,
                )
            if st.is_complete and not st.has_alarm:
                if not saw_received and not saw_executing:
                    self._append_log("六轴", f"快速完成 {record.query_key}", "成功", f"LONG(34)={st.raw}")
                self._append_log("六轴", f"执行完成 {record.query_key}", "成功", f"LONG(34)={st.raw}")
                break
            if st.is_complete and st.has_alarm:
                # 完成加报警：运动已结束，读取报警详情并记录警告，不中断流程。
                alarm_read = self.service.build_six_alarm_detail_read()
                alarm_vals = client.read_modbus_long(alarm_read)
                alarm_detail = self.service.parse_six_alarm_detail(alarm_vals)
                self._append_log("六轴", f"完成+报警 {record.query_key}", "警告",
                                 f"LONG(34)={st.raw}, 详情: {alarm_detail}")
                break
        else:
            raise RuntimeError(
                f"六轴执行超时: {record.query_key} | timeout={self._fmt(max_wait_sec)}s | IEEE(56)={last_motion_state}"
            )

    @staticmethod
    def _system_state_bit(val: int, bit: int) -> int:
        """处理系统状态位寄存器。"""
        return 1 if (int(val) & (1 << bit)) else 0

    def _format_func104_feedback_detail(self, six_status: SixAxisStatus, system_state: int) -> str:
        """格式化函数反馈详情。"""
        return (
            f"LONG(34)={six_status.raw}, LONG(36)={system_state} | "
            f"34.25={int(six_status.is_estop)} 34.26={int(six_status.is_paused)} "
            f"34.27={int(six_status.is_cancelled)} 34.28={int(six_status.is_ready)} | "
            f"36.00={self._system_state_bit(system_state, 0)} "
            f"36.01={self._system_state_bit(system_state, 1)} "
            f"36.02={self._system_state_bit(system_state, 2)} "
            f"36.03={self._system_state_bit(system_state, 3)} "
            f"36.04={self._system_state_bit(system_state, 4)} "
            f"36.05={self._system_state_bit(system_state, 5)}"
        )

    def _validate_func104_post_state(self, six_cmd: SixAxisCommand, six_status: SixAxisStatus, system_state: int) -> None:
        """校验函数状态。"""
        mismatches: list[str] = []
        if six_cmd.estop_ctrl == 1 and not six_status.is_estop:
            mismatches.append("急停按下后期望 34.25=1")
        if six_cmd.pause_ctrl == 1 and not six_status.is_paused:
            mismatches.append("暂停按下后期望 34.26=1")
        if six_cmd.pause_ctrl == 2 and six_status.is_paused:
            mismatches.append("暂停松开后期望 34.26=0")
        if six_cmd.cancel_ctrl == 1 and not six_status.is_cancelled:
            mismatches.append("结束按下后期望 34.27=1")
        if six_cmd.cancel_ctrl == 2 and six_status.is_cancelled:
            mismatches.append("结束松开后期望 34.27=0")
        if six_cmd.reset_ctrl == 1:
            if six_status.has_alarm:
                mismatches.append("报警复位后期望 34.24=0")
            if not six_status.is_ready:
                mismatches.append("报警复位后期望 34.28=1")
        if mismatches:
            detail = self._format_func104_feedback_detail(six_status, system_state)
            raise RuntimeError(f"Func104 状态确认失败: {'; '.join(mismatches)} | {detail}")

    def _wait_func104_done(self, client: ControllerClient, six_cmd: SixAxisCommand, label: str) -> None:
        """等待函数完成。"""
        status_read = self.service.build_six_status_read()
        system_state_read = self.service.build_six_system_state_read()
        alarm_read = self.service.build_six_alarm_detail_read()
        poll_interval_sec = 0.05
        max_wait_sec = max(float(self.axis_ranges.motion_timeout_sec), poll_interval_sec)
        max_attempts = max(1, int(max_wait_sec / poll_interval_sec))
        for _ in range(max_attempts):
            time.sleep(poll_interval_sec)
            vals = client.read_modbus_long(status_read)
            six_status = self.service.parse_six_status(vals, 104)
            system_state_vals = client.read_modbus_long(system_state_read)
            system_state = self.service.parse_six_system_state(system_state_vals)
            if six_status.has_error:
                alarm_vals = client.read_modbus_long(alarm_read)
                alarm_detail = self.service.parse_six_alarm_detail(alarm_vals)
                raise RuntimeError(
                    f"Func104 执行错误: {self._format_func104_feedback_detail(six_status, system_state)}, "
                    f"LONG(38)={alarm_vals[0] if alarm_vals else 0}, 详情={alarm_detail}"
                )
            if six_status.is_complete:
                self._validate_func104_post_state(six_cmd, six_status, system_state)
                self._append_log("六轴", f"Func104 完成 {label}", "成功", self._format_func104_feedback_detail(six_status, system_state))
                return
        raise RuntimeError(f"Func104 执行超时: {label} | timeout={self._fmt(max_wait_sec)}s")

    def _read_six_command_feedback(self, client: ControllerClient, six_cmd: SixAxisCommand, record: QueryRecord) -> list[float]:
        """读取六轴命令反馈。"""
        motion_state_read = self.service.build_six_motion_state_read()
        if six_cmd.func_num == 104:
            return []
        if six_cmd.func_num == 11:
            current_point = client.read_modbus_float(VrReadRequest(start_vr=640, count=1))
            total_points = client.read_modbus_float(VrReadRequest(start_vr=642, count=1))
            self._append_log(
                "六轴",
                f"Func11 反馈 {record.query_key}",
                "成功",
                f"当前点={int(current_point[0]) if current_point else 0}, 总点数={int(total_points[0]) if total_points else 0}",
            )
        elif six_cmd.func_num == 109:
            check_result = client.read_modbus_float(VrReadRequest(start_vr=326, count=1))
            elapsed = client.read_modbus_float(VrReadRequest(start_vr=330, count=1))
            self._append_log("六轴", f"Func109 反馈 {record.query_key}", "成功",
                             f"检测={check_result[0] if check_result else 0}, 延时={elapsed[0] if elapsed else 0}")
        elif six_cmd.func_num == 110:
            delay_state = client.read_modbus_float(VrReadRequest(start_vr=328, count=1))
            elapsed = client.read_modbus_float(VrReadRequest(start_vr=332, count=1))
            timer_ms = client.read_modbus_long(VrReadRequest(start_vr=40, count=1))
            self._append_log("六轴", f"Func110 反馈 {record.query_key}", "成功",
                             f"状态={delay_state[0] if delay_state else 0}, 延时={elapsed[0] if elapsed else 0}, 计时ms={timer_ms[0] if timer_ms else 0}")
        elif six_cmd.func_num == 120:
            y_state = client.read_modbus_long(VrReadRequest(start_vr=44, count=1))
            x_state = client.read_modbus_long(VrReadRequest(start_vr=46, count=1))
            self._append_log("六轴", f"Func120 反馈 {record.query_key}", "成功",
                             f"Y={y_state[0] if y_state else 0}, X={x_state[0] if x_state else 0}")

        # 第九步：读取回传数据，位姿和关节优先使用四点三版本反馈区。
        xyz_vals = client.read_modbus_float(self.service.build_six_pose_feedback_read())
        motion_state_vals = client.read_modbus_float(motion_state_read)
        joint_vals = client.read_modbus_float(self.service.build_six_joint_feedback_read())
        motion_state = self.service.parse_six_motion_state(motion_state_vals)
        optional_feedback = self._read_optional_v43_feedback(client)
        self._append_log("六轴", "回传数据", "成功",
                         f"X={xyz_vals[0]:.1f} Y={xyz_vals[1]:.1f} Z={xyz_vals[2]:.1f} "
                         f"Rx={xyz_vals[3]:.1f} Ry={xyz_vals[4]:.1f} Rz={xyz_vals[5]:.1f} | "
                         f"运动状态={motion_state} | "
                         f"J1={joint_vals[0]:.1f} J2={joint_vals[1]:.1f} J3={joint_vals[2]:.1f} "
                         f"J4={joint_vals[3]:.1f} J5={joint_vals[4]:.1f} J6={joint_vals[5]:.1f}"
                         f"{optional_feedback}")
        self.motion_percent = "运动中" if motion_state == 1 else "空闲"
        return xyz_vals

    def _read_optional_v43_feedback(self, client: ControllerClient) -> str:
        """读取反馈。"""
        try:
            speed = client.read_modbus_float(VrReadRequest(start_vr=52, count=1))[0]
            distance = client.read_modbus_float(VrReadRequest(start_vr=54, count=1))[0]
            ecat = client.read_modbus_float(VrReadRequest(start_vr=70, count=1))[0]
            frame = client.read_modbus_float(VrReadRequest(start_vr=72, count=1))[0]
        except Exception:
            return ""
        return (
            " | 可选反馈(待固件确认): "
            f"IEEE(52)={speed:.1f}, IEEE(54)={distance:.1f}, "
            f"IEEE(70)={int(ecat)}, IEEE(72)={int(frame)}"
        )

