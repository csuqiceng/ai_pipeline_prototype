"""主窗口控件层级、页面布局和样式构建逻辑。"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from .gui_constants import FUNC_OPTIONS, STOP_CMD_LABELS, SYSTEM_COMMANDS


class GuiUiMixin:
    """构建主窗口控件、页面和样式。"""
    _UI_SCALE_BASE_WIDTH = 1834
    _UI_SCALE_BASE_HEIGHT = 784
    _UI_SCALE_MIN = 0.6
    _UI_SCALE_MAX = 1.2

    @staticmethod
    def _calculate_ui_scale(config_value: object, *, available_width: int, available_height: int) -> float:
        """Calculate effective UI scale from config and available screen size."""
        if isinstance(config_value, str) and config_value.strip().lower() == "auto":
            width_scale = float(available_width) / float(GuiUiMixin._UI_SCALE_BASE_WIDTH)
            height_scale = float(available_height) / float(GuiUiMixin._UI_SCALE_BASE_HEIGHT)
            raw = min(width_scale, height_scale, 1.0)
        else:
            try:
                raw = float(config_value)
            except (TypeError, ValueError):
                raw = 1.0
        return max(GuiUiMixin._UI_SCALE_MIN, min(GuiUiMixin._UI_SCALE_MAX, round(raw, 3)))

    def _screen_available_size(self) -> tuple[int, int]:
        # Qt returns logical pixels here; Windows 150% DPI on 2560x1080 is roughly 1706x720.
        screen = self.screen()
        if screen is None:
            return 1380, 860
        available = screen.availableGeometry()
        return max(1, int(available.width())), max(1, int(available.height()))

    def _configure_ui_scale(self) -> None:
        available_width, available_height = self._screen_available_size()
        config_value = getattr(getattr(self, "axis_ranges", None), "ui_scale", "auto")
        self._ui_scale_factor = self._calculate_ui_scale(
            config_value,
            available_width=available_width,
            available_height=available_height,
        )

    def _scaled(self, value: int | float) -> int:
        factor = float(getattr(self, "_ui_scale_factor", 1.0) or 1.0)
        return max(1, int(round(float(value) * factor)))

    def _scaled_min(self, value: int | float, minimum: int) -> int:
        return max(int(minimum), self._scaled(value))

    def _target_window_size(
        self,
        width: int,
        height: int,
        *,
        available_width: int | None = None,
        available_height: int | None = None,
    ) -> tuple[int, int]:
        if available_width is None or available_height is None:
            available_width, available_height = self._screen_available_size()
        scaled_width = self._scaled(width)
        scaled_height = self._scaled(height)
        return min(scaled_width, int(available_width)), min(scaled_height, int(available_height))

    def _resize_to_fit_screen(self, width: int, height: int) -> None:
        target_width, target_height = self._target_window_size(width, height)
        self.resize(target_width, target_height)

    def _make_workspace_scroll(self, widget: QWidget, object_name: str) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        scroll.setMinimumSize(0, 0)
        widget.setMinimumSize(0, 0)
        scroll.setWidget(widget)
        return scroll

    def _build_message_box(self, icon: QMessageBox.Icon, title: str, text: str) -> QMessageBox:
        """构建相关数据。"""
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
        """显示相关数据。"""
        self._build_message_box(QMessageBox.Warning, title, text).exec()

    def _show_info(self, title: str, text: str) -> None:
        """显示相关数据。"""
        self._build_message_box(QMessageBox.Information, title, text).exec()

    def _show_critical(self, title: str, text: str) -> None:
        """显示相关数据。"""
        self._build_message_box(QMessageBox.Critical, title, text).exec()

    def _build_ui(self) -> None:
        """构建界面。"""
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self._authenticated_role = ""
        self._login_target_role = "operator"
        self._login_role = "operator"
        self._login_health_generation = 0
        self._login_health_checks = {}
        self._login_health_started_at = 0.0
        self._configure_ui_scale()
        self._resize_to_fit_screen(900, 620)

        main = QWidget()
        main_layout = QHBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        nav = self._build_nav()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 4, 10, 10)
        content_layout.setSpacing(0)
        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.pages.addWidget(self._build_run_page())
        self.pages.addWidget(self._make_workspace_scroll(self._build_manage_page(), "manageWorkspaceScroll"))
        self.pages.addWidget(self._build_log_page())
        content_layout.addWidget(self.pages)
        right_panel = self._build_system_panel()

        main_layout.addWidget(nav)
        main_layout.addWidget(content, 1)
        main_layout.addWidget(right_panel)

        self.workspace_pages = QStackedWidget()
        self.workspace_pages.addWidget(main)
        self.workspace_pages.addWidget(self._make_workspace_scroll(self._build_operator_page(), "operatorWorkspaceScroll"))

        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.workspace_pages, 1)

        self.status_label = QLabel(f"系统就绪 | 数据源: {self.json_path}")
        self.status_label.setObjectName("footerStatus")
        self.status_label.setMinimumHeight(28)
        self.status_label.setMaximumWidth(self._scaled(1100))
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        footer = QHBoxLayout()
        footer.setContentsMargins(8, 0, 8, 0)
        footer.addWidget(self.status_label, 1)
        self._license_status_label = QLabel("")
        self._license_status_label.setObjectName("footerStatus")
        self._license_status_label.setMinimumHeight(28)
        self._update_license_status_label()
        footer.addWidget(self._license_status_label)
        self.logout_btn = QPushButton("退出登录")
        self.logout_btn.setObjectName("workspaceToggleButton")
        self.logout_btn.clicked.connect(self._show_login_page)
        footer.addWidget(self.logout_btn)

        footer_widget = QWidget()
        footer_widget.setLayout(footer)
        shell_layout.addWidget(footer_widget)
        self._main_shell = shell

        self.app_pages = QStackedWidget()
        self.app_pages.addWidget(self._build_login_page())
        root_layout.addWidget(self.app_pages, 1)

        self._apply_styles()
        self.workspace_pages.setCurrentIndex(0)
        self._workspace_mode = "engineer"
        self.app_pages.setCurrentIndex(0)
        self.setWindowTitle(" ")
        self._center_window_on_screen()
        QTimer.singleShot(100, self._start_login_health_checks)

    def _center_window_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _build_login_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(0)

        layout.addStretch(1)
        center_row = QHBoxLayout()
        center_row.addStretch(1)

        card = QFrame()
        card.setObjectName("loginCard")
        card.setFixedWidth(self._scaled(420))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 30, 32, 30)
        card_layout.setSpacing(12)

        title = QLabel("机械手控制系统")
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)

        divider = QFrame()
        divider.setObjectName("loginDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        card_layout.addWidget(divider)
        card_layout.addSpacing(8)

        access_label = QLabel("访问级别")
        access_label.setObjectName("loginFieldLabel")
        card_layout.addWidget(access_label)

        role_row = QFrame()
        role_row.setObjectName("loginSegment")
        role_layout = QHBoxLayout(role_row)
        role_layout.setContentsMargins(4, 4, 4, 4)
        role_layout.setSpacing(4)
        self.login_operator_btn = QPushButton("用户")
        self.login_engineer_btn = QPushButton("工程师")
        self.login_role_buttons = {
            "operator": self.login_operator_btn,
            "engineer": self.login_engineer_btn,
        }
        for role, btn in self.login_role_buttons.items():
            btn.setObjectName("loginSegmentButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, r=role: self._set_login_role(r))
            role_layout.addWidget(btn)
        card_layout.addWidget(role_row)

        connection_label = QLabel("控制器连接")
        connection_label.setObjectName("loginFieldLabel")
        card_layout.addWidget(connection_label)
        self.login_host_edit = QLineEdit()
        self.login_host_edit.setObjectName("loginInput")
        self.login_host_edit.setPlaceholderText("192.168.1.11")
        self.login_host_edit.setText(self.host_edit.text().strip() if hasattr(self, "host_edit") else "192.168.1.11")
        self.login_host_edit.returnPressed.connect(self._login_check_connection)
        self.login_host_edit.textChanged.connect(self._schedule_login_health_checks)
        card_layout.addWidget(self.login_host_edit)

        connection_row = QHBoxLayout()
        connection_row.setSpacing(8)
        self.login_controller_combo = QComboBox()
        self.login_controller_combo.setObjectName("loginCombo")
        self.login_controller_combo.addItems(["真实控制器", "模拟控制器"])
        if hasattr(self, "controller_combo"):
            self.login_controller_combo.setCurrentText(self.controller_combo.currentText())
        self.login_controller_combo.currentTextChanged.connect(self._schedule_login_health_checks)
        connection_row.addWidget(self.login_controller_combo, 1)
        self.login_check_btn = QPushButton("检测连接")
        self.login_check_btn.setObjectName("loginCheckButton")
        self.login_check_btn.clicked.connect(self._login_check_connection)
        connection_row.addWidget(self.login_check_btn)
        card_layout.addLayout(connection_row)

        self.login_connection_label = QLabel("连接状态: 未检测")
        self.login_connection_label.setObjectName("loginConnectionStatus")
        self.login_connection_label.setWordWrap(True)
        card_layout.addWidget(self.login_connection_label)

        operator_label = QLabel("账号")
        operator_label.setObjectName("loginFieldLabel")
        card_layout.addWidget(operator_label)
        self.login_operator_id_edit = QLineEdit()
        self.login_operator_id_edit.setObjectName("loginInput")
        self.login_operator_id_edit.setPlaceholderText("OP-0001")
        self.login_operator_id_edit.returnPressed.connect(self._authenticate_login)
        card_layout.addWidget(self.login_operator_id_edit)

        pin_label = QLabel("密码")
        pin_label.setObjectName("loginFieldLabel")
        card_layout.addWidget(pin_label)
        self.login_pin_edit = QLineEdit()
        self.login_pin_edit.setObjectName("loginPasswordInput")
        self.login_pin_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_pin_edit.setPlaceholderText("请输入密码")
        password_palette = self.login_pin_edit.palette()
        password_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#94a3b8"))
        self.login_pin_edit.setPalette(password_palette)
        self.login_pin_edit.returnPressed.connect(self._authenticate_login)
        card_layout.addWidget(self.login_pin_edit)

        self.login_error_label = QLabel("")
        self.login_error_label.setObjectName("loginError")
        self.login_error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.login_error_label)

        self.login_auth_btn = QPushButton("登录")
        self.login_auth_btn.setObjectName("loginAuthButton")
        self.login_auth_btn.clicked.connect(self._authenticate_login)
        card_layout.addWidget(self.login_auth_btn)

        center_row.addWidget(card)
        center_row.addStretch(1)
        layout.addLayout(center_row)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.login_health_label = QLabel("语音服务: 等待检测  |  AI服务: 等待检测")
        self.login_health_label.setObjectName("loginServiceStatus")
        self.login_health_label.setWordWrap(True)
        footer.addWidget(self.login_health_label, 1)
        version = QLabel("V1.0")
        version.setObjectName("loginVersion")
        footer.addWidget(version)
        layout.addLayout(footer)

        self._set_login_role("operator")
        return page

    def _set_login_role(self, role: str) -> None:
        self._login_role = "operator" if role == "operator" else "engineer"
        if hasattr(self, "login_role_buttons"):
            for name, btn in self.login_role_buttons.items():
                active = name == self._login_role
                btn.setChecked(active)
                btn.setProperty("active", "true" if active else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        if hasattr(self, "login_operator_id_edit"):
            self.login_operator_id_edit.setPlaceholderText("ENG-0001" if self._login_role == "engineer" else "OP-0001")
        if hasattr(self, "login_pin_edit"):
            self.login_pin_edit.setPlaceholderText("请输入密码")
        if hasattr(self, "login_error_label"):
            self.login_error_label.setText("")
        QTimer.singleShot(100, self._start_login_health_checks)

    def _show_login_page(self, target_role: str | None = None) -> None:
        self._sync_login_connection_from_main()
        if target_role in {"engineer", "operator"}:
            self._login_target_role = target_role
            self._set_login_role(target_role)
        else:
            self._login_target_role = "operator"
            self._set_login_role("operator")
        self._authenticated_role = ""
        self.setWindowTitle(" ")
        if hasattr(self, "login_pin_edit"):
            self.login_pin_edit.clear()
            self.login_pin_edit.setFocus()
        if hasattr(self, "login_error_label"):
            self.login_error_label.setText("")
        if hasattr(self, "app_pages"):
            if hasattr(self, "_main_shell") and self.app_pages.indexOf(self._main_shell) >= 0:
                self.app_pages.removeWidget(self._main_shell)
            self.app_pages.setCurrentIndex(0)
        if not self.isFullScreen():
            self._resize_to_fit_screen(900, 620)
            self._center_window_on_screen()

    def _sync_login_connection_from_main(self) -> None:
        if hasattr(self, "login_host_edit") and hasattr(self, "host_edit") and not self.login_host_edit.hasFocus():
            self.login_host_edit.setText(self.host_edit.text().strip())
        if hasattr(self, "login_controller_combo") and hasattr(self, "controller_combo"):
            self.login_controller_combo.setCurrentText(self.controller_combo.currentText())
        if hasattr(self, "login_connection_label") and hasattr(self, "connection_label"):
            self.login_connection_label.setText(f"连接状态: {self.connection_label.text()}")

    def _schedule_login_health_checks(self) -> None:
        if not hasattr(self, "app_pages") or self.app_pages.currentIndex() != 0:
            return
        if hasattr(self, "login_health_label"):
            self.login_health_label.setText("语音服务: 等待检测  |  AI服务: 等待检测")
        QTimer.singleShot(500, self._start_login_health_checks)

    def _set_login_health_status(self, key: str, ok: bool | None, message: str) -> None:
        checks = dict(getattr(self, "_login_health_checks", {}) or {})
        checks[key] = {
            "ok": ok,
            "message": self._format_login_health_message(key, ok, str(message or "")),
            "checked_at": time.time() if ok is not None else 0.0,
        }
        self._login_health_checks = checks
        self._refresh_login_health_label()

    @staticmethod
    def _format_login_health_message(key: str, ok: bool | None, message: str) -> str:
        raw = " ".join(str(message or "").split())
        if ok is True:
            return "正常"
        if ok is None:
            return "检测中"
        if key == "controller":
            if "地址为空" in raw:
                return "请输入控制器地址"
            return "连接失败，请检查 IP、网线或控制器状态"
        if key == "doubao":
            if "DOUBAO_API_KEY" in raw or "api_key" in raw.lower():
                return "语音服务未配置"
            if "timeout" in raw.lower() or "超时" in raw:
                return "语音服务响应超时"
            return "语音服务不可用"
        if key == "deepseek":
            if "DEEPSEEK_API_KEY" in raw or "api_key" in raw.lower():
                return "AI服务未配置"
            if "配额" in raw or "quota" in raw.lower() or "429" in raw:
                return "AI服务配额不足"
            if "timeout" in raw.lower() or "超时" in raw:
                return "AI服务响应超时"
            return "AI服务不可用"
        return raw or "异常"

    def _refresh_login_health_label(self) -> None:
        if not hasattr(self, "login_health_label"):
            return
        checks = getattr(self, "_login_health_checks", {}) or {}

        def item(label: str, key: str) -> str:
            status = checks.get(key, {})
            ok = status.get("ok")
            msg = str(status.get("message", "") or "")
            if ok is True:
                return f"✓ {label}：正常"
            if ok is False:
                return f"{label}: {msg or '异常'}"
            return f"{label}: 检测中"

        self.login_health_label.setText(
            "  |  ".join(
                (
                    item("语音服务", "doubao"),
                    item("AI服务", "deepseek"),
                )
            )
        )

    def _start_login_health_checks(self) -> None:
        if not hasattr(self, "login_host_edit") or not hasattr(self, "login_controller_combo"):
            return
        if hasattr(self, "app_pages") and self.app_pages.currentIndex() != 0:
            return
        host = self.login_host_edit.text().strip()
        controller_mode = self.login_controller_combo.currentText()
        generation = int(getattr(self, "_login_health_generation", 0) or 0) + 1
        self._login_health_generation = generation
        self._login_health_started_at = time.time()
        self._login_health_checks = {
            "controller": {"ok": None, "message": "检测中", "checked_at": 0.0},
            "doubao": {"ok": None, "message": "检测中", "checked_at": 0.0},
            "deepseek": {"ok": None, "message": "检测中", "checked_at": 0.0},
        }
        self._refresh_login_health_label()

        def work() -> dict[str, tuple[bool, str]]:
            return {
                "controller": self._probe_login_controller_connection(host, controller_mode),
                "doubao": self._probe_doubao_service(),
                "deepseek": self._probe_deepseek_service(),
            }

        def done(results) -> None:
            if generation != getattr(self, "_login_health_generation", 0):
                return
            if not isinstance(results, dict):
                return
            for key, value in results.items():
                ok, message = value if isinstance(value, tuple) and len(value) == 2 else (False, str(value))
                self._set_login_health_status(key, bool(ok), str(message))

        runner = getattr(self, "_run_in_background", None)
        if callable(runner):
            runner(work, done)
        else:
            done(work())

    def _probe_login_controller_connection(self, host: str, controller_mode: str) -> tuple[bool, str]:
        if not host:
            return False, "地址为空"
        client = None
        try:
            if controller_mode == "模拟控制器":
                try:
                    from mock_controller import MockZMotionVrClient
                except ModuleNotFoundError:
                    from ..mock_controller import MockZMotionVrClient
                client = MockZMotionVrClient(host=host, axis_ranges=self.axis_ranges.to_dict())
            else:
                client = self._client_factory(host, self.resource_root)
            client.connect()
            return True, "连接成功"
        except Exception as exc:
            return False, str(exc)
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

    def _probe_doubao_service(self) -> tuple[bool, str]:
        try:
            from .doubao_voice_client import DoubaoVoiceClient

            client = getattr(self, "_doubao_voice_client", None) or DoubaoVoiceClient()
            checker = getattr(client, "check_connection", None)
            if callable(checker):
                checker()
            return True, "连接成功"
        except Exception as exc:
            return False, str(exc)

    def _probe_deepseek_service(self) -> tuple[bool, str]:
        try:
            client = getattr(self, "_deepseek_client", None)
            if client is None:
                from .deepseek_client import DeepSeekClient

                client = DeepSeekClient.from_env()
            checker = getattr(client, "check_connection", None)
            if callable(checker):
                checker()
            else:
                client.generate_chat("ping", system_prompt="只回复 OK。")
            return True, "连接成功"
        except Exception as exc:
            return False, str(exc)

    def _login_health_failure_text(self) -> str:
        checks = getattr(self, "_login_health_checks", {}) or {}
        failed: list[str] = []
        for label, key in (("控制器", "controller"), ("语音服务", "doubao"), ("AI服务", "deepseek")):
            status = checks.get(key, {})
            if status.get("ok") is False:
                message = str(status.get("message", "") or "")
                failed.append(f"{label}: {message}" if message else label)
        if not failed:
            return ""
        return "登录前自检未通过：" + "；".join(failed)

    def _apply_login_connection_settings(self) -> bool:
        host = self.login_host_edit.text().strip() if hasattr(self, "login_host_edit") else ""
        if not host:
            if hasattr(self, "login_connection_label"):
                self.login_connection_label.setText("连接状态: 地址为空")
            self._show_warning("地址为空", "请输入控制器地址。")
            return False
        if hasattr(self, "host_edit"):
            self.host_edit.setText(host)
        if hasattr(self, "controller_combo") and hasattr(self, "login_controller_combo"):
            self.controller_combo.setCurrentText(self.login_controller_combo.currentText())
        return True

    def _login_check_connection(self) -> None:
        if not self._apply_login_connection_settings():
            return
        if hasattr(self, "login_connection_label"):
            self.login_connection_label.setText("连接状态: 检测中...")
        self._check_connection()
        if hasattr(self, "login_connection_label") and hasattr(self, "connection_label"):
            self.login_connection_label.setText(f"连接状态: {self.connection_label.text()}")

    def _authenticate_login(self) -> None:
        role = getattr(self, "_login_role", "engineer")
        pin = self.login_pin_edit.text().strip() if hasattr(self, "login_pin_edit") else ""
        operator_id = self.login_operator_id_edit.text().strip() if hasattr(self, "login_operator_id_edit") else ""
        expected_pin = os.environ.get("ROBOT_ENGINEER_PIN" if role == "engineer" else "ROBOT_OPERATOR_PIN")
        if expected_pin is None:
            expected_pin = "0000" if role == "engineer" else "1234"
        if not pin:
            if hasattr(self, "login_error_label"):
                self.login_error_label.setText(f"测试阶段密码: {expected_pin}")
            if hasattr(self, "login_pin_edit"):
                self.login_pin_edit.setFocus()
            return
        if pin != expected_pin:
            if hasattr(self, "login_error_label"):
                self.login_error_label.setText("认证失败，请检查账号或密码")
            return
        if not self._apply_login_connection_settings():
            return
        health_error = self._login_health_failure_text()
        if health_error:
            if hasattr(self, "login_error_label"):
                self.login_error_label.setText(health_error)
            self._refresh_login_health_label()
            return
        self._authenticated_role = role
        self._authenticated_operator_id = operator_id or ("ENG-0001" if role == "engineer" else "OP-0001")
        self.setWindowTitle("机械手控制系统")
        if hasattr(self, "app_pages"):
            main_index = self.app_pages.indexOf(self._main_shell) if hasattr(self, "_main_shell") else -1
            if main_index < 0 and hasattr(self, "_main_shell"):
                main_index = self.app_pages.addWidget(self._main_shell)
            self.app_pages.setCurrentIndex(main_index if main_index >= 0 else 0)
        if not self.isFullScreen():
            self._configure_ui_scale()
            self._resize_to_fit_screen(1380, 860)
            self._center_window_on_screen()
        self._set_workspace_mode("engineer" if role == "engineer" else "operator")
        if hasattr(self, "status_label"):
            self.status_label.setText(f"已登录: {self._authenticated_operator_id} | 身份: {'工程师' if role == 'engineer' else '用户'}")

    # ---------- 授权管理 ----------

    def _build_nav(self) -> QWidget:
        """构建相关数据。"""
        frame = QFrame()
        frame.setObjectName("nav")
        frame.setFixedWidth(self._scaled(96))
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
        """构建系统面板。"""
        panel = QFrame()
        panel.setObjectName("rightPanel")
        panel.setFixedWidth(self._scaled(150))
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        for text, key, klass in [
            ("报警复位", "alarm_reset", ""),
            ("暂停", "sys_pause", "yellow"),
            ("继续", "sys_resume", ""),
            ("急停", "sys_estop", "red"),
        ]:
            btn = QPushButton(text)
            btn.setProperty("klass", klass)
            btn.clicked.connect(lambda _=False, k=key: self._handle_system_action(k))
            layout.addWidget(btn)
        status_group = QGroupBox("系统状态")
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

        self._license_btn = QPushButton("授权")
        self._license_btn.setFixedHeight(self._scaled(28))
        self._license_btn.clicked.connect(self._show_license_dialog)
        layout.addWidget(self._license_btn)

        return panel

    def _show_page(self, index: int) -> None:
        """显示页面。"""
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
        """构建页面。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        link_bar = QGroupBox("反馈监控")
        link_bar.setObjectName("panel")
        link_layout = QHBoxLayout(link_bar)
        link_layout.setContentsMargins(10, 6, 10, 6)
        self.host_edit = QLineEdit("192.168.1.11")
        self.host_edit.setMaximumWidth(220)
        self.host_edit.setVisible(False)
        self.controller_combo = QComboBox()
        self.controller_combo.addItems(["真实控制器", "模拟控制器"])
        self.controller_combo.setMaximumWidth(180)
        self.controller_combo.setVisible(False)
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["Modbus TCP (V2.2)"])
        self.protocol_combo.setMaximumWidth(180)
        self.protocol_combo.setDisabled(True)
        self.protocol_combo.setVisible(False)
        link_layout.addWidget(QLabel("连接状态:"))
        self.connection_label = QLabel("检测中...")
        link_layout.addWidget(self.connection_label, 1)
        self.monitor_label = QLabel("未启动")
        read_btn = QPushButton("读取反馈")
        read_btn.clicked.connect(self._read_feedback)
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

        self.command_group = QGroupBox("固定指令执行")
        self.command_group.setObjectName("panel")
        command_box_layout = QVBoxLayout(self.command_group)
        command_toolbar = QHBoxLayout()
        command_toolbar.addWidget(QLabel("筛选:"))
        self.command_filter_edit = QLineEdit()
        self.command_filter_edit.setPlaceholderText("输入名称 / 关键词")
        self.command_filter_edit.textChanged.connect(self._refresh_command_cards)
        command_toolbar.addWidget(self.command_filter_edit, 1)
        command_toolbar.addWidget(QLabel("类型:"))
        self.command_type_combo = QComboBox()
        self.command_type_combo.addItems(["全部", "Func104", "Func108", "Func109", "Func110", "Func120"])
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
        self.nlp_use_deepseek_check = QCheckBox("在线AI解析")
        self.nlp_use_deepseek_check.setChecked(True)
        nlp_head.addWidget(self.nlp_use_deepseek_check)
        nlp_head.addWidget(QLabel("麦克风:"))
        self.mic_device_combo = QComboBox()
        self.mic_device_combo.setMinimumWidth(220)
        nlp_head.addWidget(self.mic_device_combo)
        refresh_mic_btn = QPushButton("刷新设备")
        refresh_mic_btn.setFixedWidth(self._scaled_min(100, 80))
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
        self.nlp_parse_btn.setFixedWidth(self._scaled_min(120, 80))
        self.nlp_execute_btn = QPushButton("执行")
        self.nlp_execute_btn.clicked.connect(self._execute_nlp_text)
        self.nlp_execute_btn.setFixedWidth(self._scaled_min(120, 80))
        self.nlp_execute_btn.setProperty("klass", "green")
        self.mic_toggle_btn = QPushButton("开始录音")
        self.mic_toggle_btn.clicked.connect(self._toggle_microphone_recording)
        self.mic_toggle_btn.setFixedWidth(self._scaled_min(120, 80))
        self.nlp_clear_btn = QPushButton("清空")
        self.nlp_clear_btn.clicked.connect(self._clear_nlp_text)
        self.nlp_clear_btn.setFixedWidth(self._scaled_min(120, 80))
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

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        self.robot_info = self._make_info_group("机械手状态")
        self.robot_info.setObjectName("panel")
        self.summary_info = self._make_info_group("执行摘要")
        self.summary_info.setObjectName("panel")
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

        run_splitter = QSplitter(Qt.Orientation.Vertical)
        run_splitter.setChildrenCollapsible(False)
        run_splitter.addWidget(execute_tabs)
        run_splitter.addWidget(bottom)
        run_splitter.setStretchFactor(0, 5)
        run_splitter.setStretchFactor(1, 2)
        run_splitter.setSizes([760, 220])
        layout.addWidget(run_splitter, 1)
        return page

    def _build_manage_page(self) -> QWidget:
        """构建页面。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        self.backend_info = self._make_static_group("功能说明", [
            ("用途", "维护函数号与参数模板"),
            ("支持", "Func104 停止"),
            ("支持", "Func108 直线/PTP"),
            ("支持", "Func109/110/120 检测/延时/IO"),
            ("限制", "当前阶段禁止 Func106/107 点动"),
            ("stop_cmd", "0正常 1急停 2快停 3慢停 4暂停 5恢复"),
            ("fuzzy", "0绝对 1叠加当前值"),
            ("move_type", "0直线插补 1PTP关节"),
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
        self.backend_info.setMaximumHeight(170)
        self.current_info.setMaximumHeight(170)
        action_box.setMaximumHeight(170)
        top_layout.addWidget(self.backend_info, 2)
        top_layout.addWidget(self.current_info, 2)
        top_layout.addWidget(action_box, 1)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)

        left = QGroupBox("指令模板列表")
        left.setObjectName("panel")
        left.setMinimumWidth(240)
        left.setMaximumWidth(300)
        left_layout = QVBoxLayout(left)
        self.template_tree = QTreeWidget()
        self.template_tree.setHeaderLabels(["显示名称", "函数号"])
        self.template_tree.itemSelectionChanged.connect(self._on_template_selected)
        left_layout.addWidget(self.template_tree)

        middle = QGroupBox("模板编辑")
        middle.setObjectName("panel")
        middle.setMinimumWidth(430)
        middle_layout = QVBoxLayout(middle)
        form_widget = QWidget()
        form = QFormLayout(form_widget)

        self.name_edit = QLineEdit()
        self.func_num_combo = QComboBox()
        for label, func_num in FUNC_OPTIONS.items():
            self.func_num_combo.addItem(label, func_num)
        self.keywords_edit = QLineEdit()
        self.safety_edit = QLineEdit("5")
        self.func_name_edit = QLineEdit()
        self.func_name_edit.setReadOnly(True)
        self.desc_edit = QLineEdit()
        self.stop_mode_combo = QComboBox()
        self.stop_mode_combo.addItem("急停(0)", 0)
        self.stop_mode_combo.addItem("慢停(1)", 1)
        self.system_action_combo = QComboBox()
        self.system_action_combo.addItem("自定义/无动作", "custom")
        self.system_action_combo.addItem("急停按下", "estop")
        self.system_action_combo.addItem("急停松开", "estop_release")
        self.system_action_combo.addItem("暂停按下", "pause")
        self.system_action_combo.addItem("暂停松开/继续", "resume")
        self.system_action_combo.addItem("结束按下", "cancel")
        self.system_action_combo.addItem("结束松开", "cancel_release")
        self.system_action_combo.addItem("报警复位", "reset")
        self.estop_ctrl_combo = QComboBox()
        self.estop_ctrl_combo.addItem("不操作(0)", 0)
        self.estop_ctrl_combo.addItem("按下(1)", 1)
        self.estop_ctrl_combo.addItem("松开(2)", 2)
        self.pause_ctrl_combo = QComboBox()
        self.pause_ctrl_combo.addItem("不操作(0)", 0)
        self.pause_ctrl_combo.addItem("按下(1)", 1)
        self.pause_ctrl_combo.addItem("松开/继续(2)", 2)
        self.cancel_ctrl_combo = QComboBox()
        self.cancel_ctrl_combo.addItem("不操作(0)", 0)
        self.cancel_ctrl_combo.addItem("按下(1)", 1)
        self.cancel_ctrl_combo.addItem("松开(2)", 2)
        self.reset_ctrl_combo = QComboBox()
        self.reset_ctrl_combo.addItem("不操作(0)", 0)
        self.reset_ctrl_combo.addItem("复位(1)", 1)
        self.axis_no_edit = QLineEdit("0")
        self.pos_val_edit = QLineEdit("0")
        self.spd_pct_edit = QLineEdit("50")
        self.acc_pct_edit = QLineEdit("60")
        self.dec_pct_edit = QLineEdit("60")
        self.stop_cmd_combo = QComboBox()
        for value, label in STOP_CMD_LABELS.items():
            self.stop_cmd_combo.addItem(f"{label}({value})", value)
        self.fuzzy_pos_combo = QComboBox()
        self.fuzzy_pos_combo.addItem("绝对(0)", 0)
        self.fuzzy_pos_combo.addItem("增量(1)", 1)
        self.fuzzy_spd_combo = QComboBox()
        self.fuzzy_spd_combo.addItem("绝对值(0)", 0)
        self.fuzzy_spd_combo.addItem("叠加当前值(1)", 1)
        self.fuzzy_acc_combo = QComboBox()
        self.fuzzy_acc_combo.addItem("绝对值(0)", 0)
        self.fuzzy_acc_combo.addItem("叠加当前值(1)", 1)
        self.fuzzy_dec_combo = QComboBox()
        self.fuzzy_dec_combo.addItem("绝对值(0)", 0)
        self.fuzzy_dec_combo.addItem("叠加当前值(1)", 1)
        self.x_edit = QLineEdit("0")
        self.y_edit = QLineEdit("0")
        self.z_edit = QLineEdit("0")
        self.rx_edit = QLineEdit("0")
        self.ry_edit = QLineEdit("0")
        self.rz_edit = QLineEdit("0")
        self.move_type_combo = QComboBox()
        self.move_type_combo.addItem("直线插补(0)", 0)
        self.move_type_combo.addItem("PTP(1)", 1)
        self.point_count_edit = QLineEdit("2")
        self.point_count_edit.setReadOnly(True)
        self.points_table = QTableWidget(2, 6)
        self.points_table.setHorizontalHeaderLabels(["X", "Y", "Z", "Rx", "Ry", "Rz"])
        self.points_table.setMinimumHeight(130)
        self.points_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._set_points_table_values([[0, 0, 0, 0, 0, 0], [10, 10, 10, 0, 0, 0]])
        self.point_add_btn = QPushButton("新增点")
        self.point_delete_btn = QPushButton("删除点")
        self.point_up_btn = QPushButton("上移")
        self.point_down_btn = QPushButton("下移")
        self.point_buttons = QWidget()
        point_buttons_layout = QHBoxLayout(self.point_buttons)
        point_buttons_layout.setContentsMargins(0, 0, 0, 0)
        for button in (self.point_add_btn, self.point_delete_btn, self.point_up_btn, self.point_down_btn):
            point_buttons_layout.addWidget(button)
        self.delay_sec_edit = QLineEdit("1")
        self.io_no_edit = QLineEdit("0")
        self.io_action_combo = QComboBox()
        self.io_action_combo.addItem("关闭(0)", 0)
        self.io_action_combo.addItem("打开(1)", 1)

        self.record_form_rows: dict[str, tuple[QLabel, QWidget]] = {}

        def add_record_row(key: str, label: str, widget: QWidget) -> None:
            """新增记录。"""
            row_label = QLabel(label + ":")
            form.addRow(row_label, widget)
            self.record_form_rows[key] = (row_label, widget)

        add_record_row("name", "显示名称 (query_key)", self.name_edit)
        add_record_row("func_num", "函数号 (func_id)", self.func_num_combo)
        add_record_row("func_name", "函数名 (func_name)", self.func_name_edit)
        add_record_row("keywords", "自然语言关键词 (keywords)", self.keywords_edit)
        add_record_row("safety", "安全等级 (safety_level)", self.safety_edit)
        add_record_row("desc", "说明 (description)", self.desc_edit)
        add_record_row("system_action", "快捷动作 (Func104)", self.system_action_combo)
        add_record_row("estop_ctrl", "急停控制 (estop_ctrl)", self.estop_ctrl_combo)
        add_record_row("pause_ctrl", "暂停控制 (pause_ctrl)", self.pause_ctrl_combo)
        add_record_row("cancel_ctrl", "结束控制 (cancel_ctrl)", self.cancel_ctrl_combo)
        add_record_row("reset_ctrl", "报警复位 (reset_ctrl)", self.reset_ctrl_combo)
        add_record_row("axis_no", "轴号 (axis_no)", self.axis_no_edit)
        add_record_row("pos_val", "目标值 (pos_val)", self.pos_val_edit)
        add_record_row("spd_pct", "速度百分比 (spd_pct)", self.spd_pct_edit)
        add_record_row("acc_pct", "加速度百分比 (acc_pct)", self.acc_pct_edit)
        add_record_row("dec_pct", "减速度百分比 (dec_pct)", self.dec_pct_edit)
        add_record_row("stop_cmd", "停止指令 (stop_cmd)", self.stop_cmd_combo)
        add_record_row("fuzzy_pos", "位置模式 (fuzzy_pos)", self.fuzzy_pos_combo)
        add_record_row("fuzzy_spd", "速度模式 (fuzzy_spd)", self.fuzzy_spd_combo)
        add_record_row("fuzzy_acc", "加速度模式 (fuzzy_acc)", self.fuzzy_acc_combo)
        add_record_row("fuzzy_dec", "减速度模式 (fuzzy_dec)", self.fuzzy_dec_combo)
        add_record_row("target_x", "目标X (target_x)", self.x_edit)
        add_record_row("target_y", "目标Y (target_y)", self.y_edit)
        add_record_row("target_z", "目标Z (target_z)", self.z_edit)
        add_record_row("target_rx", "目标Rx (target_rx)", self.rx_edit)
        add_record_row("target_ry", "目标Ry (target_ry)", self.ry_edit)
        add_record_row("target_rz", "目标Rz (target_rz)", self.rz_edit)
        add_record_row("move_type", "运动模式 (move_type)", self.move_type_combo)
        add_record_row("point_count", "点数 (point_count)", self.point_count_edit)
        add_record_row("points", "插补点 (points)", self.points_table)
        add_record_row("point_buttons", "点位操作", self.point_buttons)
        add_record_row("delay_sec", "延时秒 (delay_sec)", self.delay_sec_edit)
        add_record_row("io_no", "IO编号 (io_no)", self.io_no_edit)
        add_record_row("io_action", "IO动作 (io_action)", self.io_action_combo)

        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_scroll.setWidget(form_widget)
        middle_layout.addWidget(form_scroll)

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
        self.safe_r_min_edit = QLineEdit("0")
        self.safe_r_max_edit = QLineEdit("0")
        self.safe_z_min_edit = QLineEdit("0")
        self.safe_z_max_edit = QLineEdit("0")
        self.safe_speed_max_edit = QLineEdit("0")
        self.safe_acc_max_edit = QLineEdit("0")
        self.safe_dec_max_edit = QLineEdit("0")
        self.default_spd_pct_edit = QLineEdit("50")
        self.default_acc_pct_edit = QLineEdit("50")
        self.default_dec_pct_edit = QLineEdit("50")
        self.motion_timeout_edit = QLineEdit("180")
        self.operator_tts_enabled_check = QCheckBox("启用用户页语音播报")
        self.broadcast_dedupe_window_edit = QLineEdit("5")
        self.tts_retry_delay_edit = QLineEdit("5")
        self.tts_max_failures_edit = QLineEdit("3")
        self.operator_confirm_timeout_edit = QLineEdit("60")
        self.l3_min_step_delay_edit = QLineEdit("0")
        self.l3_cumulative_error_limit_edit = QLineEdit("0")
        self.joint_limit_edits: list[tuple[QLineEdit, QLineEdit]] = []
        for _index in range(6):
            self.joint_limit_edits.append((QLineEdit(), QLineEdit()))
        for label, widget in [
            ("X最小", self.range_x_min_edit),
            ("X最大", self.range_x_max_edit),
            ("Y最小", self.range_y_min_edit),
            ("Y最大", self.range_y_max_edit),
            ("Z最小", self.range_z_min_edit),
            ("Z最大", self.range_z_max_edit),
            ("最小半径", self.safe_r_min_edit),
            ("最大半径", self.safe_r_max_edit),
            ("最低高度", self.safe_z_min_edit),
            ("最高高度", self.safe_z_max_edit),
            ("最大速度", self.safe_speed_max_edit),
            ("最大加速度", self.safe_acc_max_edit),
            ("最大减速度", self.safe_dec_max_edit),
            ("默认速度(%)", self.default_spd_pct_edit),
            ("默认加速度(%)", self.default_acc_pct_edit),
            ("默认减速度(%)", self.default_dec_pct_edit),
            ("运动超时(s)", self.motion_timeout_edit),
            ("语音播报", self.operator_tts_enabled_check),
            ("播报去重窗口(s)", self.broadcast_dedupe_window_edit),
            ("TTS重试间隔(s)", self.tts_retry_delay_edit),
            ("TTS最大连续失败", self.tts_max_failures_edit),
            ("安全确认超时(s)", self.operator_confirm_timeout_edit),
            ("L3最小步间隔(ms)", self.l3_min_step_delay_edit),
            ("L3累计误差上限(mm)", self.l3_cumulative_error_limit_edit),
        ]:
            config_layout.addRow(label + ":", widget)
        for index, (min_edit, max_edit) in enumerate(self.joint_limit_edits, start=1):
            row = QHBoxLayout()
            row.addWidget(min_edit)
            row.addWidget(max_edit)
            config_layout.addRow(f"J{index}软限位:", row)
        config_buttons = QHBoxLayout()
        config_save_btn = QPushButton("保存配置")
        config_save_btn.clicked.connect(self._save_system_config)
        config_reload_btn = QPushButton("重载配置")
        config_reload_btn.clicked.connect(self._reload_system_config)
        config_read_ctrl_btn = QPushButton("读取控制器限位")
        config_read_ctrl_btn.clicked.connect(self._read_controller_safety_limits)
        config_buttons.addWidget(config_save_btn)
        config_buttons.addWidget(config_reload_btn)
        config_buttons.addWidget(config_read_ctrl_btn)
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
        self.flow_manage_delay_edit = QLineEdit("1000")
        flow_name_form.addRow("流程名称:", self.flow_manage_name_edit)
        flow_name_form.addRow("步间延时(ms):", self.flow_manage_delay_edit)
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
        self.flow_available_tree.setHeaderLabels(["模板名称", "函数号"])
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

        config_scroll = QScrollArea()
        config_scroll.setObjectName("systemConfigScroll")
        config_scroll.setWidgetResizable(True)
        config_scroll.setFrameShape(QFrame.Shape.NoFrame)
        config_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        config_scroll.setWidget(config_group)

        right_tabs = QTabWidget()
        right_tabs.setObjectName("panel")
        self.engineer_right_tabs = right_tabs
        right_tabs.addTab(preview_group, "JSON预览")
        right_tabs.addTab(config_scroll, "系统参数")
        right_tabs.addTab(avoidance_group, "安全中间点")
        right_tabs.addTab(flow_manage_group, "流程管理")

        bottom_layout.addWidget(left, 14)
        bottom_layout.addWidget(middle, 24)
        bottom_layout.addWidget(right_tabs, 22)

        manage_splitter = QSplitter(Qt.Orientation.Vertical)
        manage_splitter.setChildrenCollapsible(False)
        manage_splitter.addWidget(top)
        manage_splitter.addWidget(bottom)
        manage_splitter.setStretchFactor(0, 1)
        manage_splitter.setStretchFactor(1, 5)
        manage_splitter.setSizes([170, 760])
        layout.addWidget(manage_splitter, 1)

        for widget in [
            self.name_edit,
            self.keywords_edit,
            self.safety_edit,
            self.desc_edit,
            self.axis_no_edit,
            self.pos_val_edit,
            self.spd_pct_edit,
            self.acc_pct_edit,
            self.dec_pct_edit,
            self.x_edit,
            self.y_edit,
            self.z_edit,
            self.rx_edit,
            self.ry_edit,
            self.rz_edit,
            self.point_count_edit,
            self.delay_sec_edit,
            self.io_no_edit,
        ]:
            widget.textChanged.connect(self._render_preview)
        self.points_table.itemChanged.connect(self._sync_points_from_table)
        self.point_add_btn.clicked.connect(self._add_interp_point)
        self.point_delete_btn.clicked.connect(self._delete_interp_point)
        self.point_up_btn.clicked.connect(lambda: self._move_interp_point(-1))
        self.point_down_btn.clicked.connect(lambda: self._move_interp_point(1))
        self.func_num_combo.currentIndexChanged.connect(self._sync_func_form_mode)
        self.func_num_combo.currentIndexChanged.connect(self._sync_func_name_display)
        self.func_num_combo.currentIndexChanged.connect(self._render_preview)
        self.stop_mode_combo.currentIndexChanged.connect(self._render_preview)
        self.system_action_combo.currentIndexChanged.connect(self._on_func104_action_changed)
        self.estop_ctrl_combo.currentIndexChanged.connect(self._on_func104_control_changed)
        self.pause_ctrl_combo.currentIndexChanged.connect(self._on_func104_control_changed)
        self.cancel_ctrl_combo.currentIndexChanged.connect(self._on_func104_control_changed)
        self.reset_ctrl_combo.currentIndexChanged.connect(self._on_func104_control_changed)
        self.stop_cmd_combo.currentIndexChanged.connect(self._render_preview)
        self.fuzzy_pos_combo.currentIndexChanged.connect(self._render_preview)
        self.fuzzy_spd_combo.currentIndexChanged.connect(self._render_preview)
        self.fuzzy_acc_combo.currentIndexChanged.connect(self._render_preview)
        self.fuzzy_dec_combo.currentIndexChanged.connect(self._render_preview)
        self.move_type_combo.currentIndexChanged.connect(self._render_preview)
        self.io_action_combo.currentIndexChanged.connect(self._render_preview)
        self._load_system_config_into_form()
        self._load_avoidance_config_into_form()
        self._sync_func_name_display()
        self._sync_func_form_mode()

        return page

    def _build_log_page(self) -> QWidget:
        """构建页面。"""
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
        """应用相关数据。"""
        self.setStyleSheet("""
            QMainWindow { background: #25d9e0; }
            QWidget { font-size: 13px; color: #111; }
            QLabel { background: transparent; }
            QScrollArea {
                background: transparent;
                border: 0;
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
                min-width: 68px;
                padding: 5px 8px;
                margin-right: 2px;
                background: rgba(255,255,255,0.55);
                border: 1px solid #4a4a4a;
                border-bottom: 0;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                font-weight: 600;
                font-size: 14px;
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
            QLineEdit, QComboBox, QTextEdit, QTextBrowser, QTreeWidget, QTableWidget {
                background: rgba(255,255,255,0.82);
                border: 1px solid #666;
                border-radius: 4px;
                padding: 3px 5px;
            }
            QTextEdit, QTextBrowser, QTreeWidget, QTableWidget {
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
            QPushButton#workspaceToggleButton {
                min-height: 26px;
                padding: 2px 12px;
                border-radius: 4px;
                font-size: 13px;
                background: #f5f7fb;
            }
            QFrame#loginPage {
                background: #ffffff;
            }
            QFrame#loginCard {
                background: rgba(255,255,255,0.96);
                border: 1px solid #b8c7d6;
                border-radius: 14px;
            }
            QLabel#loginLogo {
                min-width: 58px;
                max-width: 58px;
                min-height: 58px;
                max-height: 58px;
                border-radius: 29px;
                background: #e8e8e8;
                color: #0f6b4f;
                font-size: 26px;
                font-weight: 900;
            }
            QLabel#loginTitle {
                color: #111827;
                font-size: 23px;
                font-weight: 900;
            }
            QLabel#loginSubtitle {
                color: #374151;
                font-size: 14px;
                font-weight: 500;
            }
            QFrame#loginDivider {
                color: #cbd5e1;
                background: #cbd5e1;
                min-height: 1px;
                max-height: 1px;
                border: 0;
            }
            QLabel#loginFieldLabel {
                color: #111827;
                font-size: 14px;
                font-weight: 800;
                letter-spacing: 0px;
            }
            QFrame#loginSegment {
                background: #f4f6f9;
                border: 1px solid #c7d2de;
                border-radius: 8px;
            }
            QPushButton#loginSegmentButton {
                min-height: 34px;
                border: 0;
                border-radius: 6px;
                background: transparent;
                color: #111827;
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton#loginSegmentButton[active="true"] {
                background: #ffffff;
                border: 1px solid #d6dee8;
            }
            QLineEdit#loginInput {
                min-height: 38px;
                border: 1px solid #c7d2de;
                border-radius: 0;
                background: #f7f8fb;
                padding: 4px 14px;
                color: #111827;
                font-size: 14px;
                font-weight: 700;
            }
            QLineEdit#loginInput:focus {
                border: 1px solid #1f8f68;
                background: #ffffff;
            }
            QLineEdit#loginPasswordInput {
                min-height: 38px;
                border: 1px solid #c7d2de;
                border-radius: 0;
                background: #f7f8fb;
                padding: 4px 14px;
                color: #111827;
                font-size: 14px;
                font-weight: 400;
            }
            QLineEdit#loginPasswordInput:focus {
                border: 1px solid #1f8f68;
                background: #ffffff;
            }
            QComboBox#loginCombo {
                min-height: 38px;
                border: 1px solid #c7d2de;
                border-radius: 0;
                background: #f7f8fb;
                padding: 4px 12px;
                color: #111827;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#loginCheckButton {
                min-height: 38px;
                padding: 4px 14px;
                border: 1px solid #c7d2de;
                border-radius: 4px;
                background: #ffffff;
                color: #111827;
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton#loginCheckButton:hover {
                background: #eef7f2;
                border-color: #1f8f68;
            }
            QLabel#loginConnectionStatus {
                min-height: 20px;
                color: #475569;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#loginServiceStatus {
                color: #64748b;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#loginError {
                min-height: 20px;
                color: #b91c1c;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#loginAuthButton {
                min-height: 50px;
                border: 0;
                border-radius: 8px;
                background: #1f8f68;
                color: #ffffff;
                font-size: 17px;
                font-weight: 900;
            }
            QPushButton#loginAuthButton:hover {
                background: #177a58;
            }
            QLabel#loginReady {
                color: #1f2937;
                font-size: 16px;
                font-weight: 800;
            }
            QLabel#loginVersion {
                color: #6b7280;
                font-size: 16px;
                font-weight: 800;
            }
            QFrame#operatorPage {
                background: #ffffff;
            }
            QFrame#operatorTopHeader {
                background: #ffffff;
                border-bottom: 1px solid #e5e7eb;
                min-height: 46px;
                max-height: 46px;
            }
            QLabel#operatorHeaderTitle {
                font-size: 17px;
                font-weight: 800;
                color: #111827;
            }
            QLabel#operatorHeaderStatus {
                padding: 3px 10px;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                background: #f9fafb;
                font-size: 12px;
                font-weight: 800;
            }
            QPushButton#operatorHeaderButton {
                min-height: 28px;
                padding: 3px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background: #ffffff;
                font-size: 13px;
                font-weight: 700;
            }
            QWidget#operatorBody {
                background: #ffffff;
            }
            QFrame#operatorStatusBar,
            QFrame#operatorLeftSidebar,
            QFrame#operatorRightSidebar,
            QFrame#operatorDialogPanel,
            QFrame#operatorActionBar {
                background: #f7f7f8;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
            }
            QFrame#operatorStatusCard {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QFrame#operatorScene {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QLabel#operatorStateLabel {
                font-size: 24px;
                font-weight: 800;
            }
            QLabel#operatorSmallLabel {
                font-size: 13px;
                color: #4b5563;
            }
            QLabel#operatorMetric {
                font-size: 13px;
                font-weight: 600;
                color: #111827;
            }
            QLabel#operatorStatusBadge {
                min-height: 42px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background: #f8fafc;
                color: #334155;
                font-size: 12px;
                font-weight: 800;
                padding: 4px 2px;
            }
            QLabel#operatorStatusBadge[active="true"] {
                border-color: #fca5a5;
                background: #fff1f2;
                color: #b91c1c;
            }
            QLabel#operatorPoseCell {
                min-height: 38px;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                background: #f8fafc;
                color: #111827;
                font-size: 12px;
                font-weight: 800;
                padding: 3px 2px;
            }
            QLabel#operatorMetricLarge {
                font-size: 12px;
                font-weight: 700;
                color: #475569;
            }
            QLabel#operatorSidebarTitle {
                font-size: 14px;
                font-weight: 800;
                color: #111827;
            }
            QLabel#operatorSceneTitle {
                font-size: 19px;
                font-weight: 800;
                color: #111827;
            }
            QLabel#operatorSceneSubtitle,
            QLabel#operatorChecklistItem,
            QLabel#operatorDialogText,
            QLabel#operatorAlarmText {
                font-size: 14px;
                font-weight: 600;
                color: #374151;
            }
            QLabel#operatorAlarmText {
                color: #991b1b;
            }
            QTextBrowser#operatorRecentBrowser {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                font-size: 14px;
            }
            QLabel#operatorChatTitle {
                font-size: 18px;
                font-weight: 800;
                color: #111827;
            }
            QLabel#operatorChatHint {
                font-size: 12px;
                color: #64748b;
                font-weight: 600;
            }
            QScrollArea#operatorChatScroll {
                background: #ffffff;
                border: 0;
                border-radius: 0;
            }
            QFrame#operatorChatContent {
                background: #ffffff;
            }
            QLabel#operatorAiAvatar {
                background: #334155;
                color: #ffffff;
                border-radius: 17px;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#operatorUserAvatar {
                background: #2563eb;
                color: #ffffff;
                border-radius: 17px;
                font-size: 12px;
                font-weight: 800;
            }
            QFrame#operatorAiBubble {
                background: #f6f8fb;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
            }
            QFrame#operatorUserBubble {
                background: #f4f4f4;
                border: 1px solid #ececec;
                border-radius: 16px;
            }
            QLabel#operatorAiSender {
                color: #475569;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#operatorUserSender {
                color: #6b7280;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#operatorChatText {
                font-size: 15px;
                line-height: 1.35;
                color: #111827;
            }
            QPushButton#operatorThinkingToggle {
                color: #64748b;
                background: transparent;
                border: 0;
                padding: 2px 0;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#operatorThinkingToggle:checked {
                color: #334155;
            }
            QLabel#operatorThinkingDetail {
                color: #64748b;
                font-size: 12px;
                line-height: 1.25;
                padding: 2px 0 0 0;
            }
            QFrame#operatorUserBubble QLabel#operatorChatText {
                color: #111827;
            }
            QProgressBar#operatorProgress {
                min-height: 22px;
                max-height: 22px;
                border: 1px solid #64748b;
                border-radius: 5px;
                text-align: center;
                background: #e2e8f0;
                font-size: 12px;
                font-weight: 700;
            }
            QProgressBar#operatorProgress::chunk {
                background: #2563eb;
                border-radius: 4px;
            }
            QScrollArea#operatorFlowTimelineScroll {
                background: #f8fafc;
                border: 0;
            }
            QFrame#operatorFlowTimelineContent {
                background: #f8fafc;
            }
            QFrame#operatorFlowStepCard {
                background: #ffffff;
                border: 1px solid #dbe4ee;
                border-radius: 7px;
            }
            QFrame#operatorFlowStepCard[status="current"] {
                border: 2px solid #2563eb;
                background: #ffffff;
            }
            QFrame#operatorFlowStepCard[status="done"] {
                border-color: #bbf7d0;
                background: #f7fef9;
            }
            QFrame#operatorFlowStepCard[status="pending"] {
                border-style: dashed;
                background: #f8fafc;
            }
            QLabel#operatorFlowStepDot {
                border: 2px solid #cbd5e1;
                border-radius: 15px;
                color: #64748b;
                font-size: 13px;
                font-weight: 900;
                background: #ffffff;
            }
            QLabel#operatorFlowStepDot[status="done"] {
                border-color: #10b981;
                color: #10b981;
                background: #ecfdf5;
            }
            QLabel#operatorFlowStepDot[status="current"] {
                border-color: #2563eb;
                color: #ffffff;
                background: #2563eb;
            }
            QLabel#operatorFlowStepTitle {
                color: #111827;
                font-size: 13px;
                font-weight: 900;
            }
            QLabel#operatorFlowStepTitle[status="current"] {
                color: #0b3aa5;
            }
            QLabel#operatorFlowStepBody {
                color: #334155;
                font-size: 12px;
                font-weight: 600;
            }
            QProgressBar#operatorFlowStepProgress {
                min-height: 7px;
                max-height: 7px;
                border: 0;
                border-radius: 4px;
                background: #e5e7eb;
            }
            QProgressBar#operatorFlowStepProgress::chunk {
                background: #2563eb;
                border-radius: 4px;
            }
            QPushButton#operatorActionButton {
                min-height: 34px;
                padding: 4px 12px;
                border-radius: 8px;
                border: 1px solid #d1d5db;
                background: #ffffff;
                color: #111827;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#operatorActionButton:hover {
                background: #f1f5f9;
            }
            QPushButton#operatorActionButton[klass="green"] {
                background: #19c37d;
                border-color: #19c37d;
                color: #ffffff;
            }
            QPushButton#operatorActionButton[klass="red"] {
                background: #ef4444;
                border-color: #dc2626;
                color: #ffffff;
            }
            QPushButton#operatorActionButton[klass="yellow"] {
                background: #fde68a;
                border-color: #f59e0b;
                color: #111827;
            }
            QLineEdit#operatorChatInput {
                min-height: 36px;
                border: 1px solid #d1d5db;
                border-radius: 18px;
                background: #ffffff;
                color: #111827;
                placeholder-text-color: #9ca3af;
                padding: 4px 14px;
                font-size: 14px;
            }
            QLineEdit#operatorHostEdit {
                min-height: 30px;
                border: 1px solid #d1d5db;
                border-radius: 7px;
                background: #ffffff;
                padding: 3px 9px;
                font-size: 13px;
                font-weight: 600;
            }
        """)

    def _make_info_group(self, title: str) -> QGroupBox:
        """处理分组。"""
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
            self.current_options_label = QLabel("-")
            self.current_options_label.setWordWrap(True)
            for label, widget in [
                ("显示名称", self.current_name_label), ("指令码", self.current_code_label),
                ("指令类型", self.current_cmd_label), ("模板分类", self.current_type_label),
                ("参数说明", self.current_options_label),
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
        """处理分组。"""
        group = QGroupBox(title)
        layout = QFormLayout(group)
        for label, value in rows:
            layout.addRow(label + ":", QLabel(value))
        return group

    def _refresh_status_labels(self) -> None:
        """刷新状态。"""
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
        self._refresh_overall_state_indicator()

    def _refresh_overall_state_indicator(self) -> None:
        """刷新状态。"""
        state_text, color, detail = self._compute_overall_state()
        self.status_light_label.setText(f"<span style='color:{color};'>●</span> {state_text}")
        self.status_light_detail_label.setText(detail)

    def _compute_overall_state(self) -> tuple[str, str, str]:
        """处理状态。"""
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
        """处理实时数据快照。"""
        overall_state, _, _ = self._compute_overall_state()
        return (overall_state, self.busy, self.run_state, self.alarm_code)

