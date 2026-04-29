from __future__ import annotations

import threading
import time
from typing import Callable

from .protocol import (
    ACK_VR,
    ALM_EMERGENCY_STOP,
    ALM_NORMAL,
    ALM_OUT_OF_RANGE,
    ALM_SPEED_LIMIT,
    CMD,
    EXEC_TRIGGER_VR,
    FuncSixAxis,
    MIRROR_VR_COUNT,
    MIRROR_VR_START,
    MODBUS_FUNC_ADDR,
    MODBUS_STATUS_ADDR,
    MODBUS_TRIGGER_ADDR,
    MONITOR_VR_START,
    RESULT_FAIL,
    RESULT_OK,
    SAFETY_MIN_AUTO,
    SIX_ALARM_BIT,
    SIX_ALARM_DETAIL_ADDR,
    SIX_CURR_FUNC_ADDR,
    SIX_P_AXIS_NO,
    SIX_P_SPD,
    SIX_P_ACC_V,
    SIX_P_DEC_V,
    SIX_P_FUZZY_POS,
    SIX_P_FUZZY_SPD,
    SIX_P_FUZZY_ACC,
    SIX_P_FUZZY_DEC,
    SIX_P_STOP_CMD,
    SIX_P_POS_VAL,
    SIX_P_TARGET_X,
    SIX_P_TARGET_Y,
    SIX_P_TARGET_Z,
    SIX_P_TARGET_RX,
    SIX_P_TARGET_RY,
    SIX_P_TARGET_RZ,
    SIX_RT_J_START,
    SIX_RT_XYZ_START,
    SIX_SAFE_ACC_MAX,
    SIX_SAFE_DEC_MAX,
    SIX_SAFE_R_MIN,
    SIX_108_FUZZY_POS,
    SIX_108_FUZZY_SPD,
    SIX_108_FUZZY_ACC,
    SIX_108_FUZZY_DEC,
    SIX_108_MOVE_TYPE,
    SIX_108_SPD,
    SIX_108_ACC,
    SIX_108_DEC,
    SIX_108_STOP_CMD,
    SIX_SAFE_R_MAX,
    SIX_SAFE_SPD_MAX,
    SIX_SAFE_Z_MIN,
    SIX_SAFE_Z_MAX,
    SIX_STATUS_COMPLETE,
    SIX_STATUS_COMPLETE_ALARM,
    SIX_STATUS_ERROR,
    SIX_STATUS_ERROR_ALARM,
    SIX_STATUS_EXECUTING,
    SIX_STATUS_RECEIVED,
    SPEED_MAX_AUTO,
    SPEED_MAX_DANGER,
    SPEED_MAX_DEBUG,
    STATUS_FAULT,
    STATUS_IDLE,
    STATUS_PAUSED,
    STATUS_RUNNING,
    VR_OFFSET,
    VR_TOTAL,
    X_RANGE,
    Y_RANGE,
    Z_RANGE,
)


class ValidationError(Exception):
    pass


