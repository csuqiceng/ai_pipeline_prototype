"""指令模板表单、查询表维护和导入导出逻辑。"""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtWidgets import QFileDialog, QGroupBox, QLabel, QPushButton, QTableWidgetItem, QTreeWidgetItem, QVBoxLayout

from .gui_constants import FUNC_LABELS, MOVE_TYPE_LABELS, STOP_CMD_LABELS
from .models import QueryRecord, SixAxisCommand, VrWriteRequest
from .query_table import load_query_table, save_query_table_json


class TemplateMixin:
    """为主窗口增加模板编辑和查询表维护能力。"""
    def _load_initial_record(self) -> None:
        """加载记录。"""
        if self.table:
            self.current_key = sorted(self.table)[0]
            self._load_record_into_form(self.table[self.current_key])

    @staticmethod
    def _func104_action_from_record(record: QueryRecord) -> str:
        """处理函数记录。"""
        return TemplateMixin._func104_action_from_params({
            "stop_mode": record.int_param("stop_mode"),
            "estop_ctrl": record.int_param("estop_ctrl"),
            "pause_ctrl": record.int_param("pause_ctrl"),
            "cancel_ctrl": record.int_param("cancel_ctrl"),
            "reset_ctrl": record.int_param("reset_ctrl"),
        })

    @staticmethod
    def _func104_action_from_params(params: dict[str, int]) -> str:
        """处理函数。"""
        estop = int(params.get("estop_ctrl", 0))
        pause = int(params.get("pause_ctrl", 0))
        cancel = int(params.get("cancel_ctrl", 0))
        reset = int(params.get("reset_ctrl", 0))
        if estop == 1 and pause == 0 and cancel == 0 and reset == 0:
            return "estop"
        if estop == 2 and pause == 0 and cancel == 0 and reset == 0:
            return "estop_release"
        if pause == 1 and estop == 0 and cancel == 0 and reset == 0:
            return "pause"
        if pause == 2 and estop == 0 and cancel == 0 and reset == 0:
            return "resume"
        if cancel == 1 and estop == 0 and pause == 0 and reset == 0:
            return "cancel"
        if cancel == 2 and estop == 0 and pause == 0 and reset == 0:
            return "cancel_release"
        if reset == 1 and estop == 0 and pause == 0 and cancel == 0:
            return "reset"
        return "custom"

    @staticmethod
    def _func104_stop_mode_from_params(params: dict[str, int]) -> int:
        """处理函数。"""
        if int(params.get("pause_ctrl", 0)) != 0 or int(params.get("cancel_ctrl", 0)) != 0:
            return 1
        return 0

    @staticmethod
    def _func104_params_from_action(action: str) -> dict[str, int]:
        """处理函数。"""
        params = {
            "stop_mode": 0,
            "estop_ctrl": 0,
            "pause_ctrl": 0,
            "cancel_ctrl": 0,
            "reset_ctrl": 0,
        }
        if action == "estop_release":
            params["estop_ctrl"] = 2
        elif action == "pause":
            params["stop_mode"] = 1
            params["pause_ctrl"] = 1
        elif action == "resume":
            params["stop_mode"] = 1
            params["pause_ctrl"] = 2
        elif action == "cancel":
            params["stop_mode"] = 1
            params["cancel_ctrl"] = 1
        elif action == "cancel_release":
            params["stop_mode"] = 1
            params["cancel_ctrl"] = 2
        elif action == "reset":
            params["reset_ctrl"] = 1
        elif action == "estop":
            params["estop_ctrl"] = 1
        return params

    def _func104_form_params(self) -> dict[str, int]:
        """处理函数表单。"""
        params = {
            "estop_ctrl": int(self.estop_ctrl_combo.currentData() or 0),
            "pause_ctrl": int(self.pause_ctrl_combo.currentData() or 0),
            "cancel_ctrl": int(self.cancel_ctrl_combo.currentData() or 0),
            "reset_ctrl": int(self.reset_ctrl_combo.currentData() or 0),
        }
        params["stop_mode"] = self._func104_stop_mode_from_params(params)
        return params

    def _set_func104_form_params(self, params: dict[str, int], *, update_action: bool = True) -> None:
        """设置函数表单。"""
        self._updating_func104_form = True
        try:
            self.stop_mode_combo.setCurrentIndex(self.stop_mode_combo.findData(self._func104_stop_mode_from_params(params)))
            self.estop_ctrl_combo.setCurrentIndex(self.estop_ctrl_combo.findData(int(params.get("estop_ctrl", 0))))
            self.pause_ctrl_combo.setCurrentIndex(self.pause_ctrl_combo.findData(int(params.get("pause_ctrl", 0))))
            self.cancel_ctrl_combo.setCurrentIndex(self.cancel_ctrl_combo.findData(int(params.get("cancel_ctrl", 0))))
            self.reset_ctrl_combo.setCurrentIndex(self.reset_ctrl_combo.findData(int(params.get("reset_ctrl", 0))))
            if update_action:
                action = self._func104_action_from_params(params)
                self.system_action_combo.setCurrentIndex(self.system_action_combo.findData(action))
        finally:
            self._updating_func104_form = False

    def _on_func104_action_changed(self, *_) -> None:
        """处理函数。"""
        if self._updating_func104_form:
            return
        action = str(self.system_action_combo.currentData() or "custom")
        if action != "custom":
            self._set_func104_form_params(self._func104_params_from_action(action), update_action=False)
        self._render_preview()

    def _on_func104_control_changed(self, *_) -> None:
        """处理函数。"""
        if self._updating_func104_form:
            return
        params = self._func104_form_params()
        self._updating_func104_form = True
        try:
            action = self._func104_action_from_params(params)
            self.system_action_combo.setCurrentIndex(self.system_action_combo.findData(action))
            self.stop_mode_combo.setCurrentIndex(self.stop_mode_combo.findData(self._func104_stop_mode_from_params(params)))
        finally:
            self._updating_func104_form = False
        self._render_preview()

    def _load_record_into_form(self, record: QueryRecord) -> None:
        """加载记录表单。"""
        self.name_edit.setText(record.query_key)
        self.func_num_combo.setCurrentIndex(self.func_num_combo.findData(record.func_num))
        self._sync_func_name_display()
        self.keywords_edit.setText(record.keywords)
        self._set_func104_form_params(
            {
                "stop_mode": record.int_param("stop_mode"),
                "estop_ctrl": record.int_param("estop_ctrl"),
                "pause_ctrl": record.int_param("pause_ctrl"),
                "cancel_ctrl": record.int_param("cancel_ctrl"),
                "reset_ctrl": record.int_param("reset_ctrl"),
            }
        )
        self.axis_no_edit.setText(str(record.int_param("axis_no")))
        self.pos_val_edit.setText(self._fmt(record.float_param("pos_val")))
        self.spd_pct_edit.setText(self._fmt(record.float_param("spd_pct")))
        self.acc_pct_edit.setText(self._fmt(record.float_param("acc_pct")))
        self.dec_pct_edit.setText(self._fmt(record.float_param("dec_pct")))
        self.stop_cmd_combo.setCurrentIndex(self.stop_cmd_combo.findData(record.int_param("stop_cmd")))
        self.fuzzy_pos_combo.setCurrentIndex(self.fuzzy_pos_combo.findData(record.int_param("fuzzy_pos")))
        self.fuzzy_spd_combo.setCurrentIndex(self.fuzzy_spd_combo.findData(record.int_param("fuzzy_spd")))
        self.fuzzy_acc_combo.setCurrentIndex(self.fuzzy_acc_combo.findData(record.int_param("fuzzy_acc")))
        self.fuzzy_dec_combo.setCurrentIndex(self.fuzzy_dec_combo.findData(record.int_param("fuzzy_dec")))
        self.x_edit.setText(self._fmt(record.float_param("target_x")))
        self.y_edit.setText(self._fmt(record.float_param("target_y")))
        self.z_edit.setText(self._fmt(record.float_param("target_z")))
        self.rx_edit.setText(self._fmt(record.float_param("target_rx")))
        self.ry_edit.setText(self._fmt(record.float_param("target_ry")))
        self.rz_edit.setText(self._fmt(record.float_param("target_rz")))
        self.move_type_combo.setCurrentIndex(self.move_type_combo.findData(record.int_param("move_type")))
        self.point_count_edit.setText(str(record.int_param("point_count")))
        self._set_points_table_values(record.params.get("points", []))
        self.delay_sec_edit.setText(self._fmt(record.float_param("delay_sec")))
        self.io_no_edit.setText(str(record.int_param("io_no")))
        self.io_action_combo.setCurrentIndex(self.io_action_combo.findData(record.int_param("io_action")))
        self.safety_edit.setText(str(record.safety_level))
        self.desc_edit.setText(record.description)
        self._sync_func_form_mode()
        self._update_current_template_info(record)
        self._render_preview()

    def _set_points_table_values(self, points: object) -> None:
        """设置表。"""
        rows: list[list[float]] = []
        if isinstance(points, (list, tuple)):
            for point in points:
                if isinstance(point, (list, tuple)):
                    padded = list(point[:6]) + [0.0] * max(0, 6 - len(point))
                    rows.append([float(value) for value in padded[:6]])
        if not rows:
            rows = [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]]
        self.points_table.blockSignals(True)
        self.points_table.setRowCount(len(rows))
        for row, point in enumerate(rows):
            for col, value in enumerate(point):
                self.points_table.setItem(row, col, QTableWidgetItem(self._fmt(value)))
        self.points_table.blockSignals(False)
        self.point_count_edit.setText(str(len(rows)))

    def _points_from_table(self) -> list[list[float]]:
        """处理表。"""
        points: list[list[float]] = []
        for row in range(self.points_table.rowCount()):
            point: list[float] = []
            for col in range(6):
                item = self.points_table.item(row, col)
                text = item.text().strip() if item else "0"
                point.append(float(text) if text else 0.0)
            points.append(point)
        return points

    def _sync_points_from_table(self) -> None:
        """同步表。"""
        self.point_count_edit.setText(str(self.points_table.rowCount()))
        self._render_preview()

    def _add_interp_point(self) -> None:
        """新增插补点位。"""
        row = self.points_table.rowCount()
        self.points_table.insertRow(row)
        for col in range(6):
            self.points_table.setItem(row, col, QTableWidgetItem("0"))
        self._sync_points_from_table()

    def _delete_interp_point(self) -> None:
        """删除插补点位。"""
        row = self.points_table.currentRow()
        if row < 0:
            row = self.points_table.rowCount() - 1
        if row >= 0 and self.points_table.rowCount() > 1:
            self.points_table.removeRow(row)
            self._sync_points_from_table()

    def _move_interp_point(self, direction: int) -> None:
        """处理移动插补点位。"""
        row = self.points_table.currentRow()
        target = row + direction
        if row < 0 or target < 0 or target >= self.points_table.rowCount():
            return
        values = self._points_from_table()
        values[row], values[target] = values[target], values[row]
        self._set_points_table_values(values)
        self.points_table.setCurrentCell(target, 0)
        self._render_preview()

    def _collect_record(self) -> QueryRecord:
        """收集记录。"""
        def num(text: str) -> float:
            """处理相关数据。"""
            text = text.strip().replace("%", "")
            return float(text) if text else 0.0
        func_num = int(self.func_num_combo.currentData())
        params: dict[str, object]
        if func_num == 104:
            params = self._func104_form_params()
        elif func_num in (106, 107):
            params = {
                "axis_no": int(float(self.axis_no_edit.text() or "0")),
                "pos_val": num(self.pos_val_edit.text()),
                "spd_pct": num(self.spd_pct_edit.text()),
                "acc_pct": num(self.acc_pct_edit.text()),
                "dec_pct": num(self.dec_pct_edit.text()),
                "fuzzy_pos": int(self.fuzzy_pos_combo.currentData()),
                "fuzzy_spd": int(self.fuzzy_spd_combo.currentData()),
                "fuzzy_acc": int(self.fuzzy_acc_combo.currentData()),
                "fuzzy_dec": int(self.fuzzy_dec_combo.currentData()),
                "stop_cmd": int(self.stop_cmd_combo.currentData()),
            }
        elif func_num == 11:
            params = {
                "point_count": self.points_table.rowCount(),
                "spd_pct": num(self.spd_pct_edit.text()),
                "acc_pct": num(self.acc_pct_edit.text()),
                "dec_pct": num(self.dec_pct_edit.text()),
                "points": self._points_from_table(),
            }
        elif func_num == 109:
            params = {
                "delay_sec": num(self.delay_sec_edit.text()),
            }
        elif func_num == 110:
            params = {
                "delay_sec": num(self.delay_sec_edit.text()),
            }
        elif func_num == 120:
            params = {
                "io_no": int(float(self.io_no_edit.text() or "0")),
                "io_action": int(self.io_action_combo.currentData()),
            }
        elif func_num == 108:
            params = {
                "target_x": num(self.x_edit.text()),
                "target_y": num(self.y_edit.text()),
                "target_z": num(self.z_edit.text()),
                "target_rx": num(self.rx_edit.text()),
                "target_ry": num(self.ry_edit.text()),
                "target_rz": num(self.rz_edit.text()),
                "spd_pct": num(self.spd_pct_edit.text()),
                "acc_pct": num(self.acc_pct_edit.text()),
                "dec_pct": num(self.dec_pct_edit.text()),
                "stop_cmd": int(self.stop_cmd_combo.currentData()),
                "fuzzy_pos": int(self.fuzzy_pos_combo.currentData()),
                "fuzzy_spd": int(self.fuzzy_spd_combo.currentData()),
                "fuzzy_acc": int(self.fuzzy_acc_combo.currentData()),
                "fuzzy_dec": int(self.fuzzy_dec_combo.currentData()),
                "move_type": int(self.move_type_combo.currentData()),
            }
        else:
            raise ValueError(f"不支持的函数号: {func_num}")
        return QueryRecord(
            query_key=self.name_edit.text().strip(),
            func_num=func_num,
            params=params,
            keywords=self.keywords_edit.text().strip(),
            description=self.desc_edit.text().strip(),
            safety_level=int(float(self.safety_edit.text() or "5")),
        )

    def _render_preview(self) -> None:
        """处理相关数据。"""
        try:
            record = self._collect_record()
            six_command = self.service.build_six_command_from_record(record)
            payload = {
                "queryKey": record.query_key,
                "funcNum": record.func_num,
                "funcName": FUNC_LABELS.get(record.func_num, f"Func{record.func_num}"),
                "keywords": record.keywords,
                "description": record.description,
                "safetyLevel": record.safety_level,
                "params": record.params,
                "writes": [
                    {
                        "start_vr": req.start_vr,
                        "values": list(req.values),
                    }
                    for req in [*six_command.to_func_writes(), six_command.to_trigger_write()]
                ],
            }
            self.preview_edit.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
            self._update_current_template_info(record)
        except Exception as exc:
            fallback_name = self.name_edit.text().strip() or "-"
            self.preview_edit.setPlainText(
                json.dumps(
                    {
                        "queryKey": fallback_name,
                        "status": "preview_unavailable",
                        "reason": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            self._update_current_template_info(
                QueryRecord(
                    query_key=fallback_name,
                    func_num=0,
                    params={},
                ),
            )

    def _update_current_template_info(self, record: QueryRecord) -> None:
        """更新当前模板。"""
        self.current_name_label.setText(record.query_key or "-")
        self.current_code_label.setText(str(record.func_num) if record.func_num else "-")
        self.current_cmd_label.setText(FUNC_LABELS.get(record.func_num, "-"))
        self.current_type_label.setText("函数号参数模板")
        try:
            six_cmd = self.service.build_six_command_from_record(record)
            if six_cmd.func_num in (106, 107, 108):
                self.current_options_label.setText(self._describe_six_motion_options(six_cmd))
            elif six_cmd.func_num == 104:
                self.current_options_label.setText(
                    f"estop={six_cmd.estop_ctrl}, pause={six_cmd.pause_ctrl}, "
                    f"cancel={six_cmd.cancel_ctrl}, reset={six_cmd.reset_ctrl}"
                )
            else:
                self.current_options_label.setText("-")
        except Exception:
            self.current_options_label.setText("-")

    def _refresh_all(self) -> None:
        """刷新相关数据。"""
        self._refresh_command_cards()
        self._refresh_flow_combo()
        self._refresh_flow_manage_tree()
        self._refresh_flow_available_tree()
        self._refresh_template_tree()
        self._refresh_history()
        self._render_preview()
        self._refresh_status_labels()
        self._refresh_logs()

    def _refresh_command_cards(self) -> None:
        """刷新命令。"""
        while self.command_grid_layout.count():
            item = self.command_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        filter_text = self.command_filter_edit.text().strip().lower() if hasattr(self, "command_filter_edit") else ""
        type_filter = self.command_type_combo.currentText() if hasattr(self, "command_type_combo") else "全部"

        visible_records: list[QueryRecord] = []
        for record in sorted(self.table.values(), key=lambda r: r.query_key):
            if type_filter != "全部" and type_filter != f"Func{record.func_num}":
                continue
            haystack = " ".join([record.query_key, record.keywords, record.description, record.function_name]).lower()
            if filter_text and filter_text not in haystack:
                continue
            visible_records.append(record)

        if hasattr(self, "command_count_label"):
            self.command_count_label.setText(f"{len(visible_records)} 项")

        for idx, record in enumerate(visible_records):
            card = QGroupBox(record.query_key)
            card.setObjectName("subPanel")
            card.setMinimumWidth(170)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(3)
            meta_top = QLabel(f"Func{record.func_num} | {FUNC_LABELS.get(record.func_num, '-')}")
            meta_top.setWordWrap(True)
            layout.addWidget(meta_top)
            meta_kind = QLabel(record.summary_text())
            meta_kind.setObjectName("tip")
            layout.addWidget(meta_kind)
            pose = record.pose_tuple()
            if pose is not None:
                pos_text = (
                    f"X {self._fmt(pose[0])}  "
                    f"Y {self._fmt(pose[1])}\n"
                    f"Z {self._fmt(pose[2])}"
                )
                pos_label = QLabel(pos_text)
                pos_label.setObjectName("tip")
                pos_label.setWordWrap(True)
                layout.addWidget(pos_label)
            btn = QPushButton("执行")
            btn.setProperty("klass", "green")
            btn.setMinimumHeight(28)
            btn.clicked.connect(lambda _=False, key=record.query_key: self._send_record(key))
            layout.addWidget(btn)
            self.command_grid_layout.addWidget(card, idx // 3, idx % 3)

    def _refresh_template_tree(self) -> None:
        """刷新模板树。"""
        current_key = self.current_key
        selected_item: QTreeWidgetItem | None = None
        self.template_tree.blockSignals(True)
        try:
            self.template_tree.clear()
            for record in sorted(self.table.values(), key=lambda r: r.query_key):
                kind = f"Func{record.func_num}"
                item = QTreeWidgetItem([record.query_key, kind])
                self.template_tree.addTopLevelItem(item)
                if current_key == record.query_key:
                    selected_item = item
        finally:
            self.template_tree.blockSignals(False)
        if selected_item is not None:
            self.template_tree.setCurrentItem(selected_item)

    def _refresh_history(self) -> None:
        """刷新相关数据。"""
        self.history_table.setRowCount(0)
        rows = self.history or [{"task": 1001, "code": 1001, "name": "-", "type": "参数型指令", "result": "待执行"}]
        for row_index, row in enumerate(rows):
            self.history_table.insertRow(row_index)
            for col_index, key in enumerate(["task", "code", "name", "type", "result"]):
                self.history_table.setItem(row_index, col_index, QTableWidgetItem(str(row[key])))

    def _new_record(self) -> None:
        """处理记录。"""
        self.current_key = None
        self.name_edit.setText("")
        self.func_num_combo.setCurrentIndex(self.func_num_combo.findData(108))
        self.keywords_edit.setText("")
        self._set_func104_form_params(self._func104_params_from_action("custom"))
        self.axis_no_edit.setText("0")
        self.pos_val_edit.setText("0")
        self.spd_pct_edit.setText("50")
        self.acc_pct_edit.setText("60")
        self.dec_pct_edit.setText("60")
        self.x_edit.setText("0")
        self.y_edit.setText("0")
        self.z_edit.setText("0")
        self.rx_edit.setText("0")
        self.ry_edit.setText("0")
        self.rz_edit.setText("0")
        self.stop_cmd_combo.setCurrentIndex(self.stop_cmd_combo.findData(0))
        self.fuzzy_pos_combo.setCurrentIndex(self.fuzzy_pos_combo.findData(0))
        self.fuzzy_spd_combo.setCurrentIndex(self.fuzzy_spd_combo.findData(0))
        self.fuzzy_acc_combo.setCurrentIndex(self.fuzzy_acc_combo.findData(0))
        self.fuzzy_dec_combo.setCurrentIndex(self.fuzzy_dec_combo.findData(0))
        self.move_type_combo.setCurrentIndex(self.move_type_combo.findData(0))
        self.safety_edit.setText("5")
        self.desc_edit.setText("")
        self._sync_func_form_mode()
        self.status_label.setText("已创建空白模板。")
        self._append_log("后台", "新增模板", "成功", "已创建空白模板")

    def _save_record(self) -> None:
        """保存记录。"""
        old_key = self.current_key
        try:
            record = self._collect_record()
        except ValueError:
            self._show_warning("输入错误", "模板参数必须是数字。")
            self._append_log("后台", "保存模板", "失败", "模板参数必须是数字")
            return
        if not record.query_key:
            self._show_warning("输入错误", "显示名称不能为空。")
            self._append_log("后台", "保存模板", "失败", "显示名称不能为空")
            return
        validation_error = self._validate_record(record)
        if validation_error:
            self._show_warning("输入错误", validation_error)
            self._append_log("后台", "保存模板", "失败", validation_error)
            return
        validation_warning = self._record_protocol_warning(record)
        if validation_warning:
            self._append_log("后台", "保存模板", "警告", validation_warning)
        if old_key and old_key != record.query_key and old_key in self.table:
            del self.table[old_key]
        self.table[record.query_key] = record
        save_query_table_json(self.json_path, self.table)
        self.service.reload()
        self.current_key = record.query_key
        self._refresh_all()
        self.status_label.setText(f"已保存模板: {record.query_key}")
        self._append_log("后台", "保存模板", "成功", record.query_key)

    def _clone_record(self) -> None:
        """处理记录。"""
        record = self._collect_record()
        if not record.query_key:
            self._show_warning("无法另存为", "请先填写显示名称。")
            self._append_log("后台", "另存模板", "失败", "显示名称不能为空")
            return
        clone = QueryRecord(
            query_key=f"{record.query_key} - 副本",
            func_num=record.func_num,
            params=dict(record.params),
            keywords=record.keywords,
            description=record.description,
            safety_level=record.safety_level,
        )
        self.table[clone.query_key] = clone
        save_query_table_json(self.json_path, self.table)
        self.service.reload()
        self.current_key = clone.query_key
        self._load_record_into_form(clone)
        self._refresh_all()
        self.status_label.setText(f"已另存模板: {clone.query_key}")
        self._append_log("后台", "另存模板", "成功", clone.query_key)

    def _delete_record(self) -> None:
        """删除记录。"""
        key = self.name_edit.text().strip()
        if not key:
            self._show_warning("无法删除", "当前没有选中的模板。")
            self._append_log("后台", "删除模板", "失败", "当前没有选中的模板")
            return
        if key not in self.table:
            self._show_warning("无法删除", f"模板不存在: {key}")
            self._append_log("后台", "删除模板", "失败", f"模板不存在: {key}")
            return
        del self.table[key]
        save_query_table_json(self.json_path, self.table)
        self.service.reload()
        self.current_key = None
        self._new_record()
        self._refresh_all()
        self.status_label.setText(f"已删除模板: {key}")
        self._append_log("后台", "删除模板", "成功", key)

    def _export_template_json(self) -> None:
        """导出模板配置文件。"""
        export_dir = self.runtime_root / "data" / "exported_templates"
        export_dir.mkdir(parents=True, exist_ok=True)
        default_name = export_dir / f"query_table_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出模板 JSON",
            str(default_name),
            "JSON Files (*.json)",
        )
        if not file_path:
            return
        save_query_table_json(file_path, self.table)
        self.status_label.setText(f"已导出模板 JSON: {file_path}")
        self._append_log("后台", "导出模板JSON", "成功", file_path)

    def _import_template_json(self) -> None:
        """导入模板配置文件。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入模板 JSON",
            str(self.runtime_root / "data"),
            "JSON Files (*.json)",
        )
        if not file_path:
            return
        imported = load_query_table(file_path)
        self.table = imported
        save_query_table_json(self.json_path, self.table)
        self.service.reload()
        self.current_key = None
        self._new_record()
        self._refresh_all()
        self.status_label.setText(f"已导入模板 JSON: {file_path}")
        self._append_log("后台", "导入模板JSON", "成功", file_path)

    def _on_template_selected(self) -> None:
        """处理模板选中。"""
        items = self.template_tree.selectedItems()
        if not items:
            return
        key = items[0].text(0)
        if key in self.table:
            self.current_key = key
            self._load_record_into_form(self.table[key])

    def _validate_record(self, record: QueryRecord) -> str | None:
        """校验记录。"""
        if record.func_num not in (11, 104, 106, 107, 108, 109, 110, 120):
            return "当前仅支持 Func11 / Func104 / Func106 / Func107 / Func108 / Func109 / Func110 / Func120。"
        if not (1 <= record.safety_level <= 5):
            return "安全等级必须在 1 到 5 之间。"
        if record.func_num == 104:
            if record.int_param("stop_mode") not in (0, 1):
                return "Func104 的停止模式只能是 0 或 1。"
            if record.int_param("estop_ctrl") not in (0, 1, 2):
                return "Func104 的 estop_ctrl 只能是 0 / 1 / 2。"
            if record.int_param("pause_ctrl") not in (0, 1, 2):
                return "Func104 的 pause_ctrl 只能是 0 / 1 / 2。"
            if record.int_param("cancel_ctrl") not in (0, 1, 2):
                return "Func104 的 cancel_ctrl 只能是 0 / 1 / 2。"
            if record.int_param("reset_ctrl") not in (0, 1):
                return "Func104 的 reset_ctrl 只能是 0 / 1。"
            return None
        if record.func_num in (106, 107, 108):
            if record.int_param("stop_cmd") not in (0, 1, 2, 3, 4, 5):
                return "停止指令必须在 0 到 5 之间。"
            for label, value in [
                ("位置模式", record.int_param("fuzzy_pos")),
                ("速度模式", record.int_param("fuzzy_spd")),
                ("加速度模式", record.int_param("fuzzy_acc")),
                ("减速度模式", record.int_param("fuzzy_dec")),
            ]:
                if value not in (0, 1):
                    return f"{label} 只能是 0 或 1。"
            if record.int_param("fuzzy_spd") == 0 and not (0 <= record.spd_pct_value() <= 100):
                return "速度百分比必须在 0 到 100 之间。"
            if record.int_param("fuzzy_acc") == 0 and not (0 <= record.acc_pct_value() <= 100):
                return "加速度百分比必须在 0 到 100 之间。"
            if record.int_param("fuzzy_dec") == 0 and not (0 <= record.dec_pct_value() <= 100):
                return "减速度百分比必须在 0 到 100 之间。"
        if record.func_num == 11:
            if not (0 <= record.spd_pct_value() <= 100):
                return "Func11 的速度百分比必须在 0 到 100 之间。"
            if not (0 <= record.acc_pct_value() <= 100):
                return "Func11 的加速度百分比必须在 0 到 100 之间。"
            if not (0 <= record.dec_pct_value() <= 100):
                return "Func11 的减速度百分比必须在 0 到 100 之间。"
        if record.func_num == 106:
            if not (0 <= record.int_param("axis_no") <= 5):
                return "Func106 的轴号只能是 0 到 5。"
        if record.func_num == 107:
            if not (6 <= record.int_param("axis_no") <= 11):
                return "Func107 的轴号只能是 6 到 11。"
        if record.func_num == 108:
            if record.int_param("move_type") not in (0, 1):
                return "Func108 的运动模式只能是 0 或 1。"
            pose = record.pose_tuple()
            if pose is None:
                return "Func108 缺少目标位姿参数。"
            if not (self.axis_ranges.x[0] <= pose[0] <= self.axis_ranges.x[1]):
                return f"X 坐标超出范围 {self.axis_ranges.x}。"
            if not (self.axis_ranges.y[0] <= pose[1] <= self.axis_ranges.y[1]):
                return f"Y 坐标超出范围 {self.axis_ranges.y}。"
            if not (self.axis_ranges.z[0] <= pose[2] <= self.axis_ranges.z[1]):
                return f"Z 坐标超出范围 {self.axis_ranges.z}。"
        if record.func_num == 11:
            if record.int_param("point_count") <= 0:
                return "Func11 的 point_count 必须大于 0。"
            points = record.params.get("points", [])
            if not isinstance(points, list) or len(points) < record.int_param("point_count"):
                return "Func11 的 points 数量不足。"
        if record.func_num == 109:
            if record.float_param("delay_sec") <= 0:
                return "Func109 的 delay_sec 必须大于 0。"
        if record.func_num == 110:
            if record.float_param("delay_sec") <= 0:
                return "Func110 的 delay_sec 必须大于 0。"
        if record.func_num == 120:
            if not (0 <= record.int_param("io_no") <= 11):
                return "Func120 的 io_no 必须在 0 到 11 之间。"
            if record.int_param("io_action") not in (0, 1):
                return "Func120 的 io_action 只能是 0 或 1。"
        return None

    def _record_protocol_warning(self, record: QueryRecord) -> str | None:
        """处理记录。"""
        return None

    @staticmethod
    def _status_text(status_code: int) -> str:
        """处理状态。"""
        return {
            0: "空闲",
            1: "运行中",
            2: "暂停",
            3: "故障",
        }.get(status_code, f"状态{status_code}")

    def _sync_func_form_mode(self, *_) -> None:
        """同步函数表单。"""
        func_num = int(self.func_num_combo.currentData() or 108)
        if func_num == 11 and self.points_table.rowCount() <= 0:
            self._set_points_table_values([[0, 0, 0, 0, 0, 0], [10, 10, 10, 0, 0, 0]])
        visible_keys = {"name", "func_num", "func_name", "keywords", "safety", "desc"}
        if func_num == 104:
            visible_keys |= {"system_action", "estop_ctrl", "pause_ctrl", "cancel_ctrl", "reset_ctrl"}
        elif func_num == 11:
            visible_keys |= {"point_count", "points", "point_buttons", "spd_pct", "acc_pct", "dec_pct"}
        elif func_num in (106, 107):
            visible_keys |= {
                "axis_no",
                "pos_val",
                "spd_pct",
                "acc_pct",
                "dec_pct",
                "stop_cmd",
                "fuzzy_pos",
                "fuzzy_spd",
                "fuzzy_acc",
                "fuzzy_dec",
            }
        elif func_num == 108:
            visible_keys |= {
                "target_x",
                "target_y",
                "target_z",
                "target_rx",
                "target_ry",
                "target_rz",
                "spd_pct",
                "acc_pct",
                "dec_pct",
                "stop_cmd",
                "fuzzy_pos",
                "fuzzy_spd",
                "fuzzy_acc",
                "fuzzy_dec",
                "move_type",
            }
        elif func_num == 109:
            visible_keys |= {"delay_sec"}
        elif func_num == 110:
            visible_keys |= {"delay_sec"}
        elif func_num == 120:
            visible_keys |= {"io_no", "io_action"}
        for key, (label, widget) in self.record_form_rows.items():
            is_visible = key in visible_keys
            label.setVisible(is_visible)
            widget.setVisible(is_visible)

    def _sync_func_name_display(self, *_) -> None:
        """同步函数。"""
        func_num = int(self.func_num_combo.currentData() or 108)
        self.func_name_edit.setText(FUNC_LABELS.get(func_num, f"Func{func_num}"))

    def _describe_six_motion_options(self, six_cmd: SixAxisCommand) -> str:
        """处理六轴运动。"""
        stop_cmd = int(six_cmd.stop_cmd)
        stop_desc = STOP_CMD_LABELS.get(stop_cmd, f"stop_cmd={stop_cmd}")
        detail = (
            f"stop_cmd={stop_cmd}({stop_desc}) | "
            f"fuzzy_pos={int(six_cmd.fuzzy_pos)} "
            f"fuzzy_spd={int(six_cmd.fuzzy_spd)} "
            f"fuzzy_acc={int(six_cmd.fuzzy_acc)} "
            f"fuzzy_dec={int(six_cmd.fuzzy_dec)}"
        )
        if six_cmd.func_num == 108:
            move_type = int(six_cmd.move_type)
            move_desc = MOVE_TYPE_LABELS.get(move_type, f"move_type={move_type}")
            detail += f" | move_type={move_type}({move_desc})"
        return detail

    def _evaluate_feedback_result(self, feedback: list[float] | None) -> tuple[bool, str]:
        """评估反馈结果。"""
        # 六轴协议的错误通过异常传递，由结果回调中的异常分支统一处理。
        # 能走到这里的都是成功返回的坐标值。
        return True, ""

