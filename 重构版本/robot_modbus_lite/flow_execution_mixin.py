"""流程单步、自动执行和并行组执行逻辑。"""

from __future__ import annotations

import time
from typing import Any, Callable

from PySide6.QtCore import QTimer

from .models import QueryRecord, SIX_MOTION_FUNCS


class FlowExecutionMixin:
    """执行流程步骤和并行组的主窗口能力。"""

    def _next_flow_run_id(self) -> int:
        """生成新的流程执行批次号。"""
        self.flow_run_id = int(getattr(self, "flow_run_id", 0)) + 1
        return self.flow_run_id

    def _is_flow_run_current(self, run_id: int) -> bool:
        """判断后台回调是否仍属于当前流程批次。"""
        return bool(self.flow_running and int(getattr(self, "flow_run_id", 0)) == int(run_id))

    def _mark_flow_run_started(self, run_id: int) -> None:
        """记录流程批次起始时间。"""
        self._flow_run_started_perf = time.perf_counter()
        self._flow_run_started_id = int(run_id)

    def _flow_elapsed_ms(self, run_id: int) -> int:
        """计算流程批次耗时。"""
        if int(getattr(self, "_flow_run_started_id", -1)) != int(run_id):
            return 0
        started = getattr(self, "_flow_run_started_perf", None)
        if started is None:
            return 0
        return int((time.perf_counter() - float(started)) * 1000)

    def _flow_log_extra(self, flow=None, run_id: int | None = None, **extra: Any) -> dict[str, Any]:
        """构建流程日志上下文字段。"""
        flow_name = getattr(flow, "name", None) or self.current_flow_name
        payload: dict[str, Any] = {
            "flow_name": flow_name,
            "flow_run_id": int(getattr(self, "flow_run_id", 0)) if run_id is None else int(run_id),
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        return payload

    def _append_flow_summary(
        self,
        flow,
        run_id: int,
        status: str,
        *,
        result: str,
        completed_steps: int,
        current_step: str = "-",
        detail: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """追加流程结束摘要，方便按批次复盘。"""
        total_steps = len(getattr(flow, "steps", []) or [])
        elapsed_ms = self._flow_elapsed_ms(run_id)
        summary = (
            f"status={status} | completed={completed_steps}/{total_steps} | "
            f"last_step={current_step} | elapsed_ms={elapsed_ms}"
        )
        if detail:
            summary = f"{summary} | {detail}"
        log_extra = self._flow_log_extra(
            flow,
            run_id,
            flow_status=status,
            completed_steps=completed_steps,
            total_steps=total_steps,
            current_step=current_step,
            elapsed_ms=elapsed_ms,
        )
        if extra:
            log_extra.update({key: value for key, value in extra.items() if value is not None})
        self._append_log("流程", f"流程结束摘要 {getattr(flow, 'name', '-')}", result, summary, extra=log_extra)

    def _finish_flow_run(self, status: str, *, current_step: str = "-") -> None:
        """结束当前流程批次并刷新状态。"""
        self.flow_running = False
        self.flow_status = status
        self.flow_current_step = current_step
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()

    def _cancel_flow_run(self, status: str, *, current_step: str = "-") -> None:
        """取消当前流程批次，使旧后台回调失效。"""
        self._next_flow_run_id()
        self._finish_flow_run(status, current_step=current_step)

    def _wait_controller_ready_for_flow(
        self,
        host: str,
        *,
        timeout_sec: float = 3.0,
        poll_interval_sec: float = 0.2,
        stable_required: int = 3,
    ) -> tuple[bool, str]:
        """等待控制器就绪流程。"""
        client = self._get_client(host)
        xyz_read = self.service.build_six_pose_feedback_read()
        status_read = self.service.build_six_status_read()
        last_xyz: tuple[float, ...] | None = None
        stable_count = 0
        deadline = time.time() + timeout_sec
        last_status_raw = 0

        while time.time() < deadline:
            xyz_vals = tuple(float(value) for value in client.read_modbus_float(xyz_read))
            status_vals = client.read_modbus_long(status_read)
            status = self.service.parse_six_status(status_vals)
            last_status_raw = status.raw

            if status.has_error:
                return False, f"控制器存在错误状态 IEEE(34)={status.raw}"
            if status.has_alarm:
                alarm_read = self.service.build_six_alarm_detail_read()
                alarm_vals = client.read_modbus_long(alarm_read)
                alarm_detail = self.service.parse_six_alarm_detail(alarm_vals)
                return False, f"控制器存在报警 IEEE(34)={status.raw} | {alarm_detail}"

            if last_xyz is not None and self._values_equal(last_xyz, xyz_vals, tolerance=0.5):
                stable_count += 1
            else:
                stable_count = 1
            last_xyz = xyz_vals

            if stable_count >= stable_required and status.can_send:
                return True, f"控制器已就绪 | IEEE(34)={status.raw} | 坐标稳定{stable_count}次"
            if stable_count >= stable_required + 2:
                return True, f"控制器坐标稳定，放行流程启动 | IEEE(34)={status.raw}"

            time.sleep(poll_interval_sec)

        return False, f"等待控制器就绪超时 | IEEE(34)={last_status_raw}"

    def _start_flow(self, *, on_done: Callable[[bool], None] | None = None) -> None:
        """启动流程。"""
        if self.flow_running:
            self._show_info("流程已运行", "当前流程正在执行。")
            if on_done:
                on_done(False)
            return
        flow = self._current_flow_definition()
        if flow is None:
            self._show_warning("未选择流程", "请先选择一个流程。")
            if on_done:
                on_done(False)
            return
        if not flow.steps:
            self._show_warning("空流程", "当前流程没有任何步骤。")
            if on_done:
                on_done(False)
            return
        missing = [s for s in flow.steps if s not in self.table]
        if missing:
            self._show_warning(
                "流程包含无效步骤",
                f"以下步骤在模板中不存在:\n{', '.join(missing)}\n请先修复流程或创建对应模板。",
            )
            self._append_log("流程", f"流程预检查 {flow.name}", "失败", f"缺失模板: {', '.join(missing)}")
            if on_done:
                on_done(False)
            return
        if self.flow_step_index >= len(flow.steps):
            self.flow_step_index = 0
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("流程", f"流程预检查 {flow.name}", "失败", "地址为空")
            if on_done:
                on_done(False)
            return

        run_id = self._next_flow_run_id()
        self._mark_flow_run_started(run_id)
        self._flow_done_callback = on_done
        self.flow_running = True
        self.flow_status = "等待控制器就绪"
        self.flow_current_step = "-"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        start_extra = self._flow_log_extra(flow, run_id, total_steps=len(flow.steps), start_step_index=self.flow_step_index + 1)
        self._append_log("流程", f"流程启动等待 {flow.name}", "成功", "开始等待控制器就绪", extra=start_extra)
        self._pause_polling()

        def work():
            """处理相关数据。"""
            return self._wait_controller_ready_for_flow(host)

        def on_result(result) -> None:
            """处理结果。"""
            self._resume_polling()
            if not self._is_flow_run_current(run_id):
                return
            if isinstance(result, Exception):
                self._disconnect_client()
                self._finish_flow_run("失败")
                self._show_critical("流程启动失败", str(result))
                self._append_log("流程", f"流程启动失败 {flow.name}", "失败", str(result), extra=start_extra)
                self._append_flow_summary(
                    flow,
                    run_id,
                    "失败",
                    result="失败",
                    completed_steps=self.flow_step_index,
                    current_step="-",
                    detail=str(result),
                    extra=self._log_exception_fields(result),
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(False)
                return

            ready, detail = result
            if not ready:
                self._finish_flow_run("失败")
                self._show_warning("流程启动失败", detail)
                self._append_log("流程", f"流程启动失败 {flow.name}", "失败", detail, extra=start_extra)
                self._append_flow_summary(
                    flow,
                    run_id,
                    "失败",
                    result="失败",
                    completed_steps=self.flow_step_index,
                    current_step="-",
                    detail=detail,
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(False)
                return

            self.flow_status = "运行中"
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            self._append_log("流程", f"开始流程 {flow.name}", "成功", f"共 {len(flow.steps)} 步 | {detail}", extra=start_extra)
            QTimer.singleShot(0, lambda: self._run_next_flow_step(run_id=run_id))

        self._run_in_background(work, on_result)

    def _step_flow(self) -> None:
        """处理步骤流程。"""
        if self.flow_running:
            self._show_info("流程已运行", "当前流程正在执行。")
            return
        flow = self._current_flow_definition()
        if flow is None:
            self._show_warning("未选择流程", "请先选择一个流程。")
            return
        if not flow.steps:
            self._show_warning("空流程", "当前流程没有任何步骤。")
            return
        missing = [s for s in flow.steps if s not in self.table]
        if missing:
            self._show_warning(
                "流程包含无效步骤",
                f"以下步骤在模板中不存在:\n{', '.join(missing)}\n请先修复流程或创建对应模板。",
            )
            self._append_log("流程", f"流程单步预检查 {flow.name}", "失败", f"缺失模板: {', '.join(missing)}")
            return
        if self.flow_step_index >= len(flow.steps):
            self.flow_step_index = 0
        run_id = self._next_flow_run_id()
        self._mark_flow_run_started(run_id)
        self._flow_done_callback = None
        self.flow_running = True
        self.flow_status = "单步执行"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        self._append_log(
            "流程",
            f"单步流程开始 {flow.name}",
            "成功",
            f"第 {self.flow_step_index + 1}/{len(flow.steps)} 步",
            extra=self._flow_log_extra(flow, run_id, total_steps=len(flow.steps), start_step_index=self.flow_step_index + 1),
        )
        self._run_current_flow_step(auto_continue=False, run_id=run_id)

    def _stop_flow(self) -> None:
        """停止流程。"""
        if not self.flow_running:
            return
        flow = self._current_flow_definition()
        run_id = int(getattr(self, "flow_run_id", 0))
        stopped_step = self.flow_step_index + 1
        stopped_current_step = self.flow_current_step
        self._cancel_flow_run("已停止")
        if self.current_flow_name:
            self._append_log(
                "流程",
                f"停止流程 {self.current_flow_name}",
                "成功",
                f"停止于第 {stopped_step} 步",
                extra=self._flow_log_extra(flow, run_id, flow_status="已停止", current_step=stopped_current_step),
            )
        if flow is not None:
            self._append_flow_summary(
                flow,
                run_id,
                "已停止",
                result="警告",
                completed_steps=self.flow_step_index,
                current_step=stopped_current_step,
                detail=f"operator_stop_step={stopped_step}",
            )
        callback = self._flow_done_callback
        self._flow_done_callback = None
        if callback:
            callback(False)

    def _reset_flow(self) -> None:
        """复位流程。"""
        flow = self._current_flow_definition()
        run_id = int(getattr(self, "flow_run_id", 0))
        was_running = bool(self.flow_running)
        completed_steps = self.flow_step_index
        current_step = self.flow_current_step
        if self.flow_running:
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(False)
        self._next_flow_run_id()
        self.flow_running = False
        self.flow_step_index = 0
        self.flow_status = "空闲"
        self.flow_current_step = "-"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        if self.current_flow_name:
            self._append_log(
                "流程",
                f"重置流程 {self.current_flow_name}",
                "成功",
                "流程已重置到第 1 步",
                extra=self._flow_log_extra(flow, run_id, flow_status="已重置", current_step=current_step),
            )
        if was_running and flow is not None:
            self._append_flow_summary(
                flow,
                run_id,
                "已重置",
                result="警告",
                completed_steps=completed_steps,
                current_step=current_step,
                detail="operator_reset",
            )

    def _run_next_flow_step(self, *, run_id: int | None = None) -> None:
        """运行流程步骤。"""
        active_run_id = int(getattr(self, "flow_run_id", 0)) if run_id is None else int(run_id)
        if not self._is_flow_run_current(active_run_id):
            return
        self._run_current_flow_step(auto_continue=True, run_id=active_run_id)

    def _run_current_flow_step(self, *, auto_continue: bool, run_id: int | None = None) -> None:
        """运行当前流程步骤。"""
        active_run_id = int(getattr(self, "flow_run_id", 0)) if run_id is None else int(run_id)
        if not self._is_flow_run_current(active_run_id):
            return
        flow = self._current_flow_definition()
        if flow is None:
            self._finish_flow_run("失败")
            self._append_log(
                "流程",
                "流程步骤失败",
                "失败",
                "当前流程定义不存在",
                extra=self._flow_log_extra(None, active_run_id, flow_status="失败"),
            )
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(False)
            return
        if self.flow_step_index >= len(flow.steps):
            self._finish_flow_run("完成")
            done_extra = self._flow_log_extra(flow, active_run_id, completed_steps=len(flow.steps), total_steps=len(flow.steps))
            self._append_log("流程", f"流程完成 {flow.name}", "成功", f"共完成 {len(flow.steps)} 步", extra=done_extra)
            self._append_flow_summary(
                flow,
                active_run_id,
                "完成",
                result="成功",
                completed_steps=len(flow.steps),
                current_step="-",
            )
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(True)
            return

        current_step_index = self.flow_step_index
        step_name = flow.steps[current_step_index]
        self.flow_current_step = step_name
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        step_extra = self._flow_log_extra(
            flow,
            active_run_id,
            flow_step_index=self.flow_step_index + 1,
            total_steps=len(flow.steps),
            current_step=step_name,
        )
        self._append_log("流程", f"流程第{self.flow_step_index + 1}步开始", "成功", step_name, extra=step_extra)

        if step_name not in self.table:
            self._finish_flow_run("失败")
            self._append_log("流程", f"流程第{self.flow_step_index + 1}步失败", "失败", f"模板不存在: {step_name}", extra=step_extra)
            self._append_flow_summary(
                flow,
                active_run_id,
                "失败",
                result="失败",
                completed_steps=current_step_index,
                current_step=step_name,
                detail=f"模板不存在: {step_name}",
            )
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(False)
            return

        parallel_group = self._build_parallel_flow_group(flow, current_step_index)
        if parallel_group is not None:
            # 四点三协议允许运动槽与程序槽并行。这里把相邻的
            # 运动、延时和可选输入输出合并成一组，减少流程总耗时。
            self._run_parallel_flow_group(flow, current_step_index, parallel_group, auto_continue=auto_continue, run_id=active_run_id)
            return

        def on_step_done(ok: bool) -> None:
            """处理步骤完成。"""
            if not self._is_flow_run_current(active_run_id):
                return
            if not ok:
                self._finish_flow_run("失败")
                fail_extra = self._flow_log_extra(
                    flow,
                    active_run_id,
                    flow_step_index=current_step_index + 1,
                    total_steps=len(flow.steps),
                    current_step=step_name,
                )
                self._append_log("流程", f"流程第{current_step_index + 1}步失败", "失败", step_name, extra=fail_extra)
                self._append_flow_summary(
                    flow,
                    active_run_id,
                    "失败",
                    result="失败",
                    completed_steps=current_step_index,
                    current_step=step_name,
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(False)
                return

            success_extra = self._flow_log_extra(
                flow,
                active_run_id,
                flow_step_index=current_step_index + 1,
                total_steps=len(flow.steps),
                current_step=step_name,
            )
            self._append_log("流程", f"流程第{current_step_index + 1}步成功", "成功", step_name, extra=success_extra)
            self.flow_step_index = current_step_index + 1
            current_flow = self._current_flow_definition()
            if current_flow is None or self.flow_step_index >= len(current_flow.steps):
                self._finish_flow_run("完成")
                done_extra = self._flow_log_extra(flow, active_run_id, completed_steps=len(flow.steps), total_steps=len(flow.steps))
                self._append_log("流程", f"流程完成 {flow.name}", "成功", f"共完成 {len(flow.steps)} 步", extra=done_extra)
                self._append_flow_summary(
                    flow,
                    active_run_id,
                    "完成",
                    result="成功",
                    completed_steps=len(flow.steps),
                    current_step="-",
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(True)
                return

            self.flow_status = "运行中" if auto_continue else "空闲"
            self.flow_current_step = current_flow.steps[self.flow_step_index]
            if not auto_continue:
                self.flow_running = False
                self.flow_current_step = "-"
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            if not auto_continue:
                self._append_flow_summary(
                    flow,
                    active_run_id,
                    "单步完成",
                    result="成功",
                    completed_steps=self.flow_step_index,
                    current_step="-",
                )
            if auto_continue and self.flow_running:
                delay_ms = max(0, int(getattr(current_flow, "step_delay_ms", 0)))
                if delay_ms > 0:
                    self.flow_status = f"步间等待({delay_ms}ms)"
                    self._refresh_flow_status_panel()
                    self._append_log(
                        "流程",
                        f"流程步间等待 {flow.name}",
                        "成功",
                        f"第{current_step_index + 1}步后等待 {delay_ms}ms，再执行 {current_flow.steps[self.flow_step_index]}",
                        extra=self._flow_log_extra(
                            flow,
                            active_run_id,
                            flow_step_index=current_step_index + 1,
                            next_step=current_flow.steps[self.flow_step_index],
                            delay_ms=delay_ms,
                        ),
                    )
                    QTimer.singleShot(delay_ms, lambda: self._run_next_flow_step(run_id=active_run_id))
                else:
                    QTimer.singleShot(0, lambda: self._run_next_flow_step(run_id=active_run_id))

        self._execute_query_key(
            step_name,
            on_done=on_step_done,
            show_error_dialog=False,
            should_process=lambda run_id=active_run_id: self._is_flow_run_current(run_id),
            log_extra=self._flow_log_extra(
                flow,
                active_run_id,
                flow_step_index=current_step_index + 1,
                total_steps=len(flow.steps),
                current_step=step_name,
            ),
        )

    def _build_parallel_flow_group(self, flow, start_index: int) -> list[QueryRecord] | None:
        """构建并行流程分组。"""
        # 目前只识别一种稳定组合：
        # 目前只合并“运动函数 + 延时函数 + 可选输入输出函数”。
        # 其它组合保持串行，避免把同槽互斥规则藏在流程调度里。
        if start_index + 1 >= len(flow.steps):
            return None
        first = self.table.get(flow.steps[start_index])
        second = self.table.get(flow.steps[start_index + 1])
        if not isinstance(first, QueryRecord) or not isinstance(second, QueryRecord):
            return None
        if first.func_num not in SIX_MOTION_FUNCS or second.func_num != 110:
            return None
        try:
            first_plan, _ = self._build_execution_plan(first)
            second_plan, _ = self._build_execution_plan(second)
        except Exception:
            return None
        if len(first_plan) != 1 or len(second_plan) != 1:
            return None
        group = [first, second]
        if start_index + 2 < len(flow.steps):
            third = self.table.get(flow.steps[start_index + 2])
            if isinstance(third, QueryRecord) and third.func_num == 120:
                try:
                    third_plan, _ = self._build_execution_plan(third)
                except Exception:
                    third_plan = []
                if len(third_plan) == 1:
                    group.append(third)
        return group

    def _run_parallel_flow_group(
        self,
        flow,
        start_index: int,
        group: list[QueryRecord],
        *,
        auto_continue: bool,
        run_id: int,
    ) -> None:
        """运行并行流程分组。"""
        names = " + ".join(record.query_key for record in group)
        dispatch_extra = {
            "dispatch_id": self._next_dispatch_id(),
            "host": self.host_edit.text().strip(),
            "controller_mode": self._controller_mode_value(),
            "task_id": self.task_id,
            "flow_name": flow.name,
            "flow_run_id": run_id,
            "flow_step_index": start_index + 1,
            "total_steps": len(flow.steps),
            "parallel_group": [record.query_key for record in group],
        }
        self._append_log("流程", f"并行组开始 第{start_index + 1}步", "成功", names, extra=dispatch_extra)
        host = self.host_edit.text().strip()
        self._pause_polling()

        def work():
            """处理相关数据。"""
            with self._push_log_context(**dispatch_extra):
                client = self._get_client(host)
                for record in group:
                    validation_error = self._validate_record(record)
                    if validation_error:
                        raise ValueError(f"{record.query_key}: {validation_error}")

                motion_record = group[0]
                delay_record = group[1]
                io_record = group[2] if len(group) > 2 else None

                # 并行组的顺序是刻意的：先启动运动且不等待，再执行程序槽；
                # 最后回头等待运动完成，避免延时和输入输出被长运动阻塞。
                with self._push_log_context(query_key=motion_record.query_key, func_num=motion_record.func_num, plan_step_index=1):
                    motion_cmd = self._trigger_six_no_wait(client, motion_record)
                with self._push_log_context(query_key=delay_record.query_key, func_num=delay_record.func_num, plan_step_index=2):
                    delay_cmd = self._trigger_six_no_wait(client, delay_record)
                    self._wait_six_command_done(client, delay_cmd, delay_record)
                    delay_feedback = self._read_six_command_feedback(client, delay_cmd, delay_record)

                io_feedback: list[float] | None = None
                if io_record is not None:
                    with self._push_log_context(query_key=io_record.query_key, func_num=io_record.func_num, plan_step_index=3):
                        io_cmd = self._trigger_six_no_wait(client, io_record)
                        self._wait_six_command_done(client, io_cmd, io_record)
                        io_feedback = self._read_six_command_feedback(client, io_cmd, io_record)

                with self._push_log_context(query_key=motion_record.query_key, func_num=motion_record.func_num, plan_step_index=1):
                    self._wait_six_command_done(client, motion_cmd, motion_record)
                    motion_feedback = self._read_six_command_feedback(client, motion_cmd, motion_record)

                results: list[tuple[QueryRecord, bool, str, list[float] | None, dict[str, Any]]] = []
                for idx, (record, feedback) in enumerate(
                    (
                        (motion_record, motion_feedback),
                        (delay_record, delay_feedback),
                        (io_record, io_feedback),
                    ),
                    start=1,
                ):
                    if record is None:
                        continue
                    step_ok, step_error = self._evaluate_feedback_result(feedback or [])
                    results.append((record, step_ok, step_error, feedback, {"query_key": record.query_key, "func_num": record.func_num, "plan_step_index": idx}))
                return results

        def on_result(result) -> None:
            """处理结果。"""
            self._resume_polling()
            if not self._is_flow_run_current(run_id):
                return
            if isinstance(result, Exception):
                self._disconnect_client()
                self._finish_flow_run("失败")
                self._append_log("流程", f"并行组失败 第{start_index + 1}步", "失败", str(result), extra={**dispatch_extra, **self._log_exception_fields(result)})
                self._append_flow_summary(
                    flow,
                    run_id,
                    "失败",
                    result="失败",
                    completed_steps=start_index,
                    current_step=names,
                    detail=str(result),
                    extra=self._log_exception_fields(result),
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(False)
                return

            failed = False
            for record, step_ok, step_error, feedback, step_extra in result:
                self._after_send(record, step_ok, step_error, feedback, log_extra={**dispatch_extra, **step_extra}, show_error_dialog=False)
                if not step_ok:
                    failed = True
            if failed:
                self._finish_flow_run("失败")
                failed_names = [record.query_key for record, step_ok, _step_error, _feedback, _step_extra in result if not step_ok]
                self._append_flow_summary(
                    flow,
                    run_id,
                    "失败",
                    result="失败",
                    completed_steps=start_index,
                    current_step=names,
                    detail=f"failed_steps={failed_names}",
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(False)
                return

            self._append_log("流程", f"并行组完成 第{start_index + 1}步", "成功", names, extra=dispatch_extra)
            self.flow_step_index = start_index + len(group)
            current_flow = self._current_flow_definition()
            if current_flow is None or self.flow_step_index >= len(current_flow.steps):
                self._finish_flow_run("完成")
                done_extra = self._flow_log_extra(flow, run_id, completed_steps=len(flow.steps), total_steps=len(flow.steps))
                self._append_log("流程", f"流程完成 {flow.name}", "成功", f"共完成 {len(flow.steps)} 步", extra=done_extra)
                self._append_flow_summary(
                    flow,
                    run_id,
                    "完成",
                    result="成功",
                    completed_steps=len(flow.steps),
                    current_step="-",
                )
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(True)
                return

            self.flow_status = "运行中" if auto_continue else "空闲"
            self.flow_current_step = current_flow.steps[self.flow_step_index]
            if not auto_continue:
                self.flow_running = False
                self.flow_current_step = "-"
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            if not auto_continue:
                self._append_flow_summary(
                    flow,
                    run_id,
                    "单步完成",
                    result="成功",
                    completed_steps=self.flow_step_index,
                    current_step="-",
                    detail=f"parallel_group={dispatch_extra['parallel_group']}",
                )
            if auto_continue and self.flow_running:
                delay_ms = max(0, int(getattr(current_flow, "step_delay_ms", 0)))
                QTimer.singleShot(delay_ms, lambda: self._run_next_flow_step(run_id=run_id))

        self._run_in_background(work, on_result)

    def _current_flow_definition(self):
        """处理当前流程。"""
        if not self.current_flow_name:
            return None
        if self.current_flow_name not in self.service.flows:
            return None
        return self.service.get_flow(self.current_flow_name)

