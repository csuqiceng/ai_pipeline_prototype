"""流程列表、流程步骤编辑、保存和选择逻辑。"""

from __future__ import annotations

from PySide6.QtWidgets import QTreeWidgetItem

from .models import FlowDefinition


class FlowManagementMixin:
    """维护流程列表、步骤树和流程文件的主窗口能力。"""
    def _refresh_flow_combo(self) -> None:
        """刷新流程下拉框。"""
        if not hasattr(self, "flow_combo"):
            return
        flow_names = self.service.list_flow_names()
        current = self.current_flow_name or self.flow_combo.currentText()
        self.flow_combo.blockSignals(True)
        self.flow_combo.clear()
        self.flow_combo.addItems(flow_names)
        if current and current in flow_names:
            self.flow_combo.setCurrentText(current)
            self.current_flow_name = current
        elif flow_names:
            self.current_flow_name = flow_names[0]
            self.flow_combo.setCurrentText(flow_names[0])
        else:
            self.current_flow_name = None
        self.flow_combo.blockSignals(False)
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()

    def _refresh_flow_manage_tree(self) -> None:
        """刷新流程树。"""
        if not hasattr(self, "flow_manage_tree"):
            return
        self.flow_manage_tree.clear()
        flow_names = self.service.list_flow_names()
        for flow_name in flow_names:
            flow = self.service.get_flow(flow_name)
            item = QTreeWidgetItem([flow.name, f"{len(flow.steps)} | {flow.step_delay_ms}ms"])
            self.flow_manage_tree.addTopLevelItem(item)
            if self.current_flow_manage_name == flow.name:
                self.flow_manage_tree.setCurrentItem(item)
        if flow_names and not self.current_flow_manage_name:
            self.current_flow_manage_name = flow_names[0]
            self._load_flow_into_manage_form(self.service.get_flow(flow_names[0]))
            top = self.flow_manage_tree.topLevelItem(0)
            if top is not None:
                self.flow_manage_tree.setCurrentItem(top)
        elif not flow_names:
            self._new_flow()

    def _refresh_flow_available_tree(self) -> None:
        """刷新流程树。"""
        if not hasattr(self, "flow_available_tree"):
            return
        self.flow_available_tree.clear()
        for record in sorted(self.table.values(), key=lambda r: r.query_key):
            kind = f"Func{record.func_num}"
            self.flow_available_tree.addTopLevelItem(QTreeWidgetItem([record.query_key, kind]))

    def _refresh_flow_step_manage_tree(self, steps: list[str] | None = None) -> None:
        """刷新流程步骤树。"""
        if not hasattr(self, "flow_step_manage_tree"):
            return
        self.flow_step_manage_tree.clear()
        for step in steps or []:
            self.flow_step_manage_tree.addTopLevelItem(QTreeWidgetItem([step]))

    def _on_flow_selected(self, name: str) -> None:
        """处理流程选中。"""
        if self.flow_running and name != self.current_flow_name:
            previous = self.current_flow_name or ""
            self._show_info("流程运行中", "当前流程执行中，不允许切换流程。")
            self._append_log("流程", "切换流程", "失败", f"流程运行中，拒绝切换: {previous or '-'} -> {name or '-'}")
            if hasattr(self, "flow_combo"):
                self.flow_combo.blockSignals(True)
                if previous:
                    self.flow_combo.setCurrentText(previous)
                self.flow_combo.blockSignals(False)
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            return
        self.current_flow_name = name or None
        if not self.flow_running:
            self.flow_step_index = 0
            self.flow_current_step = "-"
            self.flow_status = "空闲"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()

    def _on_manage_flow_selected(self) -> None:
        """处理流程选中。"""
        items = self.flow_manage_tree.selectedItems()
        if not items:
            return
        flow_name = items[0].text(0)
        if flow_name not in self.service.flows:
            return
        self.current_flow_manage_name = flow_name
        self._load_flow_into_manage_form(self.service.get_flow(flow_name))

    def _load_flow_into_manage_form(self, flow) -> None:
        """加载流程表单。"""
        self.flow_manage_name_edit.setText(flow.name)
        self.flow_manage_delay_edit.setText(str(flow.step_delay_ms))
        self._refresh_flow_step_manage_tree(list(flow.steps))

    def _new_flow(self) -> None:
        """处理流程。"""
        self.current_flow_manage_name = None
        if hasattr(self, "flow_manage_name_edit"):
            self.flow_manage_name_edit.setText("")
        if hasattr(self, "flow_manage_delay_edit"):
            self.flow_manage_delay_edit.setText("1000")
        self._refresh_flow_step_manage_tree([])
        self.status_label.setText("已创建空白流程。")
        self._append_log("后台", "新增流程", "成功", "已创建空白流程")

    def _collect_flow_steps(self) -> list[str]:
        """收集流程。"""
        steps: list[str] = []
        for index in range(self.flow_step_manage_tree.topLevelItemCount()):
            item = self.flow_step_manage_tree.topLevelItem(index)
            steps.append(item.text(0))
        return steps

    def _add_flow_step(self) -> None:
        """新增流程步骤。"""
        items = self.flow_available_tree.selectedItems()
        if not items:
            self._show_warning("未选择模板", "请先从可选模板中选择一个步骤模板。")
            return
        step_name = items[0].text(0)
        self.flow_step_manage_tree.addTopLevelItem(QTreeWidgetItem([step_name]))
        self._append_log("后台", "添加流程步骤", "成功", step_name)

    def _remove_flow_step(self) -> None:
        """移除流程步骤。"""
        item = self.flow_step_manage_tree.currentItem()
        if item is None:
            self._show_warning("未选择步骤", "请先选择要移除的流程步骤。")
            return
        index = self.flow_step_manage_tree.indexOfTopLevelItem(item)
        self.flow_step_manage_tree.takeTopLevelItem(index)
        self._append_log("后台", "移除流程步骤", "成功", item.text(0))

    def _move_flow_step_up(self) -> None:
        """处理移动流程步骤。"""
        item = self.flow_step_manage_tree.currentItem()
        if item is None:
            return
        index = self.flow_step_manage_tree.indexOfTopLevelItem(item)
        if index <= 0:
            return
        self.flow_step_manage_tree.takeTopLevelItem(index)
        self.flow_step_manage_tree.insertTopLevelItem(index - 1, item)
        self.flow_step_manage_tree.setCurrentItem(item)

    def _move_flow_step_down(self) -> None:
        """处理移动流程步骤。"""
        item = self.flow_step_manage_tree.currentItem()
        if item is None:
            return
        index = self.flow_step_manage_tree.indexOfTopLevelItem(item)
        if index < 0 or index >= self.flow_step_manage_tree.topLevelItemCount() - 1:
            return
        self.flow_step_manage_tree.takeTopLevelItem(index)
        self.flow_step_manage_tree.insertTopLevelItem(index + 1, item)
        self.flow_step_manage_tree.setCurrentItem(item)

    def _save_flow(self) -> None:
        """保存流程。"""
        flow_name = self.flow_manage_name_edit.text().strip()
        steps = self._collect_flow_steps()
        try:
            step_delay_ms = max(0, int(float(self.flow_manage_delay_edit.text().strip() or "1000")))
        except ValueError:
            self._show_warning("保存失败", "步间延时必须是数字。")
            self._append_log("后台", "保存流程", "失败", "步间延时必须是数字")
            return
        if not flow_name:
            self._show_warning("保存失败", "流程名称不能为空。")
            self._append_log("后台", "保存流程", "失败", "流程名称不能为空")
            return
        if not steps:
            self._show_warning("保存失败", "流程至少需要一个步骤。")
            self._append_log("后台", "保存流程", "失败", "流程至少需要一个步骤")
            return
        missing = [step for step in steps if step not in self.table]
        if missing:
            detail = f"存在未定义模板: {', '.join(missing)}"
            self._show_warning("保存失败", detail)
            self._append_log("后台", "保存流程", "失败", detail)
            return
        flow = self.service.get_flow(self.current_flow_manage_name) if self.current_flow_manage_name and self.current_flow_manage_name in self.service.flows else None
        if flow and self.current_flow_manage_name != flow_name:
            self.service.delete_flow(self.current_flow_manage_name)
        from .models import FlowDefinition
        new_flow = FlowDefinition(name=flow_name, steps=tuple(steps), step_delay_ms=step_delay_ms)
        self.service.save_flow(new_flow)
        self.current_flow_manage_name = flow_name
        self.current_flow_name = flow_name if self.current_flow_name in {None, "", flow_name} else self.current_flow_name
        self._refresh_flow_combo()
        self._refresh_flow_manage_tree()
        self.status_label.setText(f"已保存流程: {flow_name}")
        self._append_log("后台", "保存流程", "成功", f"{flow_name} | {len(steps)} 步 | 延时 {step_delay_ms}ms")

    def _delete_flow(self) -> None:
        """删除流程。"""
        flow_name = self.flow_manage_name_edit.text().strip()
        if not flow_name:
            self._show_warning("删除失败", "当前没有选中的流程。")
            self._append_log("后台", "删除流程", "失败", "当前没有选中的流程")
            return
        if flow_name not in self.service.flows:
            self._show_warning("删除失败", f"流程不存在: {flow_name}")
            self._append_log("后台", "删除流程", "失败", f"流程不存在: {flow_name}")
            return
        self.service.delete_flow(flow_name)
        if self.current_flow_name == flow_name:
            self.current_flow_name = None
            self.flow_step_index = 0
            self.flow_status = "空闲"
            self.flow_current_step = "-"
        self.current_flow_manage_name = None
        self._new_flow()
        self._refresh_flow_combo()
        self._refresh_flow_manage_tree()
        self.status_label.setText(f"已删除流程: {flow_name}")
        self._append_log("后台", "删除流程", "成功", flow_name)

    def _refresh_flow_steps(self) -> None:
        """刷新流程。"""
        if not hasattr(self, "flow_step_tree"):
            return
        self.flow_step_tree.clear()
        if not self.current_flow_name or self.current_flow_name not in self.service.flows:
            return
        flow = self.service.get_flow(self.current_flow_name)
        for index, step in enumerate(flow.steps):
            step_state = "待执行"
            if index < self.flow_step_index:
                step_state = "已完成"
            elif index == self.flow_step_index:
                if self.flow_running:
                    step_state = "执行中"
                elif self.flow_status == "失败":
                    step_state = "失败"
                elif self.flow_status == "已停止":
                    step_state = "已停止"
            item = QTreeWidgetItem([step, step_state])
            self.flow_step_tree.addTopLevelItem(item)
            if index == self.flow_step_index:
                self.flow_step_tree.setCurrentItem(item)

    def _refresh_flow_status_panel(self) -> None:
        """刷新流程状态面板。"""
        if not hasattr(self, "flow_name_label"):
            return
        if self.current_flow_name and self.current_flow_name in self.service.flows:
            flow = self.service.get_flow(self.current_flow_name)
            total = len(flow.steps)
            current_step = min(self.flow_step_index + 1, total) if total else 0
            if self.flow_step_index >= total and total:
                current_step = total
            self.flow_name_label.setText(flow.name)
            self.flow_progress_label.setText(f"{current_step} / {total}")
        else:
            self.flow_name_label.setText("-")
            self.flow_progress_label.setText("0 / 0")
        self.flow_status_label.setText(self.flow_status)
        self.flow_step_label.setText(self.flow_current_step)