class MockController:
    def __init__(
        self,
        *,
        x_range: tuple[float, float] | None = None,
        y_range: tuple[float, float] | None = None,
        z_range: tuple[float, float] | None = None,
    ) -> None:
        self._vr: list[float] = [0.0] * VR_TOTAL
        # Modbus 寄存器
        self._modbus_ieee: list[float] = [0.0] * 2048
        self._modbus_bit: list[int] = [0] * 24000
        self._modbus_ieee[MODBUS_STATUS_ADDR] = 0.0
        self._modbus_bit[SIX_ALARM_BIT] = 0
        self._lock = threading.RLock()
        self._on_command: Callable[[int, dict[str, float]], None] | None = None
        self._exec_thread: threading.Thread | None = None
        self._exec_thread_six: threading.Thread | None = None
        self._x_range = x_range if x_range is not None else X_RANGE
        self._y_range = y_range if y_range is not None else Y_RANGE
        self._z_range = z_range if z_range is not None else Z_RANGE
        self._set_status(STATUS_IDLE)
        self._set_result(RESULT_OK)
        self._set_alarm(ALM_NORMAL)
        self._sync_realtime_monitor_locked()
        self._running = True

    def set_on_command(self, callback: Callable[[int, dict[str, float]], None]) -> None:
        self._on_command = callback

    def set_axis_ranges(
        self,
        *,
        x_range: tuple[float, float] | None = None,
        y_range: tuple[float, float] | None = None,
        z_range: tuple[float, float] | None = None,
    ) -> None:
        with self._lock:
            if x_range is not None:
                self._x_range = (float(x_range[0]), float(x_range[1]))
            if y_range is not None:
                self._y_range = (float(y_range[0]), float(y_range[1]))
            if z_range is not None:
                self._z_range = (float(z_range[0]), float(z_range[1]))

    def write_vr(self, start: int, values: list[float] | tuple[float, ...]) -> None:
        should_dispatch = False
        cmd_code = 0
        with self._lock:
            for i, v in enumerate(values):
                idx = start + i
                if idx < 0 or idx >= VR_TOTAL:
                    raise IndexError(f"VR[{idx}] 超出范围(0~{VR_TOTAL - 1})")
                self._vr[idx] = float(v)
            if start == VR_OFFSET["CMD_CODE"].index and values and values[0] != 0:
                cmd_code = int(values[0])
                self._mirror_command_locked()
                self._vr[ACK_VR] = 1.0
            elif start == EXEC_TRIGGER_VR and values and values[0] != 0:
                cmd_code = int(self._vr[VR_OFFSET["CMD_CODE"].index])
                should_dispatch = cmd_code != 0
        if should_dispatch:
            self._dispatch_command(cmd_code)

    def read_vr(self, start: int, count: int) -> list[float]:
        with self._lock:
            if start < 0 or start + count > VR_TOTAL:
                raise IndexError(f"读取范围 VR[{start}..{start + count - 1}] 超出范围")
            return list(self._vr[start : start + count])

    def read_all(self) -> list[float]:
        with self._lock:
            return list(self._vr)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {f.name: self._vr[f.index] for f in VR_OFFSET.values()}

    # ── Modbus IEEE/BIT 方法 ──────────────────────────────────────

    def write_modbus_float(self, start: int, values: list[float] | tuple[float, ...]) -> None:
        should_dispatch = False
        with self._lock:
            for i, v in enumerate(values):
                idx = start + i
                if idx < 0 or idx >= len(self._modbus_ieee):
                    raise IndexError(f"IEEE[{idx}] 超出范围")
                self._modbus_ieee[idx] = float(v)
            if start == MODBUS_TRIGGER_ADDR and values and values[0] != 0:
                should_dispatch = True

        if should_dispatch:
            self._dispatch_v30_command()

    def read_modbus_float(self, start: int, count: int) -> list[float]:
        with self._lock:
            if start < 0 or start + count > len(self._modbus_ieee):
                raise IndexError(f"读取范围 IEEE[{start}..{start + count - 1}] 超出范围")
            return list(self._modbus_ieee[start : start + count])

    def write_modbus_bit(self, start: int, values: list[int] | tuple[int, ...]) -> None:
        with self._lock:
            for i, v in enumerate(values):
                idx = start + i
                if idx < 0 or idx >= len(self._modbus_bit):
                    raise IndexError(f"BIT[{idx}] 超出范围")
                self._modbus_bit[idx] = int(v)
            # 报警复位: BIT(151)=1 时清除 IEEE(34) 和 IEEE(38)
            if start == SIX_ALARM_BIT and values and values[0] == 1:
                self._modbus_ieee[MODBUS_STATUS_ADDR] = 0.0
                self._modbus_ieee[SIX_ALARM_DETAIL_ADDR] = 0.0

    def read_modbus_bit(self, start: int, count: int) -> list[int]:
        with self._lock:
            if start < 0 or start + count > len(self._modbus_bit):
                raise IndexError(f"读取范围 BIT[{start}..{start + count - 1}] 超出范围")
            return list(self._modbus_bit[start : start + count])

    def shutdown(self) -> None:
        self._running = False

    def _dispatch_command(self, cmd_code: int) -> None:
        current_status = self._vr[VR_OFFSET["STATUS"].index]
        if current_status == STATUS_RUNNING:
            self._set_result(RESULT_FAIL)
            self._set_alarm(ALM_NORMAL)
            return

        fields = self.snapshot()
        try:
            self._validate(cmd_code, fields)
        except ValidationError as exc:
            self._set_result(RESULT_FAIL)
            self._set_status(STATUS_FAULT)
            return

        self._set_status(STATUS_RUNNING)
        self._set_result(RESULT_OK)

        if self._on_command:
            try:
                self._on_command(cmd_code, fields)
            except Exception:
                pass

        if self._exec_thread and self._exec_thread.is_alive():
            self._exec_thread.join(timeout=5.0)

        self._exec_thread = threading.Thread(
            target=self._execute_command, args=(cmd_code, fields), daemon=True
        )
        self._exec_thread.start()

    def _execute_command(self, cmd_code: int, fields: dict[str, float]) -> None:
        is_system_cmd = cmd_code in (
            CMD.SYS_RESET, CMD.SYS_ESTOP, CMD.SYS_PAUSE, CMD.SYS_RESUME,
            CMD.AUTO_START, CMD.AUTO_STOP, CMD.EMG_RESET,
        )
        try:
            if cmd_code == CMD.MOVE_ABS:
                self._do_move_abs(fields)
            elif cmd_code == CMD.MOVE_REL:
                self._do_move_rel(fields)
            elif cmd_code == CMD.HOME:
                self._do_home()
            elif cmd_code == CMD.GRIP_SET:
                self._do_grip(fields)
            elif cmd_code == CMD.DOOR_CTRL:
                self._do_door(fields)
            elif cmd_code == CMD.WAIT_MS:
                self._do_wait(fields)
            elif cmd_code == CMD.CHECK_IN:
                self._do_check_in()
            elif cmd_code == CMD.EMG_RESET:
                self._do_emg_reset()
            elif cmd_code == CMD.SYS_RESET:
                self._do_sys_reset()
            elif cmd_code == CMD.SYS_ESTOP:
                self._do_sys_estop()
            elif cmd_code == CMD.SYS_PAUSE:
                self._do_sys_pause()
            elif cmd_code == CMD.SYS_RESUME:
                self._do_sys_resume()
            elif cmd_code == CMD.AUTO_START:
                self._do_auto_start()
            elif cmd_code == CMD.AUTO_STOP:
                self._do_auto_stop()
            else:
                self._set_result(RESULT_FAIL)
        finally:
            with self._lock:
                if not is_system_cmd and self._vr[VR_OFFSET["STATUS"].index] == STATUS_RUNNING:
                    self._set_status(STATUS_IDLE)
                self._vr[VR_OFFSET["CMD_CODE"].index] = 0.0
                self._vr[EXEC_TRIGGER_VR] = 0.0
                self._vr[ACK_VR] = 0.0
                self._sync_realtime_monitor_locked()

    def _validate(self, cmd_code: int, fields: dict[str, float]) -> None:
        safety = fields.get("SAFETY_LV", 5)
        if safety < SAFETY_MIN_AUTO:
            raise ValidationError(f"安全等级 {safety} < {SAFETY_MIN_AUTO}，禁止自动运行")

        if cmd_code in (CMD.MOVE_ABS, CMD.MOVE_REL):
            speed = fields.get("SPD_PCT", 0)
            max_speed = SPEED_MAX_AUTO
            if safety < 3:
                max_speed = SPEED_MAX_DANGER
            elif safety < 5:
                max_speed = SPEED_MAX_DEBUG
            if speed > max_speed:
                self._set_alarm(ALM_SPEED_LIMIT)
                raise ValidationError(f"速度 {speed}% 超出安全限制 {max_speed}%")

            if cmd_code == CMD.MOVE_ABS:
                x, y, z = fields.get("POS_X", 0), fields.get("POS_Y", 0), fields.get("POS_Z", 0)
                if not (self._x_range[0] <= x <= self._x_range[1]):
                    self._set_alarm(ALM_OUT_OF_RANGE)
                    raise ValidationError(f"X={x} 超出范围 {self._x_range}")
                if not (self._y_range[0] <= y <= self._y_range[1]):
                    self._set_alarm(ALM_OUT_OF_RANGE)
                    raise ValidationError(f"Y={y} 超出范围 {self._y_range}")
                if not (self._z_range[0] <= z <= self._z_range[1]):
                    self._set_alarm(ALM_OUT_OF_RANGE)
                    raise ValidationError(f"Z={z} 超出范围 {self._z_range}")

    def _do_move_abs(self, fields: dict[str, float]) -> None:
        target_x = fields.get("POS_X", 0)
        target_y = fields.get("POS_Y", 0)
        target_z = fields.get("POS_Z", 0)
        target_rx = fields.get("ROT_RX", 0)
        target_ry = fields.get("ROT_RY", 0)
        target_rz = fields.get("ROT_RZ", 0)

        with self._lock:
            cur_x = self._vr[VR_OFFSET["CUR_X"].index]
            cur_y = self._vr[VR_OFFSET["CUR_Y"].index]
            cur_z = self._vr[VR_OFFSET["CUR_Z"].index]

        steps = 5
        for i in range(1, steps + 1):
            if not self._running:
                break
            t = i / steps
            with self._lock:
                self._vr[VR_OFFSET["CUR_X"].index] = cur_x + (target_x - cur_x) * t
                self._vr[VR_OFFSET["CUR_Y"].index] = cur_y + (target_y - cur_y) * t
                self._vr[VR_OFFSET["CUR_Z"].index] = cur_z + (target_z - cur_z) * t
                self._vr[VR_OFFSET["CUR_RX"].index] = target_rx
                self._vr[VR_OFFSET["CUR_RY"].index] = target_ry
                self._vr[VR_OFFSET["CUR_RZ"].index] = target_rz
                self._sync_realtime_monitor_locked()
            time.sleep(0.02)

    def _do_move_rel(self, fields: dict[str, float]) -> None:
        with self._lock:
            base_x = self._vr[VR_OFFSET["CUR_X"].index]
            base_y = self._vr[VR_OFFSET["CUR_Y"].index]
            base_z = self._vr[VR_OFFSET["CUR_Z"].index]

        abs_fields = dict(fields)
        abs_fields["POS_X"] = base_x + fields.get("POS_X", 0)
        abs_fields["POS_Y"] = base_y + fields.get("POS_Y", 0)
        abs_fields["POS_Z"] = base_z + fields.get("POS_Z", 0)
        self._do_move_abs(abs_fields)

    def _do_home(self) -> None:
        with self._lock:
            self._vr[VR_OFFSET["CUR_X"].index] = 0.0
            self._vr[VR_OFFSET["CUR_Y"].index] = 0.0
            self._vr[VR_OFFSET["CUR_Z"].index] = 0.0
            self._vr[VR_OFFSET["CUR_RX"].index] = 0.0
            self._vr[VR_OFFSET["CUR_RY"].index] = 0.0
            self._vr[VR_OFFSET["CUR_RZ"].index] = 0.0
            self._sync_realtime_monitor_locked()
        time.sleep(0.3)

    def _do_grip(self, fields: dict[str, float]) -> None:
        grip_val = fields.get("IO_GRIP", 0)
        with self._lock:
            self._vr[VR_OFFSET["IO_GRIP"].index] = grip_val
            io_stat = int(self._vr[VR_OFFSET["IO_STAT"].index])
            if grip_val:
                io_stat |= (1 << 2)
            else:
                io_stat &= ~(1 << 2)
            self._vr[VR_OFFSET["IO_STAT"].index] = float(io_stat)
            self._sync_realtime_monitor_locked()
        time.sleep(0.1)

    def _do_door(self, fields: dict[str, float]) -> None:
        dev_id = int(fields.get("DEV_ID", 0))
        door_val = fields.get("IO_DOOR", 0)
        with self._lock:
            self._vr[VR_OFFSET["IO_DOOR"].index] = door_val
            io_stat = int(self._vr[VR_OFFSET["IO_STAT"].index])
            bit = 0 if dev_id == 1 else 1
            if door_val:
                io_stat |= (1 << bit)
            else:
                io_stat &= ~(1 << bit)
            self._vr[VR_OFFSET["IO_STAT"].index] = float(io_stat)
            self._sync_realtime_monitor_locked()
        time.sleep(0.1)

    def _do_wait(self, fields: dict[str, float]) -> None:
        delay_ms = fields.get("EXT_P1", 0)
        time.sleep(min(delay_ms / 1000.0, 2.0))

    def _do_check_in(self) -> None:
        time.sleep(0.05)

    def _do_emg_reset(self) -> None:
        with self._lock:
            self._vr[VR_OFFSET["ALM_CODE"].index] = float(ALM_NORMAL)
            if self._vr[VR_OFFSET["STATUS"].index] == STATUS_FAULT:
                self._set_status(STATUS_IDLE)
            self._set_result(RESULT_OK)
            self._sync_realtime_monitor_locked()

    def _do_sys_reset(self) -> None:
        with self._lock:
            self._set_alarm(ALM_NORMAL)
            self._set_result(RESULT_OK)
            self._set_status(STATUS_IDLE)

    def _do_sys_estop(self) -> None:
        with self._lock:
            self._set_alarm(ALM_EMERGENCY_STOP)
            self._set_result(RESULT_FAIL)
            self._set_status(STATUS_FAULT)

    def _do_sys_pause(self) -> None:
        with self._lock:
            self._set_result(RESULT_OK)
            self._set_status(STATUS_PAUSED)

    def _do_sys_resume(self) -> None:
        with self._lock:
            self._set_result(RESULT_OK)
            self._set_alarm(ALM_NORMAL)
            self._set_status(STATUS_RUNNING)
        time.sleep(0.05)

    def _do_auto_start(self) -> None:
        with self._lock:
            self._set_result(RESULT_OK)
            self._set_alarm(ALM_NORMAL)
            self._set_status(STATUS_RUNNING)
        time.sleep(0.05)

    def _do_auto_stop(self) -> None:
        with self._lock:
            self._set_result(RESULT_OK)
            self._set_alarm(ALM_NORMAL)
            self._set_status(STATUS_IDLE)

    def _set_status(self, status: int) -> None:
        self._vr[VR_OFFSET["STATUS"].index] = float(status)
        self._sync_realtime_monitor_locked()

    def _set_result(self, result: int) -> None:
        self._vr[VR_OFFSET["RESULT"].index] = float(result)

    def _set_alarm(self, code: int) -> None:
        self._vr[VR_OFFSET["ALM_CODE"].index] = float(code)
        self._sync_realtime_monitor_locked()

    def _mirror_command_locked(self) -> None:
        source = self._vr[VR_OFFSET["CMD_CODE"].index : VR_OFFSET["CMD_CODE"].index + MIRROR_VR_COUNT]
        self._vr[MIRROR_VR_START : MIRROR_VR_START + MIRROR_VR_COUNT] = list(source)

    def _sync_realtime_monitor_locked(self) -> None:
        self._vr[MONITOR_VR_START + 0] = self._vr[VR_OFFSET["CUR_X"].index]
        self._vr[MONITOR_VR_START + 1] = self._vr[VR_OFFSET["CUR_Y"].index]
        self._vr[MONITOR_VR_START + 2] = self._vr[VR_OFFSET["CUR_Z"].index]
        self._vr[MONITOR_VR_START + 3] = self._vr[VR_OFFSET["CUR_RX"].index]
        self._vr[MONITOR_VR_START + 4] = self._vr[VR_OFFSET["CUR_RY"].index]
        self._vr[MONITOR_VR_START + 5] = self._vr[VR_OFFSET["CUR_RZ"].index]
        self._vr[MONITOR_VR_START + 6] = self._vr[VR_OFFSET["IO_GRIP"].index]
        self._vr[MONITOR_VR_START + 7] = 0.0
        self._vr[MONITOR_VR_START + 8] = 0.0 if self._vr[VR_OFFSET["STATUS"].index] == STATUS_FAULT else 1.0
        self._vr[MONITOR_VR_START + 9] = self._vr[VR_OFFSET["STATUS"].index]
        self._vr[MONITOR_VR_START + 10] = self._vr[VR_OFFSET["ALM_CODE"].index]
        self._vr[MONITOR_VR_START + 11] = self._vr[VR_OFFSET["IO_STAT"].index]
        self._vr[MONITOR_VR_START + 12] = self._vr[VR_OFFSET["TASK_ID"].index]
        self._vr[MONITOR_VR_START + 13] = self._vr[VR_OFFSET["CMD_CODE"].index]
        self._vr[MONITOR_VR_START + 14] = 100.0 if self._vr[VR_OFFSET["STATUS"].index] != STATUS_RUNNING else 50.0
        self._vr[MONITOR_VR_START + 15] = self._vr[ACK_VR]
        self._vr[MONITOR_VR_START + 16] = self._vr[EXEC_TRIGGER_VR]
        self._vr[MONITOR_VR_START + 17] = 0.0
        self._vr[MONITOR_VR_START + 18] = 0.0
        self._vr[MONITOR_VR_START + 19] = 0.0

    # ── Modbus 命令分发 (V2.2) ──────────────────────────────────────

    def _dispatch_v30_command(self) -> None:
        self._dispatch_six_command()

    # ── 六轴机械手命令分发 (VPLC516E) ───────────────────────────────

    def _dispatch_six_command(self) -> None:
        with self._lock:
            raw_status = int(self._modbus_ieee[MODBUS_STATUS_ADDR])
            # Func104可在执行中调用
            func_num = int(self._modbus_ieee[MODBUS_FUNC_ADDR])
            if func_num == 0:
                return
            if func_num != FuncSixAxis.STOP and raw_status in (SIX_STATUS_EXECUTING, SIX_STATUS_RECEIVED):
                return
            self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_RECEIVED)

        if self._exec_thread_six and self._exec_thread_six.is_alive():
            self._exec_thread_six.join(timeout=5.0)

        self._exec_thread_six = threading.Thread(
            target=self._execute_six_command, args=(func_num,), daemon=True
        )
        self._exec_thread_six.start()

    def _execute_six_command(self, func_num: int) -> None:
        with self._lock:
            self._modbus_ieee[SIX_CURR_FUNC_ADDR] = float(func_num)
            self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_EXECUTING)

        try:
            if func_num == FuncSixAxis.STOP:
                self._do_six_stop()
            elif func_num == FuncSixAxis.JOINT_JOG:
                self._do_six_joint_jog()
            elif func_num == FuncSixAxis.VIRTUAL_JOG:
                self._do_six_virtual_jog()
            elif func_num == FuncSixAxis.LINE_MOVE:
                self._do_six_line_move()
            else:
                with self._lock:
                    self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_ERROR)
        finally:
            with self._lock:
                if int(self._modbus_ieee[MODBUS_STATUS_ADDR]) == SIX_STATUS_EXECUTING:
                    self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_COMPLETE)
                self._modbus_ieee[MODBUS_TRIGGER_ADDR] = 0.0
                # 六轴: 不清零IEEE(0)函数号
                self._sync_six_realtime_locked()

    def _do_six_stop(self) -> None:
        with self._lock:
            self._modbus_ieee[56] = 0.0
            self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_COMPLETE)
            self._set_status(STATUS_IDLE)

    def _do_six_joint_jog(self) -> None:
        with self._lock:
            axis_no = int(self._modbus_ieee[SIX_P_AXIS_NO])
            pos_val = self._modbus_ieee[SIX_P_POS_VAL]
            spd = self._modbus_ieee[SIX_P_SPD]
            acc_v = self._modbus_ieee[SIX_P_ACC_V]
            dec_v = self._modbus_ieee[SIX_P_DEC_V]
            fuzzy_pos = int(self._modbus_ieee[SIX_P_FUZZY_POS])
            fuzzy_spd = int(self._modbus_ieee[SIX_P_FUZZY_SPD])
            fuzzy_acc = int(self._modbus_ieee[SIX_P_FUZZY_ACC])
            fuzzy_dec = int(self._modbus_ieee[SIX_P_FUZZY_DEC])
            stop_cmd = int(self._modbus_ieee[SIX_P_STOP_CMD])
            current = self._modbus_ieee[SIX_RT_J_START + axis_no]
            current_speed = self._modbus_ieee[52]

            if fuzzy_pos == 1:
                target_pos = current + pos_val
            else:
                target_pos = pos_val
            if fuzzy_spd == 1:
                spd += current_speed
            if fuzzy_acc == 1:
                acc_v += self._modbus_ieee[SIX_P_ACC_V]
            if fuzzy_dec == 1:
                dec_v += self._modbus_ieee[SIX_P_DEC_V]
            spd, acc_v, dec_v, alarm_bits = self._clamp_six_motion_values_locked(spd, acc_v, dec_v)
            self._modbus_ieee[52] = spd
            self._modbus_ieee[56] = 0.0
            self._modbus_ieee[SIX_ALARM_DETAIL_ADDR] = float(alarm_bits)
            if spd <= 0:
                self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_ERROR_ALARM if alarm_bits else SIX_STATUS_ERROR)
                return
            stop_status = self._apply_six_stop_cmd_locked(stop_cmd, alarm_bits)
            if stop_status is not None:
                self._modbus_ieee[MODBUS_STATUS_ADDR] = float(stop_status)
                return

        steps = 3
        for i in range(1, steps + 1):
            if not self._running:
                break
            t = i / steps
            with self._lock:
                self._modbus_ieee[56] = 1.0
                self._modbus_ieee[SIX_RT_J_START + axis_no] = current + (target_pos - current) * t
                self._sync_six_realtime_locked()
            time.sleep(0.02)
        with self._lock:
            self._modbus_ieee[56] = 0.0

    def _do_six_virtual_jog(self) -> None:
        with self._lock:
            axis_no = int(self._modbus_ieee[SIX_P_AXIS_NO])
            pos_val = self._modbus_ieee[SIX_P_POS_VAL]
            spd = self._modbus_ieee[SIX_P_SPD]
            acc_v = self._modbus_ieee[SIX_P_ACC_V]
            dec_v = self._modbus_ieee[SIX_P_DEC_V]
            fuzzy_pos = int(self._modbus_ieee[SIX_P_FUZZY_POS])
            fuzzy_spd = int(self._modbus_ieee[SIX_P_FUZZY_SPD])
            fuzzy_acc = int(self._modbus_ieee[SIX_P_FUZZY_ACC])
            fuzzy_dec = int(self._modbus_ieee[SIX_P_FUZZY_DEC])
            stop_cmd = int(self._modbus_ieee[SIX_P_STOP_CMD])
            # 虚拟轴6~11映射到IEEE(1512+): 6→1512, 7→1513, ...
            target_idx = SIX_RT_XYZ_START + (axis_no - 6)
            current = self._modbus_ieee[target_idx]
            current_speed = self._modbus_ieee[52]

            if fuzzy_pos == 1:
                target_pos = current + pos_val
            else:
                target_pos = pos_val
            if fuzzy_spd == 1:
                spd += current_speed
            if fuzzy_acc == 1:
                acc_v += self._modbus_ieee[SIX_P_ACC_V]
            if fuzzy_dec == 1:
                dec_v += self._modbus_ieee[SIX_P_DEC_V]
            spd, acc_v, dec_v, alarm_bits = self._clamp_six_motion_values_locked(spd, acc_v, dec_v)
            self._modbus_ieee[52] = spd
            self._modbus_ieee[56] = 0.0
            self._modbus_ieee[SIX_ALARM_DETAIL_ADDR] = float(alarm_bits)
            if spd <= 0:
                self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_ERROR_ALARM if alarm_bits else SIX_STATUS_ERROR)
                return
            stop_status = self._apply_six_stop_cmd_locked(stop_cmd, alarm_bits)
            if stop_status is not None:
                self._modbus_ieee[MODBUS_STATUS_ADDR] = float(stop_status)
                return

        steps = 3
        for i in range(1, steps + 1):
            if not self._running:
                break
            t = i / steps
            with self._lock:
                self._modbus_ieee[56] = 1.0
                self._modbus_ieee[target_idx] = current + (target_pos - current) * t
                self._sync_six_realtime_locked()
            time.sleep(0.02)
        with self._lock:
            self._modbus_ieee[56] = 0.0

        # 检查安全限位，模拟报警
        self._check_six_safety_limit()

    def _do_six_line_move(self) -> None:
        with self._lock:
            targets = [
                self._modbus_ieee[SIX_P_TARGET_X],
                self._modbus_ieee[SIX_P_TARGET_Y],
                self._modbus_ieee[SIX_P_TARGET_Z],
                self._modbus_ieee[SIX_P_TARGET_RX],
                self._modbus_ieee[SIX_P_TARGET_RY],
                self._modbus_ieee[SIX_P_TARGET_RZ],
            ]
            stop_cmd = int(self._modbus_ieee[SIX_108_STOP_CMD])
            fuzzy_pos = int(self._modbus_ieee[SIX_108_FUZZY_POS])
            fuzzy_spd = int(self._modbus_ieee[SIX_108_FUZZY_SPD])
            fuzzy_acc = int(self._modbus_ieee[SIX_108_FUZZY_ACC])
            fuzzy_dec = int(self._modbus_ieee[SIX_108_FUZZY_DEC])
            move_type = int(self._modbus_ieee[SIX_108_MOVE_TYPE])
            current_speed = self._modbus_ieee[52]
            speed = self._modbus_ieee[SIX_108_SPD]
            acc_v = self._modbus_ieee[SIX_108_ACC]
            dec_v = self._modbus_ieee[SIX_108_DEC]
            if fuzzy_pos == 1:
                for i in range(6):
                    targets[i] = self._modbus_ieee[SIX_RT_XYZ_START + i] + targets[i]
            if fuzzy_spd == 1:
                speed += current_speed
            if fuzzy_acc == 1:
                acc_v += self._modbus_ieee[16]
            if fuzzy_dec == 1:
                dec_v += self._modbus_ieee[18]
            currents = [self._modbus_ieee[SIX_RT_XYZ_START + i] for i in range(6)]
            targets, speed, acc_v, dec_v, alarm_bits = self._apply_six_func108_limits_locked(
                targets, speed, acc_v, dec_v
            )
            self._modbus_ieee[52] = speed
            self._modbus_ieee[54] = sum(abs(targets[i] - currents[i]) for i in range(3))
            self._modbus_ieee[56] = 0.0
            self._modbus_ieee[SIX_ALARM_DETAIL_ADDR] = float(alarm_bits)

            stop_status = self._apply_six_stop_cmd_locked(stop_cmd, alarm_bits)
            if stop_status is not None:
                self._modbus_ieee[MODBUS_STATUS_ADDR] = float(stop_status)
                return

        steps = 3 if move_type == 1 else 5
        for i in range(1, steps + 1):
            if not self._running:
                break
            t = i / steps
            with self._lock:
                self._modbus_ieee[56] = 1.0
                for j in range(6):
                    self._modbus_ieee[SIX_RT_XYZ_START + j] = currents[j] + (targets[j] - currents[j]) * t
                remaining = sum(abs(targets[j] - self._modbus_ieee[SIX_RT_XYZ_START + j]) for j in range(3))
                self._modbus_ieee[54] = remaining
                self._sync_six_realtime_locked()
            time.sleep(0.02)
        with self._lock:
            self._modbus_ieee[56] = 0.0

        # 检查安全限位，模拟报警
        self._check_six_safety_limit()

    def _apply_six_func108_limits_locked(
        self,
        targets: list[float],
        speed: float,
        acc_v: float,
        dec_v: float,
    ) -> tuple[list[float], float, float, float, int]:
        alarm_bits = 0
        target_x, target_y, target_z = targets[0], targets[1], targets[2]
        min_r = self._modbus_ieee[SIX_SAFE_R_MIN]
        max_r = self._modbus_ieee[SIX_SAFE_R_MAX]
        min_z = self._modbus_ieee[SIX_SAFE_Z_MIN]
        max_z = self._modbus_ieee[SIX_SAFE_Z_MAX]
        safe_spd = self._modbus_ieee[SIX_SAFE_SPD_MAX]
        safe_acc = self._modbus_ieee[SIX_SAFE_ACC_MAX]
        safe_dec = self._modbus_ieee[SIX_SAFE_DEC_MAX]

        radius = abs(target_x)
        if min_r > 0 and radius < min_r:
            target_x = min_r if target_x >= 0 else -min_r
            alarm_bits |= 1
        if max_r > 0 and radius > max_r:
            target_x = max_r if target_x >= 0 else -max_r
            alarm_bits |= 1
        if min_z or max_z:
            if min_z and target_z < min_z:
                target_z = min_z
                alarm_bits |= 2
            if max_z and target_z > max_z:
                target_z = max_z
                alarm_bits |= 2
        if safe_spd > 0 and (speed > safe_spd or speed <= 0):
            speed = safe_spd
            alarm_bits |= 8
        if safe_acc > 0 and acc_v > safe_acc:
            acc_v = safe_acc
            alarm_bits |= 16
        if safe_dec > 0 and dec_v > safe_dec:
            dec_v = safe_dec
            alarm_bits |= 32

        targets[0] = target_x
        targets[2] = target_z
        return targets, speed, acc_v, dec_v, alarm_bits

    def _clamp_six_motion_values_locked(
        self,
        speed: float,
        acc_v: float,
        dec_v: float,
    ) -> tuple[float, float, float, int]:
        alarm_bits = 0
        safe_spd = self._modbus_ieee[SIX_SAFE_SPD_MAX]
        safe_acc = self._modbus_ieee[SIX_SAFE_ACC_MAX]
        safe_dec = self._modbus_ieee[SIX_SAFE_DEC_MAX]

        if speed <= 0:
            alarm_bits |= 8
            return speed, acc_v, dec_v, alarm_bits
        if safe_spd > 0 and speed > safe_spd:
            speed = safe_spd
            alarm_bits |= 8
        if safe_acc > 0 and acc_v > safe_acc:
            acc_v = safe_acc
            alarm_bits |= 16
        if safe_dec > 0 and dec_v > safe_dec:
            dec_v = safe_dec
            alarm_bits |= 32
        return speed, acc_v, dec_v, alarm_bits

    def _apply_six_stop_cmd_locked(self, stop_cmd: int, alarm_bits: int) -> int | None:
        if stop_cmd <= 0:
            return None
        self._modbus_ieee[56] = 0.0
        self._modbus_ieee[54] = 0.0
        if stop_cmd in (1, 2):
            self._set_status(STATUS_IDLE)
        elif stop_cmd in (3, 4):
            self._set_status(STATUS_IDLE)
        elif stop_cmd == 5:
            self._set_status(STATUS_PAUSED)
        else:
            self._set_status(STATUS_IDLE)
        return SIX_STATUS_COMPLETE_ALARM if alarm_bits else SIX_STATUS_COMPLETE

    def _check_six_safety_limit(self) -> None:
        """检查六轴运动是否超出安全限位，超出则设置报警"""
        with self._lock:
            targets = [
                self._modbus_ieee[SIX_RT_XYZ_START + i]
                for i in range(6)
            ]
            targets, speed, _acc_v, _dec_v, alarm_bits = self._apply_six_func108_limits_locked(
                targets,
                self._modbus_ieee[52],
                self._modbus_ieee[SIX_108_ACC],
                self._modbus_ieee[SIX_108_DEC],
            )
            self._modbus_ieee[SIX_RT_XYZ_START] = targets[0]
            self._modbus_ieee[SIX_RT_XYZ_START + 2] = targets[2]
            self._modbus_ieee[52] = speed
            if alarm_bits:
                self._modbus_ieee[SIX_ALARM_DETAIL_ADDR] = float(alarm_bits)
                self._modbus_ieee[MODBUS_STATUS_ADDR] = float(SIX_STATUS_COMPLETE_ALARM)

    def _sync_six_realtime_locked(self) -> None:
        """同步六轴实时位姿到 VR 与文档反馈地址。"""
        for i in range(6):
            self._modbus_ieee[1500 + i * 2] = self._modbus_ieee[SIX_RT_J_START + i]
            self._modbus_ieee[1512 + i * 2] = self._modbus_ieee[SIX_RT_XYZ_START + i]
        self._vr[VR_OFFSET["CUR_X"].index] = self._modbus_ieee[SIX_RT_XYZ_START]
        self._vr[VR_OFFSET["CUR_Y"].index] = self._modbus_ieee[SIX_RT_XYZ_START + 1]
        self._vr[VR_OFFSET["CUR_Z"].index] = self._modbus_ieee[SIX_RT_XYZ_START + 2]
        self._vr[VR_OFFSET["CUR_RX"].index] = self._modbus_ieee[SIX_RT_XYZ_START + 3]
        self._vr[VR_OFFSET["CUR_RY"].index] = self._modbus_ieee[SIX_RT_XYZ_START + 4]
        self._vr[VR_OFFSET["CUR_RZ"].index] = self._modbus_ieee[SIX_RT_XYZ_START + 5]
        self._sync_realtime_monitor_locked()
