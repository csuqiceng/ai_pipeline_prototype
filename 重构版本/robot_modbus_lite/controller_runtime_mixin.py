"""控制器连接、轮询、缓存状态和主线程回调调度逻辑。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QTimer

try:
    from mock_controller import MockZMotionVrClient
except ModuleNotFoundError:
    from ..mock_controller import MockZMotionVrClient

from .background_tasks import run_background_thread
from .models import VrReadRequest


class ControllerRuntimeMixin:
    """维护控制器连接、后台轮询和线程安全界面回调。"""
    def _make_client(self, host: str):
        """处理客户端。"""
        if self.controller_combo.currentText() == "模拟控制器":
            return MockZMotionVrClient(host=host, axis_ranges=self.axis_ranges.to_dict())
        return self._client_factory(host, self.resource_root)

    def _get_client(self, host: str):
        """获取客户端。"""
        with self._client_cache_lock:
            if (
                self._cached_client is not None
                and self._cached_client_host == host
                and self._cached_client.connected
            ):
                return self._cached_client
            self._disconnect_client_locked()
            client = self._make_client(host)
            client.connect()
            self._cached_client = client
            self._cached_client_host = host
            return client

    def _disconnect_client(self) -> None:
        """断开连接客户端。"""
        with self._client_cache_lock:
            self._disconnect_client_locked()

    def _disconnect_client_locked(self) -> None:
        """断开连接客户端。"""
        if self._cached_client is not None:
            try:
                self._cached_client.disconnect()
            except Exception:
                pass
            self._cached_client = None
            self._cached_client_host = ""

    def closeEvent(self, event) -> None:
        """关闭相关数据。"""
        if self._mic_recorder_thread is not None:
            self._mic_recorder_thread.shutdown()
            self._mic_recorder_thread.wait(3000)
            self._mic_recorder_thread = None
        if getattr(self, "_local_voice_stream_stop_flag_path", None):
            try:
                from .voice_ipc import write_stop_flag

                write_stop_flag(self._local_voice_stream_stop_flag_path)
            except Exception:
                pass
        if hasattr(self, "_iflytek_local_client"):
            self._iflytek_local_client = None
        self._disconnect_client()
        super().closeEvent(event)

    def _start_realtime_polling(self) -> None:
        """启动实时数据。"""
        self.realtime_timer = QTimer(self)
        self.realtime_timer.setInterval(500)
        self.realtime_timer.timeout.connect(self._poll_feedback_silent)
        self.realtime_timer.start()

    def _pause_polling(self) -> None:
        """处理相关数据。"""
        self.realtime_timer.stop()

    def _resume_polling(self) -> None:
        """处理相关数据。"""
        if hasattr(self, "realtime_timer") and self.realtime_timer is not None:
            self.realtime_timer.start()

    def _run_on_main_thread(self, callback: Callable[[], None]) -> None:
        """运行主线程。"""
        self._main_thread_call.emit(callback)

    @staticmethod
    def _handle_main_thread_call(callback: Callable[[], None]) -> None:
        """进入程序线程。"""
        callback()

    def _run_in_background(self, work_fn: Callable, done_fn: Callable[[Any], None]) -> None:
        """运行后台。"""
        run_background_thread(work_fn, done_fn, self._run_on_main_thread)

    def _poll_feedback_silent(self) -> None:
        """轮询反馈。"""
        if self._polling_feedback:
            return
        host = self.host_edit.text().strip()
        if not host:
            self.monitor_label.setText("未启动")
            return
        self._polling_feedback = True
        try:
            client = self._get_client(host)
            joint_vals = client.read_modbus_float(self.service.build_six_joint_feedback_read())
            pose_vals = client.read_modbus_float(self.service.build_six_pose_feedback_read())
            rt = self.service.parse_six_realtime(joint_vals, pose_vals)
            self.robot_joints = (rt.j1, rt.j2, rt.j3, rt.j4, rt.j5, rt.j6)
            self.robot_x = self._fmt(rt.x)
            self.robot_y = self._fmt(rt.y)
            self.robot_z = self._fmt(rt.z)
            self.robot_r = f"{self._fmt(rt.rx)} / {self._fmt(rt.ry)} / {self._fmt(rt.rz)}"
            st_read = self.service.build_six_status_read()
            st_vals = client.read_modbus_long(st_read)
            six_status = self.service.parse_six_status(st_vals)
            system_state_vals = client.read_modbus_long(self.service.build_six_system_state_read())
            current_func_vals = client.read_modbus_float(self.service.build_six_current_func_read())
            alarm_detail_vals = client.read_modbus_long(self.service.build_six_alarm_detail_read())
            self.busy = "空闲" if six_status.can_send else "运行中"
            motion_vals = client.read_modbus_float(self.service.build_six_motion_state_read())
            motion_state = self.service.parse_six_motion_state(motion_vals)
            self.motion_percent = "运动中" if motion_state == 1 else "空闲"
            system_state = self.service.parse_six_system_state(system_state_vals)
            current_func = self.service.parse_six_current_func(current_func_vals)
            self.current_func_text = "空闲" if current_func == 0 else f"Func{current_func}"
            self.estop_active = six_status.is_estop
            self.pause_active = six_status.is_paused
            alarm_detail = self.service.parse_six_alarm_detail(alarm_detail_vals)
            if six_status.has_alarm:
                self.alarm_text = f"报警: {alarm_detail}"
                self.alarm_code = f"ERR_{six_status.raw}"
            elif six_status.has_error:
                self.alarm_text = f"错误 IEEE(34)={six_status.raw}"
                self.alarm_code = f"ERR_{six_status.raw}"
            else:
                self.alarm_text = "系统正常"
                self.alarm_code = "ERR_000"
            self.monitor_label.setText("实时监控运行中")
            self._refresh_status_labels()
            if not self._poll_started_logged:
                self._append_log("反馈", "实时监控轮询", "成功", "首次轮询成功，定时轮询已运行")
                self._poll_started_logged = True
            self._last_realtime_snapshot_raw = {
                "long34_raw": six_status.raw,
                "long36_raw": system_state,
                "long38_raw": alarm_detail,
                "ieee324_raw": current_func,
                "motion_state_56": motion_state,
            }
            self._log_realtime_state_change_if_needed()
            self._last_poll_error = ""
        except Exception as exc:
            error = str(exc)
            self._disconnect_client()
            self.monitor_label.setText("实时监控离线")
            self._refresh_overall_state_indicator()
            if error != self._last_poll_error:
                self._append_log("反馈", "实时监控", "失败", error, extra=self._log_exception_fields(exc))
                self._last_poll_error = error
        finally:
            self._polling_feedback = False

    def _read_feedback_once(self) -> tuple[list[float], VrReadRequest]:
        """读取反馈。"""
        host = self.host_edit.text().strip()
        client = self._get_client(host)
        read_request = self.service.build_six_pose_feedback_read()
        values = client.read_modbus_float(read_request)
        return values, read_request

