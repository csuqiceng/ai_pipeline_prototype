"""系统控制按钮和实时反馈面板逻辑。"""

from __future__ import annotations

from typing import Callable

from .gui_constants import SYSTEM_COMMAND_CODES
from .models import QueryRecord


class GuiSystemMixin:
    """处理系统控制按钮和反馈状态展示。"""
    def _read_feedback(self) -> None:
        """读取反馈。"""
        host = self.host_edit.text().strip()
        log_extra = {"host": host, "controller_mode": self._controller_mode_value()}
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("反馈", "读取反馈", "失败", "地址为空", extra=log_extra)
            return
        try:
            values, read_request = self._read_feedback_once()
            self._apply_feedback_values(None, values)
            self._refresh_status_labels()
            self.monitor_label.setText("实时监控运行中")
            self.status_label.setText(f"反馈区读取成功: {values}")
            self._append_log(
                "反馈",
                "读取反馈",
                "成功",
                f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {values}",
                extra={**log_extra, "read_start_vr": read_request.start_vr, "read_count": read_request.count, "read_values": values},
            )
        except Exception as exc:
            self.status_label.setText(f"读取反馈区失败: {exc}")
            self.monitor_label.setText("实时监控离线")
            self._show_critical("读取失败", str(exc))
            self._append_log("反馈", "读取反馈", "失败", str(exc), extra={**log_extra, **self._log_exception_fields(exc)})

    def _set_status(self, text: str) -> None:
        """设置状态。"""
        self.status_label.setText(text)

    def _handle_system_action(self, action_key: str, *, on_done: Callable[[bool], None] | None = None) -> None:
        """处理系统。"""
        if self.flow_running and action_key not in {"sys_estop", "sys_pause", "sys_resume", "sys_cancel"}:
            self._show_warning("流程运行中", "流程执行中不允许发送系统按钮命令。")
            self._append_log("系统", action_key, "失败", "流程执行中")
            if on_done:
                on_done(False)
            return
        self._handle_system_action_six(action_key, on_done=on_done)

    def _handle_system_action_six(self, action_key: str, *, on_done: Callable[[bool], None] | None = None) -> None:
        """处理系统六轴。"""
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            if on_done:
                on_done(False)
            return
        code = SYSTEM_COMMAND_CODES[action_key]
        dispatch_extra = {
            "dispatch_id": self._next_dispatch_id(),
            "host": host,
            "controller_mode": self._controller_mode_value(),
            "task_id": self.task_id,
            "system_action": action_key,
            "command_code": code,
        }
        self._append_log(
            "系统",
            f"系统命令准备 {action_key}",
            "成功",
            f"code={code}",
            extra={**dispatch_extra, "command_snapshot": self._build_system_dispatch_snapshot(action_key, code)},
        )
        self._pause_polling()

        def work():
            """处理相关数据。"""
            with self._push_log_context(**dispatch_extra):
                client = self._get_client(host)
                six_cmd = self.service.build_six_system_command(code)

                if six_cmd.func_num < 0:
                    self._append_log("六轴", f"本地系统命令 {action_key}", "成功", f"func={six_cmd.func_num}")
                    return []
                record = QueryRecord(
                    query_key=action_key,
                    func_num=104,
                    params={
                        "stop_mode": six_cmd.stop_mode,
                        "estop_ctrl": six_cmd.estop_ctrl,
                        "pause_ctrl": six_cmd.pause_ctrl,
                        "cancel_ctrl": six_cmd.cancel_ctrl,
                        "reset_ctrl": six_cmd.reset_ctrl,
                    },
                    description=six_cmd.desc,
                )
                self._write_six_command(client, six_cmd, record)
                self._append_log("六轴", f"系统命令 {action_key}", "成功", f"func={six_cmd.func_num}", extra={"func_num": six_cmd.func_num})
                self._wait_func104_done(client, six_cmd, action_key)
                return []

        def on_result(result):
            """处理结果。"""
            self._resume_polling()
            if isinstance(result, Exception):
                self._disconnect_client()
                self.status_label.setText(f"六轴系统命令失败: {result}")
                self._show_critical("系统命令失败", str(result))
                self._append_log("系统", f"系统命令 {action_key}", "失败", str(result), extra={**dispatch_extra, **self._log_exception_fields(result)})
                if on_done:
                    on_done(False)
                return
            self._apply_legacy_system_action(action_key, update_status=True)
            self.task_id += 1
            self.status_label.setText(f"六轴 {action_key} 完成")
            self._refresh_status_labels()
            self._append_log("系统", f"系统命令 {action_key}", "成功", f"任务{self.task_id - 1}", extra={**dispatch_extra, "task_id": self.task_id - 1})
            if on_done:
                on_done(True)

        self._run_in_background(work, on_result)

    def _apply_legacy_system_action(self, action_key: str, *, update_status: bool = True) -> None:
        """应用系统。"""
        if action_key == "alarm_reset":
            if update_status:
                self._set_status("报警已复位")
            return
        if action_key == "sys_pause":
            if getattr(self, "flow_running", False):
                self.flow_paused = True
            self._set_mode_busy(self.mode_label.text(), False, "当前任务已暂停")
            self.busy = "暂停"
            self._refresh_status_labels()
            return
        if action_key == "sys_resume":
            should_resume_flow = bool(
                getattr(self, "flow_running", False)
                and getattr(self, "flow_paused", False)
                and getattr(self, "flow_status", "") == "已暂停"
            )
            self.flow_paused = False
            self._set_mode_busy(self.mode_label.text(), True, "当前任务继续运行")
            if should_resume_flow and hasattr(self, "_run_next_flow_step"):
                self._run_next_flow_step()
            return
        if action_key == "sys_cancel":
            self.flow_paused = False
            self._set_mode_busy(self.mode_label.text(), False, "当前任务已取消")
            return
        if action_key == "sys_estop":
            self.flow_paused = False
            self._trigger_estop()

    def _set_mode_busy(self, mode: str, busy: bool, text: str) -> None:
        """设置忙。"""
        self.mode = mode
        self.busy = "运行中" if busy else "空闲"
        self.status_label.setText(text)
        self._refresh_status_labels()

    def _trigger_estop(self) -> None:
        """触发相关数据。"""
        self.busy = "空闲"
        self.result = "9"
        self.alarm_code = "ERR_900"
        self.alarm_text = "急停触发"
        self.status_label.setText("急停触发，系统锁定")
        self._refresh_status_labels()

    def _apply_feedback_values(self, record: QueryRecord | None, values: list[float]) -> None:
        """应用反馈。"""
        if not values:
            return
        if len(values) >= 3:
            rt = self.service.parse_six_realtime([], values)
            self.result = "0"
            self.busy = "空闲"
            self.robot_x = self._fmt(rt.x)
            self.robot_y = self._fmt(rt.y)
            self.robot_z = self._fmt(rt.z)
            if len(values) >= 6:
                self.robot_r = f"{self._fmt(rt.rx)} / {self._fmt(rt.ry)} / {self._fmt(rt.rz)}"
            return
        result_code = int(values[0])
        status_code = int(values[1]) if len(values) > 1 else (1 if record else 0)
        self.result = str(result_code)
        self.busy = self._status_text(status_code)
        self.alarm_code = "ERR_000" if result_code == 0 else f"ERR_{result_code}"
        self.alarm_text = "系统正常" if result_code == 0 else "执行结果异常"
        self.io_status = "0"
        if len(values) > 4:
            self.robot_x = self._fmt(values[2])
            self.robot_y = self._fmt(values[3])
            self.robot_z = self._fmt(values[4])
        elif record and record.func_num == 108:
            pose = record.pose_tuple()
            if pose is not None:
                self.robot_x = self._fmt(pose[0])
                self.robot_y = self._fmt(pose[1])
                self.robot_z = self._fmt(pose[2])
        if record and record.func_num == 108:
            pose = record.pose_tuple()
            if pose is not None:
                self.robot_r = f"{self._fmt(pose[3])} / {self._fmt(pose[4])} / {self._fmt(pose[5])}"
                self.robot_speed = f"{self._fmt(record.spd_pct_value())} / {self._fmt(record.acc_pct_value())}"

