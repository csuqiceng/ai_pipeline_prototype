from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QScrollArea,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QStackedWidget,
)

try:
    from mock_controller import MockZMotionVrClient
except ModuleNotFoundError:
    from ..mock_controller import MockZMotionVrClient
from .models import QueryRecord, VrWriteRequest
from .query_table import bootstrap_query_table_json, load_query_table, save_query_table_json
from .service import RobotModbusService
from .system_config import (
    AxisRangeConfig,
    ensure_system_config_json,
    load_system_config,
    save_system_config,
    validate_system_config,
)
from .zmotion_client import ZMotionVrClient


COMMAND_TYPES = ["MOVE_ABS", "MOVE_REL", "HOME", "GRIP_SET", "DOOR_CTRL", "WAIT_MS", "CHECK_IN", "EMG_RESET", "FIXED_FUNC"]
SYSTEM_COMMANDS = {
    "上电": ("power_on", "系统已上电"),
    "启动": ("auto_start", "系统启动"),
    "停机": ("auto_stop", "系统停机"),
    "暂停": ("sys_pause", "当前任务已暂停"),
    "继续": ("sys_resume", "当前任务继续运行"),
    "急停": ("sys_estop", "急停触发，系统锁定"),
}
SYSTEM_COMMAND_CODES = {
    "power_on": 4001,
    "auto_start": 6001,
    "auto_stop": 6002,
    "sys_pause": 4003,
    "sys_resume": 4004,
    "sys_estop": 4002,
}


