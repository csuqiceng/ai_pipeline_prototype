"""为主窗口提供模板指令校验、发送和结果处理能力。"""

from __future__ import annotations

from typing import Any, Callable

from .gui_constants import FUNC_LABELS
from .models import QueryRecord


class CommandDispatchMixin:
    """为主窗口增加指令发送和反馈处理能力。"""
    def _check_connection(self) -> None:
        """检查相关数据。"""
        host = self.host_edit.text().strip()
        log_extra = {"host": host, "controller_mode": self._controller_mode_value()}
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("连接", "检测连接", "失败", "地址为空", extra=log_extra)
            return
        try:
            self._disconnect_client()
            client = self._get_client(host)
            mode = "Mock" if self.controller_combo.currentText() == "模拟控制器" else "真实"
            self.connection_label.setText(f"{mode}连接成功: {host}")
            self.monitor_label.setText("实时监控运行中")
            self._refresh_overall_state_indicator()
            self._append_log("连接", "检测连接", "成功", f"{mode}连接成功: {host}", extra=log_extra)
        except Exception as exc:
            self._disconnect_client()
            self.connection_label.setText("连接失败")
            self.monitor_label.setText("实时监控离线")
            self._refresh_overall_state_indicator()
            self._append_log("连接", "检测连接", "失败", str(exc), extra={**log_extra, **self._log_exception_fields(exc)})

    def _send_record(self, query_key: str) -> None:
        """处理记录。"""
        if self.flow_running:
            self._show_warning("流程运行中", "当前流程执行中，请先停止流程或等待流程完成。")
            self._append_log("执行", f"发送指令 {query_key}", "失败", "流程执行中，拒绝手动执行")
            return
        self._execute_query_key(query_key)

    def _execute_query_key(
        self,
        query_key: str,
        *,
        on_done: Callable[[bool], None] | None = None,
        show_error_dialog: bool = True,
        should_process: Callable[[], bool] | None = None,
        log_extra: dict[str, Any] | None = None,
    ) -> None:
        """执行查询。"""
        host = self.host_edit.text().strip()
        dispatch_id = self._next_dispatch_id()
        dispatch_extra = {
            "dispatch_id": dispatch_id,
            "host": host,
            "controller_mode": self._controller_mode_value(),
            "task_id": self.task_id,
            "root_query_key": query_key,
        }
        if log_extra:
            dispatch_extra.update({key: value for key, value in log_extra.items() if value is not None})
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("执行", f"发送指令 {query_key}", "失败", "地址为空", extra=dispatch_extra)
            if on_done:
                on_done(False)
            return
        try:
            record = self.table[query_key]
            plan_records, plan_reason = self._build_execution_plan(record)
            for plan_record in plan_records:
                validation_error = self._validate_record(plan_record)
                if validation_error:
                    raise ValueError(validation_error)
            self._append_log(
                "执行",
                f"发送准备 {query_key}",
                "成功",
                plan_reason,
                extra={
                    **dispatch_extra,
                    "query_key": record.query_key,
                    "func_num": record.func_num,
                    "plan_step_total": len(plan_records),
                    "plan_reason": plan_reason,
                    "plan_records": [self._build_record_dispatch_snapshot(item) for item in plan_records],
                },
            )
        except Exception as exc:
            fallback = self.table.get(query_key)
            if isinstance(fallback, QueryRecord):
                if not self.history or self.history[0]["task"] != self.task_id:
                    self._after_send(
                        fallback,
                        False,
                        str(exc),
                        log_extra={
                            **dispatch_extra,
                            "query_key": fallback.query_key,
                            "func_num": fallback.func_num,
                            **self._log_exception_fields(exc),
                        },
                        show_error_dialog=show_error_dialog,
                    )
            if on_done:
                on_done(False)
            return

        self._pause_polling()

        def work():
            """处理相关数据。"""
            with self._push_log_context(**dispatch_extra):
                client = self._get_client(host)
                if len(plan_records) > 1:
                    self._append_log("执行", f"规避判断 {query_key}", "成功", plan_reason)
                results = []
                step_failed = False
                for idx, plan_record in enumerate(plan_records, start=1):
                    step_extra = {
                        "query_key": plan_record.query_key,
                        "func_num": plan_record.func_num,
                        "plan_step_index": idx,
                        "plan_step_total": len(plan_records),
                    }
                    with self._push_log_context(**step_extra):
                        if len(plan_records) > 1:
                            self._append_log(
                                "执行",
                                f"规避执行第{idx}步",
                                "成功",
                                f"{plan_record.query_key} | {plan_record.description or '-'}",
                            )
                        feedback = self._execute_send_by_protocol(client, plan_record)
                        step_ok, step_error = self._evaluate_feedback_result(feedback)
                        results.append((plan_record, step_ok, step_error, feedback, step_extra))
                        if not step_ok:
                            step_failed = True
                            break
                return results, step_failed

        def on_result(result):
            """处理结果。"""
            self._resume_polling()
            if should_process is not None and not should_process():
                return
            if isinstance(result, Exception):
                self._disconnect_client()
                fallback = plan_records[0] if plan_records else self.table.get(query_key)
                if isinstance(fallback, QueryRecord):
                    if not self.history or self.history[0]["task"] != self.task_id:
                        self._after_send(
                            fallback,
                            False,
                            str(result),
                            log_extra={
                                **dispatch_extra,
                                "query_key": fallback.query_key,
                                "func_num": fallback.func_num,
                                **self._log_exception_fields(result),
                            },
                            show_error_dialog=show_error_dialog,
                        )
                if on_done:
                    on_done(False)
                return
            results, step_failed = result
            for plan_record, step_ok, step_error, feedback, step_extra in results:
                self._after_send(
                    plan_record,
                    step_ok,
                    step_error,
                    feedback,
                    log_extra={**dispatch_extra, **step_extra},
                    show_error_dialog=show_error_dialog,
                )
            if on_done:
                on_done(not step_failed)

        self._run_in_background(work, on_result)

    def _after_send(
        self,
        record: QueryRecord,
        ok: bool,
        error: str,
        feedback: list[float] | None = None,
        *,
        log_extra: dict[str, Any] | None = None,
        show_error_dialog: bool = True,
    ) -> None:
        """处理相关数据。"""
        self.history.insert(0, {
            "task": self.task_id,
            "code": record.func_num,
            "name": record.query_key,
            "type": FUNC_LABELS.get(record.func_num, f"Func{record.func_num}"),
            "result": "成功" if ok else "失败",
        })
        if ok:
            self.busy = "运行中"
            self.result = "0"
            self.alarm_code = "ERR_000"
            self.alarm_text = "系统正常"
            if hasattr(self, "_update_memory_params_from_record"):
                self._update_memory_params_from_record(record)
            if feedback:
                self._apply_feedback_values(record, feedback)
            elif record.func_num == 108:
                pose = record.pose_tuple()
                if pose is not None:
                    self.robot_x = self._fmt(pose[0])
                    self.robot_y = self._fmt(pose[1])
                    self.robot_z = self._fmt(pose[2])
                    self.robot_r = f"{self._fmt(pose[3])} / {self._fmt(pose[4])} / {self._fmt(pose[5])}"
                    self.robot_speed = f"{self._fmt(record.spd_pct_value())} / {self._fmt(record.acc_pct_value())}"
            self.task_id += 1
            self.status_label.setText(f"已执行: {record.query_key}")
            self._append_log(
                "执行",
                f"发送指令 {record.query_key}",
                "成功",
                f"任务{self.task_id - 1}",
                extra={
                    **(log_extra or {}),
                    "query_key": record.query_key,
                    "func_num": record.func_num,
                    "task_id": self.task_id - 1,
                    "command_snapshot": self._build_record_dispatch_snapshot(record),
                },
            )
        else:
            self.busy = "空闲"
            self.result = "9"
            if "通讯故障" in error or "镜像区连续" in error:
                self.alarm_code = "ERR_COMM"
                self.alarm_text = "镜像确认失败，判定通讯故障"
            else:
                self.alarm_code = "ERR_SEND"
                self.alarm_text = error
            self.status_label.setText(f"发送失败: {error}")
            if show_error_dialog:
                self._show_critical("发送失败", error)
            self._append_log(
                "执行",
                f"发送指令 {record.query_key}",
                "失败",
                error,
                extra={
                    **(log_extra or {}),
                    "query_key": record.query_key,
                    "func_num": record.func_num,
                    "command_snapshot": self._build_record_dispatch_snapshot(record),
                },
            )
        if ok:
            self._refresh_all()
        else:
            self._refresh_status_labels()

