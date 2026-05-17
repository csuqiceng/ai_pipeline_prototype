"""自然语言输入解析、计划生成和执行逻辑。"""

from __future__ import annotations

import json

from .voice_nlp_adapter import VoiceNlpAdapter, VoiceNlpPlan


class NlpMixin:
    """为主窗口增加自然语言规划和执行能力。"""
    def _build_voice_nlp_adapter(self) -> VoiceNlpAdapter:
        """构建语音自然语言。"""
        adapter = VoiceNlpAdapter(self.table, self.service.list_flow_names())
        if self._deepseek_client:
            adapter.set_deepseek_client(self._deepseek_client)
        adapter.set_diagnostic_callback(
            lambda action, result, detail: self._append_log("自然语言", action, result, detail)
        )
        return adapter

    def _set_nlp_result_plan(self, plan: VoiceNlpPlan) -> None:
        """设置自然语言结果。"""
        self.nlp_last_plan = plan
        self.nlp_result_edit.setPlainText(json.dumps(plan.to_preview_dict(), ensure_ascii=False, indent=2))

    def _set_nlp_parse_busy(self, busy: bool) -> None:
        """设置自然语言忙。"""
        self.nlp_parse_running = busy
        if hasattr(self, "nlp_parse_btn"):
            self.nlp_parse_btn.setEnabled(not busy)
            self.nlp_parse_btn.setText("解析中" if busy else "解析文本")
        if hasattr(self, "nlp_execute_btn"):
            self.nlp_execute_btn.setEnabled(not busy)
        if hasattr(self, "nlp_clear_btn"):
            self.nlp_clear_btn.setEnabled(not busy)
        if hasattr(self, "nlp_use_deepseek_check"):
            self.nlp_use_deepseek_check.setEnabled(not busy)
        if hasattr(self, "mic_device_combo"):
            self.mic_device_combo.setEnabled(not busy)

    def _set_nlp_execute_busy(self, busy: bool) -> None:
        """设置自然语言忙。"""
        self.nlp_sequence_running = busy
        if hasattr(self, "nlp_execute_btn"):
            self.nlp_execute_btn.setEnabled(not busy)
            self.nlp_execute_btn.setText("执行中" if busy else "执行")
        if hasattr(self, "nlp_parse_btn"):
            self.nlp_parse_btn.setEnabled(not busy and not self.nlp_parse_running)
        if hasattr(self, "nlp_clear_btn"):
            self.nlp_clear_btn.setEnabled(not busy)
        if hasattr(self, "nlp_use_deepseek_check"):
            self.nlp_use_deepseek_check.setEnabled(not busy and not self.nlp_parse_running)
        if hasattr(self, "mic_device_combo"):
            self.mic_device_combo.setEnabled(not busy and not self.nlp_parse_running)

    def _parse_nlp_text(self) -> None:
        """解析自然语言。"""
        text = self.nlp_input_edit.toPlainText().strip()
        if not text:
            self._show_warning("输入为空", "请输入自然语言文本。")
            self._append_log("自然语言", "解析文本", "失败", "输入为空")
            return
        if self.nlp_parse_running:
            return
        use_deepseek = self.nlp_use_deepseek_check.isChecked()
        self._set_nlp_parse_busy(True)
        self.status_label.setText("自然语言解析中，请稍候...")

        def work():
            """处理相关数据。"""
            return self._build_voice_nlp_adapter().parse(
                text,
                use_deepseek=use_deepseek,
            )

        def on_result(result):
            """处理结果。"""
            self._set_nlp_parse_busy(False)
            if isinstance(result, Exception):
                self.status_label.setText(f"自然语言解析失败: {result}")
                self._append_log("自然语言", "解析文本", "失败", str(result))
                self._show_critical("解析失败", str(result))
                return
            plan = result
            self._set_nlp_result_plan(plan)
            first_action = plan.actions[0] if plan.actions else VoiceNlpAction("unknown", None, plan.source, text, plan.reason)
            self.status_label.setText(
                f"解析完成: {len(plan.actions)} 步 / {first_action.action_type} / {first_action.target or '-'}"
            )
            self._append_log(
                "自然语言",
                "解析文本",
                "成功" if plan.actions and plan.actions[0].action_type != "unknown" else "失败",
                f"{plan.source} | {len(plan.actions)}步 | {plan.reason}",
            )

        self._run_in_background(work, on_result)

    def _execute_nlp_text(self) -> None:
        """执行自然语言。"""
        text = self.nlp_input_edit.toPlainText().strip()
        if not text:
            self._show_warning("输入为空", "请输入自然语言文本。")
            self._append_log("自然语言", "执行解析", "失败", "输入为空")
            return
        if self.nlp_parse_running:
            self._show_info("解析中", "当前正在进行自然语言解析，请等待解析完成。")
            return
        if self.nlp_sequence_running:
            self._show_info("自然语言执行中", "当前自然语言动作序列正在执行。")
            return
        if self.flow_running:
            self._show_info("流程运行中", "当前流程执行中，请先停止流程或等待流程完成。")
            self._append_log("自然语言", "执行解析", "失败", "流程执行中，拒绝自然语言执行")
            return
        use_deepseek = self.nlp_use_deepseek_check.isChecked()
        self._set_nlp_execute_busy(True)
        self.status_label.setText("自然语言执行准备中，请稍候...")

        def work():
            """处理相关数据。"""
            return self._build_voice_nlp_adapter().parse(
                text,
                use_deepseek=use_deepseek,
            )

        def on_result(result):
            """处理结果。"""
            if isinstance(result, Exception):
                self._set_nlp_execute_busy(False)
                self.status_label.setText(f"自然语言执行准备失败: {result}")
                self._append_log("自然语言", "执行解析", "失败", str(result))
                self._show_critical("执行失败", str(result))
                return
            plan = result
            self._set_nlp_result_plan(plan)
            self._execute_nlp_plan(plan)

        self._run_in_background(work, on_result)

    def _clear_nlp_text(self) -> None:
        """清除自然语言。"""
        self.nlp_input_edit.clear()
        self.nlp_result_edit.clear()
        self.nlp_last_plan = None
        self.status_label.setText("自然语言输入已清空。")

    def _execute_nlp_plan(self, plan: VoiceNlpPlan) -> None:
        """执行自然语言。"""
        if not plan.actions:
            self._set_nlp_execute_busy(False)
            self._show_warning("无法执行", f"未识别到可执行动作。\n{plan.reason}")
            self._append_log("自然语言", "执行解析", "失败", plan.reason)
            return
        if any(action.action_type == "unknown" for action in plan.actions):
            self._set_nlp_execute_busy(False)
            self._show_warning("无法执行", f"未识别到可执行动作。\n{plan.reason}")
            self._append_log("自然语言", "执行解析", "失败", plan.reason)
            return
        self._nlp_pending_actions = list(plan.actions)
        self._nlp_pending_index = 0
        self._append_log(
            "自然语言",
            "执行解析",
            "成功",
            f"{plan.source} | {len(plan.actions)}步 | {plan.reason}",
        )
        self._run_next_nlp_action()

    def _run_next_nlp_action(self) -> None:
        """运行自然语言。"""
        if not self.nlp_sequence_running:
            return
        if self._nlp_pending_index >= len(self._nlp_pending_actions):
            total = len(self._nlp_pending_actions)
            self._set_nlp_execute_busy(False)
            self.status_label.setText(f"自然语言执行完成，共 {total} 步。")
            self._append_log("自然语言", "动作序列完成", "成功", f"共执行 {total} 步")
            return

        step_no = self._nlp_pending_index + 1
        action = self._nlp_pending_actions[self._nlp_pending_index]
        self.status_label.setText(f"自然语言执行第 {step_no} 步: {action.action_type} / {action.target or '-'}")
        self._append_log(
            "自然语言",
            f"动作序列第{step_no}步开始",
            "成功",
            f"{action.action_type} | {action.target or '-'} | {action.source}",
        )

        def on_step_done(ok: bool) -> None:
            """处理步骤完成。"""
            step_result = "成功" if ok else "失败"
            self._append_log(
                "自然语言",
                f"动作序列第{step_no}步{step_result}",
                step_result,
                f"{action.action_type} | {action.target or '-'} | {action.source}",
            )
            if not ok:
                self._set_nlp_execute_busy(False)
                self.status_label.setText(f"自然语言执行失败，停止于第 {step_no} 步。")
                self._append_log("自然语言", "动作序列终止", "失败", f"停止于第 {step_no} 步")
                return
            self._nlp_pending_index += 1
            QTimer.singleShot(0, self._run_next_nlp_action)

        if action.action_type == "template" and action.target:
            if self.flow_running:
                on_step_done(False)
                return
            self._execute_query_key(action.target, on_done=on_step_done)
            return
        if action.action_type == "system" and action.target:
            self._handle_system_action(action.target, on_done=on_step_done)
            return
        if action.action_type == "flow" and action.target:
            self.flow_combo.setCurrentText(action.target)
            self._start_flow(on_done=on_step_done)
            return

        on_step_done(False)

