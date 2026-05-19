"""由多个职责模块组合而成的图形界面主窗口。"""

from __future__ import annotations

import copy
import json
import importlib.util
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

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

from .app_state import FlowExecutionState, NlpRuntimeState, RobotRealtimeState, VoiceRuntimeState
from .avoidance_execution_mixin import AvoidanceExecutionMixin
from .background_tasks import run_background_thread
from .command_dispatch_mixin import CommandDispatchMixin
from .controller_runtime_mixin import ControllerRuntimeMixin
from .exceptions import BackgroundTaskError, SixAxisCommandRuntimeError
from .flow_execution_mixin import FlowExecutionMixin
from .flow_management_mixin import FlowManagementMixin
from .gui_logging import GuiLoggingMixin
from .gui_system_mixin import GuiSystemMixin
from .gui_ui_mixin import GuiUiMixin
from .gui_constants import (
    FUNC_LABELS,
    FUNC_OPTIONS,
    MOVE_TYPE_LABELS,
    SIX_CMD_BUSY_RECOVERY_MAX_RETRIES,
    SIX_CMD_BUSY_SLOT_WAIT_TIMEOUT_SEC,
    SIX_ECHO_COMPARE_EPSILON,
    SIX_ECHO_CONSECUTIVE_FAIL_THRESHOLD,
    SIX_ECHO_MAX_RETRY_COUNT,
    SIX_ECHO_RETRY_INTERVAL_SEC,
    SIX_ECHO_WRITE_ROUNDS,
    SIX_POST_TRIGGER_SETTLE_SEC,
    SIX_READY_RECOVERY_TIMEOUT_SEC,
    STOP_CMD_LABELS,
    SYSTEM_COMMAND_CODES,
    SYSTEM_COMMANDS,
)
from .runtime_paths import resource_dir, resolve_runtime_data_file, runtime_dir
from .six_axis_command_mixin import SixAxisCommandMixin
from .voice_mixin import VoiceMixin
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
from .models import ControllerClient, QueryRecord, SIX_MOTION_FUNCS, SixAxisCommand, SixAxisStatus, VrWriteRequest, VrReadRequest, six_func_slot
from .nlp_mixin import NlpMixin
from .operator_ui_mixin import OperatorUiMixin
from .query_table import bootstrap_query_table_json, load_query_table, save_query_table_json
from .service import RobotModbusService
from .settings_mixin import SettingsMixin
from .template_mixin import TemplateMixin
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


