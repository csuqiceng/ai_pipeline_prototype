"""流程列表、流程步骤编辑、保存和选择逻辑。"""

from __future__ import annotations

from PySide6.QtWidgets import QTreeWidgetItem

from .flow_registry import FlowEntry, FlowStep
from .models import FlowDefinition
from .permission_service import PermissionDenied, PermissionService


class FlowManagementMixin:
    """维护流程列表、步骤树和流程文件的主窗口能力。"""
    def _current_permission_actor(self) -> str:
        role = getattr(self, "_authenticated_role", None)
        if role is None:
            role = getattr(self, "current_user_role", None)
        if role is None:
            role = getattr(self, "user_role", None)
        if role is None:
            role = "engineer"
        return str(role or "operator").strip().lower()

    def _permission_service(self) -> PermissionService:
        return PermissionService(actor=self._current_permission_actor())

    def _require_permission(self, action: str, title: str) -> bool:
        try:
            self._permission_service().require(action)
            return True
        except PermissionDenied as exc:
            detail = str(exc)
            self._show_warning(title, detail)
            self._append_log("权限", action, "拒绝", detail)
            return False

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
            item = QTreeWidgetItem(self._flow_manage_tree_columns(flow_name))
            self.flow_manage_tree.addTopLevelItem(item)
            if self.current_flow_manage_name == flow_name:
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

    def _flow_entry_for_name(self, flow_name: str) -> FlowEntry | None:
        getter = getattr(self.service, "get_flow_entry", None)
        if not callable(getter):
            return None
        try:
            entry = getter(flow_name)
        except Exception:
            return None
        return entry if isinstance(entry, FlowEntry) else None

    def _flow_manage_tree_columns(self, flow_name: str) -> list[str]:
        entry = self._flow_entry_for_name(flow_name)
        if entry is not None:
            status = "已确认" if entry.confirmed else "草稿"
            summary = (
                f"{len(entry.steps)}步 | {entry.step_delay_ms}ms | "
                f"v{entry.version} | {status} | 演练{entry.rehearsal_spd}% | {entry.state}"
            )
            return [entry.name, summary]
        flow = self.service.get_flow(flow_name)
        return [flow.name, f"{len(flow.steps)} | {flow.step_delay_ms}ms"]

    def _flow_manage_step_labels(self, flow: FlowDefinition | FlowEntry) -> list[str]:
        labels: list[str] = []
        flow_name = getattr(flow, "name", "")
        for index, step in enumerate(getattr(flow, "steps", ()), start=1):
            if isinstance(step, str):
                labels.append(step)
                continue
            if isinstance(step, FlowStep):
                query_key = step.params.get("query_key")
                if query_key:
                    labels.append(str(query_key))
                    continue
                step_id = step.step_id or index
                labels.append(f"flow:{flow_name}:{step_id}")
                continue
            labels.append(str(step))
        return labels

    def _structured_flow_entry_from_labels(
        self,
        *,
        flow_name: str,
        steps: list[str],
        step_delay_ms: int,
    ) -> FlowEntry | None:
        source_name = self.current_flow_manage_name or flow_name
        source = self._flow_entry_for_name(source_name)
        if source is None:
            return None
        by_placeholder = {
            f"flow:{source.name}:{step.step_id or index}": step
            for index, step in enumerate(source.steps, start=1)
        }
        by_query_key = {
            str(step.params.get("query_key")): step
            for step in source.steps
            if isinstance(step.params, dict) and step.params.get("query_key")
        }
        selected: list[FlowStep] = []
        used_structured = False
        for label in steps:
            step = by_placeholder.get(label) or by_query_key.get(label)
            if step is None:
                if label in getattr(self, "table", {}):
                    return None
                return None
            used_structured = True
            selected.append(step)
        if not used_structured:
            return None
        return FlowEntry(
            name=flow_name,
            description=source.description,
            steps=selected,
            step_delay_ms=step_delay_ms,
            rehearsal_spd=source.rehearsal_spd,
            confirmed=source.confirmed,
            created_by=source.created_by,
            version=source.version,
            state=source.state,
            current_step=source.current_step,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    def _load_flow_into_manage_form(self, flow) -> None:
        """加载流程表单。"""
        source = self._flow_entry_for_name(flow.name) or flow
        self.flow_manage_name_edit.setText(source.name)
        self.flow_manage_delay_edit.setText(str(source.step_delay_ms))
        self._refresh_flow_step_manage_tree(self._flow_manage_step_labels(source))

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
        if not self._require_permission("flow.update", "权限不足"):
            return
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
        structured_entry = self._structured_flow_entry_from_labels(
            flow_name=flow_name,
            steps=steps,
            step_delay_ms=step_delay_ms,
        )
        if structured_entry is not None and hasattr(self.service, "save_flow_entry"):
            self.service.save_flow_entry(structured_entry)
            self.current_flow_manage_name = flow_name
            self.current_flow_name = flow_name if self.current_flow_name in {None, "", flow_name} else self.current_flow_name
            self._refresh_flow_combo()
            self._refresh_flow_manage_tree()
            self.status_label.setText(f"已保存流程: {flow_name}")
            self._append_log("后台", "保存流程", "成功", f"{flow_name} | {len(steps)} 步 | 延时 {step_delay_ms}ms")
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
        if not self._require_permission("flow.delete", "权限不足"):
            return
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

    def _start_flow_rehearsal(self) -> None:
        """启动结构化流程演练模式。"""
        if not self._require_permission("flow.rehearsal", "权限不足"):
            return
        flow_name = self.flow_manage_name_edit.text().strip() if hasattr(self, "flow_manage_name_edit") else ""
        if not flow_name:
            self._show_warning("演练失败", "当前没有选中的流程。")
            self._append_log("后台", "演练流程", "失败", "当前没有选中的流程")
            return
        ok, msg = self.service.start_flow_rehearsal(flow_name)
        if not ok:
            self._show_warning("演练失败", msg)
            self._append_log("后台", "演练流程", "失败", msg)
            return
        self.status_label.setText(msg)
        self._append_log("后台", "演练流程", "成功", msg)

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

