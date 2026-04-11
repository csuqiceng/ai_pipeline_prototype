from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QScrollArea,
    QComboBox,
    QFileDialog,
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
    QTabWidget,
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
from .avoidance_config import (
    AvoidanceConfig,
    SafePoint,
    ensure_avoidance_config_json,
    load_avoidance_config,
    save_avoidance_config,
    validate_safe_point,
)
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
MIRROR_RETRY_COUNT = 5
MIRROR_RETRY_INTERVAL_SEC = 0.1


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
        self.avoidance_config_path = ensure_avoidance_config_json(self.runtime_root / "data" / "avoidance_rules.json")
        self.axis_ranges = load_system_config(self.system_config_path)
        self.avoidance_config = load_avoidance_config(self.avoidance_config_path)
        self.table = load_query_table(self.json_path)
        self.service = RobotModbusService(self.json_path)
        self._client_factory = client_factory or (lambda host, repo_root: ZMotionVrClient(host=host, repo_root=repo_root))
        self.history: list[dict[str, str | int]] = []
        self.logs: list[dict[str, str]] = []
        self.task_id = 1001
        self.current_key: str | None = None
        self.current_safe_point_key: str | None = None
        self.robot_x = "1250.0"
        self.robot_y = "0.0"
        self.robot_z = "860.0"
        self.robot_r = "0.0 / 0.0 / 0.0"
        self.robot_speed = "30% / 40%"
        self.claw_enable = "0"
        self.claw_brake = "0"
        self.servo_enable = "0"
        self.run_state = "空闲"
        self.monitor_task = "-"
        self.motion_percent = "0%"
        self.echo_cmd = "-"
        self.exec_state = "0"
        self.mode = "自动"
        self.busy = "空闲"
        self.result = "0"
        self.alarm_code = "ERR_000"
        self.alarm_text = "系统正常"
        self.io_status = "0"
        self._polling_feedback = False
        self._last_poll_error = ""
        self._last_realtime_snapshot: tuple[str, str, str, str] | None = None
        self._poll_started_logged = False
        self._last_polled_status_values: tuple[float, ...] | None = None
        self._last_polled_monitor_values: tuple[float, ...] | None = None

        self._build_ui()
        self._load_initial_record()
        self._refresh_all()
        self._check_connection()
        self._start_realtime_polling()

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
        status_group = QGroupBox("总状态")
        status_group.setObjectName("subPanel")
        status_layout = QVBoxLayout(status_group)
        self.status_light_label = QLabel()
        self.status_light_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_light_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_light_label.setStyleSheet("font-size: 28px; font-weight: bold;")
        self.status_light_detail_label = QLabel("-")
        self.status_light_detail_label.setWordWrap(True)
        self.status_light_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.status_light_label)
        status_layout.addWidget(self.status_light_detail_label)
        layout.addStretch(1)
        layout.addWidget(status_group)
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
        link_layout.addWidget(QLabel("实时监控:"))
        self.monitor_label = QLabel("未启动")
        link_layout.addWidget(self.monitor_label)
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
        action_layout = QGridLayout(action_box)
        action_layout.setContentsMargins(10, 10, 10, 10)
        action_layout.setHorizontalSpacing(8)
        action_layout.setVerticalSpacing(8)
        actions = [
            ("新增", self._new_record),
            ("保存", self._save_record),
            ("另存为", self._clone_record),
            ("删除", self._delete_record),
            ("导出指令", self._export_template_json),
            ("导入指令", self._import_template_json),
        ]
        for index, (text, fn) in enumerate(actions):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            row = index // 2
            col = index % 2
            action_layout.addWidget(btn, row, col)
        top_layout.addWidget(self.backend_info, 2)
        top_layout.addWidget(self.current_info, 2)
        top_layout.addWidget(action_box, 1)
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
        config_reload_btn.clicked.connect(self._reload_system_config)
        config_buttons.addWidget(config_save_btn)
        config_buttons.addWidget(config_reload_btn)
        config_layout.addRow(config_buttons)
        avoidance_group = QGroupBox("安全中间点")
        avoidance_group.setObjectName("subPanel")
        avoidance_layout = QVBoxLayout(avoidance_group)
        avoidance_rule_form = QFormLayout()
        self.avoidance_mode_combo = QComboBox()
        self.avoidance_mode_combo.addItems(["关闭", "自动判断", "每次都经过中间点"])
        self.rule_rx_threshold_edit = QLineEdit("30")
        self.rule_ry_threshold_edit = QLineEdit("30")
        self.rule_rz_threshold_edit = QLineEdit("45")
        self.rule_low_z_threshold_edit = QLineEdit("150")
        self.rule_xy_move_threshold_edit = QLineEdit("100")
        for label, widget in [
            ("规避模式", self.avoidance_mode_combo),
            ("RX变化>=", self.rule_rx_threshold_edit),
            ("RY变化>=", self.rule_ry_threshold_edit),
            ("RZ变化>=", self.rule_rz_threshold_edit),
            ("低位Z<", self.rule_low_z_threshold_edit),
            ("XY移动>", self.rule_xy_move_threshold_edit),
        ]:
            avoidance_rule_form.addRow(label + ":", widget)
        avoidance_layout.addLayout(avoidance_rule_form)

        avoidance_split = QWidget()
        avoidance_split_layout = QHBoxLayout(avoidance_split)
        avoidance_split_layout.setContentsMargins(0, 0, 0, 0)
        avoidance_split_layout.setSpacing(8)

        self.safe_point_tree = QTreeWidget()
        self.safe_point_tree.setHeaderLabels(["中间点", "说明"])
        self.safe_point_tree.itemSelectionChanged.connect(self._on_safe_point_selected)
        avoidance_split_layout.addWidget(self.safe_point_tree, 1)

        safe_point_editor = QWidget()
        safe_point_form = QFormLayout(safe_point_editor)
        self.safe_point_name_edit = QLineEdit()
        self.safe_point_x_edit = QLineEdit("0")
        self.safe_point_y_edit = QLineEdit("0")
        self.safe_point_z_edit = QLineEdit("0")
        self.safe_point_rx_edit = QLineEdit("0")
        self.safe_point_ry_edit = QLineEdit("0")
        self.safe_point_rz_edit = QLineEdit("0")
        self.safe_point_speed_edit = QLineEdit("20")
        self.safe_point_acc_edit = QLineEdit("20")
        self.safe_point_desc_edit = QLineEdit()
        for label, widget in [
            ("名称", self.safe_point_name_edit),
            ("X", self.safe_point_x_edit),
            ("Y", self.safe_point_y_edit),
            ("Z", self.safe_point_z_edit),
            ("RX", self.safe_point_rx_edit),
            ("RY", self.safe_point_ry_edit),
            ("RZ", self.safe_point_rz_edit),
            ("速度%", self.safe_point_speed_edit),
            ("加速度%", self.safe_point_acc_edit),
            ("说明", self.safe_point_desc_edit),
        ]:
            safe_point_form.addRow(label + ":", widget)

        safe_point_buttons = QGridLayout()
        safe_point_new_btn = QPushButton("新增中间点")
        safe_point_new_btn.clicked.connect(self._new_safe_point)
        safe_point_save_btn = QPushButton("保存中间点")
        safe_point_save_btn.clicked.connect(self._save_safe_point)
        safe_point_delete_btn = QPushButton("删除中间点")
        safe_point_delete_btn.clicked.connect(self._delete_safe_point)
        safe_point_save_cfg_btn = QPushButton("保存规避配置")
        safe_point_save_cfg_btn.clicked.connect(self._save_avoidance_config_only)
        safe_point_buttons.addWidget(safe_point_new_btn, 0, 0)
        safe_point_buttons.addWidget(safe_point_save_btn, 0, 1)
        safe_point_buttons.addWidget(safe_point_delete_btn, 1, 0)
        safe_point_buttons.addWidget(safe_point_save_cfg_btn, 1, 1)
        safe_point_form.addRow(safe_point_buttons)

        avoidance_split_layout.addWidget(safe_point_editor, 1)
        avoidance_layout.addWidget(avoidance_split)

        right_tabs = QTabWidget()
        right_tabs.setObjectName("panel")
        right_tabs.addTab(preview_group, "JSON预览")
        right_tabs.addTab(config_group, "系统参数")
        right_tabs.addTab(avoidance_group, "安全中间点")

        bottom_layout.addWidget(left, 1)
        bottom_layout.addWidget(middle, 1)
        bottom_layout.addWidget(right_tabs, 1)
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
        self._load_avoidance_config_into_form()

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
        export_btn = QPushButton("导出日志")
        export_btn.clicked.connect(self._export_logs)
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self._clear_logs)
        action_layout.addWidget(refresh_btn)
        action_layout.addWidget(export_btn)
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
            self.claw_enable_label = QLabel(self.claw_enable)
            self.claw_brake_label = QLabel(self.claw_brake)
            self.servo_enable_label = QLabel(self.servo_enable)
            self.run_state_label = QLabel(self.run_state)
            self.monitor_task_label = QLabel(self.monitor_task)
            self.motion_percent_label = QLabel(self.motion_percent)
            self.echo_cmd_label = QLabel(self.echo_cmd)
            self.exec_state_label = QLabel(self.exec_state)
            for label, widget in [
                ("X", self.robot_x_label), ("Y", self.robot_y_label), ("Z", self.robot_z_label),
                ("RX / RY / RZ", self.robot_r_label), ("速度 / 加速度", self.robot_speed_label),
                ("夹爪使能", self.claw_enable_label), ("夹爪刹车", self.claw_brake_label),
                ("伺服使能", self.servo_enable_label), ("实时状态", self.run_state_label),
                ("监控任务ID", self.monitor_task_label), ("运动进度", self.motion_percent_label),
                ("命令回显", self.echo_cmd_label), ("执行触发", self.exec_state_label),
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

    def _reload_system_config(self) -> None:
        self._load_system_config_into_form()
        self.status_label.setText(f"已重载系统范围配置: {self.system_config_path}")
        self._append_log("后台", "重载范围", "成功", json.dumps(self.axis_ranges.to_dict(), ensure_ascii=False))

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

    def _load_avoidance_config_into_form(self) -> None:
        self.avoidance_config = load_avoidance_config(self.avoidance_config_path)
        mode_text = {
            "off": "关闭",
            "auto": "自动判断",
            "always": "每次都经过中间点",
        }.get(self.avoidance_config.mode, "关闭")
        self.avoidance_mode_combo.setCurrentText(mode_text)
        self.rule_rx_threshold_edit.setText(self._fmt(self.avoidance_config.rx_threshold))
        self.rule_ry_threshold_edit.setText(self._fmt(self.avoidance_config.ry_threshold))
        self.rule_rz_threshold_edit.setText(self._fmt(self.avoidance_config.rz_threshold))
        self.rule_low_z_threshold_edit.setText(self._fmt(self.avoidance_config.low_z_threshold))
        self.rule_xy_move_threshold_edit.setText(self._fmt(self.avoidance_config.xy_move_threshold))
        self._refresh_safe_point_tree()
        if self.current_safe_point_key and self.current_safe_point_key in self.avoidance_config.safe_points:
            self._load_safe_point_into_form(self.avoidance_config.safe_points[self.current_safe_point_key])
        elif self.avoidance_config.safe_points:
            first_key = sorted(self.avoidance_config.safe_points)[0]
            self.current_safe_point_key = first_key
            self._load_safe_point_into_form(self.avoidance_config.safe_points[first_key])
        else:
            self._new_safe_point()

    def _refresh_safe_point_tree(self) -> None:
        self.safe_point_tree.clear()
        for point in sorted(self.avoidance_config.safe_points.values(), key=lambda item: item.name):
            item = QTreeWidgetItem([point.name, point.description or "-"])
            self.safe_point_tree.addTopLevelItem(item)
            if self.current_safe_point_key == point.name:
                self.safe_point_tree.setCurrentItem(item)

    def _load_safe_point_into_form(self, point: SafePoint) -> None:
        self.safe_point_name_edit.setText(point.name)
        self.safe_point_x_edit.setText(self._fmt(point.x))
        self.safe_point_y_edit.setText(self._fmt(point.y))
        self.safe_point_z_edit.setText(self._fmt(point.z))
        self.safe_point_rx_edit.setText(self._fmt(point.rx))
        self.safe_point_ry_edit.setText(self._fmt(point.ry))
        self.safe_point_rz_edit.setText(self._fmt(point.rz))
        self.safe_point_speed_edit.setText(self._fmt(point.speed_percent))
        self.safe_point_acc_edit.setText(self._fmt(point.acc_percent))
        self.safe_point_desc_edit.setText(point.description)

    def _new_safe_point(self) -> None:
        self.current_safe_point_key = None
        self.safe_point_name_edit.setText("")
        self.safe_point_x_edit.setText("0")
        self.safe_point_y_edit.setText("0")
        self.safe_point_z_edit.setText("200")
        self.safe_point_rx_edit.setText("0")
        self.safe_point_ry_edit.setText("0")
        self.safe_point_rz_edit.setText("0")
        self.safe_point_speed_edit.setText("20")
        self.safe_point_acc_edit.setText("20")
        self.safe_point_desc_edit.setText("")
        self.status_label.setText("已创建空白安全中间点。")
        self._append_log("后台", "新增中间点", "成功", "已创建空白安全中间点")

    def _collect_safe_point(self) -> SafePoint:
        def num(text: str) -> float:
            text = text.strip().replace("%", "")
            return float(text) if text else 0.0

        return SafePoint(
            name=self.safe_point_name_edit.text().strip(),
            x=num(self.safe_point_x_edit.text()),
            y=num(self.safe_point_y_edit.text()),
            z=num(self.safe_point_z_edit.text()),
            rx=num(self.safe_point_rx_edit.text()),
            ry=num(self.safe_point_ry_edit.text()),
            rz=num(self.safe_point_rz_edit.text()),
            speed_percent=num(self.safe_point_speed_edit.text()),
            acc_percent=num(self.safe_point_acc_edit.text()),
            description=self.safe_point_desc_edit.text().strip(),
        )

    def _build_avoidance_config(self, safe_points: dict[str, SafePoint] | None = None) -> AvoidanceConfig:
        def num(text: str) -> float:
            text = text.strip().replace("%", "")
            return float(text) if text else 0.0

        mode = {
            "关闭": "off",
            "自动判断": "auto",
            "每次都经过中间点": "always",
        }.get(self.avoidance_mode_combo.currentText(), "off")
        return AvoidanceConfig(
            mode=mode,
            rx_threshold=num(self.rule_rx_threshold_edit.text()),
            ry_threshold=num(self.rule_ry_threshold_edit.text()),
            rz_threshold=num(self.rule_rz_threshold_edit.text()),
            low_z_threshold=num(self.rule_low_z_threshold_edit.text()),
            xy_move_threshold=num(self.rule_xy_move_threshold_edit.text()),
            safe_points=safe_points if safe_points is not None else dict(self.avoidance_config.safe_points),
            rules=self.avoidance_config.rules,
        )

    def _save_safe_point(self) -> None:
        try:
            point = self._collect_safe_point()
        except ValueError:
            QMessageBox.warning(self, "保存失败", "中间点参数必须是数字。")
            self._append_log("后台", "保存中间点", "失败", "中间点参数必须是数字")
            return
        validation_error = validate_safe_point(point)
        if validation_error:
            QMessageBox.warning(self, "保存失败", validation_error)
            self._append_log("后台", "保存中间点", "失败", validation_error)
            return
        safe_points = dict(self.avoidance_config.safe_points)
        if self.current_safe_point_key and self.current_safe_point_key != point.name and self.current_safe_point_key in safe_points:
            del safe_points[self.current_safe_point_key]
        safe_points[point.name] = point
        self.avoidance_config = self._build_avoidance_config(safe_points)
        save_avoidance_config(self.avoidance_config_path, self.avoidance_config)
        self.current_safe_point_key = point.name
        self._refresh_safe_point_tree()
        self.status_label.setText(f"已保存安全中间点: {point.name}")
        self._append_log("后台", "保存中间点", "成功", point.name)

    def _delete_safe_point(self) -> None:
        key = self.safe_point_name_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "无法删除", "当前没有选中的中间点。")
            self._append_log("后台", "删除中间点", "失败", "当前没有选中的中间点")
            return
        safe_points = dict(self.avoidance_config.safe_points)
        if key not in safe_points:
            QMessageBox.warning(self, "无法删除", f"中间点不存在: {key}")
            self._append_log("后台", "删除中间点", "失败", f"中间点不存在: {key}")
            return
        del safe_points[key]
        self.avoidance_config = self._build_avoidance_config(safe_points)
        save_avoidance_config(self.avoidance_config_path, self.avoidance_config)
        self.current_safe_point_key = None
        self._refresh_safe_point_tree()
        self._new_safe_point()
        self.status_label.setText(f"已删除安全中间点: {key}")
        self._append_log("后台", "删除中间点", "成功", key)

    def _save_avoidance_config_only(self) -> None:
        self.avoidance_config = self._build_avoidance_config()
        save_avoidance_config(self.avoidance_config_path, self.avoidance_config)
        self.status_label.setText(f"已保存规避配置: {self.avoidance_config_path}")
        self._append_log(
            "后台",
            "保存规避配置",
            "成功",
            json.dumps(
                {
                    "mode": self.avoidance_config.mode,
                    "rx_threshold": self.avoidance_config.rx_threshold,
                    "ry_threshold": self.avoidance_config.ry_threshold,
                    "rz_threshold": self.avoidance_config.rz_threshold,
                    "safe_points": list(self.avoidance_config.safe_points),
                },
                ensure_ascii=False,
            ),
        )

    def _on_safe_point_selected(self) -> None:
        items = self.safe_point_tree.selectedItems()
        if not items:
            return
        key = items[0].text(0)
        if key in self.avoidance_config.safe_points:
            self.current_safe_point_key = key
            self._load_safe_point_into_form(self.avoidance_config.safe_points[key])

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
        self.claw_enable_label.setText(self.claw_enable)
        self.claw_brake_label.setText(self.claw_brake)
        self.servo_enable_label.setText(self.servo_enable)
        self.run_state_label.setText(self.run_state)
        self.monitor_task_label.setText(self.monitor_task)
        self.motion_percent_label.setText(self.motion_percent)
        self.echo_cmd_label.setText(self.echo_cmd)
        self.exec_state_label.setText(self.exec_state)
        self.mode_label.setText(self.mode)
        self.busy_label.setText(self.busy)
        self.result_label.setText(self.result)
        self.alarm_code_label.setText(self.alarm_code)
        self.alarm_text_label.setText(self.alarm_text)
        self.io_status_label.setText(self.io_status)
        self.task_label.setText(str(self.task_id))
        self.header_status.setText("第一版：任务运行中" if self.busy == "运行中" else "第一版：固定指令 + 后台模板")
        self._refresh_overall_state_indicator()

    def _refresh_overall_state_indicator(self) -> None:
        state_text, color, detail = self._compute_overall_state()
        self.status_light_label.setText(f"<span style='color:{color};'>●</span> {state_text}")
        self.status_light_detail_label.setText(detail)

    def _compute_overall_state(self) -> tuple[str, str, str]:
        monitor_offline = self.monitor_label.text() == "实时监控离线" or "失败" in self.connection_label.text()
        if monitor_offline:
            return "离线", "#7a7a7a", "未连接或无实时反馈"
        if self.alarm_code not in {"0", "ERR_000"}:
            return "异常", "#ef5a5a", self.alarm_text or "报警或通讯故障"
        if self.busy == "暂停" or self.run_state == "暂停":
            return "暂停", "#ffe46d", "系统处于暂停状态"
        if self.busy == "运行中" or self.run_state == "运行中":
            return "运行中", "#4f7cff", "下位机正在执行任务"
        return "空闲", "#42d84a", "系统已连接，当前空闲"

    def _capture_realtime_snapshot(self) -> tuple[str, str, str, str]:
        overall_state, _, _ = self._compute_overall_state()
        return (overall_state, self.busy, self.run_state, self.alarm_code)

    def _log_realtime_state_change_if_needed(self) -> None:
        current = self._capture_realtime_snapshot()
        if self._last_realtime_snapshot is None:
            self._last_realtime_snapshot = current
            return
        if current == self._last_realtime_snapshot:
            return
        prev_overall, prev_busy, prev_run, prev_alarm = self._last_realtime_snapshot
        curr_overall, curr_busy, curr_run, curr_alarm = current
        detail = (
            f"总状态 {prev_overall} -> {curr_overall} | "
            f"忙闲 {prev_busy} -> {curr_busy} | "
            f"实时 {prev_run} -> {curr_run} | "
            f"报警 {prev_alarm} -> {curr_alarm}"
        )
        self._append_log("反馈", "实时状态变化", "成功", detail)
        self._last_realtime_snapshot = current

    def _log_poll_register_values_if_needed(
        self,
        *,
        status_values: list[float],
        status_request,
        monitor_values: list[float] | None = None,
        monitor_request=None,
    ) -> None:
        status_tuple = tuple(float(v) for v in status_values)
        should_log_status = self._last_polled_status_values is None or self._last_polled_status_values != status_tuple
        if should_log_status:
            self._append_log(
                "反馈",
                "轮询状态区",
                "成功",
                f"{self._format_read_request(status_request.start_vr, status_request.count)} -> {status_values}",
            )
            self._last_polled_status_values = status_tuple

        if monitor_values is None or monitor_request is None:
            return

        monitor_tuple = tuple(float(v) for v in monitor_values)
        should_log_monitor = self._last_polled_monitor_values is None or self._last_polled_monitor_values != monitor_tuple
        if should_log_monitor:
            self._append_log(
                "反馈",
                "轮询监控区",
                "成功",
                f"{self._format_read_request(monitor_request.start_vr, monitor_request.count)} -> {monitor_values}",
            )
            self._last_polled_monitor_values = monitor_tuple

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
        cleared_count = len(self.logs)
        self.logs.clear()
        self._refresh_logs()
        self.status_label.setText("日志已清空。")
        self.logs.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "category": "日志",
            "action": "清空日志",
            "result": "成功",
            "detail": f"已清空 {cleared_count} 条日志",
        })
        self._refresh_logs()

    def _export_logs(self) -> None:
        export_dir = self.runtime_root / "data" / "exported_logs"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"robot_qt_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with export_path.open("w", encoding="utf-8") as fh:
            json.dump(self.logs, fh, ensure_ascii=False, indent=2)
        self.status_label.setText(f"日志已导出: {export_path}")
        self._append_log("日志", "导出日志", "成功", str(export_path))

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

    def _export_template_json(self) -> None:
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
        self.service = RobotModbusService(self.json_path)
        self.current_key = None
        self._new_record()
        self._refresh_all()
        self.status_label.setText(f"已导入模板 JSON: {file_path}")
        self._append_log("后台", "导入模板JSON", "成功", file_path)

    def _on_template_selected(self) -> None:
        items = self.template_tree.selectedItems()
        if not items:
            return
        key = items[0].text(0)
        if key in self.table:
            self.current_key = key
            self._load_record_into_form(self.table[key])

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
            self.monitor_label.setText("实时监控运行中")
            self._refresh_overall_state_indicator()
            self._append_log("连接", "检测连接", "成功", f"{mode}连接成功: {host}")
        except Exception as exc:
            self.connection_label.setText(f"连接失败: {exc}")
            self.monitor_label.setText("实时监控离线")
            self._refresh_overall_state_indicator()
            self._append_log("连接", "检测连接", "失败", str(exc))

    def _send_record(self, query_key: str) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "地址为空", "请输入控制器地址。")
            self._append_log("执行", f"发送指令 {query_key}", "失败", "地址为空")
            return
        try:
            record = self.table[query_key]
            plan_records, plan_reason = self._build_execution_plan(record)
            for plan_record in plan_records:
                validation_error = self._validate_record(plan_record)
                if validation_error:
                    raise ValueError(validation_error)
            client = self._make_client(host)
            client.connect()
            try:
                if len(plan_records) > 1:
                    self._append_log("执行", f"规避判断 {query_key}", "成功", plan_reason)
                feedback = None
                for idx, plan_record in enumerate(plan_records, start=1):
                    if len(plan_records) > 1:
                        self._append_log(
                            "执行",
                            f"规避执行第{idx}步",
                            "成功",
                            f"{plan_record.query_key} | {plan_record.description or '-'}",
                        )
                    feedback = self._execute_send_by_protocol(client, plan_record)
                    self._after_send(plan_record, True, "", feedback)
            finally:
                client.disconnect()
        except Exception as exc:
            self._after_send(locals().get("plan_record", self.table[query_key]), False, str(exc))

    def _build_execution_plan(self, record: QueryRecord) -> tuple[list[QueryRecord], str]:
        safe_point, reason = self._select_safe_point_for_record(record)
        if safe_point is None:
            return [record], "未命中规避规则，直接发送"
        safe_record = self._build_safe_point_record(safe_point, record)
        return [safe_record, record], reason

    def _select_safe_point_for_record(self, record: QueryRecord) -> tuple[SafePoint | None, str]:
        self.avoidance_config = self._build_avoidance_config(dict(self.avoidance_config.safe_points))
        if self.avoidance_config.mode == "off":
            return None, "规避模式=关闭"
        if record.function_name.upper() not in {"MOVE_ABS", "MOVE_REL"}:
            return None, "当前指令不是运动类指令"
        safe_point = self._get_active_safe_point()
        if safe_point is None:
            return None, "未配置安全中间点"
        if self.avoidance_config.mode == "always":
            return safe_point, f"规避模式=每次都经过中间点，使用 {safe_point.name}"

        current_rx, current_ry, current_rz = self._current_robot_r_components()
        target_x, target_y, target_z, target_rx, target_ry, target_rz, _ = record.registers
        if (
            abs(target_rx - current_rx) >= self.avoidance_config.rx_threshold
            or abs(target_ry - current_ry) >= self.avoidance_config.ry_threshold
            or abs(target_rz - current_rz) >= self.avoidance_config.rz_threshold
        ):
            return safe_point, (
                f"姿态变化过大，使用 {safe_point.name} 过渡 "
                f"(ΔRX={self._fmt(abs(target_rx - current_rx))}, "
                f"ΔRY={self._fmt(abs(target_ry - current_ry))}, "
                f"ΔRZ={self._fmt(abs(target_rz - current_rz))})"
            )
        current_z = self._current_robot_xyz()[2]
        current_x, current_y, _ = self._current_robot_xyz()
        if (
            current_z < self.avoidance_config.low_z_threshold
            and target_z < self.avoidance_config.low_z_threshold
            and (
                abs(target_x - current_x) > self.avoidance_config.xy_move_threshold
                or abs(target_y - current_y) > self.avoidance_config.xy_move_threshold
            )
        ):
            return safe_point, f"低位大范围移动，使用 {safe_point.name} 过渡"
        return None, "未命中自动规避规则"

    def _get_active_safe_point(self) -> SafePoint | None:
        if self.current_safe_point_key and self.current_safe_point_key in self.avoidance_config.safe_points:
            return self.avoidance_config.safe_points[self.current_safe_point_key]
        if self.avoidance_config.safe_points:
            first_key = sorted(self.avoidance_config.safe_points)[0]
            return self.avoidance_config.safe_points[first_key]
        return None

    def _build_safe_point_record(self, point: SafePoint, target_record: QueryRecord) -> QueryRecord:
        return QueryRecord(
            query_key=f"中间点-{point.name}",
            function_id=1001,
            function_name="MOVE_ABS",
            data_format="IEE",
            template_type="parametric",
            keywords=point.name,
            description=f"规避中间点：{point.name}",
            pos_id=target_record.pos_id,
            device_id=target_record.device_id,
            acc_percent=point.acc_percent,
            safety_level=target_record.safety_level,
            io_grip=0,
            io_door=0,
            ext_p1=0.0,
            ext_p2=0.0,
            registers=(
                point.x,
                point.y,
                point.z,
                point.rx,
                point.ry,
                point.rz,
                point.speed_percent,
            ),
        )

    def _current_robot_xyz(self) -> tuple[float, float, float]:
        return (float(self.robot_x), float(self.robot_y), float(self.robot_z))

    def _current_robot_r_components(self) -> tuple[float, float, float]:
        parts = [part.strip() for part in self.robot_r.split("/")]
        values = [float(part) for part in parts[:3]]
        while len(values) < 3:
            values.append(0.0)
        return values[0], values[1], values[2]

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
            mirror_request = self.service.build_standard_mirror_ack_read()
            mirror_values = client.read_vr(mirror_request)
            self._append_log(
                "寄存器",
                f"读取镜像区 {record.query_key}",
                "成功",
                f"{self._format_read_request(mirror_request.start_vr, mirror_request.count)} -> {mirror_values}",
            )
            mirror_ack = self.service.parse_standard_mirror_ack(mirror_values)
            self._wait_for_mirror_match(client, write_request, mirror_request, record.query_key, mirror_ack.mirror_values)
            exec_request = self.service.build_standard_execute_trigger_write()
            client.write_vr(exec_request)
            self._append_log(
                "寄存器",
                f"写入执行触发 {record.query_key}",
                "成功",
                self._format_write_request(exec_request),
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
            if "通讯故障" in error or "镜像区连续" in error:
                self.alarm_code = "ERR_COMM"
                self.alarm_text = "镜像确认失败，判定通讯故障"
            else:
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
            values, read_request = self._read_feedback_once()
            self._apply_feedback_values(None, values)
            self._refresh_status_labels()
            self.monitor_label.setText("实时监控运行中")
            self.status_label.setText(f"反馈区读取成功: {values}")
            self._append_log("反馈", "读取反馈", "成功", f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {values}")
        except Exception as exc:
            self.status_label.setText(f"读取反馈区失败: {exc}")
            self.monitor_label.setText("实时监控离线")
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
            action_text = next(k for k, v in SYSTEM_COMMANDS.items() if v[0] == action_key)
            command = self.service.build_standard_system_command(
                code=code,
                task_id=self.task_id,
                desc=SYSTEM_COMMANDS[action_text][1],
            )
            client = self._make_client(host)
            client.connect()
            try:
                write_request = command.to_write_request()
                client.write_vr(write_request)
                self._append_log("寄存器", f"写入系统命令 {action_key}", "成功", self._format_write_request(write_request))
                mirror_request = self.service.build_standard_mirror_ack_read()
                mirror_values = client.read_vr(mirror_request)
                self._append_log(
                    "寄存器",
                    f"读取系统镜像区 {action_key}",
                    "成功",
                    f"{self._format_read_request(mirror_request.start_vr, mirror_request.count)} -> {mirror_values}",
                )
                mirror_ack = self.service.parse_standard_mirror_ack(mirror_values)
                self._wait_for_mirror_match(client, write_request, mirror_request, action_key, mirror_ack.mirror_values)
                exec_request = self.service.build_standard_execute_trigger_write()
                client.write_vr(exec_request)
                self._append_log(
                    "寄存器",
                    f"写入系统执行触发 {action_key}",
                    "成功",
                    self._format_write_request(exec_request),
                )
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
            self.status_label.setText(SYSTEM_COMMANDS[action_text][1])
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

    def _start_realtime_polling(self) -> None:
        self.realtime_timer = QTimer(self)
        self.realtime_timer.setInterval(500)
        self.realtime_timer.timeout.connect(self._poll_feedback_silent)
        self.realtime_timer.start()

    def _poll_feedback_silent(self) -> None:
        if self._polling_feedback:
            return
        host = self.host_edit.text().strip()
        if not host:
            self.monitor_label.setText("未启动")
            return
        self._polling_feedback = True
        try:
            if self.protocol_combo.currentText() == "最终标准协议":
                status_values, status_request = self._read_feedback_once()
                self._apply_feedback_values(None, status_values)
                values, monitor_request = self._read_realtime_once()
                self._apply_realtime_values(values)
                self._log_poll_register_values_if_needed(
                    status_values=status_values,
                    status_request=status_request,
                    monitor_values=values,
                    monitor_request=monitor_request,
                )
            else:
                values, read_request = self._read_feedback_once()
                self._apply_feedback_values(None, values)
                self._log_poll_register_values_if_needed(
                    status_values=values,
                    status_request=read_request,
                )
            self.monitor_label.setText("实时监控运行中")
            self._refresh_status_labels()
            if not self._poll_started_logged:
                self._append_log("反馈", "实时监控轮询", "成功", "首次轮询成功，定时轮询已运行")
                self._poll_started_logged = True
            self._log_realtime_state_change_if_needed()
            self._last_poll_error = ""
        except Exception as exc:
            error = str(exc)
            self.monitor_label.setText("实时监控离线")
            self._refresh_overall_state_indicator()
            if error != self._last_poll_error:
                self._append_log("反馈", "实时监控", "失败", error)
                self._last_poll_error = error
        finally:
            self._polling_feedback = False

    def _read_feedback_once(self) -> tuple[list[float], VrReadRequest]:
        host = self.host_edit.text().strip()
        client = self._make_client(host)
        client.connect()
        try:
            if self.protocol_combo.currentText() == "最终标准协议":
                read_request = self.service.build_standard_status_read()
                values = client.read_vr(read_request)
            else:
                read_request = self.service.build_status_read()
                values = client.read_vr(read_request)
        finally:
            client.disconnect()
        return values, read_request

    def _read_realtime_once(self) -> tuple[list[float], VrReadRequest]:
        host = self.host_edit.text().strip()
        client = self._make_client(host)
        client.connect()
        try:
            read_request = self.service.build_standard_monitor_read()
            values = client.read_vr(read_request)
        finally:
            client.disconnect()
        return values, read_request

    def _apply_realtime_values(self, values: list[float]) -> None:
        if len(values) < 12:
            return
        status = self.service.parse_standard_realtime_status(values)
        self.robot_x = self._fmt(status.cur_x)
        self.robot_y = self._fmt(status.cur_y)
        self.robot_z = self._fmt(status.cur_z)
        self.robot_r = f"{self._fmt(status.cur_rx)} / {self._fmt(status.cur_ry)} / {self._fmt(status.cur_rz)}"
        self.claw_enable = str(status.claw_enable)
        self.claw_brake = str(status.claw_brake)
        self.servo_enable = str(status.servo_enable)
        self.run_state = self._status_text(status.run_state)
        self.busy = self._status_text(status.run_state)
        self.alarm_code = "ERR_000" if status.alm_code == 0 else f"ERR_{status.alm_code}"
        self.alarm_text = "系统正常" if status.alm_code == 0 else "控制器报警"
        self.io_status = str(status.io_stat)
        self.monitor_task = str(status.echo_task_id) if status.echo_task_id else "-"
        self.motion_percent = f"{self._fmt(status.motion_percent)}%"
        self.echo_cmd = str(status.echo_cmd_code) if status.echo_cmd_code else "-"
        self.exec_state = str(status.exec_trigger)

    def _wait_for_mirror_match(
        self,
        client: ZMotionVrClient,
        request: VrWriteRequest,
        mirror_request,
        query_key: str,
        initial_mirror_values: tuple[float, ...] | None = None,
    ) -> None:
        attempts: list[tuple[float, ...]] = []
        if initial_mirror_values is not None:
            attempts.append(tuple(float(v) for v in initial_mirror_values))

        if self._values_equal(request.values, attempts[0]) if attempts else False:
            self._append_log("执行", f"镜像确认 {query_key}", "成功", "首次比对一致，直接进入执行触发")
            return

        while len(attempts) < MIRROR_RETRY_COUNT:
            time.sleep(MIRROR_RETRY_INTERVAL_SEC)
            mirror_values = client.read_vr(mirror_request)
            self._append_log(
                "寄存器",
                f"轮询镜像区 {query_key}",
                "成功",
                f"{self._format_read_request(mirror_request.start_vr, mirror_request.count)} -> {mirror_values}",
            )
            mirror_data = self.service.parse_standard_mirror_ack(mirror_values).mirror_values
            attempts.append(mirror_data)
            if self._values_equal(request.values, mirror_data):
                self._append_log(
                    "执行",
                    f"镜像确认 {query_key}",
                    "成功",
                    f"第 {len(attempts)} 次比对一致，进入执行触发",
                )
                return

        raise RuntimeError(f"镜像区连续 {MIRROR_RETRY_COUNT} 次比对不一致，判定通讯故障。")

    @staticmethod
    def _values_equal(left: tuple[float, ...], right: tuple[float, ...], tolerance: float = 1e-6) -> bool:
        if len(left) != len(right):
            return False
        return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))

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


def _resolve_runtime_data_file(filename: str) -> Path:
    runtime_file = _runtime_dir() / "data" / filename
    if runtime_file.exists():
        return runtime_file
    return _resource_dir() / "data" / filename


def main() -> None:
    import sys

    app = QApplication(sys.argv)
    resource_base = _resource_dir()
    json_path = _resolve_runtime_data_file("query_table.json")
    system_config_path = _resolve_runtime_data_file("system_config.json")
    csv_path = resource_base / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=system_config_path)
    window.show()
    sys.exit(app.exec())