class RobotQtWindow(QMainWindow):
    def __init__(
        self,
        *,
        json_path: Path,
        csv_path: Path,
        system_config_path: Path | None = None,
        client_factory: Callable[[str, Path], ZMotionVrClient] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("双车床机械手自然语言编程控制系统 - Qt版")
        self.resize(1380, 860)

        self.runtime_root = _runtime_dir()
        self.resource_root = _resource_dir()
        self.json_path = bootstrap_query_table_json(json_path, csv_path)
        self.system_config_path = ensure_system_config_json(system_config_path or (self.runtime_root / "data" / "system_config.json"))
        self.axis_ranges = load_system_config(self.system_config_path)
        self.table = load_query_table(self.json_path)
        self.service = RobotModbusService(self.json_path)
        self._client_factory = client_factory or (lambda host, repo_root: ZMotionVrClient(host=host, repo_root=repo_root))
        self.history: list[dict[str, str | int]] = []
        self.logs: list[dict[str, str]] = []
        self.task_id = 1001
        self.current_key: str | None = None
        self.robot_x = "1250.0"
        self.robot_y = "0.0"
        self.robot_z = "860.0"
        self.robot_r = "0.0 / 0.0 / 0.0"
        self.robot_speed = "30% / 40%"
        self.mode = "自动"
        self.busy = "空闲"
        self.result = "0"
        self.alarm_code = "ERR_000"
        self.alarm_text = "系统正常"
        self.io_status = "0"

        self._build_ui()
        self._load_initial_record()
        self._refresh_all()
        self._check_connection()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = self._build_header()
        root_layout.addWidget(header)

        main = QWidget()
        main_layout = QHBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        nav = self._build_nav()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(0)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_run_page())
        self.pages.addWidget(self._build_manage_page())
        self.pages.addWidget(self._build_log_page())
        content_layout.addWidget(self.pages)
        right_panel = self._build_system_panel()

        main_layout.addWidget(nav)
        main_layout.addWidget(content, 1)
        main_layout.addWidget(right_panel)
        root_layout.addWidget(main, 1)

        self.status_label = QLabel(f"第一版 Qt 页面已就绪 | 数据源: {self.json_path}")
        self.status_label.setObjectName("footerStatus")
        self.status_label.setMinimumHeight(34)
        root_layout.addWidget(self.status_label)

        self._apply_styles()

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("header")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 4, 12, 4)

        brand = QLabel("双车床机械手\nQt 页面原型")
        brand.setObjectName("brand")
        title = QLabel("双车床机械手自然语言编程控制系统")
        title.setObjectName("title")
        self.header_status = QLabel("第一版：固定指令 + 后台模板")
        self.header_status.setObjectName("headerStatus")

        layout.addWidget(brand)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(self.header_status)
        return frame

    def _build_nav(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("nav")
        frame.setFixedWidth(96)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.run_nav_btn = QPushButton("运行")
        self.run_nav_btn.setObjectName("navButton")
        self.run_nav_btn.clicked.connect(lambda: self._show_page(0))
        self.manage_nav_btn = QPushButton("后台")
        self.manage_nav_btn.setObjectName("navButton")
        self.manage_nav_btn.clicked.connect(lambda: self._show_page(1))
        self.log_nav_btn = QPushButton("日志")
        self.log_nav_btn.setObjectName("navButton")
        self.log_nav_btn.clicked.connect(lambda: self._show_page(2))

        layout.addWidget(self.run_nav_btn)
        layout.addWidget(self.manage_nav_btn)
        layout.addWidget(self.log_nav_btn)
        layout.addStretch(1)
        self._show_page(0)
        return frame

    def _build_system_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("rightPanel")
        panel.setFixedWidth(150)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        for text, key, klass in [
            ("上电", "power_on", ""),
            ("启动", "auto_start", "green"),
            ("停机", "auto_stop", ""),
            ("暂停", "sys_pause", "yellow"),
            ("继续", "sys_resume", ""),
            ("急停", "sys_estop", "red"),
        ]:
            btn = QPushButton(text)
            btn.setProperty("klass", klass)
            btn.clicked.connect(lambda _=False, k=key: self._handle_system_action(k))
            layout.addWidget(btn)
        layout.addStretch(1)
        return panel

    def _show_page(self, index: int) -> None:
        if hasattr(self, "pages"):
            self.pages.setCurrentIndex(index)
        active_style = "active"
        inactive_style = ""
        if hasattr(self, "run_nav_btn"):
            self.run_nav_btn.setProperty("state", active_style if index == 0 else inactive_style)
            self.manage_nav_btn.setProperty("state", active_style if index == 1 else inactive_style)
            self.log_nav_btn.setProperty("state", active_style if index == 2 else inactive_style)
            self.run_nav_btn.style().unpolish(self.run_nav_btn)
            self.run_nav_btn.style().polish(self.run_nav_btn)
            self.manage_nav_btn.style().unpolish(self.manage_nav_btn)
            self.manage_nav_btn.style().polish(self.manage_nav_btn)
            self.log_nav_btn.style().unpolish(self.log_nav_btn)
            self.log_nav_btn.style().polish(self.log_nav_btn)

    def _build_run_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        link_bar = QGroupBox("连接与反馈")
        link_bar.setObjectName("panel")
        link_layout = QHBoxLayout(link_bar)
        link_layout.setContentsMargins(10, 8, 10, 8)
        link_layout.addWidget(QLabel("控制器地址:"))
        self.host_edit = QLineEdit("192.168.1.11")
        self.host_edit.setMaximumWidth(220)
        link_layout.addWidget(self.host_edit)
        link_layout.addWidget(QLabel("控制器类型:"))
        self.controller_combo = QComboBox()
        self.controller_combo.addItems(["真实控制器", "模拟控制器"])
        self.controller_combo.setMaximumWidth(180)
        link_layout.addWidget(self.controller_combo)
        link_layout.addWidget(QLabel("发送协议:"))
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["当前简化协议", "最终标准协议"])
        self.protocol_combo.setCurrentText("最终标准协议")
        self.protocol_combo.setMaximumWidth(180)
        link_layout.addWidget(self.protocol_combo)
        link_layout.addWidget(QLabel("连接状态:"))
        self.connection_label = QLabel("检测中...")
        link_layout.addWidget(self.connection_label, 1)
        check_btn = QPushButton("检测连接")
        check_btn.clicked.connect(self._check_connection)
        read_btn = QPushButton("读取反馈")
        read_btn.clicked.connect(self._read_feedback)
        link_layout.addWidget(check_btn)
        link_layout.addWidget(read_btn)
        layout.addWidget(link_bar)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        self.command_group = QGroupBox("固定指令执行页")
        self.command_group.setObjectName("panel")
        self.command_group.setMinimumHeight(380)
        command_box_layout = QVBoxLayout(self.command_group)
        command_tip = QLabel("第一版只做少量固定按钮，不做复杂自然语言输入。支持参数型指令与固定函数型无参数指令。")
        command_tip.setWordWrap(True)
        command_tip.setObjectName("tip")
        command_box_layout.addWidget(command_tip)
        command_scroll = QScrollArea()
        command_scroll.setWidgetResizable(True)
        command_scroll.setFrameShape(QFrame.Shape.NoFrame)
        command_scroll_widget = QWidget()
        command_layout = QGridLayout(command_scroll_widget)
        command_layout.setSpacing(10)
        self.command_grid_layout = command_layout
        command_scroll.setWidget(command_scroll_widget)
        command_box_layout.addWidget(command_scroll)
        top_layout.addWidget(self.command_group, 11)

        self.summary_info = self._make_info_group("执行摘要")
        self.summary_info.setObjectName("panel")
        top_layout.addWidget(self.summary_info, 8)
        layout.addWidget(top)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        self.robot_info = self._make_info_group("机械手状态")
        self.robot_info.setObjectName("panel")
        self.boundary_info = self._make_static_group("第一版边界", [
            ("支持", "固定指令按钮"),
            ("支持", "后台模板管理"),
            ("支持", "5001 固定函数"),
            ("暂不做", "自由自然语言"),
            ("暂不做", "视觉联动"),
        ])
        self.boundary_info.setObjectName("panel")
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["任务ID", "指令码", "指令类型", "结果"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.history_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        history_group = QGroupBox("最近执行记录")
        history_group.setObjectName("panel")
        hist_layout = QVBoxLayout(history_group)
        hist_layout.addWidget(self.history_table)

        left_stack = QWidget()
        left_stack_layout = QVBoxLayout(left_stack)
        left_stack_layout.setContentsMargins(0, 0, 0, 0)
        left_stack_layout.setSpacing(10)
        left_stack_layout.addWidget(self.robot_info, 1)
        left_stack_layout.addWidget(self.boundary_info, 1)

        bottom_layout.addWidget(left_stack, 1)
        bottom_layout.addWidget(history_group, 1)
        layout.addWidget(bottom, 0)
        return page

    def _build_manage_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        self.backend_info = self._make_static_group("后台作用", [
            ("用途", "维护按钮与模板映射"),
            ("支持", "参数型指令"),
            ("支持", "固定函数型无参数"),
            ("示例", "5001 固定函数"),
        ])
        self.backend_info.setObjectName("panel")
        self.current_info = self._make_info_group("当前选中模板")
        self.current_info.setObjectName("panel")
        action_box = QGroupBox("后台操作")
        action_box.setObjectName("panel")
        action_layout = QVBoxLayout(action_box)
        for text, fn in [
            ("新增", self._new_record),
            ("保存", self._save_record),
            ("另存为", self._clone_record),
            ("删除", self._delete_record),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            action_layout.addWidget(btn)
        action_layout.addStretch(1)
        top_layout.addWidget(self.backend_info)
        top_layout.addWidget(self.current_info)
        top_layout.addWidget(action_box)
        layout.addWidget(top)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)

        left = QGroupBox("指令模板列表")
        left.setObjectName("panel")
        left_layout = QVBoxLayout(left)
        tip = QLabel("维护“按钮显示名 -> 指令码 -> 参数模板”的对应关系。")
        tip.setWordWrap(True)
        tip.setObjectName("tip")
        left_layout.addWidget(tip)
        self.template_tree = QTreeWidget()
        self.template_tree.setHeaderLabels(["显示名称", "模板分类"])
        self.template_tree.itemSelectionChanged.connect(self._on_template_selected)
        left_layout.addWidget(self.template_tree)

        middle = QGroupBox("工程师后台管理")
        middle.setObjectName("panel")
        middle_layout = QVBoxLayout(middle)
        form_widget = QWidget()
        form = QFormLayout(form_widget)

        self.name_edit = QLineEdit()
        self.code_edit = QLineEdit()
        self.cmd_combo = QComboBox()
        self.cmd_combo.addItems(COMMAND_TYPES)
        self.template_type_combo = QComboBox()
        self.template_type_combo.addItems(["parametric", "fixed"])
        self.keywords_edit = QLineEdit()
        self.pos_id_edit = QLineEdit("0")
        self.device_id_edit = QLineEdit("1")
        self.x_edit = QLineEdit("0")
        self.y_edit = QLineEdit("0")
        self.z_edit = QLineEdit("0")
        self.rx_edit = QLineEdit("0")
        self.ry_edit = QLineEdit("0")
        self.rz_edit = QLineEdit("0")
        self.speed_edit = QLineEdit("30")
        self.acc_edit = QLineEdit("40")
        self.io_grip_edit = QLineEdit("0")
        self.io_door_edit = QLineEdit("0")
        self.ext_p1_edit = QLineEdit("0")
        self.ext_p2_edit = QLineEdit("0")
        self.safety_edit = QLineEdit("5")
        self.desc_edit = QLineEdit()
        self.param_widgets = [
            self.x_edit,
            self.y_edit,
            self.z_edit,
            self.rx_edit,
            self.ry_edit,
            self.rz_edit,
            self.speed_edit,
            self.acc_edit,
        ]

        for label, widget in [
            ("显示名称", self.name_edit),
            ("指令码", self.code_edit),
            ("指令类型", self.cmd_combo),
            ("模板分类", self.template_type_combo),
            ("自然语言关键词", self.keywords_edit),
            ("工位ID", self.pos_id_edit),
            ("设备ID", self.device_id_edit),
            ("X", self.x_edit),
            ("Y", self.y_edit),
            ("Z", self.z_edit),
            ("RX", self.rx_edit),
            ("RY", self.ry_edit),
            ("RZ", self.rz_edit),
            ("速度%", self.speed_edit),
            ("加速度%", self.acc_edit),
            ("夹爪动作", self.io_grip_edit),
            ("机床门动作", self.io_door_edit),
            ("扩展参数1", self.ext_p1_edit),
            ("扩展参数2", self.ext_p2_edit),
            ("安全等级", self.safety_edit),
            ("说明", self.desc_edit),
        ]:
            form.addRow(label + ":", widget)

        middle_layout.addWidget(form_widget)

        preview_group = QGroupBox("结构化 JSON 预览")
        preview_group.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_edit = QTextEdit()
        self.preview_edit.setReadOnly(True)
        preview_layout.addWidget(self.preview_edit)

        config_group = QGroupBox("系统参数")
        config_group.setObjectName("subPanel")
        config_layout = QFormLayout(config_group)
        self.range_x_min_edit = QLineEdit()
        self.range_x_max_edit = QLineEdit()
        self.range_y_min_edit = QLineEdit()
        self.range_y_max_edit = QLineEdit()
        self.range_z_min_edit = QLineEdit()
        self.range_z_max_edit = QLineEdit()
        for label, widget in [
            ("X最小", self.range_x_min_edit),
            ("X最大", self.range_x_max_edit),
            ("Y最小", self.range_y_min_edit),
            ("Y最大", self.range_y_max_edit),
            ("Z最小", self.range_z_min_edit),
            ("Z最大", self.range_z_max_edit),
        ]:
            config_layout.addRow(label + ":", widget)
        config_buttons = QHBoxLayout()
        config_save_btn = QPushButton("保存范围")
        config_save_btn.clicked.connect(self._save_system_config)
        config_reload_btn = QPushButton("重载范围")
        config_reload_btn.clicked.connect(self._load_system_config_into_form)
        config_buttons.addWidget(config_save_btn)
        config_buttons.addWidget(config_reload_btn)
        config_layout.addRow(config_buttons)
        preview_layout.addWidget(config_group)

        bottom_layout.addWidget(left, 1)
        bottom_layout.addWidget(middle, 1)
        bottom_layout.addWidget(preview_group, 1)
        layout.addWidget(bottom, 1)

        for widget in [
            self.name_edit, self.code_edit, self.keywords_edit, self.pos_id_edit, self.device_id_edit,
            self.x_edit, self.y_edit, self.z_edit, self.rx_edit, self.ry_edit, self.rz_edit,
            self.speed_edit, self.acc_edit, self.io_grip_edit, self.io_door_edit,
            self.ext_p1_edit, self.ext_p2_edit, self.safety_edit, self.desc_edit,
        ]:
            widget.textChanged.connect(self._render_preview)
        self.cmd_combo.currentTextChanged.connect(self._render_preview)
        self.template_type_combo.currentTextChanged.connect(self._render_preview)
        self.template_type_combo.currentTextChanged.connect(self._sync_template_type_mode)
        self._load_system_config_into_form()

        return page

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        log_info = self._make_static_group("日志说明", [
            ("记录范围", "系统按钮 / 连接检测"),
            ("记录范围", "指令发送 / 反馈读取"),
            ("记录范围", "模板新增 / 保存 / 删除"),
        ])
        log_info.setObjectName("panel")

        log_summary = self._make_info_group("日志摘要")
        log_summary.setObjectName("panel")

        action_box = QGroupBox("日志操作")
        action_box.setObjectName("panel")
        action_layout = QVBoxLayout(action_box)
        refresh_btn = QPushButton("刷新日志")
        refresh_btn.clicked.connect(self._refresh_logs)
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self._clear_logs)
        action_layout.addWidget(refresh_btn)
        action_layout.addWidget(clear_btn)
        action_layout.addStretch(1)

        top_layout.addWidget(log_info)
        top_layout.addWidget(log_summary)
        top_layout.addWidget(action_box)
        layout.addWidget(top)

        log_group = QGroupBox("操作日志")
        log_group.setObjectName("panel")
        log_layout = QVBoxLayout(log_group)
        self.log_table = QTableWidget(0, 5)
        self.log_table.setHorizontalHeaderLabels(["时间", "分类", "操作", "结果", "详情"])
        self.log_table.horizontalHeader().setStretchLastSection(True)
        log_layout.addWidget(self.log_table)
        layout.addWidget(log_group, 1)
        return page

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QMainWindow { background: #25d9e0; }
            QWidget { font-size: 13px; color: #111; }
            QLabel { background: transparent; }
            #header {
                border-bottom: 2px solid #222;
                background: #25d9e0;
                min-height: 64px;
            }
            #nav {
                background: #cfcfcf;
                border-right: 2px solid #222;
            }
            QPushButton#navButton {
                min-height: 88px;
                border: 0;
                border-bottom: 2px solid #888;
                border-radius: 0;
                background: transparent;
                font-size: 22px;
                font-weight: 500;
                color: #111;
            }
            QPushButton#navButton[state="active"] {
                background: #46eef4;
                font-weight: 700;
            }
            #rightPanel {
                background: #25d9e0;
            }
            QGroupBox#panel, QGroupBox#subPanel {
                background: rgba(255,255,255,0.34);
                border: 2px solid #2d2d2d;
                border-radius: 6px;
                margin-top: 8px;
                font-weight: bold;
            }
            QGroupBox#subPanel {
                background: rgba(255,255,255,0.24);
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                min-height: 42px;
                background: #ececec;
                border: 2px solid #333;
                border-radius: 6px;
                font-size: 17px;
                font-weight: bold;
            }
            QPushButton:hover { background: #f8f8f8; }
            QPushButton[klass="green"] { background: #43e74f; }
            QPushButton[klass="yellow"] { background: #ffe46d; }
            QPushButton[klass="red"] { background: #ef5a5a; color: white; }
            QLineEdit, QComboBox, QTextEdit, QTreeWidget, QTableWidget {
                background: rgba(255,255,255,0.82);
                border: 1px solid #666;
            }
            QLabel#title { font-size: 28px; font-weight: bold; }
            QLabel#brand { font-size: 16px; font-weight: bold; }
            QLabel#headerStatus { font-size: 15px; font-weight: bold; }
            QLabel#tip {
                margin-bottom: 8px;
                padding: 7px 9px;
                font-size: 14px;
                border: 1px dashed #666;
                background: rgba(255,255,255,0.3);
            }
            QLabel#footerStatus {
                background: #25d9e0;
                border-top: 2px solid #222;
                padding: 6px 12px;
                font-size: 14px;
            }
            QTableWidget {
                background: rgba(255,255,255,0.22);
                gridline-color: #555;
            }
        """)

    def _make_info_group(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        layout = QFormLayout(group)
        if title == "机械手状态":
            self.robot_x_label = QLabel(self.robot_x)
            self.robot_y_label = QLabel(self.robot_y)
            self.robot_z_label = QLabel(self.robot_z)
            self.robot_r_label = QLabel(self.robot_r)
            self.robot_speed_label = QLabel(self.robot_speed)
            for label, widget in [
                ("X", self.robot_x_label), ("Y", self.robot_y_label), ("Z", self.robot_z_label),
                ("RX / RY / RZ", self.robot_r_label), ("速度 / 加速度", self.robot_speed_label),
            ]:
                layout.addRow(label + ":", widget)
        elif title == "执行摘要":
            self.mode_label = QLabel(self.mode)
            self.busy_label = QLabel(self.busy)
            self.result_label = QLabel(self.result)
            self.alarm_code_label = QLabel(self.alarm_code)
            self.alarm_text_label = QLabel(self.alarm_text)
            self.task_label = QLabel(str(self.task_id))
            self.io_status_label = QLabel(self.io_status)
            for label, widget in [
                ("当前模式", self.mode_label), ("忙闲状态", self.busy_label), ("执行结果", self.result_label),
                ("报警码", self.alarm_code_label), ("系统说明", self.alarm_text_label),
                ("IO状态位", self.io_status_label), ("当前任务ID", self.task_label),
            ]:
                layout.addRow(label + ":", widget)
        elif title == "当前选中模板":
            self.current_name_label = QLabel("-")
            self.current_code_label = QLabel("-")
            self.current_cmd_label = QLabel("-")
            self.current_type_label = QLabel("-")
            for label, widget in [
                ("显示名称", self.current_name_label), ("指令码", self.current_code_label),
                ("指令类型", self.current_cmd_label), ("模板分类", self.current_type_label),
            ]:
                layout.addRow(label + ":", widget)
        elif title == "日志摘要":
            self.log_count_label = QLabel("0")
            self.log_last_time_label = QLabel("-")
            for label, widget in [
                ("日志条数", self.log_count_label),
                ("最近时间", self.log_last_time_label),
            ]:
                layout.addRow(label + ":", widget)
        return group

    def _make_static_group(self, title: str, rows: list[tuple[str, str]]) -> QGroupBox:
        group = QGroupBox(title)
        layout = QFormLayout(group)
        for label, value in rows:
            layout.addRow(label + ":", QLabel(value))
        return group

    def _load_initial_record(self) -> None:
        if self.table:
            self.current_key = sorted(self.table)[0]
            self._load_record_into_form(self.table[self.current_key])

    def _load_system_config_into_form(self) -> None:
        self.axis_ranges = load_system_config(self.system_config_path)
        self.range_x_min_edit.setText(self._fmt(self.axis_ranges.x[0]))
        self.range_x_max_edit.setText(self._fmt(self.axis_ranges.x[1]))
        self.range_y_min_edit.setText(self._fmt(self.axis_ranges.y[0]))
        self.range_y_max_edit.setText(self._fmt(self.axis_ranges.y[1]))
        self.range_z_min_edit.setText(self._fmt(self.axis_ranges.z[0]))
        self.range_z_max_edit.setText(self._fmt(self.axis_ranges.z[1]))

    def _collect_system_config(self) -> AxisRangeConfig:
        def num(text: str) -> float:
            return float(text.strip()) if text.strip() else 0.0

        return AxisRangeConfig(
            x=(num(self.range_x_min_edit.text()), num(self.range_x_max_edit.text())),
            y=(num(self.range_y_min_edit.text()), num(self.range_y_max_edit.text())),
            z=(num(self.range_z_min_edit.text()), num(self.range_z_max_edit.text())),
        )

    def _save_system_config(self) -> None:
        try:
            config = self._collect_system_config()
        except ValueError:
            QMessageBox.warning(self, "保存失败", "系统范围必须是数字。")
            self._append_log("后台", "保存范围", "失败", "系统范围必须是数字")
            return
        validation_error = validate_system_config(config)
        if validation_error:
            QMessageBox.warning(self, "保存失败", validation_error)
            self._append_log("后台", "保存范围", "失败", validation_error)
            return
        save_system_config(self.system_config_path, config)
        self.axis_ranges = config
        self.status_label.setText(f"已保存系统范围配置: {self.system_config_path}")
        self._append_log("后台", "保存范围", "成功", json.dumps(config.to_dict(), ensure_ascii=False))

    def _load_record_into_form(self, record: QueryRecord) -> None:
        standard_command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
        self.name_edit.setText(record.query_key)
        self.code_edit.setText(str(standard_command.code))
        self.cmd_combo.setCurrentText(standard_command.cmd)
        self.template_type_combo.setCurrentText(record.template_type)
        self.keywords_edit.setText(record.keywords)
        self.pos_id_edit.setText(str(record.pos_id))
        self.device_id_edit.setText(str(record.device_id))
        self.x_edit.setText(self._fmt(record.registers[0]))
        self.y_edit.setText(self._fmt(record.registers[1]))
        self.z_edit.setText(self._fmt(record.registers[2]))
        self.rx_edit.setText(self._fmt(record.registers[3]))
        self.ry_edit.setText(self._fmt(record.registers[4]))
        self.rz_edit.setText(self._fmt(record.registers[5]))
        self.speed_edit.setText(self._fmt(record.registers[6]))
        self.acc_edit.setText(self._fmt(record.acc_percent))
        self.io_grip_edit.setText(str(record.io_grip))
        self.io_door_edit.setText(str(record.io_door))
        self.ext_p1_edit.setText(self._fmt(record.ext_p1))
        self.ext_p2_edit.setText(self._fmt(record.ext_p2))
        self.safety_edit.setText(str(record.safety_level))
        self.desc_edit.setText(record.description)
        self._sync_template_type_mode(record.template_type)
        self._update_current_template_info(record, standard_command.code, standard_command.cmd)
        self._render_preview()

    def _collect_record(self) -> QueryRecord:
        def num(text: str) -> float:
            text = text.strip().replace("%", "")
            return float(text) if text else 0.0
        return QueryRecord(
            query_key=self.name_edit.text().strip(),
            function_id=int(float(self.code_edit.text() or "0")),
            function_name=self.cmd_combo.currentText(),
            data_format="IEE",
            template_type=self.template_type_combo.currentText(),
            keywords=self.keywords_edit.text().strip(),
            description=self.desc_edit.text().strip(),
            pos_id=int(float(self.pos_id_edit.text() or "0")),
            device_id=int(float(self.device_id_edit.text() or "1")),
            acc_percent=num(self.acc_edit.text()),
            safety_level=int(float(self.safety_edit.text() or "5")),
            io_grip=int(float(self.io_grip_edit.text() or "0")),
            io_door=int(float(self.io_door_edit.text() or "0")),
            ext_p1=num(self.ext_p1_edit.text()),
            ext_p2=num(self.ext_p2_edit.text()),
            registers=(
                num(self.x_edit.text()), num(self.y_edit.text()), num(self.z_edit.text()),
                num(self.rx_edit.text()), num(self.ry_edit.text()), num(self.rz_edit.text()),
                num(self.speed_edit.text()),
            ),
        )

    def _render_preview(self) -> None:
        record = self._collect_record()
        standard_command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
        payload = standard_command.to_json_dict()
        payload["templateType"] = record.template_type
        payload["queryKey"] = record.query_key
        payload["keywords"] = record.keywords
        self.preview_edit.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        self._update_current_template_info(record, standard_command.code, standard_command.cmd)

    def _update_current_template_info(self, record: QueryRecord, code: int, cmd: str) -> None:
        self.current_name_label.setText(record.query_key or "-")
        self.current_code_label.setText(str(code) if record.query_key else "-")
        self.current_cmd_label.setText(cmd or "-")
        self.current_type_label.setText("固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令")

    def _refresh_all(self) -> None:
        self._refresh_command_cards()
        self._refresh_template_tree()
        self._refresh_history()
        self._render_preview()
        self._refresh_status_labels()
        self._refresh_logs()

    def _refresh_command_cards(self) -> None:
        while self.command_grid_layout.count():
            item = self.command_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for idx, record in enumerate(sorted(self.table.values(), key=lambda r: r.query_key)):
            standard_command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
            card = QGroupBox(record.query_key)
            layout = QVBoxLayout(card)
            layout.addWidget(QLabel(f"指令码: {standard_command.code}"))
            layout.addWidget(QLabel(f"指令类型: {standard_command.cmd}"))
            layout.addWidget(QLabel("固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令"))
            layout.addWidget(QLabel(record.description or record.query_key))
            btn = QPushButton("执行")
            btn.setProperty("klass", "yellow" if record.template_type == "fixed" else "green")
            btn.clicked.connect(lambda _=False, key=record.query_key: self._send_record(key))
            layout.addWidget(btn)
            self.command_grid_layout.addWidget(card, idx // 2, idx % 2)

    def _refresh_template_tree(self) -> None:
        self.template_tree.clear()
        for record in sorted(self.table.values(), key=lambda r: r.query_key):
            kind = "固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令"
            item = QTreeWidgetItem([record.query_key, kind])
            self.template_tree.addTopLevelItem(item)
            if self.current_key == record.query_key:
                self.template_tree.setCurrentItem(item)

    def _refresh_history(self) -> None:
        self.history_table.setRowCount(0)
        rows = self.history or [{"task": 1001, "code": 1001, "type": "参数型指令", "result": "待执行"}]
        for row_index, row in enumerate(rows):
            self.history_table.insertRow(row_index)
            for col_index, key in enumerate(["task", "code", "type", "result"]):
                self.history_table.setItem(row_index, col_index, QTableWidgetItem(str(row[key])))

    def _refresh_status_labels(self) -> None:
        self.robot_x_label.setText(self.robot_x)
        self.robot_y_label.setText(self.robot_y)
        self.robot_z_label.setText(self.robot_z)
        self.robot_r_label.setText(self.robot_r)
        self.robot_speed_label.setText(self.robot_speed)
        self.mode_label.setText(self.mode)
        self.busy_label.setText(self.busy)
        self.result_label.setText(self.result)
        self.alarm_code_label.setText(self.alarm_code)
        self.alarm_text_label.setText(self.alarm_text)
        self.io_status_label.setText(self.io_status)
        self.task_label.setText(str(self.task_id))
        self.header_status.setText("第一版：任务运行中" if self.busy == "运行中" else "第一版：固定指令 + 后台模板")

    def _refresh_logs(self) -> None:
        if not hasattr(self, "log_table"):
            return
        self.log_table.setRowCount(0)
        for row_index, row in enumerate(self.logs[:200]):
            self.log_table.insertRow(row_index)
            for col_index, key in enumerate(["time", "category", "action", "result", "detail"]):
                self.log_table.setItem(row_index, col_index, QTableWidgetItem(row.get(key, "")))
        self.log_count_label.setText(str(len(self.logs)))
        self.log_last_time_label.setText(self.logs[0]["time"] if self.logs else "-")

    def _append_log(self, category: str, action: str, result: str, detail: str) -> None:
        self.logs.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "category": category,
            "action": action,
            "result": result,
            "detail": detail,
        })
        self.logs = self.logs[:200]
        self._refresh_logs()

    @staticmethod
    def _format_write_request(request: VrWriteRequest) -> str:
        values = ", ".join(str(v) for v in request.values)
        return f"VR[{request.start_vr}..{request.start_vr + len(request.values) - 1}] = [{values}]"

    @staticmethod
    def _format_read_request(start_vr: int, count: int) -> str:
        return f"读取 VR[{start_vr}..{start_vr + count - 1}]"

    def _clear_logs(self) -> None:
        self.logs.clear()
        self._refresh_logs()
        self.status_label.setText("日志已清空。")

    def _new_record(self) -> None:
        self.current_key = None
        self.name_edit.setText("")
        self.code_edit.setText("1001")
        self.cmd_combo.setCurrentText("MOVE_ABS")
        self.template_type_combo.setCurrentText("parametric")
        self.keywords_edit.setText("")
        self.pos_id_edit.setText("0")
        self.device_id_edit.setText("1")
        self.x_edit.setText("0")
        self.y_edit.setText("0")
        self.z_edit.setText("0")
        self.rx_edit.setText("0")
        self.ry_edit.setText("0")
        self.rz_edit.setText("0")
        self.speed_edit.setText("30")
        self.acc_edit.setText("40")
        self.io_grip_edit.setText("0")
        self.io_door_edit.setText("0")
        self.ext_p1_edit.setText("0")
        self.ext_p2_edit.setText("0")
        self.safety_edit.setText("5")
        self.desc_edit.setText("")
        self._sync_template_type_mode("parametric")
        self.status_label.setText("已创建空白模板。")
        self._append_log("后台", "新增模板", "成功", "已创建空白模板")

    def _save_record(self) -> None:
        record = self._collect_record()
        if not record.query_key:
            QMessageBox.warning(self, "输入错误", "显示名称不能为空。")
            self._append_log("后台", "保存模板", "失败", "显示名称不能为空")
            return
        self.table[record.query_key] = record
        save_query_table_json(self.json_path, self.table)
        self.service = RobotModbusService(self.json_path)
        self.current_key = record.query_key
        self._refresh_all()
        self.status_label.setText(f"已保存模板: {record.query_key}")
        self._append_log("后台", "保存模板", "成功", record.query_key)

    def _clone_record(self) -> None:
        record = self._collect_record()
        if not record.query_key:
            QMessageBox.warning(self, "无法另存为", "请先填写显示名称。")
            self._append_log("后台", "另存模板", "失败", "显示名称不能为空")
            return
        clone = QueryRecord(
            query_key=f"{record.query_key} - 副本",
            function_id=record.function_id,
            registers=record.registers,
            function_name=record.function_name,
            data_format=record.data_format,
            template_type=record.template_type,
            keywords=record.keywords,
            description=record.description,
            pos_id=record.pos_id,
            device_id=record.device_id,
            acc_percent=record.acc_percent,
            safety_level=record.safety_level,
            io_grip=record.io_grip,
            io_door=record.io_door,
            ext_p1=record.ext_p1,
            ext_p2=record.ext_p2,
        )
        self.table[clone.query_key] = clone
        save_query_table_json(self.json_path, self.table)
        self.service = RobotModbusService(self.json_path)
        self.current_key = clone.query_key
        self._load_record_into_form(clone)
        self._refresh_all()
        self.status_label.setText(f"已另存模板: {clone.query_key}")
        self._append_log("后台", "另存模板", "成功", clone.query_key)

    def _delete_record(self) -> None:
        key = self.name_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "无法删除", "当前没有选中的模板。")
            self._append_log("后台", "删除模板", "失败", "当前没有选中的模板")
            return
        if key not in self.table:
            QMessageBox.warning(self, "无法删除", f"模板不存在: {key}")
            self._append_log("后台", "删除模板", "失败", f"模板不存在: {key}")
            return
        del self.table[key]
        save_query_table_json(self.json_path, self.table)
        self.service = RobotModbusService(self.json_path)
        self.current_key = None
        self._new_record()
        self._refresh_all()
        self.status_label.setText(f"已删除模板: {key}")
        self._append_log("后台", "删除模板", "成功", key)

    def _on_template_selected(self) -> None:
        items = self.template_tree.selectedItems()
        if not items:
            self._append_log("后台", "选择模板", "失败", "没有选中任何模板")
            return
        key = items[0].text(0)
        if key in self.table:
            standard_command = self.service.build_standard_command_from_record(self.table[key], task_id=self.task_id)
            self._append_log("后台", "选择模板", "成功", f"{key} / {standard_command.code} / {standard_command.cmd}")
            self.current_key = key
            self._load_record_into_form(self.table[key])
        else:
            self._append_log("后台", "选择模板", "失败", f"模板不存在: {key}")

    def _check_connection(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "地址为空", "请输入控制器地址。")
            self._append_log("连接", "检测连接", "失败", "地址为空")
            return
        try:
            client = self._make_client(host)
            client.connect()
            client.disconnect()
            mode = "Mock" if self.controller_combo.currentText() == "模拟控制器" else "真实"
            self.connection_label.setText(f"{mode}连接成功: {host}")
            self._append_log("连接", "检测连接", "成功", f"{mode}连接成功: {host}")
        except Exception as exc:
            self.connection_label.setText(f"连接失败: {exc}")
            self._append_log("连接", "检测连接", "失败", str(exc))

    def _send_record(self, query_key: str) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "地址为空", "请输入控制器地址。")
            self._append_log("执行", f"发送指令 {query_key}", "失败", "地址为空")
            return
        try:
            record = self.table[query_key]
            validation_error = self._validate_record(record)
            if validation_error:
                raise ValueError(validation_error)
            client = self._make_client(host)
            client.connect()
            try:
                feedback = self._execute_send_by_protocol(client, record)
            finally:
                client.disconnect()
            self._after_send(record, True, "", feedback)
        except Exception as exc:
            self._after_send(self.table[query_key], False, str(exc))

    def _execute_send_by_protocol(self, client: ZMotionVrClient, record: QueryRecord) -> list[float]:
        if self.protocol_combo.currentText() == "最终标准协议":
            command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
            write_request = command.to_write_request()
            client.write_vr(write_request)
            self._append_log(
                "寄存器",
                f"写入标准协议 {record.query_key}",
                "成功",
                self._format_write_request(write_request),
            )
            read_request = self.service.build_standard_status_read()
            values = client.read_vr(read_request)
            self._append_log(
                "寄存器",
                f"读取标准反馈 {record.query_key}",
                "成功",
                f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {values}",
            )
            return values
        _, command = self.service.build_fixed_command_from_key(record.query_key)
        payload_request = VrWriteRequest(start_vr=command.payload_start_vr, values=command.payload_values)
        trigger_request = VrWriteRequest(start_vr=command.trigger_vr, values=(command.trigger_value,))
        client.write_vr(payload_request)
        self._append_log(
            "寄存器",
            f"写入简化负载 {record.query_key}",
            "成功",
            self._format_write_request(payload_request),
        )
        client.write_vr(trigger_request)
        self._append_log(
            "寄存器",
            f"写入简化触发 {record.query_key}",
            "成功",
            self._format_write_request(trigger_request),
        )
        read_request = self.service.build_status_read()
        values = client.read_vr(read_request)
        self._append_log(
            "寄存器",
            f"读取简化反馈 {record.query_key}",
            "成功",
            f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {values}",
        )
        return values

    def _after_send(self, record: QueryRecord, ok: bool, error: str, feedback: list[float] | None = None) -> None:
        self.history.insert(0, {
            "task": self.task_id,
            "code": self.service.build_standard_command_from_record(record, task_id=self.task_id).code,
            "type": "固定函数型无参数指令" if record.template_type == "fixed" else "参数型指令",
            "result": "成功" if ok else "失败",
        })
        if ok:
            self.busy = "运行中"
            self.result = "0"
            self.alarm_code = "ERR_000"
            self.alarm_text = "系统正常"
            if feedback:
                self._apply_feedback_values(record, feedback)
            elif record.template_type != "fixed":
                self.robot_x = self._fmt(record.registers[0])
                self.robot_y = self._fmt(record.registers[1])
                self.robot_z = self._fmt(record.registers[2])
                self.robot_r = f"{self._fmt(record.registers[3])} / {self._fmt(record.registers[4])} / {self._fmt(record.registers[5])}"
                self.robot_speed = f"{self._fmt(record.registers[6])}% / {self.acc_edit.text()}%"
            self.task_id += 1
            self.status_label.setText(f"已执行: {record.query_key}")
            self._append_log("执行", f"发送指令 {record.query_key}", "成功", f"任务{self.task_id - 1}")
        else:
            self.busy = "空闲"
            self.result = "9"
            self.alarm_code = "ERR_SEND"
            self.alarm_text = error
            self.status_label.setText(f"发送失败: {error}")
            QMessageBox.critical(self, "发送失败", error)
            self._append_log("执行", f"发送指令 {record.query_key}", "失败", error)
        self._refresh_all()

    def _read_feedback(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "地址为空", "请输入控制器地址。")
            self._append_log("反馈", "读取反馈", "失败", "地址为空")
            return
        try:
            client = self._make_client(host)
            client.connect()
            if self.protocol_combo.currentText() == "最终标准协议":
                read_request = self.service.build_standard_status_read()
                values = client.read_vr(read_request)
            else:
                read_request = self.service.build_status_read()
                values = client.read_vr(read_request)
            client.disconnect()
            self._apply_feedback_values(None, values)
            self._refresh_status_labels()
            self.status_label.setText(f"反馈区读取成功: {values}")
            self._append_log("反馈", "读取反馈", "成功", f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {values}")
        except Exception as exc:
            self.status_label.setText(f"读取反馈区失败: {exc}")
            QMessageBox.critical(self, "读取失败", str(exc))
            self._append_log("反馈", "读取反馈", "失败", str(exc))

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _handle_system_action(self, action_key: str) -> None:
        if self.protocol_combo.currentText() != "最终标准协议":
            self._apply_legacy_system_action(action_key)
            self._append_log("系统", action_key, "成功", "简化协议页面动作")
            return
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "地址为空", "请输入控制器地址。")
            self._append_log("系统", action_key, "失败", "地址为空")
            return
        try:
            code = SYSTEM_COMMAND_CODES[action_key]
            command = self.service.build_standard_system_command(
                code=code,
                task_id=self.task_id,
                desc=SYSTEM_COMMANDS[[k for k, v in SYSTEM_COMMANDS.items() if v[0] == action_key][0]][1],
            )
            client = self._make_client(host)
            client.connect()
            try:
                write_request = command.to_write_request()
                client.write_vr(write_request)
                self._append_log("寄存器", f"写入系统命令 {action_key}", "成功", self._format_write_request(write_request))
                read_request = self.service.build_standard_status_read()
                feedback = client.read_vr(read_request)
                self._append_log(
                    "寄存器",
                    f"读取系统反馈 {action_key}",
                    "成功",
                    f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {feedback}",
                )
            finally:
                client.disconnect()
            self._apply_feedback_values(None, feedback)
            self._apply_legacy_system_action(action_key, update_status=False)
            self.task_id += 1
            self.status_label.setText(SYSTEM_COMMANDS[[k for k, v in SYSTEM_COMMANDS.items() if v[0] == action_key][0]][1])
            self._refresh_status_labels()
            self._append_log("系统", action_key, "成功", f"命令码 {code}")
        except Exception as exc:
            self.status_label.setText(f"系统命令失败: {exc}")
            QMessageBox.critical(self, "系统命令失败", str(exc))
            self._append_log("系统", action_key, "失败", str(exc))

    def _apply_legacy_system_action(self, action_key: str, *, update_status: bool = True) -> None:
        if action_key == "power_on":
            if update_status:
                self._set_status("系统已上电")
            return
        if action_key == "auto_start":
            self._set_mode_busy("自动", True, "系统启动")
            return
        if action_key == "auto_stop":
            self._set_mode_busy(self.mode_label.text(), False, "系统停机")
            return
        if action_key == "sys_pause":
            self._set_mode_busy(self.mode_label.text(), False, "当前任务已暂停")
            self.busy = "暂停"
            self._refresh_status_labels()
            return
        if action_key == "sys_resume":
            self._set_mode_busy(self.mode_label.text(), True, "当前任务继续运行")
            return
        if action_key == "sys_estop":
            self._trigger_estop()

    def _set_mode_busy(self, mode: str, busy: bool, text: str) -> None:
        self.mode = mode
        self.busy = "运行中" if busy else "空闲"
        self.status_label.setText(text)
        self._refresh_status_labels()

    def _trigger_estop(self) -> None:
        self.busy = "空闲"
        self.result = "9"
        self.alarm_code = "ERR_900"
        self.alarm_text = "急停触发"
        self.status_label.setText("急停触发，系统锁定")
        self._refresh_status_labels()

    def _apply_feedback_values(self, record: QueryRecord | None, values: list[float]) -> None:
        if not values:
            return
        if len(values) >= 10 and self.protocol_combo.currentText() == "最终标准协议":
            status = self.service.parse_standard_status(values)
            self.result = str(status.result)
            self.busy = self._status_text(status.status)
            self.alarm_code = "ERR_000" if status.alm_code == 0 else f"ERR_{status.alm_code}"
            self.alarm_text = "系统正常" if status.alm_code == 0 else "控制器报警"
            self.robot_x = self._fmt(status.cur_x)
            self.robot_y = self._fmt(status.cur_y)
            self.robot_z = self._fmt(status.cur_z)
            self.robot_r = f"{self._fmt(status.cur_rx)} / {self._fmt(status.cur_ry)} / {self._fmt(status.cur_rz)}"
            self.io_status = str(status.io_stat)
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
        elif record and record.template_type != "fixed":
            self.robot_x = self._fmt(record.registers[0])
            self.robot_y = self._fmt(record.registers[1])
            self.robot_z = self._fmt(record.registers[2])
        if record and record.template_type != "fixed":
            self.robot_r = f"{self._fmt(record.registers[3])} / {self._fmt(record.registers[4])} / {self._fmt(record.registers[5])}"
            self.robot_speed = f"{self._fmt(record.registers[6])}% / {self._fmt(record.acc_percent)}%"

    def _validate_record(self, record: QueryRecord) -> str | None:
        if record.function_id <= 0:
            return "指令码必须大于0。"
        if not (1 <= record.safety_level <= 5):
            return "安全等级必须在 1 到 5 之间。"
        if record.template_type == "fixed":
            return None
        if not (0 <= record.registers[6] <= 100):
            return "速度百分比必须在 0 到 100 之间。"
        if not (0 <= record.acc_percent <= 100):
            return "加速度百分比必须在 0 到 100 之间。"
        if self.protocol_combo.currentText() == "最终标准协议":
            standard_command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
            if standard_command.code == 1001:
                if not (self.axis_ranges.x[0] <= record.registers[0] <= self.axis_ranges.x[1]):
                    return f"X 坐标超出最终标准范围 {self.axis_ranges.x}。"
                if not (self.axis_ranges.y[0] <= record.registers[1] <= self.axis_ranges.y[1]):
                    return f"Y 坐标超出最终标准范围 {self.axis_ranges.y}。"
                if not (self.axis_ranges.z[0] <= record.registers[2] <= self.axis_ranges.z[1]):
                    return f"Z 坐标超出最终标准范围 {self.axis_ranges.z}。"
        return None

    @staticmethod
    def _status_text(status_code: int) -> str:
        return {
            0: "空闲",
            1: "运行中",
            2: "暂停",
            3: "故障",
        }.get(status_code, f"状态{status_code}")

    def _sync_template_type_mode(self, template_type: str) -> None:
        is_fixed = template_type == "fixed"
        for widget in self.param_widgets:
            widget.setDisabled(is_fixed)

    def _make_client(self, host: str):
        if self.controller_combo.currentText() == "模拟控制器":
            return MockZMotionVrClient(host=host)
        return self._client_factory(host, self.resource_root)

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
    import sys

    app = QApplication(sys.argv)
    runtime_base = _runtime_dir()
    resource_base = _resource_dir()
    data_dir = runtime_base / "data"
    if not data_dir.exists():
        data_dir = resource_base / "data"
    json_path = data_dir / "query_table.json"
    system_config_path = data_dir / "system_config.json"
    csv_path = resource_base / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=system_config_path)
    window.show()
    sys.exit(app.exec())
