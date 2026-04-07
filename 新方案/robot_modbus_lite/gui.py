from __future__ import annotations

import threading
import tkinter as tk
import json
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from .models import FixedVrCommand, FlowDefinition, QueryRecord, VrWriteRequest
from .query_table import (
    bootstrap_query_table_json,
    load_query_table,
    save_query_table_json,
)
from .zmotion_client import ZMotionClientError, ZMotionVrClient
from .service import RobotModbusService


class QueryTableEditorApp:
    def __init__(self, root: tk.Tk, *, json_path: Path, csv_path: Path) -> None:
        self.root = root
        self.root.title("Robot Modbus Lite - Query Table Editor")
        self.root.geometry("1200x720")
        self.root.minsize(980, 620)

        self.runtime_root = _runtime_dir()
        self.resource_root = _resource_dir()
        self.csv_path = csv_path
        self.json_path = bootstrap_query_table_json(json_path, csv_path)
        self.flows_path = self.runtime_root / "data" / "flows.json"
        self.table = load_query_table(self.json_path)
        self.service = RobotModbusService(self.json_path, flows_path=self.flows_path)

        self.query_key_var = tk.StringVar()
        self.function_id_var = tk.StringVar()
        self.function_name_var = tk.StringVar(value="movabs")
        self.data_format_var = tk.StringVar(value="IEE")
        self.x_var = tk.StringVar(value="0")
        self.y_var = tk.StringVar(value="0")
        self.z_var = tk.StringVar(value="0")
        self.rx_var = tk.StringVar(value="0")
        self.ry_var = tk.StringVar(value="0")
        self.rz_var = tk.StringVar(value="0")
        self.speed_var = tk.StringVar(value="10")
        self.host_var = tk.StringVar(value="192.168.1.11")
        self.connection_var = tk.StringVar(value="检测中...")
        self.command_input_var = tk.StringVar(value="移动到位置A")
        self.send_status_var = tk.StringVar(value="等待执行命令。")
        self.flow_name_var = tk.StringVar()
        self.flow_steps_var = tk.StringVar(value="归零,home,位置A,位置B")
        self.flow_status_var = tk.StringVar(value="等待执行流程。")
        self.vr_command_var = tk.StringVar(value="VR500=触发位, VR501-VR507=参数1-7")
        self.vr_status_var = tk.StringVar(value="VR600-VR604")
        self.status_var = tk.StringVar(value=f"JSON 数据源: {self.json_path}")

        self._build_layout()
        self._refresh_table()
        self._refresh_flow_list()
        self.root.after(200, self._run_connection_check)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew")

        command_tab = ttk.Frame(notebook, padding=12)
        command_tab.columnconfigure(0, weight=1)
        command_tab.rowconfigure(2, weight=1)
        command_tab.rowconfigure(4, weight=1)
        notebook.add(command_tab, text="连接与执行")

        manage_tab = ttk.Frame(notebook, padding=12)
        manage_tab.columnconfigure(0, weight=3)
        manage_tab.columnconfigure(1, weight=2)
        manage_tab.rowconfigure(0, weight=1)
        notebook.add(manage_tab, text="添加与删除")

        self._build_command_tab(command_tab)
        self._build_manage_tab(manage_tab)

        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", relief="sunken")
        status.grid(row=1, column=0, sticky="ew")

    def _build_command_tab(self, parent: ttk.Frame) -> None:
        top_bar = ttk.LabelFrame(parent, text="连接状态", padding=12)
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top_bar.columnconfigure(1, weight=1)
        ttk.Label(top_bar, text="控制器地址:", font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(top_bar, textvariable=self.host_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Label(top_bar, text="连接状态:", font=("Microsoft YaHei UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(top_bar, textvariable=self.connection_var).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Button(top_bar, text="重新检测连接", command=self._run_connection_check).grid(row=0, column=2, rowspan=2, sticky="e")
        ttk.Label(top_bar, text="写入区:", font=("Microsoft YaHei UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(top_bar, textvariable=self.vr_command_var).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(top_bar, text="反馈区:", font=("Microsoft YaHei UI", 10, "bold")).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(top_bar, textvariable=self.vr_status_var).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        command_frame = ttk.LabelFrame(parent, text="执行命令", padding=12)
        command_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        command_frame.columnconfigure(1, weight=1)
        ttk.Label(command_frame, text="命令文本:").grid(row=0, column=0, sticky="w")
        ttk.Entry(command_frame, textvariable=self.command_input_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(command_frame, text="解析预览", command=self._preview_command).grid(row=0, column=2, sticky="e")
        ttk.Button(command_frame, text="写入VR并触发", command=self._send_command).grid(row=1, column=2, sticky="e", pady=(10, 0))
        ttk.Button(command_frame, text="读取反馈区", command=self._read_status_block).grid(row=2, column=2, sticky="e", pady=(10, 0))
        ttk.Label(command_frame, textvariable=self.send_status_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        result_frame = ttk.LabelFrame(parent, text="执行预览", padding=12)
        result_frame.grid(row=2, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self.preview_text = scrolledtext.ScrolledText(result_frame, wrap="word", font=("Consolas", 10))
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        self.preview_text.insert("1.0", "解析后的请求数据会显示在这里。")

        flow_frame = ttk.LabelFrame(parent, text="流程执行", padding=12)
        flow_frame.grid(row=3, column=0, sticky="ew", pady=(10, 10))
        flow_frame.columnconfigure(1, weight=1)
        ttk.Label(flow_frame, text="流程名称:").grid(row=0, column=0, sticky="w")
        self.flow_combo = ttk.Combobox(flow_frame, textvariable=self.flow_name_var, state="readonly")
        self.flow_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.flow_combo.bind("<<ComboboxSelected>>", self._on_flow_select)
        ttk.Button(flow_frame, text="预览流程", command=self._preview_flow).grid(row=0, column=2, sticky="e")
        ttk.Button(flow_frame, text="执行流程", command=self._send_flow).grid(row=1, column=2, sticky="e", pady=(10, 0))
        ttk.Label(flow_frame, textvariable=self.flow_status_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        flow_result_frame = ttk.LabelFrame(parent, text="流程预览", padding=12)
        flow_result_frame.grid(row=4, column=0, sticky="nsew")
        flow_result_frame.columnconfigure(0, weight=1)
        flow_result_frame.rowconfigure(0, weight=1)
        self.flow_preview_text = scrolledtext.ScrolledText(flow_result_frame, wrap="word", font=("Consolas", 10))
        self.flow_preview_text.grid(row=0, column=0, sticky="nsew")
        self.flow_preview_text.insert("1.0", "流程步骤和发送值会显示在这里。")

    def _build_manage_tab(self, parent: ttk.Frame) -> None:
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(1, weight=3)
        left.rowconfigure(3, weight=2)
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.columnconfigure(1, weight=1)

        ttk.Label(left, text="参数模板表(JSON)", font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        columns = ("query_key", "function_id", "function_name", "data_format", "x", "y", "z", "rx", "ry", "rz", "speed")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=20)
        headings = {
            "query_key": "查询键",
            "function_id": "记录编号",
            "function_name": "记录类型",
            "data_format": "格式",
            "x": "VR501",
            "y": "VR502",
            "z": "VR503",
            "rx": "VR504",
            "ry": "VR505",
            "rz": "VR506",
            "speed": "VR507",
        }
        widths = {
            "query_key": 120,
            "function_id": 80,
            "function_name": 90,
            "data_format": 70,
            "x": 90,
            "y": 90,
            "z": 90,
            "rx": 80,
            "ry": 80,
            "rz": 80,
            "speed": 80,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="center")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        button_row = ttk.Frame(left)
        button_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(button_row, text="刷新", command=self._reload_from_json).pack(side="left")
        ttk.Button(button_row, text="导出/保存 JSON", command=self._save_json).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="清空表单", command=self._reset_form).pack(side="left", padx=(8, 0))

        ttk.Label(left, text="已保存流程", font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=3, column=0, sticky="w", pady=(16, 8)
        )
        flow_columns = ("name", "steps")
        self.flow_tree = ttk.Treeview(left, columns=flow_columns, show="headings", height=8)
        self.flow_tree.heading("name", text="流程名称")
        self.flow_tree.heading("steps", text="步骤列表")
        self.flow_tree.column("name", width=140, anchor="center")
        self.flow_tree.column("steps", width=520, anchor="w")
        self.flow_tree.grid(row=4, column=0, sticky="nsew")
        self.flow_tree.bind("<<TreeviewSelect>>", self._on_flow_tree_select)

        ttk.Label(right, text="新增/编辑 VR 参数模板", font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        fields = [
            ("查询键", self.query_key_var),
            ("记录编号", self.function_id_var),
            ("记录类型", self.function_name_var),
            ("格式", self.data_format_var),
            ("VR501", self.x_var),
            ("VR502", self.y_var),
            ("VR503", self.z_var),
            ("VR504", self.rx_var),
            ("VR505", self.ry_var),
            ("VR506", self.rz_var),
            ("VR507", self.speed_var),
        ]
        for index, (label, variable) in enumerate(fields, start=1):
            ttk.Label(right, text=f"{label}:").grid(row=index, column=0, sticky="w", pady=4, padx=(0, 8))
            entry = ttk.Entry(right, textvariable=variable)
            if label in {"函数", "格式"}:
                entry.configure(state="readonly")
            entry.grid(row=index, column=1, sticky="ew", pady=4)

        action_row = ttk.Frame(right)
        action_row.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        action_row.columnconfigure((0, 1), weight=1)
        ttk.Button(action_row, text="保存到 JSON", command=self._save_record).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(action_row, text="删除当前记录", command=self._delete_record).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        ttk.Label(
            right,
            text="说明：当前正式VR协议下，VR500固定为触发位；本页只维护VR501-VR507参数模板。GUI 不修改原始 CSV；首次启动会从 CSV 导入一份 JSON，后续只保存到 JSON。",
            wraplength=320,
            foreground="#666666",
        ).grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(12, 0))

        flow_editor = ttk.LabelFrame(right, text="流程定义", padding=12)
        flow_editor.grid(row=len(fields) + 3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        flow_editor.columnconfigure(1, weight=1)
        ttk.Label(flow_editor, text="流程名称:").grid(row=0, column=0, sticky="w")
        ttk.Entry(flow_editor, textvariable=self.flow_name_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(flow_editor, text="步骤列表:").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        ttk.Entry(flow_editor, textvariable=self.flow_steps_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(flow_editor, text="用英文逗号分隔，例如：归零,home,位置A,抓取", foreground="#666666").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        flow_actions = ttk.Frame(flow_editor)
        flow_actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(flow_actions, text="保存流程", command=self._save_flow).pack(side="left")
        ttk.Button(flow_actions, text="删除流程", command=self._delete_flow).pack(side="left", padx=(8, 0))

    def _refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for key in sorted(self.table):
            record = self.table[key]
            self.tree.insert(
                "",
                "end",
                iid=record.query_key,
                values=(
                    record.query_key,
                    record.function_id,
                    record.function_name,
                    record.data_format,
                    *record.registers,
                ),
            )

    def _reload_from_json(self) -> None:
        self.table = load_query_table(self.json_path)
        self._refresh_table()
        self.service = RobotModbusService(self.json_path, flows_path=self.flows_path)
        self._refresh_flow_list()
        self.status_var.set(f"已从 JSON 重新加载: {self.json_path}")

    def _reset_form(self) -> None:
        self.query_key_var.set("")
        self.function_id_var.set("")
        self.function_name_var.set("movabs")
        self.data_format_var.set("IEE")
        for variable in (self.x_var, self.y_var, self.z_var, self.rx_var, self.ry_var, self.rz_var):
            variable.set("0")
        self.speed_var.set("10")

    def _on_select(self, _event: object) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        key = selected[0]
        record = self.table[key]
        self.query_key_var.set(record.query_key)
        self.function_id_var.set(str(record.function_id))
        self.function_name_var.set(record.function_name)
        self.data_format_var.set(record.data_format)
        self.x_var.set(self._fmt(record.registers[0]))
        self.y_var.set(self._fmt(record.registers[1]))
        self.z_var.set(self._fmt(record.registers[2]))
        self.rx_var.set(self._fmt(record.registers[3]))
        self.ry_var.set(self._fmt(record.registers[4]))
        self.rz_var.set(self._fmt(record.registers[5]))
        self.speed_var.set(self._fmt(record.registers[6]))

    def _save_record(self) -> None:
        try:
            record = QueryRecord(
                query_key=self.query_key_var.get().strip(),
                function_id=int(self.function_id_var.get().strip()),
                function_name=self.function_name_var.get().strip() or "movabs",
                data_format=self.data_format_var.get().strip() or "IEE",
                registers=(
                    self._parse_float(self.x_var.get()),
                    self._parse_float(self.y_var.get()),
                    self._parse_float(self.z_var.get()),
                    self._parse_float(self.rx_var.get()),
                    self._parse_float(self.ry_var.get()),
                    self._parse_float(self.rz_var.get()),
                    self._parse_float(self.speed_var.get()),
                ),
            )
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))
            return

        if not record.query_key:
            messagebox.showerror("输入错误", "查询键不能为空。")
            return

        self.table[record.query_key] = record
        self._save_json()
        self._refresh_table()
        self.tree.selection_set(record.query_key)
        self.status_var.set(f"已保存记录: {record.query_key}")

    def _delete_record(self) -> None:
        key = self.query_key_var.get().strip()
        if not key:
            messagebox.showwarning("无法删除", "当前没有选中的记录。")
            return
        if key not in self.table:
            messagebox.showwarning("无法删除", f"记录不存在: {key}")
            return
        del self.table[key]
        self._save_json()
        self._refresh_table()
        self._reset_form()
        self.status_var.set(f"已删除记录: {key}")

    def _save_json(self) -> None:
        save_query_table_json(self.json_path, self.table)
        self.service = RobotModbusService(self.json_path, flows_path=self.flows_path)
        self._refresh_flow_list()

    def _parse_float(self, value: str) -> float:
        normalized = value.strip().replace("%", "")
        return float(normalized)

    def _fmt(self, value: float) -> str:
        return str(int(value)) if float(value).is_integer() else str(value)

    def _run_connection_check(self) -> None:
        if not self._current_host():
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return
        self.connection_var.set("检测中...")

        def job() -> None:
            try:
                result = self._check_controller_connection()
            except Exception as exc:
                result = {"ok": False, "message": f"检测异常: {exc}"}
            self.root.after(0, lambda: self._finish_connection_check(result))

        threading.Thread(target=job, daemon=True).start()

    def _check_controller_connection(self) -> dict[str, str | bool]:
        try:
            client = ZMotionVrClient(host=self._current_host(), repo_root=self.resource_root)
        except Exception as exc:
            return {"ok": False, "message": f"SDK加载失败: {exc}"}
        try:
            client.connect()
            return {"ok": True, "message": f"连接成功: {self._current_host()}"}
        except ZMotionClientError as exc:
            return {"ok": False, "message": f"连接失败: {exc}"}
        except Exception as exc:
            return {"ok": False, "message": f"连接异常: {exc}"}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _finish_connection_check(self, result: dict[str, str | bool]) -> None:
        self.connection_var.set(str(result["message"]))

    def _preview_command(self) -> None:
        text = self.command_input_var.get().strip()
        if not text:
            messagebox.showwarning("输入为空", "请输入一条命令。")
            return
        try:
            parsed, record, command = self.service.build_fixed_command(text)
        except Exception as exc:
            self.send_status_var.set(f"解析失败: {exc}")
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", str(exc))
            return

        payload = self._build_preview_payload(parsed.raw_text, parsed.query_key, record, command)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        self.send_status_var.set(f"已解析命令: {parsed.query_key}")

    def _preview_flow(self) -> None:
        flow_name = self.flow_name_var.get().strip()
        if not flow_name:
            messagebox.showwarning("未选择流程", "请先选择一个流程。")
            return
        try:
            steps = self.service.build_flow_requests(flow_name)
        except Exception as exc:
            self.flow_status_var.set(f"流程预览失败: {exc}")
            self.flow_preview_text.delete("1.0", tk.END)
            self.flow_preview_text.insert("1.0", str(exc))
            return

        payload = {
            "flow_name": flow_name,
            "steps": [
                {
                    "step": step_name,
                    "function_id": record.function_id,
                    "note": "当前VR协议下，函数序号仅用于上位机记录，未写入VR500；VR500固定写1.0触发",
                    "vr_command": command.preview_dict(),
                }
                for step_name, record, command in steps
            ],
        }
        self.flow_preview_text.delete("1.0", tk.END)
        self.flow_preview_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        self.flow_status_var.set(f"已预览流程: {flow_name}")

    def _send_command(self) -> None:
        text = self.command_input_var.get().strip()
        if not text:
            messagebox.showwarning("输入为空", "请输入一条命令。")
            return
        if not self._current_host():
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return

        self.send_status_var.set("发送中...")

        def job() -> None:
            result = self._execute_send(text)
            self.root.after(0, lambda: self._finish_send(result))

        threading.Thread(target=job, daemon=True).start()

    def _execute_send(self, text: str) -> dict:
        try:
            parsed, record, command = self.service.build_fixed_command(text)
        except Exception as exc:
            return {"ok": False, "error": f"解析失败: {exc}"}

        client = ZMotionVrClient(host=self._current_host(), repo_root=self.resource_root)
        try:
            client.connect()
            client.write_vr(VrWriteRequest(start_vr=command.payload_start_vr, values=command.payload_values))
            client.write_vr(VrWriteRequest(start_vr=command.trigger_vr, values=(command.trigger_value,)))
            status_values = client.read_vr(self.service.build_status_read())
            return {
                "ok": True,
                "message": f"发送成功: {parsed.query_key}",
                "payload": self._build_preview_payload(parsed.raw_text, parsed.query_key, record, command, status_values),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"发送失败: {exc}",
                "payload": self._build_preview_payload(parsed.raw_text, parsed.query_key, record, command),
            }
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _finish_send(self, result: dict) -> None:
        payload = result.get("payload")
        if payload is not None:
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))

        if result.get("ok"):
            self.send_status_var.set(str(result["message"]))
            self.status_var.set(str(result["message"]))
        else:
            self.send_status_var.set(str(result["error"]))
            self.status_var.set(str(result["error"]))
            messagebox.showerror("发送失败", str(result["error"]))

    def _send_flow(self) -> None:
        flow_name = self.flow_name_var.get().strip()
        if not flow_name:
            messagebox.showwarning("未选择流程", "请先选择一个流程。")
            return
        if not self._current_host():
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return
        self.flow_status_var.set("流程发送中...")

        def job() -> None:
            result = self._execute_flow_send(flow_name)
            self.root.after(0, lambda: self._finish_flow_send(result))

        threading.Thread(target=job, daemon=True).start()

    def _execute_flow_send(self, flow_name: str) -> dict:
        try:
            steps = self.service.build_flow_requests(flow_name)
        except Exception as exc:
            return {"ok": False, "error": f"流程解析失败: {exc}"}

        client = ZMotionVrClient(host=self._current_host(), repo_root=self.resource_root)
        try:
            client.connect()
            sent_steps = []
            for step_name, record, command in steps:
                client.write_vr(VrWriteRequest(start_vr=command.payload_start_vr, values=command.payload_values))
                client.write_vr(VrWriteRequest(start_vr=command.trigger_vr, values=(command.trigger_value,)))
                sent_steps.append(
                    {
                        "step": step_name,
                        "function_id": record.function_id,
                        "note": "VR500固定写1.0触发；参数写入VR501-VR507",
                        "vr_command": command.preview_dict(),
                    }
                )
            status_values = client.read_vr(self.service.build_status_read())
            return {"ok": True, "message": f"流程发送成功: {flow_name}", "payload": {"flow_name": flow_name, "steps": sent_steps, "status_block": status_values}}
        except Exception as exc:
            return {"ok": False, "error": f"流程发送失败: {exc}"}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _finish_flow_send(self, result: dict) -> None:
        payload = result.get("payload")
        if payload is not None:
            self.flow_preview_text.delete("1.0", tk.END)
            self.flow_preview_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        if result.get("ok"):
            self.flow_status_var.set(str(result["message"]))
            self.status_var.set(str(result["message"]))
        else:
            self.flow_status_var.set(str(result["error"]))
            self.status_var.set(str(result["error"]))
            messagebox.showerror("流程发送失败", str(result["error"]))

    def _build_preview_payload(
        self,
        raw_text: str,
        query_key: str,
        record: QueryRecord,
        command: FixedVrCommand,
        status_values: list[float] | None = None,
    ) -> dict:
        payload = {
            "text": raw_text,
            "query_key": query_key,
            "function_id": record.function_id,
            "function_name": record.function_name,
            "data_format": record.data_format,
            "registers": list(record.registers),
            "note": "当前正式VR协议下，VR500固定为触发位1.0；本次发送只把7个参数写入VR501-VR507",
            "vr_command": command.preview_dict(),
        }
        if status_values is not None:
            payload["vr_status"] = {
                "start_vr": self.service.status_vr_start,
                "values": status_values,
            }
        return payload

    def _refresh_flow_list(self) -> None:
        names = self.service.list_flow_names()
        self.flow_combo["values"] = names
        if names and self.flow_name_var.get() not in names:
            self.flow_name_var.set(names[0])
        if hasattr(self, "flow_tree"):
            for item in self.flow_tree.get_children():
                self.flow_tree.delete(item)
            for name in names:
                flow = self.service.get_flow(name)
                self.flow_tree.insert("", "end", iid=name, values=(flow.name, " -> ".join(flow.steps)))

    def _save_flow(self) -> None:
        name = self.flow_name_var.get().strip()
        steps = tuple(item.strip() for item in self.flow_steps_var.get().split(",") if item.strip())
        if not name:
            messagebox.showerror("输入错误", "流程名称不能为空。")
            return
        if not steps:
            messagebox.showerror("输入错误", "流程步骤不能为空。")
            return
        missing = [step for step in steps if step not in self.table]
        if missing:
            messagebox.showerror("输入错误", f"以下步骤不存在于查询表中: {', '.join(missing)}")
            return
        self.service.save_flow(FlowDefinition(name=name, steps=steps))
        self._refresh_flow_list()
        self.status_var.set(f"已保存流程: {name}")
        self.flow_status_var.set(f"已保存流程: {name}")

    def _delete_flow(self) -> None:
        name = self.flow_name_var.get().strip()
        if not name:
            messagebox.showwarning("无法删除", "请先选择一个流程。")
            return
        self.service.delete_flow(name)
        self._refresh_flow_list()
        self.flow_steps_var.set("")
        self.status_var.set(f"已删除流程: {name}")
        self.flow_status_var.set(f"已删除流程: {name}")

    def _on_flow_select(self, _event: object) -> None:
        name = self.flow_name_var.get().strip()
        if not name:
            return
        try:
            flow = self.service.get_flow(name)
        except Exception:
            return
        self.flow_steps_var.set(",".join(flow.steps))

    def _on_flow_tree_select(self, _event: object) -> None:
        selected = self.flow_tree.selection()
        if not selected:
            return
        name = selected[0]
        self.flow_name_var.set(name)
        try:
            flow = self.service.get_flow(name)
        except Exception:
            return
        self.flow_steps_var.set(",".join(flow.steps))

    def _current_host(self) -> str:
        return self.host_var.get().strip()

    def _read_status_block(self) -> None:
        if not self._current_host():
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return
        self.send_status_var.set("读取反馈区中...")

        def job() -> None:
            result = self._execute_read_status()
            self.root.after(0, lambda: self._finish_status_read(result))

        threading.Thread(target=job, daemon=True).start()

    def _execute_read_status(self) -> dict:
        client = ZMotionVrClient(host=self._current_host(), repo_root=self.resource_root)
        try:
            client.connect()
            request = self.service.build_status_read()
            values = client.read_vr(request)
            return {"ok": True, "payload": {"start_vr": request.start_vr, "values": values}}
        except Exception as exc:
            return {"ok": False, "error": f"读取反馈区失败: {exc}"}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _finish_status_read(self, result: dict) -> None:
        if result.get("ok"):
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", json.dumps({"vr_status": result["payload"]}, ensure_ascii=False, indent=2))
            self.send_status_var.set("读取反馈区成功")
        else:
            self.send_status_var.set(str(result["error"]))
            messagebox.showerror("读取失败", str(result["error"]))


def _runtime_dir() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resource_dir() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def main() -> None:
    root = tk.Tk()
    runtime_base = _runtime_dir()
    resource_base = _resource_dir()
    data_dir = runtime_base / "data"
    json_path = data_dir / "query_table.json"
    csv_path = resource_base / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    app = QueryTableEditorApp(
        root,
        json_path=json_path,
        csv_path=csv_path,
    )
    root.mainloop()
