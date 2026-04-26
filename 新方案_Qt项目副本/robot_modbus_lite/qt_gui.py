from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QScrollArea,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
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
from .models import ControllerClient, QueryRecord, VrWriteRequest, VrReadRequest
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
from .voice_nlp_adapter import VoiceNlpAction, VoiceNlpAdapter, VoiceNlpPlan
from .license_manager import LicenseManager
from .license_dialog import LicenseDialog


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
EXECUTION_RETRY_COUNT = 100
EXECUTION_RETRY_INTERVAL_SEC = 0.1


class RobotQtWindow(QMainWindow):
    _main_thread_call = Signal(object)

    def __init__(
        self,
        *,
        json_path: Path,
        csv_path: Path,
        system_config_path: Path | None = None,
        client_factory: Callable[[str, Path], ZMotionVrClient] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("机械手自然语言编程控制系统 - Qt版")
        self.resize(1380, 860)

        self.runtime_root = _runtime_dir()
        self.resource_root = _resource_dir()
        self.flows_path = _resolve_runtime_data_file("flows.json")
        self.json_path = bootstrap_query_table_json(json_path, csv_path)
        self.system_config_path = ensure_system_config_json(system_config_path or (self.runtime_root / "data" / "system_config.json"))
        self.avoidance_config_path = ensure_avoidance_config_json(self.runtime_root / "data" / "avoidance_rules.json")
        self.axis_ranges = load_system_config(self.system_config_path)
        self.avoidance_config = load_avoidance_config(self.avoidance_config_path)
        self.table = load_query_table(self.json_path)
        self.service = RobotModbusService(self.json_path, flows_path=self.flows_path, table=self.table)
        self._client_factory = client_factory or (lambda host, repo_root: ZMotionVrClient(host=host, repo_root=repo_root))
        self.history: list[dict[str, str | int]] = []
        self.logs: list[dict[str, str]] = []
        self.task_id = 1001
        self.current_key: str | None = None
        self.current_safe_point_key: str | None = None
        self.current_flow_manage_name: str | None = None
        self.current_flow_name: str | None = None
        self.flow_step_index = 0
        self.flow_status = "空闲"
        self.flow_running = False
        self.flow_current_step = "-"
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
        self._cached_client = None
        self._cached_client_host = ""
        self._client_cache_lock = threading.Lock()
        self.nlp_last_plan: VoiceNlpPlan | None = None
        self.nlp_sequence_running = False
        self.nlp_parse_running = False
        self._nlp_pending_actions: list[VoiceNlpAction] = []
        self._nlp_pending_index = 0
        self._flow_done_callback: Callable[[bool], None] | None = None
        self._mic_process: subprocess.Popen[str] | None = None
        self._mic_poll_timer: QTimer | None = None
        self._mic_stop_flag_path: Path | None = None
        self._mic_result_path: Path | None = None
        self._mic_recorder_thread = None  # 代理模式 QThread 持久录音线程
        self._proxy_mic_capturing = False  # 代理模式是否正在采集

        # 授权相关
        self.license_manager = LicenseManager(self.runtime_root / "data")
        self._deepseek_client = None  # 外部注入的 DeepSeek 客户端
        self._use_license_voice = False  # 是否使用订阅模式语音

        self._main_thread_call.connect(self._handle_main_thread_call)

        self._build_ui()
        self._init_api_clients()
        self._refresh_microphone_devices()
        self._load_initial_record()
        self._refresh_all()
        self._check_connection()
        self._start_realtime_polling()

    def _build_message_box(self, icon: QMessageBox.Icon, title: str, text: str) -> QMessageBox:
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        box.setDefaultButton(QMessageBox.Ok)
        box.setMinimumWidth(0)
        box.setSizeGripEnabled(False)
        box.setStyleSheet(
            """
            QMessageBox {
                background-color: #d9f4f7;
            }
            QMessageBox QLabel {
                color: #102a43;
                font-size: 13px;
                min-width: 0px;
                padding: 2px 0px;
            }
            QMessageBox QPushButton {
                min-width: 76px;
                min-height: 30px;
                padding: 2px 10px;
                border: 2px solid #23313f;
                border-radius: 6px;
                background-color: #f3f5f7;
                color: #111827;
                font-size: 12px;
                font-weight: 600;
            }
            QMessageBox QPushButton:hover {
                background-color: #e7edf2;
            }
            QMessageBox QPushButton:pressed {
                background-color: #d6e1ea;
            }
            """
        )
        button = box.button(QMessageBox.Ok)
        if button is not None:
            button.setText("确定")
        return box

    def _show_warning(self, title: str, text: str) -> None:
        self._build_message_box(QMessageBox.Warning, title, text).exec()

    def _show_info(self, title: str, text: str) -> None:
        self._build_message_box(QMessageBox.Information, title, text).exec()

    def _show_critical(self, title: str, text: str) -> None:
        self._build_message_box(QMessageBox.Critical, title, text).exec()

    def _build_ui(self) -> None:
        self._setup_license_menu()

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
        self.status_label.setMinimumHeight(28)

        footer = QHBoxLayout()
        footer.setContentsMargins(8, 0, 8, 0)
        footer.addWidget(self.status_label, 1)
        self._license_status_label = QLabel("")
        self._license_status_label.setObjectName("footerStatus")
        self._license_status_label.setMinimumHeight(28)
        self._update_license_status_label()
        footer.addWidget(self._license_status_label)

        footer_widget = QWidget()
        footer_widget.setLayout(footer)
        root_layout.addWidget(footer_widget)

        self._apply_styles()

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("header")
        frame.setFixedHeight(64)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 2, 12, 2)

        title = QLabel("机械手自然语言编程控制系统")
        title.setObjectName("title")
        self.header_status = None
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addStretch(1)

        # 授权状态快捷按钮
        self._license_btn = QPushButton("授权")
        self._license_btn.setFlat(True)
        self._license_btn.setFixedHeight(28)
        self._license_btn.clicked.connect(self._show_license_dialog)
        layout.addWidget(self._license_btn)

        return frame

    # ---------- 授权管理 ----------

    def _setup_license_menu(self) -> None:
        menu_bar = self.menuBar()
        license_menu = menu_bar.addMenu("授权(&L)")

        show_action = QAction("授权管理(&M)...", self)
        show_action.triggered.connect(self._show_license_dialog)
        license_menu.addAction(show_action)

    def _init_api_clients(self) -> None:
        """初始化 API 客户端，按优先级：订阅 > 自带 Key > 免费"""
        self._deepseek_client = None
        self._use_license_voice = False

        try:
            status = self.license_manager.check_status()
            if status.valid:
                # 订阅模式 DeepSeek
                if status.deepseek_enabled:
                    try:
                        from .deepseek_client import DeepSeekClient
                        self._deepseek_client = DeepSeekClient.from_license(self.license_manager)
                    except Exception:
                        pass

                # 订阅模式语音
                if status.voice_enabled:
                    self._use_license_voice = True

                self._update_license_status_label()
                # 预打开麦克风流（无论订阅还是本地模式，都零延迟）
                self._ensure_mic_stream()
                return
        except Exception:
            pass

        # 降级到自带 Key（仅测试模式，发布版本设 ALLOW_LOCAL_KEY=false）
        if os.getenv("ALLOW_LOCAL_KEY", "true").lower() == "true":
            try:
                from .deepseek_client import DeepSeekClient
                self._deepseek_client = DeepSeekClient.from_env()
            except Exception:
                self._deepseek_client = None

        self._update_license_status_label()
        self._ensure_mic_stream()

    def _show_license_dialog(self) -> None:
        dlg = LicenseDialog(self.license_manager, self)
        dlg.license_activated.connect(self._on_license_changed)
        dlg.license_deactivated.connect(self._on_license_changed)
        dlg.exec()

    def _on_license_changed(self) -> None:
        self._init_api_clients()

    def _update_license_status_label(self) -> None:
        try:
            status = self.license_manager.check_status()
        except Exception:
            status = None

        if status and status.valid:
            type_names = {
                "trial": "试用版",
                "monthly": "月度订阅",
                "yearly": "年度订阅",
                "lifetime": "永久授权",
            }
            label = type_names.get(status.license_type, status.license_type)
            self._license_status_label.setText(f"[{label}]")
            self._license_status_label.setStyleSheet("color: green;")
            self._license_btn.setText(label)
            self._license_btn.setStyleSheet("color: green;")
        else:
            self._license_status_label.setText("[未授权]")
            self._license_status_label.setStyleSheet("color: gray;")
            self._license_btn.setText("授权")
            self._license_btn.setStyleSheet("")

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
        layout.setSpacing(8)

        link_bar = QGroupBox("连接与反馈")
        link_bar.setObjectName("panel")
        link_layout = QHBoxLayout(link_bar)
        link_layout.setContentsMargins(10, 6, 10, 6)
        link_layout.addWidget(QLabel("控制器地址:"))
        self.host_edit = QLineEdit("192.168.1.11")
        self.host_edit.setMaximumWidth(220)
        link_layout.addWidget(self.host_edit)
        link_layout.addWidget(QLabel("控制器类型:"))
        self.controller_combo = QComboBox()
        self.controller_combo.addItems(["真实控制器", "模拟控制器"])
        self.controller_combo.setMaximumWidth(180)
        link_layout.addWidget(self.controller_combo)
        link_layout.addWidget(QLabel("协议:"))
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["当前简化协议", "最终标准协议", "V3.0 Modbus TCP"])
        self.protocol_combo.setCurrentText("V3.0 Modbus TCP")
        self.protocol_combo.setMaximumWidth(180)
        link_layout.addWidget(self.protocol_combo)
        link_layout.addWidget(QLabel("连接状态:"))
        self.connection_label = QLabel("检测中...")
        link_layout.addWidget(self.connection_label, 1)
        self.monitor_label = QLabel("未启动")
        check_btn = QPushButton("检测连接")
        check_btn.clicked.connect(self._check_connection)
        read_btn = QPushButton("读取反馈")
        read_btn.clicked.connect(self._read_feedback)
        link_layout.addWidget(check_btn)
        link_layout.addWidget(read_btn)
        layout.addWidget(link_bar)

        flow_group = QGroupBox("流程执行")
        flow_group.setObjectName("panel")
        flow_layout = QVBoxLayout(flow_group)
        flow_head = QHBoxLayout()
        flow_head.addWidget(QLabel("流程:"))
        self.flow_combo = QComboBox()
        self.flow_combo.currentTextChanged.connect(self._on_flow_selected)
        flow_head.addWidget(self.flow_combo, 1)
        flow_layout.addLayout(flow_head)

        flow_body = QWidget()
        flow_body_layout = QHBoxLayout(flow_body)
        flow_body_layout.setContentsMargins(0, 0, 0, 0)
        flow_body_layout.setSpacing(8)

        self.flow_step_tree = QTreeWidget()
        self.flow_step_tree.setHeaderLabels(["步骤", "状态"])
        flow_body_layout.addWidget(self.flow_step_tree, 1)

        flow_side = QWidget()
        flow_side_layout = QVBoxLayout(flow_side)
        flow_side_layout.setContentsMargins(0, 0, 0, 0)
        flow_side_layout.setSpacing(8)
        flow_info_form = QFormLayout()
        self.flow_name_label = QLabel("-")
        self.flow_progress_label = QLabel("0 / 0")
        self.flow_status_label = QLabel(self.flow_status)
        self.flow_step_label = QLabel(self.flow_current_step)
        for label, widget in [
            ("当前流程", self.flow_name_label),
            ("当前步骤", self.flow_progress_label),
            ("流程状态", self.flow_status_label),
            ("执行模板", self.flow_step_label),
        ]:
            flow_info_form.addRow(label + ":", widget)
        flow_side_layout.addLayout(flow_info_form)
        flow_btn_layout = QGridLayout()
        start_flow_btn = QPushButton("开始流程")
        start_flow_btn.clicked.connect(self._start_flow)
        step_flow_btn = QPushButton("单步执行")
        step_flow_btn.clicked.connect(self._step_flow)
        stop_flow_btn = QPushButton("停止流程")
        stop_flow_btn.clicked.connect(self._stop_flow)
        reset_flow_btn = QPushButton("重置流程")
        reset_flow_btn.clicked.connect(self._reset_flow)
        flow_btn_layout.addWidget(start_flow_btn, 0, 0)
        flow_btn_layout.addWidget(step_flow_btn, 0, 1)
        flow_btn_layout.addWidget(stop_flow_btn, 1, 0)
        flow_btn_layout.addWidget(reset_flow_btn, 1, 1)
        flow_side_layout.addLayout(flow_btn_layout)
        flow_side_layout.addStretch(1)
        flow_body_layout.addWidget(flow_side, 1)
        flow_layout.addWidget(flow_body)

        self.command_group = QGroupBox("固定指令执行页")
        self.command_group.setObjectName("panel")
        self.command_group.setMinimumHeight(300)
        command_box_layout = QVBoxLayout(self.command_group)
        command_toolbar = QHBoxLayout()
        command_toolbar.addWidget(QLabel("筛选:"))
        self.command_filter_edit = QLineEdit()
        self.command_filter_edit.setPlaceholderText("输入名称 / 关键词")
        self.command_filter_edit.textChanged.connect(self._refresh_command_cards)
        command_toolbar.addWidget(self.command_filter_edit, 1)
        command_toolbar.addWidget(QLabel("类型:"))
        self.command_type_combo = QComboBox()
        self.command_type_combo.addItems(["全部", "参数型", "固定型"])
        self.command_type_combo.currentIndexChanged.connect(self._refresh_command_cards)
        command_toolbar.addWidget(self.command_type_combo)
        self.command_count_label = QLabel("0 项")
        command_toolbar.addWidget(self.command_count_label)
        command_box_layout.addLayout(command_toolbar)
        command_scroll = QScrollArea()
        command_scroll.setWidgetResizable(True)
        command_scroll.setFrameShape(QFrame.Shape.NoFrame)
        command_scroll_widget = QWidget()
        command_layout = QGridLayout(command_scroll_widget)
        command_layout.setHorizontalSpacing(8)
        command_layout.setVerticalSpacing(8)
        command_layout.setContentsMargins(0, 0, 0, 0)
        self.command_grid_layout = command_layout
        command_scroll.setWidget(command_scroll_widget)
        command_box_layout.addWidget(command_scroll)

        nlp_group = QGroupBox("自然语言执行")
        nlp_group.setObjectName("panel")
        nlp_layout = QHBoxLayout(nlp_group)
        nlp_layout.setContentsMargins(10, 8, 10, 8)
        nlp_layout.setSpacing(10)

        nlp_left = QWidget()
        nlp_left_layout = QVBoxLayout(nlp_left)
        nlp_left_layout.setContentsMargins(0, 0, 0, 0)
        nlp_left_layout.setSpacing(6)
        nlp_head = QHBoxLayout()
        nlp_head.addWidget(QLabel("输入文本:"))
        self.nlp_use_deepseek_check = QCheckBox("使用DeepSeek")
        self.nlp_use_deepseek_check.setChecked(True)
        nlp_head.addWidget(self.nlp_use_deepseek_check)
        nlp_head.addWidget(QLabel("麦克风:"))
        self.mic_device_combo = QComboBox()
        self.mic_device_combo.setMinimumWidth(220)
        nlp_head.addWidget(self.mic_device_combo)
        refresh_mic_btn = QPushButton("刷新设备")
        refresh_mic_btn.setFixedWidth(100)
        refresh_mic_btn.clicked.connect(self._refresh_microphone_devices)
        nlp_head.addWidget(refresh_mic_btn)
        nlp_head.addStretch(1)
        nlp_left_layout.addLayout(nlp_head)
        self.nlp_input_edit = QTextEdit()
        self.nlp_input_edit.setPlaceholderText("例如：执行流程11 / 回零 / 启动 / 把机械手移动到位置A")
        self.nlp_input_edit.setMinimumHeight(110)
        nlp_left_layout.addWidget(self.nlp_input_edit)
        nlp_btn_layout = QHBoxLayout()
        self.nlp_parse_btn = QPushButton("解析文本")
        self.nlp_parse_btn.clicked.connect(self._parse_nlp_text)
        self.nlp_parse_btn.setFixedWidth(120)
        self.nlp_execute_btn = QPushButton("执行")
        self.nlp_execute_btn.clicked.connect(self._execute_nlp_text)
        self.nlp_execute_btn.setFixedWidth(120)
        self.nlp_execute_btn.setProperty("klass", "green")
        self.mic_toggle_btn = QPushButton("开始录音")
        self.mic_toggle_btn.clicked.connect(self._toggle_microphone_recording)
        self.mic_toggle_btn.setFixedWidth(120)
        self.nlp_clear_btn = QPushButton("清空")
        self.nlp_clear_btn.clicked.connect(self._clear_nlp_text)
        self.nlp_clear_btn.setFixedWidth(120)
        nlp_btn_layout.addWidget(self.nlp_parse_btn)
        nlp_btn_layout.addWidget(self.mic_toggle_btn)
        nlp_btn_layout.addWidget(self.nlp_clear_btn)
        nlp_btn_layout.addWidget(self.nlp_execute_btn)
        nlp_btn_layout.addStretch(1)
        nlp_left_layout.addLayout(nlp_btn_layout)

        nlp_layout.addWidget(nlp_left, 3)

        nlp_right = QGroupBox("解析结果")
        nlp_right.setObjectName("subPanel")
        nlp_right_layout = QVBoxLayout(nlp_right)
        self.nlp_result_edit = QTextEdit()
        self.nlp_result_edit.setReadOnly(True)
        nlp_right_layout.addWidget(self.nlp_result_edit)
        nlp_layout.addWidget(nlp_right, 2)

        execute_tabs = QTabWidget()
        execute_tabs.setObjectName("panel")
        execute_tabs.addTab(self.command_group, "单次执行")
        execute_tabs.addTab(flow_group, "流程执行")
        execute_tabs.addTab(nlp_group, "自然语言执行")
        layout.addWidget(execute_tabs, 1)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        self.robot_info = self._make_info_group("机械手状态")
        self.robot_info.setObjectName("panel")
        self.robot_info.setMaximumHeight(280)
        self.summary_info = self._make_info_group("执行摘要")
        self.summary_info.setObjectName("panel")
        self.summary_info.setMaximumHeight(280)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["任务ID", "指令码", "名称", "指令类型", "结果"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.history_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        history_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        history_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        history_group = QGroupBox("最近执行记录")
        history_group.setObjectName("panel")
        history_group.setMaximumHeight(280)
        hist_layout = QVBoxLayout(history_group)
        hist_layout.addWidget(self.history_table)

        left_stack = QWidget()
        left_stack_layout = QVBoxLayout(left_stack)
        left_stack_layout.setContentsMargins(0, 0, 0, 0)
        left_stack_layout.setSpacing(10)
        left_stack_layout.addWidget(self.robot_info, 1)
        bottom_layout.addWidget(left_stack, 17)
        bottom_layout.addWidget(self.summary_info, 13)
        bottom_layout.addWidget(history_group, 34)
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

        flow_manage_group = QGroupBox("流程管理")
        flow_manage_group.setObjectName("subPanel")
        flow_manage_layout = QVBoxLayout(flow_manage_group)
        flow_name_form = QFormLayout()
        self.flow_manage_name_edit = QLineEdit()
        flow_name_form.addRow("流程名称:", self.flow_manage_name_edit)
        flow_manage_layout.addLayout(flow_name_form)

        flow_manage_split = QWidget()
        flow_manage_split_layout = QHBoxLayout(flow_manage_split)
        flow_manage_split_layout.setContentsMargins(0, 0, 0, 0)
        flow_manage_split_layout.setSpacing(8)

        flow_left = QWidget()
        flow_left_layout = QVBoxLayout(flow_left)
        flow_left_layout.setContentsMargins(0, 0, 0, 0)
        flow_left_layout.setSpacing(6)
        flow_left_layout.addWidget(QLabel("已有流程"))
        self.flow_manage_tree = QTreeWidget()
        self.flow_manage_tree.setHeaderLabels(["流程名称", "步数"])
        self.flow_manage_tree.itemSelectionChanged.connect(self._on_manage_flow_selected)
        flow_left_layout.addWidget(self.flow_manage_tree)
        flow_manage_split_layout.addWidget(flow_left, 1)

        flow_middle = QWidget()
        flow_middle_layout = QVBoxLayout(flow_middle)
        flow_middle_layout.setContentsMargins(0, 0, 0, 0)
        flow_middle_layout.setSpacing(6)
        flow_middle_layout.addWidget(QLabel("流程步骤"))
        self.flow_step_manage_tree = QTreeWidget()
        self.flow_step_manage_tree.setHeaderLabels(["步骤模板"])
        flow_middle_layout.addWidget(self.flow_step_manage_tree)
        flow_step_button_layout = QGridLayout()
        add_step_btn = QPushButton("添加步骤")
        add_step_btn.clicked.connect(self._add_flow_step)
        remove_step_btn = QPushButton("移除步骤")
        remove_step_btn.clicked.connect(self._remove_flow_step)
        step_up_btn = QPushButton("上移")
        step_up_btn.clicked.connect(self._move_flow_step_up)
        step_down_btn = QPushButton("下移")
        step_down_btn.clicked.connect(self._move_flow_step_down)
        flow_step_button_layout.addWidget(add_step_btn, 0, 0)
        flow_step_button_layout.addWidget(remove_step_btn, 0, 1)
        flow_step_button_layout.addWidget(step_up_btn, 1, 0)
        flow_step_button_layout.addWidget(step_down_btn, 1, 1)
        flow_middle_layout.addLayout(flow_step_button_layout)
        flow_manage_split_layout.addWidget(flow_middle, 1)

        flow_right = QWidget()
        flow_right_layout = QVBoxLayout(flow_right)
        flow_right_layout.setContentsMargins(0, 0, 0, 0)
        flow_right_layout.setSpacing(6)
        flow_right_layout.addWidget(QLabel("可选模板"))
        self.flow_available_tree = QTreeWidget()
        self.flow_available_tree.setHeaderLabels(["模板名称", "类型"])
        flow_right_layout.addWidget(self.flow_available_tree)
        flow_action_layout = QGridLayout()
        new_flow_btn = QPushButton("新增流程")
        new_flow_btn.clicked.connect(self._new_flow)
        save_flow_btn = QPushButton("保存流程")
        save_flow_btn.clicked.connect(self._save_flow)
        delete_flow_btn = QPushButton("删除流程")
        delete_flow_btn.clicked.connect(self._delete_flow)
        flow_action_layout.addWidget(new_flow_btn, 0, 0)
        flow_action_layout.addWidget(save_flow_btn, 0, 1)
        flow_action_layout.addWidget(delete_flow_btn, 1, 0, 1, 2)
        flow_right_layout.addLayout(flow_action_layout)
        flow_manage_split_layout.addWidget(flow_right, 1)

        flow_manage_layout.addWidget(flow_manage_split)

        right_tabs = QTabWidget()
        right_tabs.setObjectName("panel")
        right_tabs.addTab(preview_group, "JSON预览")
        right_tabs.addTab(config_group, "系统参数")
        right_tabs.addTab(avoidance_group, "安全中间点")
        right_tabs.addTab(flow_manage_group, "流程管理")

        bottom_layout.addWidget(left, 19)
        bottom_layout.addWidget(middle, 19)
        bottom_layout.addWidget(right_tabs, 26)
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
            QScrollArea {
                background: transparent;
                border: 0;
            }
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
            QTabWidget::pane {
                background: rgba(255,255,255,0.22);
                border: 1px solid #2d2d2d;
                border-radius: 6px;
                top: -1px;
            }
            QTabBar::tab {
                min-width: 88px;
                padding: 7px 12px;
                margin-right: 4px;
                background: rgba(255,255,255,0.55);
                border: 1px solid #4a4a4a;
                border-bottom: 0;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: rgba(255,255,255,0.9);
            }
            QTabBar::tab:hover:!selected {
                background: rgba(255,255,255,0.72);
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
                border-radius: 4px;
                padding: 3px 5px;
            }
            QTextEdit, QTreeWidget, QTableWidget {
                background: rgba(255,255,255,0.74);
            }
            QHeaderView::section {
                background: rgba(255,255,255,0.9);
                border: 1px solid #6a6a6a;
                padding: 4px 6px;
                font-weight: 600;
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
                background: rgba(255,255,255,0.4);
                gridline-color: #555;
            }
        """)

    def _make_info_group(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        layout = QFormLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setVerticalSpacing(4)
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
            self._show_warning("保存失败", "系统范围必须是数字。")
            self._append_log("后台", "保存范围", "失败", "系统范围必须是数字")
            return
        validation_error = validate_system_config(config)
        if validation_error:
            self._show_warning("保存失败", validation_error)
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
            self._show_warning("保存失败", "中间点参数必须是数字。")
            self._append_log("后台", "保存中间点", "失败", "中间点参数必须是数字")
            return
        validation_error = validate_safe_point(point)
        if validation_error:
            self._show_warning("保存失败", validation_error)
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
            self._show_warning("无法删除", "当前没有选中的中间点。")
            self._append_log("后台", "删除中间点", "失败", "当前没有选中的中间点")
            return
        safe_points = dict(self.avoidance_config.safe_points)
        if key not in safe_points:
            self._show_warning("无法删除", f"中间点不存在: {key}")
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
        self._refresh_flow_combo()
        self._refresh_flow_manage_tree()
        self._refresh_flow_available_tree()
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
        filter_text = self.command_filter_edit.text().strip().lower() if hasattr(self, "command_filter_edit") else ""
        type_filter = self.command_type_combo.currentText() if hasattr(self, "command_type_combo") else "全部"

        visible_records: list[QueryRecord] = []
        for record in sorted(self.table.values(), key=lambda r: r.query_key):
            if type_filter == "参数型" and record.template_type != "parametric":
                continue
            if type_filter == "固定型" and record.template_type != "fixed":
                continue
            haystack = " ".join([record.query_key, record.keywords, record.description, record.function_name]).lower()
            if filter_text and filter_text not in haystack:
                continue
            visible_records.append(record)

        if hasattr(self, "command_count_label"):
            self.command_count_label.setText(f"{len(visible_records)} 项")

        for idx, record in enumerate(visible_records):
            standard_command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
            card = QGroupBox(record.query_key)
            card.setObjectName("subPanel")
            card.setMinimumWidth(170)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(3)
            meta_top = QLabel(f"{standard_command.cmd} | {standard_command.code}")
            meta_top.setWordWrap(True)
            layout.addWidget(meta_top)
            meta_kind = QLabel("固定型" if record.template_type == "fixed" else "参数型")
            meta_kind.setObjectName("tip")
            layout.addWidget(meta_kind)
            if record.template_type == "parametric":
                pos_text = (
                    f"X {self._fmt(record.registers[0])}  "
                    f"Y {self._fmt(record.registers[1])}\n"
                    f"Z {self._fmt(record.registers[2])}"
                )
                pos_label = QLabel(pos_text)
                pos_label.setObjectName("tip")
                pos_label.setWordWrap(True)
                layout.addWidget(pos_label)
            btn = QPushButton("执行")
            btn.setProperty("klass", "yellow" if record.template_type == "fixed" else "green")
            btn.setMinimumHeight(28)
            btn.clicked.connect(lambda _=False, key=record.query_key: self._send_record(key))
            layout.addWidget(btn)
            self.command_grid_layout.addWidget(card, idx // 3, idx % 3)

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
        rows = self.history or [{"task": 1001, "code": 1001, "name": "-", "type": "参数型指令", "result": "待执行"}]
        for row_index, row in enumerate(rows):
            self.history_table.insertRow(row_index)
            for col_index, key in enumerate(["task", "code", "name", "type", "result"]):
                self.history_table.setItem(row_index, col_index, QTableWidgetItem(str(row[key])))

    def _refresh_flow_combo(self) -> None:
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
        if not hasattr(self, "flow_manage_tree"):
            return
        self.flow_manage_tree.clear()
        flow_names = self.service.list_flow_names()
        for flow_name in flow_names:
            flow = self.service.get_flow(flow_name)
            item = QTreeWidgetItem([flow.name, str(len(flow.steps))])
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
        if not hasattr(self, "flow_available_tree"):
            return
        self.flow_available_tree.clear()
        for record in sorted(self.table.values(), key=lambda r: r.query_key):
            kind = "固定" if record.template_type == "fixed" else "参数"
            self.flow_available_tree.addTopLevelItem(QTreeWidgetItem([record.query_key, kind]))

    def _refresh_flow_step_manage_tree(self, steps: list[str] | None = None) -> None:
        if not hasattr(self, "flow_step_manage_tree"):
            return
        self.flow_step_manage_tree.clear()
        for step in steps or []:
            self.flow_step_manage_tree.addTopLevelItem(QTreeWidgetItem([step]))

    def _on_flow_selected(self, name: str) -> None:
        self.current_flow_name = name or None
        if not self.flow_running:
            self.flow_step_index = 0
            self.flow_current_step = "-"
            self.flow_status = "空闲"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()

    def _on_manage_flow_selected(self) -> None:
        items = self.flow_manage_tree.selectedItems()
        if not items:
            return
        flow_name = items[0].text(0)
        if flow_name not in self.service.flows:
            return
        self.current_flow_manage_name = flow_name
        self._load_flow_into_manage_form(self.service.get_flow(flow_name))

    def _load_flow_into_manage_form(self, flow) -> None:
        self.flow_manage_name_edit.setText(flow.name)
        self._refresh_flow_step_manage_tree(list(flow.steps))

    def _new_flow(self) -> None:
        self.current_flow_manage_name = None
        if hasattr(self, "flow_manage_name_edit"):
            self.flow_manage_name_edit.setText("")
        self._refresh_flow_step_manage_tree([])
        self.status_label.setText("已创建空白流程。")
        self._append_log("后台", "新增流程", "成功", "已创建空白流程")

    def _collect_flow_steps(self) -> list[str]:
        steps: list[str] = []
        for index in range(self.flow_step_manage_tree.topLevelItemCount()):
            item = self.flow_step_manage_tree.topLevelItem(index)
            steps.append(item.text(0))
        return steps

    def _add_flow_step(self) -> None:
        items = self.flow_available_tree.selectedItems()
        if not items:
            self._show_warning("未选择模板", "请先从可选模板中选择一个步骤模板。")
            return
        step_name = items[0].text(0)
        self.flow_step_manage_tree.addTopLevelItem(QTreeWidgetItem([step_name]))
        self._append_log("后台", "添加流程步骤", "成功", step_name)

    def _remove_flow_step(self) -> None:
        item = self.flow_step_manage_tree.currentItem()
        if item is None:
            self._show_warning("未选择步骤", "请先选择要移除的流程步骤。")
            return
        index = self.flow_step_manage_tree.indexOfTopLevelItem(item)
        self.flow_step_manage_tree.takeTopLevelItem(index)
        self._append_log("后台", "移除流程步骤", "成功", item.text(0))

    def _move_flow_step_up(self) -> None:
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
        flow_name = self.flow_manage_name_edit.text().strip()
        steps = self._collect_flow_steps()
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
        new_flow = FlowDefinition(name=flow_name, steps=tuple(steps))
        self.service.save_flow(new_flow)
        self.current_flow_manage_name = flow_name
        self.current_flow_name = flow_name if self.current_flow_name in {None, "", flow_name} else self.current_flow_name
        self._refresh_flow_combo()
        self._refresh_flow_manage_tree()
        self.status_label.setText(f"已保存流程: {flow_name}")
        self._append_log("后台", "保存流程", "成功", f"{flow_name} | {len(steps)} 步")

    def _delete_flow(self) -> None:
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
        if self.header_status is not None:
            self.header_status.setText("第一版：任务运行中" if self.busy == "运行中" else "第一版：固定指令 + 后台模板")
        self._refresh_overall_state_indicator()

    def _refresh_overall_state_indicator(self) -> None:
        state_text, color, detail = self._compute_overall_state()
        self.status_light_label.setText(f"<span style='color:{color};'>●</span> {state_text}")
        self.status_light_detail_label.setText(detail)

    def _compute_overall_state(self) -> tuple[str, str, str]:
        monitor_offline = self.monitor_label.text() == "实时监控离线" or "失败" in self.connection_label.text()
        if monitor_offline:
            return "离线", "#7a7a7a", f"{self.monitor_label.text()}\n未连接或无实时反馈"
        if self.alarm_code not in {"0", "ERR_000"}:
            return "异常", "#ef5a5a", f"{self.monitor_label.text()}\n{self.alarm_text or '报警或通讯故障'}"
        if self.busy == "暂停" or self.run_state == "暂停":
            return "暂停", "#ffe46d", f"{self.monitor_label.text()}\n系统处于暂停状态"
        if self.busy == "运行中" or self.run_state == "运行中":
            return "运行中", "#4f7cff", f"{self.monitor_label.text()}\n下位机正在执行任务"
        return "空闲", "#42d84a", f"{self.monitor_label.text()}\n系统已连接，当前空闲"

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
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "category": category,
            "action": action,
            "result": result,
            "detail": detail,
        }
        if threading.current_thread() is threading.main_thread():
            self._append_log_entry(entry)
        else:
            self._run_on_main_thread(lambda e=entry: self._append_log_entry(e))

    def _append_log_entry(self, entry: dict) -> None:
        self.logs.insert(0, entry)
        self.logs = self.logs[:200]
        self._refresh_logs()

    def _build_voice_nlp_adapter(self) -> VoiceNlpAdapter:
        adapter = VoiceNlpAdapter(self.table, self.service.list_flow_names())
        if self._deepseek_client:
            adapter.set_deepseek_client(self._deepseek_client)
        return adapter

    def _set_nlp_result_plan(self, plan: VoiceNlpPlan) -> None:
        self.nlp_last_plan = plan
        self.nlp_result_edit.setPlainText(json.dumps(plan.to_preview_dict(), ensure_ascii=False, indent=2))

    def _set_nlp_parse_busy(self, busy: bool) -> None:
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
            return self._build_voice_nlp_adapter().parse(
                text,
                use_deepseek=use_deepseek,
            )

        def on_result(result):
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
        use_deepseek = self.nlp_use_deepseek_check.isChecked()
        self._set_nlp_execute_busy(True)
        self.status_label.setText("自然语言执行准备中，请稍候...")

        def work():
            return self._build_voice_nlp_adapter().parse(
                text,
                use_deepseek=use_deepseek,
            )

        def on_result(result):
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
        self.nlp_input_edit.clear()
        self.nlp_result_edit.clear()
        self.nlp_last_plan = None
        self.status_label.setText("自然语言输入已清空。")

    def _execute_nlp_plan(self, plan: VoiceNlpPlan) -> None:
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

    def _create_iflytek_client(self):
        # 订阅模式：无需本地凭证
        if self._use_license_voice:
            return True

        from .iflytek_iat import IFlytekIATConfig, IFlytekRTASRError, expected_env_locations

        try:
            IFlytekIATConfig.from_env()
            if importlib.util.find_spec("xfyunsdkspeech") is None:
                raise RuntimeError("未安装 xfyunsdkspeech，请先安装讯飞官方 SDK。")
            return True
        except IFlytekRTASRError as exc:
            env_locations = " / ".join(str(path) for path in expected_env_locations())
            raise RuntimeError(
                f"{exc}\n请在以下任一文件配置讯飞凭证后重试：\n{env_locations}\n"
                "需要的键：IFLYTEK_APP_ID、IFLYTEK_API_KEY、IFLYTEK_API_SECRET"
            ) from exc

    def _run_iflytek_worker(self, args: list[str]) -> str:
        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        debug_pcm = log_dir / f"voice_debug_{timestamp}.pcm"
        worker_log = log_dir / f"iflytek_worker_{timestamp}.log"
        result_path = log_dir / f"iflytek_result_{timestamp}.json"

        cmd = self._build_iflytek_worker_command(args, debug_pcm, result_path)

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            cwd=str(self.runtime_root),
        )

        stderr_text = (completed.stderr or "").strip()
        worker_log.write_text(
            json.dumps(
                {
                    "cmd": cmd,
                    "returncode": completed.returncode,
                    "stdout": (completed.stdout or ""),
                    "stderr": (completed.stderr or ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if not result_path.exists():
            detail = stderr_text or "讯飞 worker 未返回结果。"
            raise RuntimeError(f"{detail}\n调试日志: {worker_log}")

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"讯飞 worker 返回结果文件不是合法 JSON。\n{stderr_text}\n调试日志: {worker_log}") from exc

        if not payload.get("ok"):
            raise RuntimeError(f"{payload.get('error', '讯飞识别失败。')}\n调试日志: {worker_log}")
        return str(payload.get("text", "")).strip()

    def _build_iflytek_worker_command(self, args: list[str], debug_pcm: Path, result_path: Path) -> list[str]:
        license_args: list[str] = []
        if self._use_license_voice:
            license_args = ["--use-license", "--cache-dir", str(self.runtime_root / "data")]

        if getattr(sys, "frozen", False):
            return [sys.executable, "--iflytek-worker", *args, *license_args, "--debug-save-path", str(debug_pcm), "--result-path", str(result_path)]
        return [
            sys.executable,
            str(self.runtime_root / "gui_main.py"),
            "--iflytek-worker",
            *args,
            *license_args,
            "--debug-save-path",
            str(debug_pcm),
            "--result-path",
            str(result_path),
        ]

    def _refresh_microphone_devices(self) -> None:
        if not hasattr(self, "mic_device_combo"):
            return
        previous = self.mic_device_combo.currentData() if self.mic_device_combo.count() else None
        self.mic_device_combo.clear()
        self.mic_device_combo.addItem("系统默认麦克风", None)
        try:
            import sounddevice as sd

            added = 0
            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                name = str(device.get("name", f"设备{index}")).strip() or f"设备{index}"
                self.mic_device_combo.addItem(f"{index}: {name}", index)
                added += 1
            if previous is not None:
                restore_index = self.mic_device_combo.findData(previous)
                if restore_index >= 0:
                    self.mic_device_combo.setCurrentIndex(restore_index)
            self._append_log("语音", "刷新麦克风设备", "成功", f"检测到 {added} 个输入设备")
        except Exception as exc:
            self._append_log("语音", "刷新麦克风设备", "失败", str(exc))

    def _selected_microphone_device(self) -> int | None:
        if not hasattr(self, "mic_device_combo"):
            return None
        data = self.mic_device_combo.currentData()
        return int(data) if data is not None else None

    def _transcribe_audio_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            str(self.runtime_root),
            "音频文件 (*.pcm *.wav *.mp3 *.m4a);;所有文件 (*.*)",
        )
        if not file_path:
            return
        try:
            self._create_iflytek_client()
            text = self._run_iflytek_worker(["--mode", "audio", "--input", file_path])
            self.nlp_input_edit.setPlainText(text)
            self.status_label.setText(f"音频识别完成: {Path(file_path).name}")
            self._append_log("语音", "导入音频识别", "成功", f"{Path(file_path).name} -> {text or '-'}")
        except Exception as exc:
            self._show_critical("音频识别失败", str(exc))
            self._append_log("语音", "导入音频识别", "失败", str(exc))

    def _transcribe_microphone(self) -> None:
        try:
            self._create_iflytek_client()
            args = ["--mode", "mic", "--duration", "4.0"]
            selected_device = self._selected_microphone_device()
            if selected_device is not None:
                args.extend(["--device", str(selected_device)])
            text = self._run_iflytek_worker(args)
            self.nlp_input_edit.setPlainText(text)
            self.status_label.setText("麦克风识别完成")
            self._append_log("语音", "麦克风识别", "成功", text or "-")
        except Exception as exc:
            self._show_critical("麦克风识别失败", str(exc))
            self._append_log("语音", "麦克风识别", "失败", str(exc))

    def _toggle_microphone_recording(self) -> None:
        # 持久线程：正在采集 → 停止
        if self._proxy_mic_capturing and self._mic_recorder_thread is not None:
            self._mic_recorder_thread.stop_capturing()
            self._proxy_mic_capturing = False
            self.mic_toggle_btn.setEnabled(False)
            self.status_label.setText("正在停止录音并等待识别结果。")
            self._append_log("语音", "停止录音", "成功", "已发送停止信号")
            return
        # 子进程模式：停止
        if self._mic_process and self._mic_process.poll() is None:
            self._stop_microphone_recording()
            return
        self._start_microphone_recording()

    def _start_microphone_recording(self) -> None:
        if self._mic_process and self._mic_process.poll() is None:
            return
        if self._proxy_mic_capturing:
            return
        try:
            self._create_iflytek_client()

            # 优先使用持久线程（零延迟）
            if self._mic_recorder_thread is not None:
                self._mic_recorder_thread.start_capturing()
                self._proxy_mic_capturing = True
                self.mic_toggle_btn.setText("停止录音")
                self.status_label.setText("麦克风录音中，请说话...")
                self._append_log("语音", "开始录音", "成功", "零延迟模式")
            else:
                # 降级到子进程模式
                self._start_subprocess_recording()
        except Exception as exc:
            self._show_critical("开始录音失败", str(exc))
            self._append_log("语音", "开始录音", "失败", str(exc))

    def _ensure_mic_stream(self) -> None:
        """预打开麦克风流，保持待命状态，点击录音时零延迟（订阅/本地模式通用）。"""
        if self._mic_recorder_thread is not None:
            return
        try:
            import sounddevice as sd
        except ImportError:
            return

        from PySide6.QtCore import QThread as _QThread

        selected_device = self._selected_microphone_device()
        sample_rate = 16000

        class MicStreamThread(_QThread):
            audio_captured = Signal(bytes)  # 原始 PCM 数据

            def __init__(self, parent_win, sample_rate, device):
                super().__init__(parent_win)
                self._sample_rate = sample_rate
                self._device = device
                self._capturing = False
                self._shutdown = False
                self._frames = []
                self._stop_requested = False

            def start_capturing(self):
                self._frames = []
                self._capturing = True

            def stop_capturing(self):
                self._capturing = False
                self._stop_requested = True

            def shutdown(self):
                self._shutdown = True
                self._capturing = False

            def run(self):
                try:
                    def callback(indata, frames_count, time_info, status):
                        if self._capturing:
                            self._frames.append(indata.copy())
                        if self._shutdown:
                            raise sd.CallbackStop()

                    with sd.InputStream(
                        samplerate=self._sample_rate,
                        channels=1,
                        dtype='int16',
                        device=self._device,
                        callback=callback,
                    ):
                        while not self._shutdown:
                            time.sleep(0.05)
                            if self._stop_requested:
                                self._stop_requested = False
                                import numpy as np
                                captured = self._frames
                                self._frames = []
                                if captured:
                                    audio = np.concatenate(captured)
                                    self.audio_captured.emit(audio.tobytes())
                                else:
                                    self.audio_captured.emit(b'')
                except sd.CallbackStop:
                    pass
                except Exception:
                    if not self._shutdown:
                        self.audio_captured.emit(b'')

        self._mic_recorder_thread = MicStreamThread(self, sample_rate, selected_device)
        self._mic_recorder_thread.audio_captured.connect(self._on_mic_audio_captured)
        self._mic_recorder_thread.start()
        self._append_log("语音", "预热麦克风", "成功", "麦克风流已后台启动")

    def _on_mic_audio_captured(self, pcm_data: bytes) -> None:
        """录音采集完成，根据模式选择识别方式"""
        self._proxy_mic_capturing = False

        if not pcm_data:
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            self._show_critical("麦克风识别失败", "未录到音频")
            self._append_log("语音", "麦克风识别", "失败", "未录到音频")
            return

        if self._use_license_voice:
            self._recognize_via_proxy(pcm_data)
        else:
            self._recognize_via_local(pcm_data)

    def _recognize_via_proxy(self, pcm_data: bytes) -> None:
        """订阅模式：上传代理服务器识别"""
        import base64
        import requests as _requests

        self.status_label.setText("正在上传语音识别...")
        self.mic_toggle_btn.setEnabled(False)
        self.mic_toggle_btn.setText("识别中...")

        def work():
            audio_b64 = base64.b64encode(pcm_data).decode()
            token = self.license_manager.get_access_token()
            if not token:
                raise RuntimeError("授权已过期")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
            payload = {
                "audio_data": audio_b64,
                "audio_format": "pcm",
                "sample_rate": 16000,
            }
            proxy_url = f"{self.license_manager.SERVER_URL}/api/v1/proxy/voice/transcribe"
            resp = _requests.post(proxy_url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 401:
                raise RuntimeError("授权已过期")
            elif resp.status_code == 429:
                raise RuntimeError("今日语音配额已用尽")
            resp.raise_for_status()
            return resp.json().get("data", {}).get("text", "").strip()

        def on_result(result):
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            if isinstance(result, Exception):
                self._show_critical("麦克风识别失败", str(result))
                self._append_log("语音", "麦克风识别", "失败", str(result))
            else:
                self.nlp_input_edit.setPlainText(result)
                self.status_label.setText("麦克风识别完成")
                self._append_log("语音", "麦克风识别", "成功", result or "-")

        self._run_in_background(work, on_result)

    def _recognize_via_local(self, pcm_data: bytes) -> None:
        """本地模式：保存临时文件 + 调用 iflytek worker 识别"""
        import tempfile

        self.status_label.setText("正在识别语音...")
        self.mic_toggle_btn.setEnabled(False)
        self.mic_toggle_btn.setText("识别中...")

        def work():
            tmp = tempfile.NamedTemporaryFile(suffix='.pcm', delete=False)
            tmp.write(pcm_data)
            tmp_name = tmp.name
            tmp.close()
            try:
                return self._run_iflytek_worker(["--mode", "audio", "--input", tmp_name])
            finally:
                Path(tmp_name).unlink(missing_ok=True)

        def on_result(result):
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            if isinstance(result, Exception):
                self._show_critical("麦克风识别失败", str(result))
                self._append_log("语音", "麦克风识别", "失败", str(result))
            else:
                self.nlp_input_edit.setPlainText(result)
                self.status_label.setText("麦克风识别完成")
                self._append_log("语音", "麦克风识别", "成功", result or "-")

        self._run_in_background(work, on_result)

    def _start_subprocess_recording(self) -> None:
        """直连模式：子进程录音"""
        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_pcm = log_dir / f"voice_debug_{timestamp}.pcm"
        result_path = log_dir / f"iflytek_result_mic_{timestamp}.json"
        stop_flag = log_dir / f"voice_stop_{timestamp}.flag"
        if stop_flag.exists():
            stop_flag.unlink()
        selected_device = self._selected_microphone_device()
        mic_args = ["--mode", "mic", "--duration", "3600", "--stop-flag-path", str(stop_flag)]
        if selected_device is not None:
            mic_args.extend(["--device", str(selected_device)])
        cmd = self._build_iflytek_worker_command(
            mic_args,
            debug_pcm,
            result_path,
        )
        self._mic_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(self.runtime_root),
        )
        self._mic_stop_flag_path = stop_flag
        self._mic_result_path = result_path
        self.mic_toggle_btn.setText("停止录音")

        self.status_label.setText("麦克风初始化中，请稍候...")
        detail = "麦克风录音已启动（系统默认）" if selected_device is None else f"麦克风录音已启动（设备 {selected_device}）"
        self._append_log("语音", "开始录音", "成功", detail)

        # 延迟 1.5 秒后提示可以说话
        self._mic_ready_timer = QTimer(self)
        self._mic_ready_timer.setSingleShot(True)
        self._mic_ready_timer.timeout.connect(self._on_mic_ready)
        self._mic_ready_timer.start(1500)

        if self._mic_poll_timer is None:
            self._mic_poll_timer = QTimer(self)
            self._mic_poll_timer.setInterval(300)
            self._mic_poll_timer.timeout.connect(self._poll_microphone_recording)
        self._mic_poll_timer.start()

    def _on_mic_ready(self) -> None:
        """麦克风初始化完成，提示用户可以说话"""

        self.status_label.setText("麦克风录音中，请说话... 点击'停止录音'结束。")

    def _stop_microphone_recording(self) -> None:
        # 代理模式：停止采集并上传
        if self._proxy_mic_capturing and self._mic_recorder_thread is not None:
            self._mic_recorder_thread.stop_capturing()
            self._proxy_mic_capturing = False
            self.mic_toggle_btn.setEnabled(False)
            self.status_label.setText("正在停止录音并等待识别结果。")
            self._append_log("语音", "停止录音", "成功", "已发送停止信号")
            return
        # 直连模式：写停止标记给子进程
        if not self._mic_process or self._mic_process.poll() is not None:
            return
        if self._mic_stop_flag_path:
            self._mic_stop_flag_path.write_text("stop", encoding="utf-8")
        self.mic_toggle_btn.setEnabled(False)
        self.status_label.setText("正在停止录音并等待识别结果。")
        self._append_log("语音", "停止录音", "成功", "已发送停止信号")

    def _poll_microphone_recording(self) -> None:
        if not self._mic_process:
            if self._mic_poll_timer:
                self._mic_poll_timer.stop()
            return
        exit_code = self._mic_process.poll()
        if exit_code is None:
            return
        if self._mic_poll_timer:
            self._mic_poll_timer.stop()
        stdout, stderr = self._mic_process.communicate()
        self._mic_process = None
        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        worker_log = log_dir / f"iflytek_worker_mic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        worker_log.write_text(
            json.dumps(
                {
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "stop_flag": str(self._mic_stop_flag_path) if self._mic_stop_flag_path else "",
                    "returncode": exit_code,
                    "result_path": str(self._mic_result_path) if getattr(self, "_mic_result_path", None) else "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if self._mic_stop_flag_path and self._mic_stop_flag_path.exists():
            try:
                self._mic_stop_flag_path.unlink()
            except Exception:
                pass
        self._mic_stop_flag_path = None
        self.mic_toggle_btn.setEnabled(True)
        self.mic_toggle_btn.setText("开始录音")
        result_path = getattr(self, "_mic_result_path", None)
        self._mic_result_path = None
        if not result_path or not Path(result_path).exists():
            error_text = (stderr or "").strip() or "麦克风识别未返回结果。"
            error_text = f"{error_text}\n调试日志: {worker_log}"
    
            self._show_critical("麦克风识别失败", error_text)
            self._append_log("语音", "麦克风识别", "失败", error_text)
            return
        try:
            payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            message = f"麦克风识别结果文件不是合法 JSON。\n调试日志: {worker_log}"
    
            self._show_critical("麦克风识别失败", message)
            self._append_log("语音", "麦克风识别", "失败", message)
            return
        if not payload.get("ok"):
            message = f"{payload.get('error', '麦克风识别失败。')}\n调试日志: {worker_log}"
    
            self._show_critical("麦克风识别失败", message)
            self._append_log("语音", "麦克风识别", "失败", message)
            return

        text = str(payload.get("text", "")).strip()
        self.nlp_input_edit.setPlainText(text)

        self.status_label.setText("麦克风识别完成")
        self._append_log("语音", "麦克风识别", "成功", text or "-")

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
            self._show_warning("输入错误", "显示名称不能为空。")
            self._append_log("后台", "保存模板", "失败", "显示名称不能为空")
            return
        self.table[record.query_key] = record
        save_query_table_json(self.json_path, self.table)
        self.service.reload()
        self.current_key = record.query_key
        self._refresh_all()
        self.status_label.setText(f"已保存模板: {record.query_key}")
        self._append_log("后台", "保存模板", "成功", record.query_key)

    def _clone_record(self) -> None:
        record = self._collect_record()
        if not record.query_key:
            self._show_warning("无法另存为", "请先填写显示名称。")
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
        self.service.reload()
        self.current_key = clone.query_key
        self._load_record_into_form(clone)
        self._refresh_all()
        self.status_label.setText(f"已另存模板: {clone.query_key}")
        self._append_log("后台", "另存模板", "成功", clone.query_key)

    def _delete_record(self) -> None:
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
        self.service.reload()
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
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("连接", "检测连接", "失败", "地址为空")
            return
        try:
            self._disconnect_client()
            client = self._get_client(host)
            mode = "Mock" if self.controller_combo.currentText() == "模拟控制器" else "真实"
            self.connection_label.setText(f"{mode}连接成功: {host}")
            self.monitor_label.setText("实时监控运行中")
            self._refresh_overall_state_indicator()
            self._append_log("连接", "检测连接", "成功", f"{mode}连接成功: {host}")
        except Exception as exc:
            self._disconnect_client()
            self.connection_label.setText(f"连接失败: {exc}")
            self.monitor_label.setText("实时监控离线")
            self._refresh_overall_state_indicator()
            self._append_log("连接", "检测连接", "失败", str(exc))

    def _send_record(self, query_key: str) -> None:
        if self.flow_running:
            self._show_warning("流程运行中", "当前流程执行中，请先停止流程或等待流程完成。")
            self._append_log("执行", f"发送指令 {query_key}", "失败", "流程执行中，拒绝手动执行")
            return
        self._execute_query_key(query_key)

    def _execute_query_key(self, query_key: str, *, on_done: Callable[[bool], None] | None = None) -> None:
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("执行", f"发送指令 {query_key}", "失败", "地址为空")
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
        except Exception as exc:
            fallback = self.table.get(query_key)
            if isinstance(fallback, QueryRecord):
                if not self.history or self.history[0]["task"] != self.task_id:
                    self._after_send(fallback, False, str(exc))
            if on_done:
                on_done(False)
            return

        self._pause_polling()

        def work():
            client = self._get_client(host)
            if len(plan_records) > 1:
                self._append_log("执行", f"规避判断 {query_key}", "成功", plan_reason)
            results = []
            step_failed = False
            for idx, plan_record in enumerate(plan_records, start=1):
                if len(plan_records) > 1:
                    self._append_log(
                        "执行",
                        f"规避执行第{idx}步",
                        "成功",
                        f"{plan_record.query_key} | {plan_record.description or '-'}",
                    )
                feedback = self._execute_send_by_protocol(client, plan_record)
                step_ok, step_error = self._evaluate_feedback_result(feedback)
                results.append((plan_record, step_ok, step_error, feedback))
                if not step_ok:
                    step_failed = True
                    break
            return results, step_failed

        def on_result(result):
            self._resume_polling()
            if isinstance(result, Exception):
                self._disconnect_client()
                fallback = plan_records[0] if plan_records else self.table.get(query_key)
                if isinstance(fallback, QueryRecord):
                    if not self.history or self.history[0]["task"] != self.task_id:
                        self._after_send(fallback, False, str(result))
                if on_done:
                    on_done(False)
                return
            results, step_failed = result
            for plan_record, step_ok, step_error, feedback in results:
                self._after_send(plan_record, step_ok, step_error, feedback)
            if on_done:
                on_done(not step_failed)

        self._run_in_background(work, on_result)

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

    def _execute_send_by_protocol(self, client: ControllerClient, record: QueryRecord) -> list[float]:
        if self.protocol_combo.currentText() == "V3.0 Modbus TCP":
            return self._execute_send_v30(client, record)
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
            self._wait_for_mirror_match(client, write_request, mirror_request, record.query_key)
            exec_request = self.service.build_standard_execute_trigger_write()
            client.write_vr(exec_request)
            self._append_log(
                "寄存器",
                f"写入执行触发 {record.query_key}",
                "成功",
                self._format_write_request(exec_request),
            )
            read_request = self.service.build_standard_status_read()
            values = self._wait_for_execution_complete(client, read_request, record.query_key)
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

    def _execute_send_v30(self, client: ControllerClient, record: QueryRecord) -> list[float]:
        # 1. 前置检查: BIT(243)==0 且 IEEE(34)==0或4
        precheck_ieee, precheck_bit = self.service.build_v30_precheck_reads()
        alarm_bits = client.read_modbus_bit(precheck_bit, 1)
        if alarm_bits and alarm_bits[0] != 0:
            raise RuntimeError(f"前置检查失败: BIT({precheck_bit})={alarm_bits[0]} 控制器有报警")
        status_vals = client.read_modbus_float(precheck_ieee)
        v30_status = self.service.parse_v30_status(status_vals)
        if not v30_status.can_send:
            raise RuntimeError(f"前置检查失败: IEEE(34)={v30_status.raw} 控制器未就绪")

        # 2. 构建 V3.0 命令并写入
        v30_cmd = self.service.build_v30_command_from_record(record)

        # GRIP_SET: 写BIT口控制夹爪
        if v30_cmd.func_num == -1:
            grip_bit = 20000  # BIT(20000) 对应 OUT(0)
            client.write_modbus_bit(grip_bit, [v30_cmd.io_grip])
            self._append_log("Modbus", f"夹爪控制 {record.query_key}", "成功", f"BIT({grip_bit})={v30_cmd.io_grip}")
            time.sleep(0.1)
            rt_read = self.service.build_v30_realtime_read()
            return client.read_modbus_float(rt_read)

        # WAIT_MS: 上位机本地延时
        if v30_cmd.func_num == -2:
            delay_ms = v30_cmd.ext_p1
            self._append_log("Modbus", f"等待 {record.query_key}", "成功", f"延时{delay_ms}ms")
            time.sleep(min(delay_ms / 1000.0, 2.0))
            rt_read = self.service.build_v30_realtime_read()
            return client.read_modbus_float(rt_read)

        # DOOR_CTRL: 写BIT口控制门
        if v30_cmd.func_num == -3:
            door_bit = 20001  # BIT(20001) 对应 OUT(1)
            client.write_modbus_bit(door_bit, [v30_cmd.io_door])
            self._append_log("Modbus", f"门控制 {record.query_key}", "成功", f"BIT({door_bit})={v30_cmd.io_door}")
            time.sleep(0.1)
            rt_read = self.service.build_v30_realtime_read()
            return client.read_modbus_float(rt_read)

        # 本地操作 (CHECK_IN, RESUME, AUTO_START/STOP, FIXED_FUNC): 无需发下位机
        if v30_cmd.func_num < 0:
            self._append_log("Modbus", f"本地操作 {record.query_key}", "成功", f"func={v30_cmd.func_num}")
            time.sleep(0.05)
            rt_read = self.service.build_v30_realtime_read()
            return client.read_modbus_float(rt_read)

        for wr in v30_cmd.to_func_writes():
            client.write_modbus_float(wr)
            self._append_log("Modbus", f"写入IEEE({wr.start_vr})", "成功", f"values={list(wr.values)}")

        # 3. 写触发 IEEE(32)=1
        trigger = v30_cmd.to_trigger_write()
        client.write_modbus_float(trigger)
        self._append_log("Modbus", f"写入触发 {record.query_key}", "成功", f"IEEE(32)=1")

        # 4. 轮询 IEEE(34)==4 等待完成
        status_read = self.service.build_v30_status_read()
        for _ in range(100):
            time.sleep(0.05)
            vals = client.read_modbus_float(status_read)
            st = self.service.parse_v30_status(vals)
            if st.is_complete:
                if st.has_alarm:
                    raise RuntimeError(f"V3.0完成但带报警: IEEE(34)={st.raw}")
                self._append_log("Modbus", f"执行完成 {record.query_key}", "成功", f"IEEE(34)={st.raw}")
                break
            if st.has_alarm:
                raise RuntimeError(f"V3.0执行报警: IEEE(34)={st.raw}")
        else:
            raise RuntimeError(f"V3.0执行超时: {record.query_key}")

        # 5. 读实时坐标作为反馈
        rt_read = self.service.build_v30_realtime_read()
        rt_vals = client.read_modbus_float(rt_read)
        self._append_log("Modbus", f"读取实时坐标", "成功", f"X={rt_vals[0]:.1f} Y={rt_vals[1]:.1f} Z={rt_vals[2]:.1f}")
        return rt_vals

    def _after_send(self, record: QueryRecord, ok: bool, error: str, feedback: list[float] | None = None) -> None:
        self.history.insert(0, {
            "task": self.task_id,
            "code": self.service.build_standard_command_from_record(record, task_id=self.task_id).code,
            "name": record.query_key,
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
            self._show_critical("发送失败", error)
            self._append_log("执行", f"发送指令 {record.query_key}", "失败", error)
        if ok:
            self._refresh_all()
        else:
            self._refresh_status_labels()

    def _read_feedback(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
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
            self._show_critical("读取失败", str(exc))
            self._append_log("反馈", "读取反馈", "失败", str(exc))

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _handle_system_action(self, action_key: str, *, on_done: Callable[[bool], None] | None = None) -> None:
        if self.flow_running:
            self._show_warning("流程运行中", "流程执行中不允许发送系统按钮命令。")
            self._append_log("系统", action_key, "失败", "流程执行中")
            if on_done:
                on_done(False)
            return
        if self.protocol_combo.currentText() == "V3.0 Modbus TCP":
            self._handle_system_action_v30(action_key, on_done=on_done)
            return
        if self.protocol_combo.currentText() != "最终标准协议":
            self._apply_legacy_system_action(action_key)
            self._append_log("系统", action_key, "成功", "简化协议页面动作")
            if on_done:
                on_done(True)
            return
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            self._append_log("系统", action_key, "失败", "地址为空")
            if on_done:
                on_done(False)
            return

        code = SYSTEM_COMMAND_CODES[action_key]
        action_text = next(k for k, v in SYSTEM_COMMANDS.items() if v[0] == action_key)
        command = self.service.build_standard_system_command(
            code=code,
            task_id=self.task_id,
            desc=SYSTEM_COMMANDS[action_text][1],
        )
        self._pause_polling()

        def work():
            client = self._get_client(host)
            write_request = command.to_write_request()
            client.write_vr(write_request)
            self._append_log("寄存器", f"写入系统命令 {action_key}", "成功", self._format_write_request(write_request))
            mirror_request = self.service.build_standard_mirror_ack_read()
            self._wait_for_mirror_match(client, write_request, mirror_request, action_key)
            exec_request = self.service.build_standard_execute_trigger_write()
            client.write_vr(exec_request)
            self._append_log(
                "寄存器",
                f"写入系统执行触发 {action_key}",
                "成功",
                self._format_write_request(exec_request),
            )
            read_request = self.service.build_standard_status_read()
            feedback = self._wait_for_execution_complete(client, read_request, action_key)
            return feedback

        def on_result(result):
            self._resume_polling()
            if isinstance(result, Exception):
                self._disconnect_client()
                self.status_label.setText(f"系统命令失败: {result}")
                self._show_critical("系统命令失败", str(result))
                self._append_log("系统", action_key, "失败", str(result))
                if on_done:
                    on_done(False)
                return
            feedback = result
            ok, error = self._evaluate_feedback_result(feedback)
            if ok:
                self._apply_feedback_values(None, feedback)
                self._apply_legacy_system_action(action_key, update_status=False)
                self.task_id += 1
                self.status_label.setText(SYSTEM_COMMANDS[action_text][1])
                self._refresh_status_labels()
                self._append_log("系统", action_key, "成功", f"命令码 {code}")
            else:
                self.status_label.setText(f"系统命令失败: {error}")
                self._show_critical("系统命令失败", error)
                self._append_log("系统", action_key, "失败", error)
                self._refresh_status_labels()
            if on_done:
                on_done(ok)

        self._run_in_background(work, on_result)

    def _handle_system_action_v30(self, action_key: str, *, on_done: Callable[[bool], None] | None = None) -> None:
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            if on_done:
                on_done(False)
            return
        code = SYSTEM_COMMAND_CODES[action_key]
        self._pause_polling()

        def work():
            client = self._get_client(host)
            v30_cmd = self.service.build_v30_system_command(code)
            # 本地操作 (RESUME, AUTO_START/STOP): 不发下位机
            if v30_cmd.func_num < 0:
                self._append_log("Modbus", f"本地系统命令 {action_key}", "成功", f"func={v30_cmd.func_num}")
                return []
            for wr in v30_cmd.to_func_writes():
                client.write_modbus_float(wr)
            client.write_modbus_float(v30_cmd.to_trigger_write())
            self._append_log("Modbus", f"系统命令 {action_key}", "成功", f"func={v30_cmd.func_num}")
            # 轮询完成
            status_read = self.service.build_v30_status_read()
            for _ in range(60):
                time.sleep(0.05)
                vals = client.read_modbus_float(status_read)
                st = self.service.parse_v30_status(vals)
                if st.is_complete or st.is_idle:
                    return []
                if st.has_alarm:
                    raise RuntimeError(f"V3.0系统命令报警: IEEE(34)={st.raw}")
            raise RuntimeError(f"V3.0系统命令超时: {action_key}")

        def on_result(result):
            self._resume_polling()
            if isinstance(result, Exception):
                self._disconnect_client()
                self.status_label.setText(f"系统命令失败: {result}")
                self._show_critical("系统命令失败", str(result))
                if on_done:
                    on_done(False)
                return
            self._apply_legacy_system_action(action_key, update_status=True)
            self.task_id += 1
            self.status_label.setText(f"V3.0 {action_key} 完成")
            self._refresh_status_labels()
            if on_done:
                on_done(True)

        self._run_in_background(work, on_result)

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
        if self.protocol_combo.currentText() == "V3.0 Modbus TCP" and len(values) >= 3:
            rt = self.service.parse_v30_realtime(values)
            self.result = "0"
            self.busy = "空闲"
            self.robot_x = self._fmt(rt.x)
            self.robot_y = self._fmt(rt.y)
            self.robot_z = self._fmt(rt.z)
            if len(values) >= 6:
                self.robot_r = f"{self._fmt(rt.rx)} / {self._fmt(rt.ry)} / {self._fmt(rt.rz)}"
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
        if self.protocol_combo.currentText() in ("最终标准协议", "V3.0 Modbus TCP"):
            standard_command = self.service.build_standard_command_from_record(record, task_id=self.task_id)
            if standard_command.code == 1001:
                if not (self.axis_ranges.x[0] <= record.registers[0] <= self.axis_ranges.x[1]):
                    return f"X 坐标超出范围 {self.axis_ranges.x}。"
                if not (self.axis_ranges.y[0] <= record.registers[1] <= self.axis_ranges.y[1]):
                    return f"Y 坐标超出范围 {self.axis_ranges.y}。"
                if not (self.axis_ranges.z[0] <= record.registers[2] <= self.axis_ranges.z[1]):
                    return f"Z 坐标超出范围 {self.axis_ranges.z}。"
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
            return MockZMotionVrClient(host=host, axis_ranges=self.axis_ranges.to_dict())
        return self._client_factory(host, self.resource_root)

    def _get_client(self, host: str):
        with self._client_cache_lock:
            if (
                self._cached_client is not None
                and self._cached_client_host == host
                and self._cached_client.connected
            ):
                return self._cached_client
            self._disconnect_client_locked()
            client = self._make_client(host)
            client.connect()
            self._cached_client = client
            self._cached_client_host = host
            return client

    def _disconnect_client(self) -> None:
        with self._client_cache_lock:
            self._disconnect_client_locked()

    def _disconnect_client_locked(self) -> None:
        if self._cached_client is not None:
            try:
                self._cached_client.disconnect()
            except Exception:
                pass
            self._cached_client = None
            self._cached_client_host = ""

    def closeEvent(self, event) -> None:
        if self._mic_recorder_thread is not None:
            self._mic_recorder_thread.shutdown()
            self._mic_recorder_thread.wait(3000)
            self._mic_recorder_thread = None
        self._disconnect_client()
        super().closeEvent(event)

    def _start_realtime_polling(self) -> None:
        self.realtime_timer = QTimer(self)
        self.realtime_timer.setInterval(500)
        self.realtime_timer.timeout.connect(self._poll_feedback_silent)
        self.realtime_timer.start()

    def _pause_polling(self) -> None:
        self.realtime_timer.stop()

    def _resume_polling(self) -> None:
        if hasattr(self, "realtime_timer") and self.realtime_timer is not None:
            self.realtime_timer.start()

    def _run_on_main_thread(self, callback: Callable[[], None]) -> None:
        self._main_thread_call.emit(callback)

    @staticmethod
    def _handle_main_thread_call(callback: Callable[[], None]) -> None:
        callback()

    def _run_in_background(self, work_fn: Callable, done_fn: Callable[[Any], None]) -> None:
        def wrapper():
            try:
                result = work_fn()
            except Exception as exc:
                result = exc
            self._run_on_main_thread(lambda: done_fn(result))
        thread = threading.Thread(target=wrapper, daemon=True)
        thread.start()

    def _poll_feedback_silent(self) -> None:
        if self._polling_feedback:
            return
        host = self.host_edit.text().strip()
        if not host:
            self.monitor_label.setText("未启动")
            return
        self._polling_feedback = True
        try:
            if self.protocol_combo.currentText() == "V3.0 Modbus TCP":
                rt_read = self.service.build_v30_realtime_read()
                host = self.host_edit.text().strip()
                client = self._get_client(host)
                rt_vals = client.read_modbus_float(rt_read)
                self._apply_feedback_values(None, rt_vals)
                st_read = self.service.build_v30_status_read()
                st_vals = client.read_modbus_float(st_read)
                v30_status = self.service.parse_v30_status(st_vals)
                self.busy = "空闲" if v30_status.is_idle or v30_status.is_complete else "运行中"
                if v30_status.has_alarm:
                    self.alarm_text = f"V3.0报警 IEEE(34)={v30_status.raw}"
                    self.alarm_code = f"ERR_V30_{v30_status.raw}"
                else:
                    self.alarm_text = "系统正常"
                    self.alarm_code = "ERR_000"
                self._refresh_status_labels()
            elif self.protocol_combo.currentText() == "最终标准协议":
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
            self._disconnect_client()
            self.monitor_label.setText("实时监控离线")
            self._refresh_overall_state_indicator()
            if error != self._last_poll_error:
                self._append_log("反馈", "实时监控", "失败", error)
                self._last_poll_error = error
        finally:
            self._polling_feedback = False

    def _read_feedback_once(self) -> tuple[list[float], VrReadRequest]:
        host = self.host_edit.text().strip()
        client = self._get_client(host)
        if self.protocol_combo.currentText() == "V3.0 Modbus TCP":
            read_request = self.service.build_v30_status_read()
            values = client.read_modbus_float(read_request)
        elif self.protocol_combo.currentText() == "最终标准协议":
            read_request = self.service.build_standard_status_read()
            values = client.read_vr(read_request)
        else:
            read_request = self.service.build_status_read()
            values = client.read_vr(read_request)
        return values, read_request

    def _read_realtime_once(self) -> tuple[list[float], VrReadRequest]:
        host = self.host_edit.text().strip()
        client = self._get_client(host)
        if self.protocol_combo.currentText() == "V3.0 Modbus TCP":
            read_request = self.service.build_v30_realtime_read()
            values = client.read_modbus_float(read_request)
        else:
            read_request = self.service.build_standard_monitor_read()
            values = client.read_vr(read_request)
        return values, read_request

    def _apply_realtime_values(self, values: list[float]) -> None:
        if len(values) < 12:
            self._append_log("反馈", "实时状态更新", "跳过", f"数据不足: 期望 >=12，实际 {len(values)}")
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
        client: ControllerClient,
        request: VrWriteRequest,
        mirror_request,
        query_key: str,
    ) -> None:
        attempts: list[tuple[float, ...]] = []

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

    def _wait_for_execution_complete(
        self,
        client: ControllerClient,
        read_request: VrReadRequest,
        query_key: str,
    ) -> list[float]:
        self._append_log(
            "执行",
            f"等待执行完成 {query_key}",
            "成功",
            f"轮询 {self._format_read_request(read_request.start_vr, read_request.count)}，最多 {EXECUTION_RETRY_COUNT} 次",
        )
        last_values: list[float] = []
        for attempt in range(1, EXECUTION_RETRY_COUNT + 1):
            values = client.read_vr(read_request)
            last_values = values
            status = self.service.parse_standard_status(values)
            self._append_log(
                "寄存器",
                f"轮询执行状态 {query_key}",
                "成功",
                f"第 {attempt} 次 {self._format_read_request(read_request.start_vr, read_request.count)} -> {values}",
            )
            if status.status != 1:
                self._append_log(
                    "执行",
                    f"执行完成确认 {query_key}",
                    "成功" if status.result == 0 else "失败",
                    f"STATUS={status.status}, RESULT={status.result}, ALM={status.alm_code}",
                )
                self._append_log(
                    "寄存器",
                    f"读取标准反馈 {query_key}",
                    "成功",
                    f"{self._format_read_request(read_request.start_vr, read_request.count)} -> {values}",
                )
                return values
            time.sleep(EXECUTION_RETRY_INTERVAL_SEC)
        raise RuntimeError(
            f"等待执行完成超时: {query_key} 在 {EXECUTION_RETRY_COUNT * EXECUTION_RETRY_INTERVAL_SEC:.1f}s 内未结束，最后反馈 {last_values}"
        )

    def _evaluate_feedback_result(self, feedback: list[float] | None) -> tuple[bool, str]:
        if not feedback:
            return True, ""
        if self.protocol_combo.currentText() == "V3.0 Modbus TCP":
            return True, ""
        if self.protocol_combo.currentText() == "最终标准协议" and len(feedback) >= 10:
            status = self.service.parse_standard_status(feedback)
            if status.result == 0:
                return True, ""
            detail = f"控制器执行失败: RESULT={status.result}, ALM={status.alm_code}"
            return False, detail
        try:
            result_code = int(feedback[0])
        except (ValueError, TypeError, IndexError):
            return True, ""
        if result_code == 0:
            return True, ""
        return False, f"控制器执行失败: RESULT={result_code}"

    def _start_flow(self, *, on_done: Callable[[bool], None] | None = None) -> None:
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
        self._flow_done_callback = on_done
        self.flow_running = True
        self.flow_status = "运行中"
        self.flow_current_step = "-"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        self._append_log("流程", f"开始流程 {flow.name}", "成功", f"共 {len(flow.steps)} 步")
        QTimer.singleShot(0, self._run_next_flow_step)

    def _step_flow(self) -> None:
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
        if self.flow_step_index >= len(flow.steps):
            self.flow_step_index = 0
        self.flow_status = "单步执行"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        self._run_current_flow_step(auto_continue=False)

    def _stop_flow(self) -> None:
        if not self.flow_running:
            return
        self.flow_running = False
        self.flow_status = "已停止"
        self.flow_current_step = "-"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        if self.current_flow_name:
            self._append_log("流程", f"停止流程 {self.current_flow_name}", "成功", f"停止于第 {self.flow_step_index + 1} 步")
        callback = self._flow_done_callback
        self._flow_done_callback = None
        if callback:
            callback(False)

    def _reset_flow(self) -> None:
        if self.flow_running:
            self.flow_running = False
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(False)
        self.flow_step_index = 0
        self.flow_status = "空闲"
        self.flow_current_step = "-"
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        if self.current_flow_name:
            self._append_log("流程", f"重置流程 {self.current_flow_name}", "成功", "流程已重置到第 1 步")

    def _run_next_flow_step(self) -> None:
        if not self.flow_running:
            return
        self._run_current_flow_step(auto_continue=True)

    def _run_current_flow_step(self, *, auto_continue: bool) -> None:
        flow = self._current_flow_definition()
        if flow is None:
            self.flow_running = False
            self.flow_status = "失败"
            self._refresh_flow_status_panel()
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(False)
            return
        if self.flow_step_index >= len(flow.steps):
            self.flow_running = False
            self.flow_status = "完成"
            self.flow_current_step = "-"
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            self._append_log("流程", f"流程完成 {flow.name}", "成功", f"共完成 {len(flow.steps)} 步")
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(True)
            return

        step_name = flow.steps[self.flow_step_index]
        self.flow_current_step = step_name
        self._refresh_flow_steps()
        self._refresh_flow_status_panel()
        self._append_log("流程", f"流程第{self.flow_step_index + 1}步开始", "成功", step_name)

        if step_name not in self.table:
            self.flow_running = False
            self.flow_status = "失败"
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            self._append_log("流程", f"流程第{self.flow_step_index + 1}步失败", "失败", f"模板不存在: {step_name}")
            callback = self._flow_done_callback
            self._flow_done_callback = None
            if callback:
                callback(False)
            return

        current_step_index = self.flow_step_index

        def on_step_done(ok: bool) -> None:
            if not ok:
                self.flow_running = False
                self.flow_status = "失败"
                self._refresh_flow_steps()
                self._refresh_flow_status_panel()
                self._append_log("流程", f"流程第{current_step_index + 1}步失败", "失败", step_name)
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(False)
                return

            self._append_log("流程", f"流程第{current_step_index + 1}步成功", "成功", step_name)
            self.flow_step_index = current_step_index + 1
            current_flow = self._current_flow_definition()
            if current_flow is None or self.flow_step_index >= len(current_flow.steps):
                self.flow_running = False
                self.flow_status = "完成"
                self.flow_current_step = "-"
                self._refresh_flow_steps()
                self._refresh_flow_status_panel()
                self._append_log("流程", f"流程完成 {flow.name}", "成功", f"共完成 {len(flow.steps)} 步")
                callback = self._flow_done_callback
                self._flow_done_callback = None
                if callback:
                    callback(True)
                return

            self.flow_status = "运行中" if auto_continue else "空闲"
            self.flow_current_step = current_flow.steps[self.flow_step_index]
            self._refresh_flow_steps()
            self._refresh_flow_status_panel()
            if auto_continue and self.flow_running:
                QTimer.singleShot(0, self._run_next_flow_step)

        self._execute_query_key(step_name, on_done=on_step_done)

    def _current_flow_definition(self):
        if not self.current_flow_name:
            return None
        if self.current_flow_name not in self.service.flows:
            return None
        return self.service.get_flow(self.current_flow_name)

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
    return Path(__file__).resolve().parents[1]


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