class RobotQtWindow(OperatorUiMixin, GuiUiMixin, TemplateMixin, FlowManagementMixin, SettingsMixin, CommandDispatchMixin, AvoidanceExecutionMixin, NlpMixin, FlowExecutionMixin, VoiceMixin, GuiSystemMixin, ControllerRuntimeMixin, SixAxisCommandMixin, GuiLoggingMixin, QMainWindow):
    """图形界面主窗口，组合各个职责模块。"""
    _main_thread_call = Signal(object)

    def __init__(
        self,
        *,
        json_path: Path,
        csv_path: Path,
        system_config_path: Path | None = None,
        client_factory: Callable[[str, Path], ZMotionVrClient] | None = None,
    ) -> None:
        """初始化对象。"""
        super().__init__()
        self.setWindowTitle("机械手控制系统")
        self.resize(1380, 860)

        self.runtime_root = runtime_dir()
        self.resource_root = resource_dir()
        self.flows_path = resolve_runtime_data_file("flows.json")
        self.json_path = bootstrap_query_table_json(json_path, csv_path)
        self.system_config_path = ensure_system_config_json(system_config_path or (self.runtime_root / "data" / "system_config.json"))
        self.avoidance_config_path = ensure_avoidance_config_json(self.runtime_root / "data" / "avoidance_rules.json")
        self.axis_ranges = load_system_config(self.system_config_path)
        self.avoidance_config = load_avoidance_config(self.avoidance_config_path)
        self.table = load_query_table(self.json_path)
        self.service = RobotModbusService(self.json_path, flows_path=self.flows_path, table=self.table)
        self._client_factory = client_factory or (lambda host, repo_root: ZMotionVrClient(host=host, repo_root=repo_root))
        self._updating_command_fields = False
        self._updating_func104_form = False
        self.history: list[dict[str, str | int]] = []
        self.logs: list[dict[str, Any]] = []
        self.task_id = 1001
        self.current_key: str | None = None
        self.current_safe_point_key: str | None = None
        self.current_flow_manage_name: str | None = None
        flow_state = FlowExecutionState()
        self.current_flow_name = flow_state.current_flow_name
        self.flow_step_index = flow_state.step_index
        self.flow_status = flow_state.status
        self.flow_running = flow_state.running
        self.flow_current_step = flow_state.current_step
        self.flow_run_id = flow_state.run_id
        realtime_state = RobotRealtimeState()
        self.robot_x = realtime_state.robot_x
        self.robot_y = realtime_state.robot_y
        self.robot_z = realtime_state.robot_z
        self.robot_r = realtime_state.robot_r
        self.robot_joints = realtime_state.robot_joints
        self.robot_speed = realtime_state.robot_speed
        self.claw_enable = realtime_state.claw_enable
        self.claw_brake = realtime_state.claw_brake
        self.servo_enable = realtime_state.servo_enable
        self.run_state = realtime_state.run_state
        self.monitor_task = realtime_state.monitor_task
        self.motion_percent = realtime_state.motion_percent
        self.echo_cmd = realtime_state.echo_cmd
        self.exec_state = realtime_state.exec_state
        self.current_func_text = "空闲"
        self.estop_active = False
        self.pause_active = False
        self.mode = realtime_state.mode
        self.busy = realtime_state.busy
        self.result = realtime_state.result
        self.alarm_code = realtime_state.alarm_code
        self.alarm_text = realtime_state.alarm_text
        self.io_status = realtime_state.io_status
        self.task_id = realtime_state.task_id
        self._polling_feedback = False
        self._last_poll_error = ""
        self._last_realtime_snapshot: tuple[str, str, str, str] | None = None
        self._last_realtime_snapshot_raw: dict[str, Any] | None = None
        self._poll_started_logged = False
        self._cached_client = None
        self._cached_client_host = ""
        self._client_cache_lock = threading.Lock()
        self.session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self._log_seq = 0
        self._log_seq_lock = threading.Lock()
        self._log_context = threading.local()
        self._session_start_perf = time.perf_counter()
        self._log_dir = self.runtime_root / "data" / "exported_logs"
        self._log_session_path = self._log_dir / f"session_{self.session_id}.jsonl"
        self._log_persist_error_reported = False
        nlp_state = NlpRuntimeState()
        self.nlp_last_plan: VoiceNlpPlan | None = nlp_state.last_plan
        self.nlp_sequence_running = nlp_state.sequence_running
        self.nlp_parse_running = nlp_state.parse_running
        self._nlp_pending_actions: list[VoiceNlpAction] = nlp_state.pending_actions
        self._nlp_pending_index = nlp_state.pending_index
        self._flow_done_callback: Callable[[bool], None] | None = None
        voice_state = VoiceRuntimeState()
        self._mic_process: subprocess.Popen[str] | None = voice_state.process
        self._mic_poll_timer: QTimer | None = None
        self._mic_stop_flag_path: Path | None = voice_state.stop_flag_path
        self._mic_result_path: Path | None = voice_state.result_path
        self._mic_recorder_thread = voice_state.recorder_thread  # 代理模式持久录音线程
        self._proxy_mic_capturing = voice_state.proxy_capturing  # 代理模式是否正在采集
        self._iflytek_local_client = None
        self._iflytek_local_client_lock = threading.Lock()
        self._local_voice_streaming = False
        self._local_voice_stream_stop_flag_path: Path | None = None
        self._local_voice_stream_debug_path: Path | None = None
        self._local_voice_stream_started_perf = 0.0
        self._local_voice_stream_stop_pending = False

        # 授权相关
        self.license_manager = LicenseManager(self.runtime_root / "data")
        self._deepseek_client = None  # 外部注入的大模型客户端
        self._use_license_voice = False  # 是否使用订阅模式语音

        self._main_thread_call.connect(self._handle_main_thread_call)

        self._build_ui()
        self._init_api_clients()
        self._refresh_microphone_devices()
        self._load_initial_record()
        self._refresh_all()
        self._check_connection()
        self._start_realtime_polling()

    def _setup_license_menu(self) -> None:
        """处理授权。"""
        menu_bar = self.menuBar()
        license_menu = menu_bar.addMenu("授权(&L)")

        show_action = QAction("授权管理(&M)...", self)
        show_action.triggered.connect(self._show_license_dialog)
        license_menu.addAction(show_action)

    def _init_api_clients(self) -> None:
        """初始化相关数据。"""
        self._deepseek_client = None
        self._use_license_voice = False

        try:
            status = self.license_manager.check_status()
            if status.valid:
                # 订阅模式下的大模型客户端。
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

        # 降级到内置密钥（仅测试模式，发布版本需关闭本地密钥开关）。
        if os.getenv("ALLOW_LOCAL_KEY", "true").lower() == "true":
            try:
                from .deepseek_client import DeepSeekClient
                self._deepseek_client = DeepSeekClient.from_env()
            except Exception:
                self._deepseek_client = None

        self._update_license_status_label()
        self._ensure_mic_stream()

    def _show_license_dialog(self) -> None:
        """显示授权对话框。"""
        dlg = LicenseDialog(self.license_manager, self)
        dlg.license_activated.connect(self._on_license_changed)
        dlg.license_deactivated.connect(self._on_license_changed)
        dlg.exec()

    def _on_license_changed(self) -> None:
        """处理授权。"""
        self._init_api_clients()

    def _update_license_status_label(self) -> None:
        """更新授权状态。"""
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

    @staticmethod
    def _values_equal(left: tuple[float, ...], right: tuple[float, ...], tolerance: float = 1e-6) -> bool:
        """处理相关数据。"""
        if len(left) != len(right):
            return False
        return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))

    @staticmethod
    def _fmt(value: float) -> str:
        """处理相关数据。"""
        return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def main() -> None:
    """执行命令行入口逻辑。"""
    import sys

    app = QApplication(sys.argv)
    resource_base = resource_dir()
    json_path = resolve_runtime_data_file("query_table.json")
    system_config_path = resolve_runtime_data_file("system_config.json")
    csv_path = resource_base / "附件" / "机械臂AI地址表.csv"
    if not csv_path.exists():
        csv_path = json_path
    window = RobotQtWindow(json_path=json_path, csv_path=csv_path, system_config_path=system_config_path)
    window.show()
    sys.exit(app.exec())
