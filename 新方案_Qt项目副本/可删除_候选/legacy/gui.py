from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from .models import QueryRecord, VrWriteRequest
from .query_table import bootstrap_query_table_json, load_query_table, save_query_table_json
from .service import RobotModbusService
from .zmotion_client import ZMotionVrClient


COMMAND_TYPES = ["MOVE_ABS", "MOVE_SAFE", "LATHE_DOOR_OPEN", "LATHE_DOOR_CLOSE", "FIXED_FUNC"]


class QueryTableEditorApp:
    def __init__(self, root: tk.Tk, *, json_path: Path, csv_path: Path) -> None:
        self.root = root
        self.root.title("双车床机械手自然语言编程控制系统")
        self.root.geometry("1280x760")
        self.root.minsize(1180, 700)

        self.runtime_root = _runtime_dir()
        self.resource_root = _resource_dir()
        self.json_path = bootstrap_query_table_json(json_path, csv_path)
        self.table = load_query_table(self.json_path)
        self.service = RobotModbusService(self.json_path)
        self.history: list[dict[str, str | int]] = []
        self.task_id = 1001

        self.host_var = tk.StringVar(value="192.168.1.11")
        self.connection_var = tk.StringVar(value="检测中...")
        self.status_var = tk.StringVar(value=f"第一版已就绪 | 数据源: {self.json_path}")
        self.mode_var = tk.StringVar(value="自动")
        self.busy_var = tk.StringVar(value="空闲")
        self.result_var = tk.StringVar(value="0")
        self.alarm_code_var = tk.StringVar(value="ERR_000")
        self.alarm_text_var = tk.StringVar(value="系统正常")
        self.task_var = tk.StringVar(value=str(self.task_id))
        self.robot_x_var = tk.StringVar(value="1250.0")
        self.robot_y_var = tk.StringVar(value="-22.0")
        self.robot_z_var = tk.StringVar(value="1488.0")
        self.robot_r_var = tk.StringVar(value="0 / 0 / 90")
        self.robot_speed_var = tk.StringVar(value="30% / 40%")

        self.query_key_var = tk.StringVar()
        self.function_id_var = tk.StringVar(value="1001")
        self.function_name_var = tk.StringVar(value="MOVE_ABS")
        self.template_type_var = tk.StringVar(value="parametric")
        self.keywords_var = tk.StringVar()
        self.description_var = tk.StringVar()
        self.pos_id_var = tk.StringVar(value="0")
        self.device_id_var = tk.StringVar(value="1")
        self.x_var = tk.StringVar(value="0")
        self.y_var = tk.StringVar(value="0")
        self.z_var = tk.StringVar(value="0")
        self.rx_var = tk.StringVar(value="0")
        self.ry_var = tk.StringVar(value="0")
        self.rz_var = tk.StringVar(value="0")
        self.speed_var = tk.StringVar(value="30")
        self.acc_var = tk.StringVar(value="40")
        self.safety_var = tk.StringVar(value="5")

        self.current_name_var = tk.StringVar(value="-")
        self.current_code_var = tk.StringVar(value="-")
        self.current_cmd_var = tk.StringVar(value="-")
        self.current_type_var = tk.StringVar(value="-")

        self.command_parent: ttk.Frame | None = None
        self.history_tree: ttk.Treeview | None = None
        self.template_tree: ttk.Treeview | None = None
        self.preview_text: scrolledtext.ScrolledText | None = None

        self._build_ui()
        self._load_first_record()
        self._refresh_all()
        self.root.after(200, self._run_connection_check)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        run_tab = ttk.Frame(notebook, padding=10)
        run_tab.columnconfigure(0, weight=1)
        run_tab.columnconfigure(1, weight=0)
        run_tab.rowconfigure(1, weight=1)
        notebook.add(run_tab, text="运行")

        manage_tab = ttk.Frame(notebook, padding=10)
        manage_tab.columnconfigure(0, weight=1)
        manage_tab.rowconfigure(1, weight=1)
        notebook.add(manage_tab, text="后台")

        self._build_run_tab(run_tab)
        self._build_manage_tab(manage_tab)

        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").grid(
            row=1, column=0, sticky="ew", padx=8, pady=(0, 8)
        )

        for var in [
            self.query_key_var, self.function_id_var, self.function_name_var, self.template_type_var,
            self.keywords_var, self.description_var, self.pos_id_var, self.device_id_var, self.x_var, self.y_var,
            self.z_var, self.rx_var, self.ry_var, self.rz_var, self.speed_var, self.acc_var, self.safety_var,
        ]:
            var.trace_add("write", lambda *_: self._render_preview())

    def _build_run_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for i in range(6):
            top.columnconfigure(i, weight=1 if i in {1, 3} else 0)
        ttk.Label(top, text="控制器地址:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.host_var, width=20).grid(row=0, column=1, sticky="w", padx=(6, 12))
        ttk.Label(top, text="连接状态:").grid(row=0, column=2, sticky="w")
        ttk.Label(top, textvariable=self.connection_var).grid(row=0, column=3, sticky="w", padx=(6, 12))
        ttk.Button(top, text="检测连接", command=self._run_connection_check).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(top, text="读取反馈", command=self._read_feedback).grid(row=0, column=5)

        left = ttk.Frame(parent)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)

        self.command_parent = ttk.LabelFrame(left, text="固定指令执行页", padding=8)
        self.command_parent.grid(row=0, column=0, sticky="ew")
        self.command_parent.columnconfigure((0, 1), weight=1)

        status_row = ttk.Frame(left)
        status_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        status_row.columnconfigure((0, 1, 2), weight=1)
        self._build_info(status_row, 0, "机械手状态", [
            ("X", self.robot_x_var), ("Y", self.robot_y_var), ("Z", self.robot_z_var),
            ("RX / RY / RZ", self.robot_r_var), ("速度 / 加速度", self.robot_speed_var),
        ])
        self._build_info(status_row, 1, "执行摘要", [
            ("当前模式", self.mode_var), ("忙闲状态", self.busy_var), ("执行结果", self.result_var),
            ("报警码", self.alarm_code_var), ("系统说明", self.alarm_text_var), ("当前任务ID", self.task_var),
        ])
        history = ttk.LabelFrame(status_row, text="最近执行记录", padding=8)
        history.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        history.columnconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(history, columns=("task", "code", "type", "result"), show="headings", height=8)
        for col, txt, width in [("task", "任务ID", 80), ("code", "指令码", 80), ("type", "指令类型", 140), ("result", "结果", 80)]:
            self.history_tree.heading(col, text=txt)
            self.history_tree.column(col, width=width, anchor="center")
        self.history_tree.grid(row=0, column=0, sticky="nsew")

        right = ttk.LabelFrame(parent, text="系统操作", padding=8)
        right.grid(row=1, column=1, sticky="ns")
        for idx, (text, cmd) in enumerate([
            ("上电", lambda: self._set_status("系统已上电")),
            ("启动", lambda: self._set_mode_busy("自动", True, "系统启动")),
            ("停机", lambda: self._set_mode_busy(self.mode_var.get(), False, "系统停机")),
            ("暂停", lambda: self._set_mode_busy(self.mode_var.get(), False, "当前任务已暂停")),
            ("继续", lambda: self._set_mode_busy(self.mode_var.get(), True, "当前任务继续运行")),
            ("急停", self._trigger_estop),
        ]):
            ttk.Button(right, text=text, command=cmd).grid(row=idx, column=0, sticky="ew", pady=4)

    def _build_manage_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure((0, 1, 2), weight=1)
        self._build_info(top, 0, "后台作用", [
            ("用途", "维护按钮与模板映射"), ("支持", "参数型指令"),
            ("支持", "固定函数型无参数"), ("示例", "5001 固定函数"),
        ], dynamic=False)
        self._build_info(top, 1, "当前选中模板", [
            ("显示名称", self.current_name_var), ("指令码", self.current_code_var),
            ("指令类型", self.current_cmd_var), ("模板分类", self.current_type_var),
        ])
        ops = ttk.LabelFrame(top, text="后台操作", padding=8)
        ops.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        for idx, (text, cmd) in enumerate([("新增", self._new_record), ("保存", self._save_record), ("另存为", self._clone_record), ("删除", self._delete_record)]):
            ttk.Button(ops, text=text, command=cmd).grid(row=idx, column=0, sticky="ew", pady=3)

        bottom = ttk.Frame(parent)
        bottom.grid(row=1, column=0, sticky="nsew")
        bottom.columnconfigure(1, weight=1)
        bottom.rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(bottom, text="指令模板列表", padding=8)
        list_frame.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        self.template_tree = ttk.Treeview(list_frame, columns=("name", "type"), show="headings", height=20)
        self.template_tree.heading("name", text="显示名称")
        self.template_tree.heading("type", text="模板分类")
        self.template_tree.column("name", width=190, anchor="w")
        self.template_tree.column("type", width=120, anchor="center")
        self.template_tree.grid(row=0, column=0, sticky="nsew")
        self.template_tree.bind("<<TreeviewSelect>>", self._on_select)

        editor = ttk.LabelFrame(bottom, text="工程师后台管理", padding=8)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        fields = [
            ("显示名称", self.query_key_var, "entry"), ("指令码", self.function_id_var, "entry"),
            ("指令类型", self.function_name_var, "combo"), ("模板分类", self.template_type_var, "typecombo"),
            ("自然语言关键词", self.keywords_var, "entry"), ("工位ID", self.pos_id_var, "entry"),
            ("X", self.x_var, "entry"), ("Y", self.y_var, "entry"),
            ("Z", self.z_var, "entry"), ("RX", self.rx_var, "entry"),
            ("RY", self.ry_var, "entry"), ("RZ", self.rz_var, "entry"),
            ("速度%", self.speed_var, "entry"), ("加速度%", self.acc_var, "entry"),
            ("设备ID", self.device_id_var, "entry"), ("安全等级", self.safety_var, "entry"),
        ]
        for idx, (label, var, kind) in enumerate(fields):
            row = idx // 2
            col = (idx % 2) * 2
            ttk.Label(editor, text=label + ":").grid(row=row, column=col, sticky="w", padx=(0, 6), pady=3)
            if kind == "combo":
                widget = ttk.Combobox(editor, textvariable=var, values=COMMAND_TYPES, state="readonly")
            elif kind == "typecombo":
                widget = ttk.Combobox(editor, textvariable=var, values=["parametric", "fixed"], state="readonly")
            else:
                widget = ttk.Entry(editor, textvariable=var)
            widget.grid(row=row, column=col + 1, sticky="ew", pady=3)

        desc_row = len(fields) // 2 + 1
        ttk.Label(editor, text="说明:").grid(row=desc_row, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(editor, textvariable=self.description_var).grid(row=desc_row, column=1, columnspan=3, sticky="ew", pady=3)

        preview = ttk.LabelFrame(editor, text="结构化 JSON 预览", padding=8)
        preview.grid(row=desc_row + 1, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        preview.columnconfigure(0, weight=1)
        self.preview_text = scrolledtext.ScrolledText(preview, wrap="word", font=("Consolas", 9), height=10)
        self.preview_text.grid(row=0, column=0, sticky="nsew")

    def _build_info(self, parent: ttk.Frame, column: int, title: str, rows: list[tuple[str, str | tk.StringVar]], dynamic: bool = True) -> None:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
        frame.columnconfigure(1, weight=1)
        for idx, (label, value) in enumerate(rows):
            ttk.Label(frame, text=label + ":").grid(row=idx, column=0, sticky="w", pady=2)
            if dynamic and isinstance(value, tk.StringVar):
                ttk.Label(frame, textvariable=value).grid(row=idx, column=1, sticky="e", pady=2)
            else:
                ttk.Label(frame, text=str(value)).grid(row=idx, column=1, sticky="e", pady=2)

    def _load_first_record(self) -> None:
        if self.table:
            self._load_record(next(iter(sorted(self.table.values(), key=lambda r: r.query_key))))

    def _load_record(self, record: QueryRecord) -> None:
        self.query_key_var.set(record.query_key)
        self.function_id_var.set(str(record.function_id))
        self.function_name_var.set(record.function_name)
        self.template_type_var.set(record.template_type)
        self.keywords_var.set(record.keywords)
        self.description_var.set(record.description)
        self.pos_id_var.set("0")
        self.device_id_var.set("1")
        self.x_var.set(self._fmt(record.registers[0]))
        self.y_var.set(self._fmt(record.registers[1]))
        self.z_var.set(self._fmt(record.registers[2]))
        self.rx_var.set(self._fmt(record.registers[3]))
        self.ry_var.set(self._fmt(record.registers[4]))
        self.rz_var.set(self._fmt(record.registers[5]))
        self.speed_var.set(self._fmt(record.registers[6]))
        self.acc_var.set("40")
        self.safety_var.set("5")
        self._render_preview()

    def _collect_record(self) -> QueryRecord:
        def num(value: str) -> float:
            text = value.strip().replace("%", "")
            return float(text) if text else 0.0

        return QueryRecord(
            query_key=self.query_key_var.get().strip(),
            function_id=int(float(self.function_id_var.get() or "0")),
            function_name=self.function_name_var.get().strip() or "MOVE_ABS",
            data_format="IEE",
            template_type=self.template_type_var.get().strip() or "parametric",
            keywords=self.keywords_var.get().strip(),
            description=self.description_var.get().strip(),
            registers=(
                num(self.x_var.get()), num(self.y_var.get()), num(self.z_var.get()),
                num(self.rx_var.get()), num(self.ry_var.get()), num(self.rz_var.get()),
                num(self.speed_var.get()),
            ),
        )

    def _render_preview(self) -> None:
        if self.preview_text is None:
            return
        record = self._collect_record()
        payload = {
            "taskId": self.task_id,
            "code": record.function_id,
            "cmd": record.function_name,
            "templateType": record.template_type,
            "params": self._payload_params(record),
            "safetyLevel": int(float(self.safety_var.get() or "5")),
            "desc": record.description or record.query_key,
        }
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        self.current_name_var.set(record.query_key or "-")
        self.current_code_var.set(str(record.function_id) if record.query_key else "-")
        self.current_cmd_var.set(record.function_name or "-")
        self.current_type_var.set("固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令")

    def _payload_params(self, record: QueryRecord) -> dict[str, float | int]:
        base = {"deviceId": int(float(self.device_id_var.get() or "0")), "posId": int(float(self.pos_id_var.get() or "0"))}
        if record.template_type == "fixed":
            return base
        return {
            **base,
            "x": record.registers[0], "y": record.registers[1], "z": record.registers[2],
            "rx": record.registers[3], "ry": record.registers[4], "rz": record.registers[5],
            "speedPercent": record.registers[6], "accPercent": float(self.acc_var.get() or "0"),
        }

    def _refresh_all(self) -> None:
        self._refresh_command_cards()
        self._refresh_template_tree()
        self._refresh_history()
        self._render_preview()

    def _refresh_command_cards(self) -> None:
        if self.command_parent is None:
            return
        for child in self.command_parent.winfo_children():
            child.destroy()
        for idx, record in enumerate(list(sorted(self.table.values(), key=lambda r: r.query_key))[:6]):
            frame = ttk.LabelFrame(self.command_parent, text=record.query_key, padding=8)
            row, col = divmod(idx, 2)
            frame.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            self.command_parent.rowconfigure(row, weight=1)
            ttk.Label(frame, text=f"指令码: {record.function_id}").grid(row=0, column=0, sticky="w")
            ttk.Label(frame, text=f"类型: {record.function_name}").grid(row=1, column=0, sticky="w")
            ttk.Label(frame, text="固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令").grid(row=2, column=0, sticky="w")
            ttk.Label(frame, text=record.description or record.query_key, wraplength=240).grid(row=3, column=0, sticky="w", pady=(3, 5))
            ttk.Button(frame, text="执行", command=lambda key=record.query_key: self._send_record(key)).grid(row=4, column=0, sticky="ew")

    def _refresh_template_tree(self) -> None:
        if self.template_tree is None:
            return
        for item in self.template_tree.get_children():
            self.template_tree.delete(item)
        for record in sorted(self.table.values(), key=lambda r: r.query_key):
            kind = "固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令"
            self.template_tree.insert("", "end", iid=record.query_key, values=(record.query_key, kind))

    def _refresh_history(self) -> None:
        if self.history_tree is None:
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        rows = self.history or [{"task": 1001, "code": 1001, "type": "参数型指令", "result": "待执行"}]
        for idx, item in enumerate(rows[:8]):
            self.history_tree.insert("", "end", iid=str(idx), values=(item["task"], item["code"], item["type"], item["result"]))

    def _new_record(self) -> None:
        self.query_key_var.set("")
        self.function_id_var.set("1001")
        self.function_name_var.set("MOVE_ABS")
        self.template_type_var.set("parametric")
        self.keywords_var.set("")
        self.description_var.set("")
        self.pos_id_var.set("0")
        self.device_id_var.set("1")
        self.x_var.set("0")
        self.y_var.set("0")
        self.z_var.set("0")
        self.rx_var.set("0")
        self.ry_var.set("0")
        self.rz_var.set("0")
        self.speed_var.set("30")
        self.acc_var.set("40")
        self.safety_var.set("5")
        self.status_var.set("已创建空白模板。")

    def _save_record(self) -> None:
        record = self._collect_record()
        if not record.query_key:
            messagebox.showerror("输入错误", "显示名称不能为空。")
            return
        self.table[record.query_key] = record
        self._save_json()
        self._refresh_all()
        self.status_var.set(f"已保存模板: {record.query_key}")

    def _clone_record(self) -> None:
        record = self._collect_record()
        if not record.query_key:
            messagebox.showwarning("无法另存为", "请先填写显示名称。")
            return
        clone = QueryRecord(
            query_key=f"{record.query_key} - 副本",
            function_id=record.function_id,
            function_name=record.function_name,
            data_format=record.data_format,
            template_type=record.template_type,
            keywords=record.keywords,
            description=record.description,
            registers=record.registers,
        )
        self.table[clone.query_key] = clone
        self._save_json()
        self._load_record(clone)
        self._refresh_all()
        self.status_var.set(f"已另存模板: {clone.query_key}")

    def _delete_record(self) -> None:
        key = self.query_key_var.get().strip()
        if not key:
            messagebox.showwarning("无法删除", "当前没有选中的模板。")
            return
        if key not in self.table:
            messagebox.showwarning("无法删除", f"模板不存在: {key}")
            return
        del self.table[key]
        self._save_json()
        self._new_record()
        self._refresh_all()
        self.status_var.set(f"已删除模板: {key}")

    def _save_json(self) -> None:
        save_query_table_json(self.json_path, self.table)
        self.service = RobotModbusService(self.json_path)

    def _on_select(self, _event: object) -> None:
        if self.template_tree is None:
            return
        selected = self.template_tree.selection()
        if selected and selected[0] in self.table:
            self._load_record(self.table[selected[0]])

    def _run_connection_check(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return
        self.connection_var.set("检测中...")

        def job() -> None:
            result = self._check_connection(host)
            self.root.after(0, lambda: self.connection_var.set(result))

        threading.Thread(target=job, daemon=True).start()

    def _check_connection(self, host: str) -> str:
        try:
            client = ZMotionVrClient(host=host, repo_root=self.resource_root)
            client.connect()
            client.disconnect()
            return f"连接成功: {host}"
        except Exception as exc:
            return f"连接失败: {exc}"

    def _send_record(self, query_key: str) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return
        self.busy_var.set("发送中...")
        self.task_var.set(str(self.task_id))

        def job() -> None:
            result = self._execute_send(host, query_key)
            self.root.after(0, lambda: self._finish_send(result))

        threading.Thread(target=job, daemon=True).start()

    def _execute_send(self, host: str, query_key: str) -> dict[str, object]:
        record, command = self.service.build_fixed_command_from_key(query_key)
        client = ZMotionVrClient(host=host, repo_root=self.resource_root)
        try:
            client.connect()
            client.write_vr(VrWriteRequest(start_vr=command.payload_start_vr, values=command.payload_values))
            client.write_vr(VrWriteRequest(start_vr=command.trigger_vr, values=(command.trigger_value,)))
            return {"ok": True, "record": record}
        except Exception as exc:
            return {"ok": False, "record": record, "error": str(exc)}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _finish_send(self, result: dict[str, object]) -> None:
        record = result["record"]
        assert isinstance(record, QueryRecord)
        self.history.insert(0, {
            "task": self.task_id,
            "code": record.function_id,
            "type": "固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令",
            "result": "成功" if result.get("ok") else "失败",
        })
        if result.get("ok"):
            self.busy_var.set("运行中")
            self.result_var.set("0")
            self.alarm_code_var.set("ERR_000")
            self.alarm_text_var.set("系统正常")
            if record.template_type != "fixed":
                self.robot_x_var.set(self._fmt(record.registers[0]))
                self.robot_y_var.set(self._fmt(record.registers[1]))
                self.robot_z_var.set(self._fmt(record.registers[2]))
                self.robot_r_var.set(f"{self._fmt(record.registers[3])} / {self._fmt(record.registers[4])} / {self._fmt(record.registers[5])}")
                self.robot_speed_var.set(f"{self._fmt(record.registers[6])}% / {self.acc_var.get()}%")
            self.status_var.set(f"已执行: {record.query_key}")
            self.task_id += 1
            self.task_var.set(str(self.task_id))
        else:
            self.busy_var.set("空闲")
            self.result_var.set("9")
            self.alarm_code_var.set("ERR_SEND")
            self.alarm_text_var.set(str(result.get("error", "发送失败")))
            self.status_var.set(str(result.get("error", "发送失败")))
            messagebox.showerror("发送失败", str(result.get("error", "发送失败")))
        self._refresh_history()

    def _read_feedback(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning("地址为空", "请输入控制器地址。")
            return

        def job() -> None:
            client = ZMotionVrClient(host=host, repo_root=self.resource_root)
            try:
                client.connect()
                values = client.read_vr(self.service.build_status_read())
                result = f"反馈区读取成功: {values}"
            except Exception as exc:
                result = f"读取反馈区失败: {exc}"
            finally:
                try:
                    client.disconnect()
                except Exception:
                    pass
            self.root.after(0, lambda: self.status_var.set(result))

        threading.Thread(target=job, daemon=True).start()

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_mode_busy(self, mode: str, busy: bool, text: str) -> None:
        self.mode_var.set(mode)
        self.busy_var.set("运行中" if busy else "空闲")
        self.status_var.set(text)

    def _trigger_estop(self) -> None:
        self.busy_var.set("空闲")
        self.result_var.set("9")
        self.alarm_code_var.set("ERR_900")
        self.alarm_text_var.set("急停触发")
        self.status_var.set("急停触发，系统锁定")

    @staticmethod
    def _fmt(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


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
    QueryTableEditorApp(root, json_path=json_path, csv_path=csv_path)
    root.mainloop()
