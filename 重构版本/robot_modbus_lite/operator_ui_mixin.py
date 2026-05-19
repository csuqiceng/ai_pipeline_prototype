"""Operator-facing Qt page for the natural-language robot interface."""

from __future__ import annotations

import html
import re
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QStackedWidget,
)


class OperatorUiMixin:
    """Build and update the simplified operator page."""

    def _build_operator_page(self) -> QWidget:
        """Build the user-facing page described by the companion UI spec."""
        self._operator_scene_override: str | None = None
        self._operator_compact = False
        self._operator_previous_geometry = None
        self._operator_fullscreen_geometry = None
        self._operator_pending_confirm_plan = None
        self._operator_chat_messages: list[tuple[str, str]] = [("assistant", "系统在线")]
        self._operator_last_user_text = ""
        self._operator_last_response_text = "系统在线"
        self._operator_chat_rendered = False
        self._operator_chat_autoscroll_pending = False
        self._operator_full_status_html = ""
        self._operator_recent_events_html = ""

        page = QFrame()
        page.setObjectName("operatorPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("operatorTopHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(10)
        title = QLabel("机械手 AI 助手")
        title.setObjectName("operatorHeaderTitle")
        self.operator_header_status_label = QLabel("空闲")
        self.operator_header_status_label.setObjectName("operatorHeaderStatus")
        header_layout.addWidget(title)
        header_layout.addWidget(self.operator_header_status_label)
        header_layout.addStretch(1)
        header_engineer_btn = QPushButton("工程师")
        header_engineer_btn.setObjectName("operatorHeaderButton")
        header_engineer_btn.clicked.connect(lambda: self._set_workspace_mode("engineer"))
        header_layout.addWidget(header_engineer_btn)
        layout.addWidget(header)

        body = QWidget()
        body.setObjectName("operatorBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 14)
        body_layout.setSpacing(12)

        self.operator_scene_stack = QStackedWidget()
        self._operator_scene_indexes: dict[str, int] = {}
        for name, widget in [
            ("idle", self._build_operator_idle_scene()),
            ("precheck", self._build_operator_precheck_scene()),
            ("execute", self._build_operator_execute_scene()),
            ("confirm", self._build_operator_confirm_scene()),
            ("alarm", self._build_operator_alarm_scene()),
            ("query", self._build_operator_query_scene()),
        ]:
            self._operator_scene_indexes[name] = self.operator_scene_stack.addWidget(widget)

        body_layout.addWidget(self._build_operator_status_bar())
        body_layout.addWidget(self._build_operator_dialog_panel(), 1)
        body_layout.addWidget(self._build_operator_right_sidebar())
        layout.addWidget(body, 1)

        self.operator_refresh_timer = QTimer(self)
        self.operator_refresh_timer.setInterval(500)
        self.operator_refresh_timer.timeout.connect(self._refresh_operator_view)
        self.operator_refresh_timer.start()
        self._refresh_operator_view()
        return page

    def _build_operator_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("operatorLeftSidebar")
        bar.setFixedWidth(268)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        state_card = QFrame()
        state_card.setObjectName("operatorStatusCard")
        state_layout = QVBoxLayout(state_card)
        state_layout.setContentsMargins(12, 10, 12, 10)
        self.operator_state_label = QLabel("● 空闲")
        self.operator_state_label.setObjectName("operatorStateLabel")
        self.operator_current_label = QLabel("当前: 空闲")
        self.operator_current_label.setObjectName("operatorSmallLabel")
        self.operator_current_label.setWordWrap(True)
        state_layout.addWidget(self.operator_state_label)
        state_layout.addWidget(self.operator_current_label)
        layout.addWidget(state_card)

        connection_card = QFrame()
        connection_card.setObjectName("operatorStatusCard")
        connection_layout = QVBoxLayout(connection_card)
        connection_layout.setContentsMargins(12, 10, 12, 10)
        connection_layout.setSpacing(8)
        connection_title = QLabel("控制器连接")
        connection_title.setObjectName("operatorSidebarTitle")
        self.operator_host_edit = QLineEdit()
        self.operator_host_edit.setObjectName("operatorHostEdit")
        self.operator_host_edit.setPlaceholderText("控制器地址")
        self.operator_host_edit.setText(self.host_edit.text() if hasattr(self, "host_edit") else "")
        self.operator_host_edit.returnPressed.connect(self._operator_check_connection)
        connection_button_row = QHBoxLayout()
        apply_host_btn = QPushButton("应用")
        apply_host_btn.setObjectName("operatorActionButton")
        apply_host_btn.clicked.connect(self._operator_apply_connection_settings)
        check_host_btn = QPushButton("检测连接")
        check_host_btn.setObjectName("operatorActionButton")
        check_host_btn.clicked.connect(self._operator_check_connection)
        connection_button_row.addWidget(apply_host_btn)
        connection_button_row.addWidget(check_host_btn)
        connection_layout.addWidget(connection_title)
        connection_layout.addWidget(self.operator_host_edit)
        connection_layout.addLayout(connection_button_row)
        layout.addWidget(connection_card)

        flags_card = QFrame()
        flags_card.setObjectName("operatorStatusCard")
        flags_layout = QVBoxLayout(flags_card)
        flags_layout.setContentsMargins(12, 10, 12, 10)
        self.operator_flags_label = QLabel("急停:关  暂停:关  报警:无")
        self.operator_flags_label.setObjectName("operatorMetric")
        self.operator_flags_label.setWordWrap(True)
        self.operator_comm_label = QLabel("通讯:检测中")
        self.operator_comm_label.setObjectName("operatorMetric")
        self.operator_comm_label.setWordWrap(True)
        flags_layout.addWidget(self.operator_flags_label)
        flags_layout.addWidget(self.operator_comm_label)
        layout.addWidget(flags_card)

        axis_card = QFrame()
        axis_card.setObjectName("operatorStatusCard")
        axis_layout = QVBoxLayout(axis_card)
        axis_layout.setContentsMargins(12, 10, 12, 10)
        axis_layout.setSpacing(6)
        axis_title = QLabel("实时位置")
        axis_title.setObjectName("operatorSidebarTitle")
        axis_layout.addWidget(axis_title)
        joint_grid = QGridLayout()
        joint_grid.setHorizontalSpacing(10)
        joint_grid.setVerticalSpacing(4)
        self.operator_joint_labels = []
        for idx in range(6):
            label = QLabel(f"J{idx + 1}: -")
            label.setObjectName("operatorMetric")
            self.operator_joint_labels.append(label)
            joint_grid.addWidget(label, idx // 2, idx % 2)
        axis_layout.addLayout(joint_grid)
        self.operator_pose_label = QLabel("X:-  Y:-  Z:-  RX/RY/RZ:-")
        self.operator_pose_label.setObjectName("operatorMetric")
        self.operator_pose_label.setWordWrap(True)
        axis_layout.addWidget(self.operator_pose_label)
        layout.addWidget(axis_card)

        action_card = QFrame()
        action_card.setObjectName("operatorStatusCard")
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(12, 10, 12, 10)
        action_layout.setSpacing(8)
        action_title = QLabel("快捷操作")
        action_title.setObjectName("operatorSidebarTitle")
        action_layout.addWidget(action_title)
        for text, slot, klass in [
            ("急停", lambda: self._handle_system_action("sys_estop"), "red"),
            ("暂停", lambda: self._handle_system_action("sys_pause"), "yellow"),
            ("继续", lambda: self._handle_system_action("sys_resume"), ""),
            ("停止流程", self._operator_stop_current, ""),
            ("小窗口", self._operator_toggle_compact, ""),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            if klass:
                btn.setProperty("klass", klass)
            btn.clicked.connect(slot)
            action_layout.addWidget(btn)
            if text == "小窗口":
                self.operator_compact_btn = btn
        layout.addWidget(action_card)
        layout.addStretch(1)

        return bar

    def _build_operator_right_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("operatorRightSidebar")
        sidebar.setFixedWidth(336)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        scene_title = QLabel("运行面板")
        scene_title.setObjectName("operatorSidebarTitle")
        layout.addWidget(scene_title)
        layout.addWidget(self.operator_scene_stack, 1)

        quick_card = QFrame()
        quick_card.setObjectName("operatorStatusCard")
        quick_layout = QGridLayout(quick_card)
        quick_layout.setContentsMargins(10, 10, 10, 10)
        quick_layout.setHorizontalSpacing(8)
        quick_layout.setVerticalSpacing(8)
        for idx, (text, slot) in enumerate([
            ("完整状态", self._operator_show_full_status),
            ("主界面", self._operator_go_home),
            ("全屏", self._operator_show_fullscreen),
            ("工程师", lambda: self._set_workspace_mode("engineer")),
        ]):
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            btn.clicked.connect(slot)
            quick_layout.addWidget(btn, idx // 2, idx % 2)
            if text == "全屏":
                self.operator_fullscreen_btn = btn
        layout.addWidget(quick_card)
        return sidebar

    def _build_operator_idle_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.operator_idle_title = QLabel("系统在线，等待指令")
        self.operator_idle_title.setObjectName("operatorSceneTitle")
        self.operator_idle_subtitle = QLabel("最近操作")
        self.operator_idle_subtitle.setObjectName("operatorSceneSubtitle")
        self.operator_recent_browser = QTextBrowser()
        self.operator_recent_browser.setObjectName("operatorRecentBrowser")
        self.operator_recent_browser.setOpenExternalLinks(False)
        self.operator_recent_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_recent_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.operator_idle_title)
        layout.addWidget(self.operator_idle_subtitle)
        layout.addWidget(self.operator_recent_browser, 1)
        return scene

    def _build_operator_precheck_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.operator_precheck_title = QLabel("安全预检")
        self.operator_precheck_title.setObjectName("operatorSceneTitle")
        layout.addWidget(self.operator_precheck_title)
        self.operator_precheck_checks = []
        for text in ["指令接收", "设备状态检查", "安全参数检查", "运动规划预演"]:
            label = QLabel(text)
            label.setObjectName("operatorChecklistItem")
            self.operator_precheck_checks.append(label)
            layout.addWidget(label)
        self.operator_precheck_progress = QProgressBar()
        self.operator_precheck_progress.setObjectName("operatorProgress")
        self.operator_precheck_progress.setRange(0, 100)
        self.operator_precheck_progress.setValue(0)
        layout.addWidget(self.operator_precheck_progress)
        layout.addStretch(1)
        return scene

    def _build_operator_execute_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.operator_execute_title = QLabel("设备执行中")
        self.operator_execute_title.setObjectName("operatorSceneTitle")
        self.operator_execute_detail = QLabel("-")
        self.operator_execute_detail.setObjectName("operatorSceneSubtitle")
        self.operator_execute_detail.setWordWrap(True)
        self.operator_execute_progress = QProgressBar()
        self.operator_execute_progress.setObjectName("operatorProgress")
        self.operator_execute_progress.setRange(0, 100)
        self.operator_execute_progress.setFormat("估算 %p%")
        self.operator_execute_position = QLabel("-")
        self.operator_execute_position.setObjectName("operatorMetricLarge")
        self.operator_execute_position.setWordWrap(True)
        layout.addWidget(self.operator_execute_title)
        layout.addWidget(self.operator_execute_detail)
        layout.addWidget(self.operator_execute_progress)
        layout.addWidget(self.operator_execute_position)
        layout.addStretch(1)
        return scene

    def _build_operator_confirm_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.operator_confirm_title = QLabel("等待安全确认")
        self.operator_confirm_title.setObjectName("operatorSceneTitle")
        self.operator_confirm_detail = QLabel("当前没有需要确认的风险。")
        self.operator_confirm_detail.setObjectName("operatorSceneSubtitle")
        self.operator_confirm_detail.setWordWrap(True)
        button_row = QHBoxLayout()
        for text, slot in [
            ("确认执行", self._operator_confirm_execute),
            ("采纳建议", self._operator_accept_suggestion),
            ("取消", self._operator_cancel_confirm),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            btn.clicked.connect(slot)
            button_row.addWidget(btn)
        layout.addWidget(self.operator_confirm_title)
        layout.addWidget(self.operator_confirm_detail)
        layout.addLayout(button_row)
        layout.addStretch(1)
        return scene

    def _build_operator_alarm_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.operator_alarm_title = QLabel("报警")
        self.operator_alarm_title.setObjectName("operatorSceneTitle")
        self.operator_alarm_detail = QLabel("-")
        self.operator_alarm_detail.setObjectName("operatorAlarmText")
        self.operator_alarm_detail.setWordWrap(True)
        reset_btn = QPushButton("复位")
        reset_btn.setObjectName("operatorActionButton")
        reset_btn.setProperty("klass", "green")
        reset_btn.clicked.connect(lambda: self._handle_system_action("alarm_reset"))
        layout.addWidget(self.operator_alarm_title)
        layout.addWidget(self.operator_alarm_detail)
        layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return scene

    def _build_operator_query_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("完整状态")
        title.setObjectName("operatorSceneTitle")
        self.operator_full_status_browser = QTextBrowser()
        self.operator_full_status_browser.setObjectName("operatorRecentBrowser")
        self.operator_full_status_browser.setOpenExternalLinks(False)
        self.operator_full_status_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_full_status_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        back_btn = QPushButton("回到主界面")
        back_btn.setObjectName("operatorActionButton")
        back_btn.clicked.connect(self._operator_go_home)
        layout.addWidget(title)
        layout.addWidget(self.operator_full_status_browser, 1)
        layout.addWidget(back_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return scene

    def _build_operator_dialog_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("operatorDialogPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        chat_header = QHBoxLayout()
        chat_title = QLabel("AI 助手对话")
        chat_title.setObjectName("operatorChatTitle")
        chat_hint = QLabel("自然语言指令与系统回应")
        chat_hint.setObjectName("operatorChatHint")
        chat_header.addWidget(chat_title)
        chat_header.addStretch(1)
        chat_header.addWidget(chat_hint)
        layout.addLayout(chat_header)

        self.operator_chat_scroll = QScrollArea()
        self.operator_chat_scroll.setObjectName("operatorChatScroll")
        self.operator_chat_scroll.setWidgetResizable(True)
        self.operator_chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.operator_chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.operator_chat_scroll.verticalScrollBar().rangeChanged.connect(self._operator_on_chat_range_changed)
        self.operator_chat_content = QFrame()
        self.operator_chat_content.setObjectName("operatorChatContent")
        self.operator_chat_layout = QVBoxLayout(self.operator_chat_content)
        self.operator_chat_layout.setContentsMargins(12, 12, 12, 12)
        self.operator_chat_layout.setSpacing(10)
        self.operator_chat_layout.addStretch(1)
        self.operator_chat_scroll.setWidget(self.operator_chat_content)
        self.operator_chat_scroll.setMinimumHeight(220)
        layout.addWidget(self.operator_chat_scroll, 1)

        self.operator_voice_label = QLabel("语音输入: 等待语音输入...")
        self.operator_voice_label.setObjectName("operatorDialogText")
        self.operator_voice_label.setWordWrap(True)
        self.operator_voice_label.setVisible(False)
        self.operator_response_label = QLabel("系统回应: 系统在线")
        self.operator_response_label.setObjectName("operatorDialogText")
        self.operator_response_label.setWordWrap(True)
        self.operator_response_label.setVisible(False)

        input_row = QHBoxLayout()
        self.operator_command_edit = QLineEdit()
        self.operator_command_edit.setObjectName("operatorChatInput")
        self.operator_command_edit.setPlaceholderText("输入自然语言指令")
        self.operator_command_edit.returnPressed.connect(self._operator_execute_text)
        input_row.addWidget(self.operator_command_edit, 1)
        for text, slot, klass in [
            ("解析", self._operator_parse_text, ""),
            ("执行", self._operator_execute_text, "green"),
            ("录音", self._operator_toggle_microphone_recording, ""),
            ("清空", self._operator_clear_text, ""),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            if klass:
                btn.setProperty("klass", klass)
            btn.clicked.connect(slot)
            input_row.addWidget(btn)
            if text == "录音":
                self.operator_mic_btn = btn
        layout.addLayout(input_row)
        return panel

    def _build_operator_action_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("operatorActionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        buttons = [
            ("急停", lambda: self._handle_system_action("sys_estop"), "red"),
            ("暂停", lambda: self._handle_system_action("sys_pause"), "yellow"),
            ("继续", lambda: self._handle_system_action("sys_resume"), ""),
            ("停止流程", self._operator_stop_current, ""),
            ("完整状态", self._operator_show_full_status, ""),
            ("小窗口", self._operator_toggle_compact, ""),
            ("工程师", lambda: self._set_workspace_mode("engineer"), ""),
        ]
        for text, slot, klass in buttons:
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            if klass:
                btn.setProperty("klass", klass)
            btn.clicked.connect(slot)
            layout.addWidget(btn)
            if text == "小窗口":
                self.operator_compact_btn = btn
            if text == "工程师":
                self.operator_engineer_btn = btn
        return bar

    def _toggle_workspace_mode(self) -> None:
        target = "engineer"
        if getattr(self, "_workspace_mode", "engineer") == "engineer":
            target = "operator"
        self._set_workspace_mode(target)

    def _set_workspace_mode(self, mode: str) -> None:
        if not hasattr(self, "workspace_pages"):
            return
        previous_mode = getattr(self, "_workspace_mode", None)
        operator_mode = mode == "operator"
        if not operator_mode and getattr(self, "_authenticated_role", "") != "engineer":
            if hasattr(self, "_show_login_page"):
                self._show_login_page("engineer")
            return
        self._workspace_mode = "operator" if operator_mode else "engineer"
        self.workspace_pages.setCurrentIndex(1 if operator_mode else 0)
        if hasattr(self, "workspace_toggle_btn"):
            self.workspace_toggle_btn.setText("切换到工程师页面" if operator_mode else "切换到用户页面")
        if hasattr(self, "status_label") and previous_mode is not None:
            self.status_label.setText("已切换到用户操作页面。" if operator_mode else "已切换到工程师页面。")
        if operator_mode:
            self._refresh_operator_view()

    def _refresh_status_labels(self) -> None:
        super()._refresh_status_labels()
        self._refresh_operator_view()

    def _append_log_entry(self, entry: dict[str, Any]) -> None:
        super()._append_log_entry(entry)
        self._operator_add_chat_from_log(entry)
        self._refresh_operator_view()

    def _set_nlp_result_plan(self, plan) -> None:
        super()._set_nlp_result_plan(plan)
        if getattr(self, "nlp_sequence_running", False):
            self._operator_pending_confirm_plan = None
            self._operator_scene_override = None
        elif self._operator_plan_is_executable(plan):
            self._operator_pending_confirm_plan = plan
            self._operator_scene_override = "confirm"
            self._operator_add_chat_message("assistant", self._operator_plan_chat_text(plan))
        else:
            self._operator_pending_confirm_plan = None
            self._operator_scene_override = None
            self._operator_add_chat_message("assistant", f"我没有识别到可执行动作。{getattr(plan, 'reason', '')}")
        self._refresh_operator_view()

    def _set_nlp_parse_busy(self, busy: bool) -> None:
        super()._set_nlp_parse_busy(busy)
        if busy:
            self._operator_add_chat_message("assistant", "收到，正在解析指令。")
        self._operator_scene_override = None
        self._refresh_operator_view()

    def _set_nlp_execute_busy(self, busy: bool) -> None:
        super()._set_nlp_execute_busy(busy)
        if busy:
            self._operator_pending_confirm_plan = None
        self._operator_scene_override = None
        self._refresh_operator_view()

    def _parse_nlp_text(self) -> None:
        text = self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else ""
        if self._handle_operator_ui_command(text):
            return
        super()._parse_nlp_text()

    def _execute_nlp_text(self) -> None:
        text = self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else ""
        if self._handle_operator_ui_command(text):
            return
        self._operator_pending_confirm_plan = None
        super()._execute_nlp_text()

    def _clear_nlp_text(self) -> None:
        super()._clear_nlp_text()
        if hasattr(self, "operator_command_edit"):
            self.operator_command_edit.clear()
        self._operator_scene_override = None
        self._operator_pending_confirm_plan = None
        self._refresh_operator_view()

    def _operator_parse_text(self) -> None:
        if not self._operator_push_text_to_nlp():
            return
        self._operator_scene_override = None
        self._parse_nlp_text()

    def _operator_execute_text(self) -> None:
        if not self._operator_push_text_to_nlp():
            return
        self._operator_scene_override = None
        self._execute_nlp_text()

    def _operator_clear_text(self) -> None:
        self._clear_nlp_text()

    def _operator_toggle_microphone_recording(self) -> None:
        self._toggle_microphone_recording()
        self._sync_operator_mic_button()

    def _operator_push_text_to_nlp(self) -> bool:
        text = self.operator_command_edit.text().strip() if hasattr(self, "operator_command_edit") else ""
        if not text and hasattr(self, "nlp_input_edit"):
            text = self.nlp_input_edit.toPlainText().strip()
        if not text:
            self._show_warning("输入为空", "请输入自然语言文本。")
            return False
        self.nlp_input_edit.setPlainText(text)
        if hasattr(self, "operator_voice_label"):
            self.operator_voice_label.setText(f"语音输入: {text}")
        self._operator_add_chat_message("user", text)
        self._operator_last_user_text = text
        return True

    def _operator_stop_current(self) -> None:
        if getattr(self, "flow_running", False):
            self._stop_flow()
            return
        self.status_label.setText("当前没有正在运行的流程。")
        self._append_log("用户页面", "停止流程", "提示", "当前没有正在运行的流程")
        self._refresh_operator_view()

    def _operator_apply_connection_settings(self) -> bool:
        host = self.operator_host_edit.text().strip() if hasattr(self, "operator_host_edit") else ""
        if not host:
            self._show_warning("地址为空", "请输入控制器地址。")
            return False
        if hasattr(self, "host_edit"):
            self.host_edit.setText(host)
        self.status_label.setText(f"控制器地址已设置为 {host}。")
        self._append_log("用户页面", "设置控制器地址", "成功", host)
        self._refresh_operator_view()
        return True

    def _operator_check_connection(self) -> None:
        if not self._operator_apply_connection_settings():
            return
        self._check_connection()
        self._refresh_operator_view()

    def _operator_show_full_status(self) -> None:
        self._operator_scene_override = "query"
        self._refresh_operator_view()

    def _operator_go_home(self) -> None:
        self._operator_scene_override = None
        self._refresh_operator_view()

    def _operator_no_pending_confirm(self) -> None:
        self.status_label.setText("当前没有待确认的安全风险。")
        self._append_log("用户页面", "安全确认", "提示", "当前没有待确认的安全风险")
        self._refresh_operator_view()

    def _operator_confirm_execute(self) -> None:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if not self._operator_plan_is_executable(plan):
            self._operator_no_pending_confirm()
            return
        self._operator_pending_confirm_plan = None
        self._operator_scene_override = None
        self._set_nlp_execute_busy(True)
        self.status_label.setText("确认收到，开始执行。")
        self._operator_add_chat_message("assistant", "确认收到，开始执行。")
        self._append_log("用户页面", "确认执行", "成功", getattr(plan, "reason", "已确认执行"))
        self._execute_nlp_plan(plan)

    def _operator_accept_suggestion(self) -> None:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if not self._operator_plan_is_executable(plan):
            self._operator_no_pending_confirm()
            return
        self._append_log("用户页面", "采纳建议", "提示", "当前版本暂无参数改写建议，按原计划执行")
        self._operator_confirm_execute()

    def _operator_cancel_confirm(self) -> None:
        if getattr(self, "_operator_pending_confirm_plan", None) is not None:
            self._operator_pending_confirm_plan = None
            self._operator_scene_override = None
            self.status_label.setText("已取消待确认的执行计划。")
            self._append_log("用户页面", "取消确认", "成功", "已取消待确认计划")
            self._refresh_operator_view()
            return
        self._operator_stop_current()

    def _handle_operator_ui_command(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False

        def has_any(*keywords: str) -> bool:
            return any(keyword in compact for keyword in keywords)

        if compact in {"确认执行", "确认", "执行确认"}:
            self._operator_confirm_execute()
            return True
        if compact in {"采纳建议", "采用建议", "接受建议"}:
            self._operator_accept_suggestion()
            return True
        if compact in {"取消", "取消执行", "取消计划"}:
            self._operator_cancel_confirm()
            return True

        if has_any("切换到用户页面", "打开用户页面", "用户页面", "操作页面"):
            self._set_workspace_mode("operator")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("切换到工程师", "工程师页面", "工程师模式"):
            self._set_workspace_mode("engineer")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("显示完整状态", "完整状态", "状态看板", "七类看板"):
            self._set_workspace_mode("operator")
            self._operator_show_full_status()
            self.status_label.setText("已显示完整状态看板。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("回到主界面", "返回主界面", "主界面", "待机画面"):
            self._set_workspace_mode("operator")
            self._operator_go_home()
            self.status_label.setText("已回到主界面。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("退出全屏", "恢复窗口", "普通窗口"):
            self._set_workspace_mode("operator")
            self._operator_restore_normal_window()
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("全屏", "放大界面"):
            self._set_workspace_mode("operator")
            self._operator_show_fullscreen()
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("小窗口", "缩小界面"):
            self._set_workspace_mode("operator")
            if not getattr(self, "_operator_compact", False):
                self._operator_toggle_compact()
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("上一条"):
            self._operator_scroll_recent(-1)
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("下一条"):
            self._operator_scroll_recent(1)
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("显示安全参数", "安全参数"):
            self._set_workspace_mode("operator")
            self._operator_scene_override = "query"
            self.status_label.setText("已显示完整状态中的安全参数看板。")
            self._refresh_operator_view()
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("显示标定画面", "标定画面"):
            self._set_workspace_mode("engineer")
            self._show_page(1)
            self.status_label.setText("当前 Qt 后台页面包含系统参数和模板维护。")
            self._append_log("用户页面", "界面语音指令", "提示", "已切换工程师后台页")
            return True

        if compact in {"急停", "紧急停止"}:
            self._handle_system_action("sys_estop")
            return True
        if compact == "暂停":
            self._handle_system_action("sys_pause")
            return True
        if compact in {"继续", "恢复"}:
            self._handle_system_action("sys_resume")
            return True
        if compact in {"复位", "报警复位"}:
            self._handle_system_action("alarm_reset")
            return True
        if compact in {"停止流程", "停止当前流程"}:
            self._operator_stop_current()
            return True

        return False

    @staticmethod
    def _operator_plan_is_executable(plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        return bool(actions) and all(getattr(action, "action_type", "unknown") != "unknown" for action in actions)

    def _operator_toggle_compact(self) -> None:
        if not getattr(self, "_operator_compact", False):
            if not self.isFullScreen():
                self._operator_previous_geometry = self.saveGeometry()
            self.showNormal()
            self.resize(620, 820)
            screen = self.screen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.right() - self.width() + 1, geo.top())
            self._operator_compact = True
            self._operator_update_window_mode_buttons()
            self.status_label.setText("已切换到小窗口模式。")
            return

        self._operator_restore_normal_window()

    def _operator_show_fullscreen(self) -> None:
        if self.isFullScreen():
            self._operator_restore_normal_window()
            return
        if not getattr(self, "_operator_compact", False):
            self._operator_fullscreen_geometry = self.saveGeometry()
        self._operator_compact = False
        self.showFullScreen()
        self._operator_update_window_mode_buttons()
        if hasattr(self, "operator_fullscreen_btn"):
            self.operator_fullscreen_btn.setText("退出全屏")
        self.status_label.setText("已切换到全屏模式。")

    def _operator_restore_normal_window(self) -> None:
        self.showNormal()
        geometry = self._operator_previous_geometry or self._operator_fullscreen_geometry
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1380, 860)
        self._operator_compact = False
        self._operator_update_window_mode_buttons()
        self.status_label.setText("已恢复普通窗口模式。")

    def _operator_update_window_mode_buttons(self) -> None:
        fullscreen = self.isFullScreen()
        if hasattr(self, "operator_compact_btn"):
            self.operator_compact_btn.setText("恢复窗口" if getattr(self, "_operator_compact", False) else "小窗口")
        if hasattr(self, "operator_fullscreen_btn"):
            self.operator_fullscreen_btn.setText("退出全屏" if fullscreen else "全屏")

    def _operator_scroll_recent(self, direction: int) -> None:
        scroll_bar = None
        if self._operator_desired_scene() == "query" and hasattr(self, "operator_full_status_browser"):
            scroll_bar = self.operator_full_status_browser.verticalScrollBar()
        elif hasattr(self, "operator_recent_browser"):
            scroll_bar = self.operator_recent_browser.verticalScrollBar()
        elif hasattr(self, "operator_chat_scroll"):
            scroll_bar = self.operator_chat_scroll.verticalScrollBar()
        if scroll_bar is None:
            return
        step = max(60, scroll_bar.pageStep() // 2)
        scroll_bar.setValue(scroll_bar.value() + (step if direction > 0 else -step))
        self.status_label.setText("已滚动到下一条。" if direction > 0 else "已滚动到上一条。")

    def _refresh_operator_view(self) -> None:
        if not hasattr(self, "operator_scene_stack"):
            return

        state_text, color, detail = self._compute_overall_state()
        self.operator_state_label.setText(f"● {state_text}")
        self.operator_state_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: 800;")
        if hasattr(self, "operator_header_status_label"):
            self.operator_header_status_label.setText(state_text)
            self.operator_header_status_label.setStyleSheet(f"color: {color};")

        alarm_active = self._operator_alarm_active()
        pause_active = bool(getattr(self, "pause_active", False)) or self.busy == "暂停" or self.run_state == "暂停"
        estop_active = bool(getattr(self, "estop_active", False)) or "急停" in f"{self.alarm_text} {self.run_state} {self.busy}"
        self.operator_flags_label.setText(
            f"急停:{'开' if estop_active else '关'}  "
            f"暂停:{'开' if pause_active else '关'}  "
            f"报警:{'有' if alarm_active else '无'}"
        )

        monitor_text = self.monitor_label.text() if hasattr(self, "monitor_label") else "未启动"
        comm_text = "正常" if monitor_text == "实时监控运行中" else monitor_text
        self.operator_comm_label.setText(f"通讯:{comm_text}  控制器:{self._operator_controller_mode_text()}")
        if hasattr(self, "operator_host_edit") and hasattr(self, "host_edit") and not self.operator_host_edit.hasFocus():
            self.operator_host_edit.setText(self.host_edit.text().strip())

        self.operator_current_label.setText(f"当前: {self._operator_current_task_text()}")
        self._refresh_operator_axis_labels()
        self._refresh_operator_scene_content(detail)
        self._refresh_operator_recent_events()
        self._refresh_operator_dialog_labels()
        self._refresh_operator_full_status()
        self._sync_operator_mic_button()

        scene = self._operator_desired_scene()
        self.operator_scene_stack.setCurrentIndex(self._operator_scene_indexes.get(scene, 0))

    def _operator_alarm_active(self) -> bool:
        return str(getattr(self, "alarm_code", "")) not in {"", "0", "ERR_000"}

    def _operator_controller_mode_text(self) -> str:
        if not hasattr(self, "controller_combo"):
            return "-"
        return self.controller_combo.currentText()

    def _operator_current_task_text(self) -> str:
        if getattr(self, "nlp_parse_running", False):
            return "自然语言预检"
        if getattr(self, "nlp_sequence_running", False):
            return "自然语言动作序列"
        if getattr(self, "flow_running", False):
            name = self.current_flow_name or (self.flow_combo.currentText() if hasattr(self, "flow_combo") else "-")
            step = self.flow_current_step or "-"
            return f"流程 {name} / {step}"
        if self.busy == "运行中" or self.run_state == "运行中":
            current_func = getattr(self, "current_func_text", "")
            return current_func or "控制器执行中"
        if self.busy == "暂停" or self.run_state == "暂停":
            return "暂停"
        return "空闲"

    def _refresh_operator_axis_labels(self) -> None:
        joints = list(getattr(self, "robot_joints", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)))
        joints = joints[:6] + [0.0] * max(0, 6 - len(joints))
        for idx, label in enumerate(self.operator_joint_labels):
            label.setText(f"J{idx + 1}: {self._operator_fmt(joints[idx])}")
        self.operator_pose_label.setText(
            f"X:{self.robot_x}  Y:{self.robot_y}  Z:{self.robot_z}  RX/RY/RZ:{self.robot_r}"
        )

    def _refresh_operator_scene_content(self, state_detail: str) -> None:
        if getattr(self, "nlp_parse_running", False):
            self.operator_precheck_title.setText("安全预检进行中")
            for label, text in zip(
                self.operator_precheck_checks,
                ["指令接收: 已收到", "设备状态检查: 进行中", "安全参数检查: 等待", "运动规划预演: 等待"],
            ):
                label.setText(text)
            self.operator_precheck_progress.setRange(0, 0)
        else:
            self.operator_precheck_title.setText("安全预检")
            for label, text in zip(
                self.operator_precheck_checks,
                ["指令接收: 等待", "设备状态检查: 等待", "安全参数检查: 等待", "运动规划预演: 等待"],
            ):
                label.setText(text)
            self.operator_precheck_progress.setRange(0, 100)
            self.operator_precheck_progress.setValue(0)

        self.operator_execute_title.setText(self._operator_execute_title())
        self.operator_execute_detail.setText(self._operator_execute_detail_text(state_detail))
        progress = self._operator_execution_progress()
        if progress is None:
            self.operator_execute_progress.setRange(0, 0)
        else:
            self.operator_execute_progress.setRange(0, 100)
            self.operator_execute_progress.setValue(progress)
            self.operator_execute_progress.setFormat(f"估算 {progress}%")
        self.operator_execute_position.setText(
            f"当前位置 X:{self.robot_x}  Y:{self.robot_y}  Z:{self.robot_z}  RX/RY/RZ:{self.robot_r}"
        )

        self.operator_confirm_title.setText("等待安全确认")
        self.operator_confirm_detail.setText(self._operator_confirm_detail_text())

        alarm_detail = self.alarm_text or "报警详情待读取"
        self.operator_alarm_title.setText(f"报警: {self.alarm_code}")
        self.operator_alarm_detail.setText(alarm_detail)

    def _operator_execute_title(self) -> str:
        if getattr(self, "flow_running", False):
            return "流程执行中"
        if getattr(self, "nlp_sequence_running", False):
            return "自然语言执行中"
        if self.busy == "暂停" or self.run_state == "暂停":
            return "设备已暂停"
        return "设备执行中"

    def _operator_execute_detail_text(self, state_detail: str) -> str:
        if getattr(self, "flow_running", False):
            return f"{self.current_flow_name or '-'} / {self.flow_current_step or '-'}"
        if getattr(self, "nlp_sequence_running", False):
            total = len(getattr(self, "_nlp_pending_actions", []))
            current = min(getattr(self, "_nlp_pending_index", 0) + 1, total) if total else 0
            return f"自然语言动作 {current} / {total}"
        return state_detail

    def _operator_confirm_detail_text(self) -> str:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if not self._operator_plan_is_executable(plan):
            return "当前没有需要确认的风险。"
        rows = []
        for idx, action in enumerate(getattr(plan, "actions", ()), start=1):
            target = getattr(action, "target", "") or "-"
            action_type = getattr(action, "action_type", "")
            rows.append(f"{idx}. {action_type} / {target}")
        reason = getattr(plan, "reason", "") or "已完成规则解析"
        return "解析完成，等待确认执行:\n" + "\n".join(rows) + f"\n\n说明: {reason}"

    def _operator_execution_progress(self) -> int | None:
        if getattr(self, "flow_running", False):
            total = 0
            try:
                flow_name = self.current_flow_name or self.flow_combo.currentText()
                total = len(self.service.get_flow(flow_name).steps)
            except Exception:
                total = 0
            if total > 0:
                current = min(max(int(self.flow_step_index) + 1, 1), total)
                return int(round(current / total * 100))
            return None

        percent = self._operator_parse_percent(getattr(self, "motion_percent", ""))
        if percent is not None:
            return percent
        if self.busy == "运行中" or self.run_state == "运行中" or getattr(self, "nlp_sequence_running", False):
            return None
        return 0

    def _operator_desired_scene(self) -> str:
        if self._operator_alarm_active():
            return "alarm"
        if self._operator_plan_is_executable(getattr(self, "_operator_pending_confirm_plan", None)):
            return "confirm"
        if getattr(self, "nlp_parse_running", False):
            return "precheck"
        if (
            getattr(self, "nlp_sequence_running", False)
            or getattr(self, "flow_running", False)
            or self.busy in {"运行中", "暂停"}
            or self.run_state in {"运行中", "暂停"}
        ):
            return "execute"
        if self._operator_scene_override:
            return self._operator_scene_override
        return "idle"

    def _refresh_operator_recent_events(self) -> None:
        if not hasattr(self, "operator_recent_browser"):
            return
        rows = []
        for entry in list(getattr(self, "logs", []))[:50]:
            result = str(entry.get("result", ""))
            color = "#0f8a3b" if result == "成功" else "#b45309" if result == "警告" else "#b91c1c" if result == "失败" else "#334155"
            rows.append(
                "<div class='event'>"
                f"<span style='color:#475569'>{html.escape(str(entry.get('time', '-')))}</span> "
                f"<b style='color:{color}'>{html.escape(result or '-')}</b> "
                f"{html.escape(str(entry.get('action', '-')))}"
                f"<br><span style='color:#334155'>{html.escape(str(entry.get('detail', '-')))}</span>"
                "</div>"
            )
        if not rows:
            rows.append("<div class='event'><b>待执行</b><br><span>暂无操作记录</span></div>")
        html_text = (
            "<style>.event{margin:6px 0;padding:6px 0;border-bottom:1px solid rgba(15,23,42,.18);}</style>"
            + "".join(rows)
        )
        if html_text == getattr(self, "_operator_recent_events_html", ""):
            return

        bar = self.operator_recent_browser.verticalScrollBar()
        previous_value = bar.value()
        previous_max = bar.maximum()
        was_at_bottom = previous_max > 0 and previous_value >= previous_max - 8

        self._operator_recent_events_html = html_text
        self.operator_recent_browser.setHtml(html_text)
        if previous_max <= 0:
            return

        def restore_scroll() -> None:
            new_bar = self.operator_recent_browser.verticalScrollBar()
            if was_at_bottom:
                new_bar.setValue(new_bar.maximum())
            else:
                new_bar.setValue(min(previous_value, new_bar.maximum()))

        QTimer.singleShot(0, restore_scroll)

    def _refresh_operator_dialog_labels(self) -> None:
        if not hasattr(self, "operator_chat_scroll"):
            return
        nlp_text = self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else ""
        if nlp_text and hasattr(self, "operator_command_edit") and not self.operator_command_edit.hasFocus():
            if not self.operator_command_edit.text().strip():
                self.operator_command_edit.setText(nlp_text)
        voice_text = nlp_text or (self.operator_command_edit.text().strip() if hasattr(self, "operator_command_edit") else "")
        if hasattr(self, "operator_voice_label"):
            self.operator_voice_label.setText(f"语音输入: {voice_text or '等待语音输入...'}")
        if nlp_text and nlp_text != getattr(self, "_operator_last_user_text", ""):
            self._operator_add_chat_message("user", nlp_text)
            self._operator_last_user_text = nlp_text
        response = self.status_label.text().strip() if hasattr(self, "status_label") else "系统在线"
        response = response or "系统在线"
        if hasattr(self, "operator_response_label"):
            self.operator_response_label.setText(f"系统回应: {response}")
        self._operator_last_response_text = response
        if not getattr(self, "_operator_chat_rendered", False):
            self._render_operator_chat()

    def _operator_add_chat_message(self, role: str, text: str, *, scroll_to_bottom: bool = True) -> None:
        clean_text = (text or "").strip()
        if not clean_text:
            return
        if not hasattr(self, "_operator_chat_messages"):
            self._operator_chat_messages = []
        if self._operator_chat_messages and self._operator_chat_messages[-1] == (role, clean_text):
            if not getattr(self, "_operator_chat_rendered", False):
                self._render_operator_chat()
            return
        self._operator_chat_messages.append((role, clean_text))
        self._operator_chat_messages = self._operator_chat_messages[-80:]
        self._operator_chat_autoscroll_pending = scroll_to_bottom
        self._render_operator_chat()

    def _operator_add_chat_from_log(self, entry: dict[str, Any]) -> None:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        result = str(entry.get("result", ""))
        detail = str(entry.get("detail", ""))
        if category != "自然语言":
            return
        if action == "动作序列完成" and result == "成功":
            self._operator_add_chat_message("assistant", detail or "执行完成。")
            return
        if action == "动作序列终止":
            self._operator_add_chat_message("assistant", f"执行失败：{detail or '动作序列已终止'}")

    def _operator_plan_chat_text(self, plan) -> str:
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions:
            return f"我没有识别到可执行动作。{getattr(plan, 'reason', '')}"
        lines = []
        for idx, action in enumerate(actions, start=1):
            action_type = getattr(action, "action_type", "-")
            target = getattr(action, "target", "") or "-"
            lines.append(f"{idx}. {action_type} / {target}")
        return "我已理解为：\n" + "\n".join(lines) + "\n请确认执行、采纳建议或取消。"

    def _render_operator_chat(self) -> None:
        if not hasattr(self, "operator_chat_layout"):
            return
        while self.operator_chat_layout.count():
            item = self.operator_chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for role, text in getattr(self, "_operator_chat_messages", []):
            self.operator_chat_layout.addWidget(self._build_operator_chat_row(role, text))
        self.operator_chat_layout.addStretch(1)
        self._operator_chat_rendered = True
        if getattr(self, "_operator_chat_autoscroll_pending", False):
            self._operator_scroll_chat_to_bottom()

    def _operator_on_chat_range_changed(self, _minimum: int, _maximum: int) -> None:
        if getattr(self, "_operator_chat_autoscroll_pending", False):
            self._operator_scroll_chat_to_bottom()

    def _operator_scroll_chat_to_bottom(self) -> None:
        if not hasattr(self, "operator_chat_scroll"):
            return

        def scroll() -> None:
            bar = self.operator_chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._operator_chat_autoscroll_pending = False

        QTimer.singleShot(0, scroll)
        QTimer.singleShot(40, scroll)

    def _build_operator_chat_row(self, role: str, text: str) -> QWidget:
        row = QWidget()
        row.setObjectName("operatorChatRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        is_user = role == "user"
        avatar = QLabel("我" if is_user else "AI")
        avatar.setObjectName("operatorUserAvatar" if is_user else "operatorAiAvatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(34, 34)

        bubble = QFrame()
        bubble.setObjectName("operatorUserBubble" if is_user else "operatorAiBubble")
        bubble.setMaximumWidth(760)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 9, 12, 10)
        bubble_layout.setSpacing(4)

        sender = QLabel("用户" if is_user else "AI 助手")
        sender.setObjectName("operatorUserSender" if is_user else "operatorAiSender")
        sender.setTextFormat(Qt.TextFormat.PlainText)

        content = QLabel(text)
        content.setObjectName("operatorChatText")
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if is_user:
            sender.setStyleSheet("color: #6b7280;")
            content.setStyleSheet("color: #111827;")

        bubble_layout.addWidget(sender)
        bubble_layout.addWidget(content)

        if is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
            row_layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        else:
            row_layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)
        return row

    def _refresh_operator_full_status(self) -> None:
        if not hasattr(self, "operator_full_status_browser"):
            return
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        pending_actions = tuple(getattr(plan, "actions", ()) or ())
        pending_text = "无"
        if pending_actions:
            pending_text = "；".join(
                f"{idx}.{getattr(action, 'action_type', '-')} / {getattr(action, 'target', '-') or '-'}"
                for idx, action in enumerate(pending_actions, start=1)
            )

        sections = [
            self._operator_html_section(
                "看板1 设备健康状态",
                [
                    ("设备状态", self.operator_state_label.text().replace("● ", "")),
                    ("通讯状态", self.operator_comm_label.text()),
                    ("急停/暂停/报警", self.operator_flags_label.text()),
                    ("伺服使能", getattr(self, "servo_enable", "-")),
                    ("夹爪使能/刹车", f"{getattr(self, 'claw_enable', '-')} / {getattr(self, 'claw_brake', '-')}"),
                ],
            ),
            self._operator_html_section(
                "看板2 位置与运动状态",
                [
                    ("关节位置", "  ".join(label.text() for label in self.operator_joint_labels)),
                    ("空间位置", self.operator_pose_label.text()),
                    ("运动进度", getattr(self, "motion_percent", "-")),
                    ("速度/加速度", getattr(self, "robot_speed", "-")),
                ],
            ),
            self._operator_html_section(
                "看板3 当前执行",
                [
                    ("当前执行", self._operator_current_task_text()),
                    ("控制器函数", getattr(self, "current_func_text", "空闲")),
                    ("忙闲状态", getattr(self, "busy", "-")),
                    ("实时状态", getattr(self, "run_state", "-")),
                    ("任务ID", getattr(self, "task_id", "-")),
                ],
            ),
            self._operator_html_section(
                "看板4 报警与安全",
                [
                    ("报警码", getattr(self, "alarm_code", "-")),
                    ("报警说明", getattr(self, "alarm_text", "-")),
                    ("执行结果", getattr(self, "result", "-")),
                    ("IO状态位", getattr(self, "io_status", "-")),
                ],
            ),
            self._operator_html_section(
                "看板5 工艺流程预演/执行进度",
                [
                    ("流程状态", f"{getattr(self, 'flow_status', '-')} / {getattr(self, 'flow_current_step', '-')}"),
                    ("当前流程", getattr(self, "current_flow_name", None) or (self.flow_combo.currentText() if hasattr(self, "flow_combo") else "-")),
                    ("流程步数", self._operator_flow_progress_text()),
                    ("待确认计划", pending_text),
                ],
            ),
            self._operator_html_section(
                "看板6 安全参数",
                self._operator_safety_rows(),
            ),
            self._operator_html_section(
                "看板7 对话与最近操作",
                [
                    ("语音/文本输入", self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else "-"),
                    ("系统回应", self.status_label.text().strip() if hasattr(self, "status_label") else "-"),
                    ("最近日志", self._operator_recent_log_text()),
                ],
            ),
        ]
        html_text = (
            "<style>"
            "body{font-family:'Microsoft YaHei',Arial,sans-serif;color:#0f172a;}"
            ".board{margin:0 0 12px 0;padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc;}"
            ".title{font-weight:800;font-size:17px;margin-bottom:6px;color:#0f172a;}"
            "table{width:100%;border-collapse:collapse;}"
            "th{width:138px;text-align:left;color:#334155;}"
            "td,th{vertical-align:top;padding:5px 6px;border-bottom:1px solid rgba(15,23,42,.12);font-size:14px;}"
            "</style>"
            + "".join(sections)
        )
        if html_text == getattr(self, "_operator_full_status_html", ""):
            return
        bar = self.operator_full_status_browser.verticalScrollBar()
        previous_value = bar.value()
        previous_max = bar.maximum()
        was_at_bottom = previous_max > 0 and previous_value >= previous_max - 8
        self._operator_full_status_html = html_text
        self.operator_full_status_browser.setHtml(html_text)
        if previous_max <= 0:
            return

        def restore_scroll() -> None:
            new_bar = self.operator_full_status_browser.verticalScrollBar()
            if was_at_bottom:
                new_bar.setValue(new_bar.maximum())
            else:
                new_bar.setValue(min(previous_value, new_bar.maximum()))

        QTimer.singleShot(0, restore_scroll)

    def _operator_html_section(self, title: str, rows: list[tuple[str, object]]) -> str:
        table_rows = "".join(
            "<tr>"
            f"<th>{html.escape(label)}</th>"
            f"<td>{html.escape(str(value if value not in (None, '') else '-'))}</td>"
            "</tr>"
            for label, value in rows
        )
        return f"<div class='board'><div class='title'>{html.escape(title)}</div><table>{table_rows}</table></div>"

    def _operator_safety_rows(self) -> list[tuple[str, object]]:
        config = getattr(self, "axis_ranges", None)
        if config is None:
            return [("安全参数", "-")]
        return [
            ("X范围", f"{self._operator_fmt(config.x[0])} ~ {self._operator_fmt(config.x[1])}"),
            ("Y范围", f"{self._operator_fmt(config.y[0])} ~ {self._operator_fmt(config.y[1])}"),
            ("Z范围", f"{self._operator_fmt(config.z[0])} ~ {self._operator_fmt(config.z[1])}"),
            ("R安全范围", f"{self._operator_fmt(config.safe_r_min)} ~ {self._operator_fmt(config.safe_r_max)}"),
            ("Z安全范围", f"{self._operator_fmt(config.safe_z_min)} ~ {self._operator_fmt(config.safe_z_max)}"),
            ("速度/加速度/减速度上限", f"{self._operator_fmt(config.safe_speed_max)} / {self._operator_fmt(config.safe_acc_max)} / {self._operator_fmt(config.safe_dec_max)}"),
            ("运动超时", f"{self._operator_fmt(config.motion_timeout_sec)}s"),
        ]

    def _operator_flow_progress_text(self) -> str:
        try:
            flow_name = self.current_flow_name or self.flow_combo.currentText()
            total = len(self.service.get_flow(flow_name).steps)
        except Exception:
            total = 0
        if total <= 0:
            return "0 / 0"
        current = min(max(int(getattr(self, "flow_step_index", 0)) + 1, 1), total)
        return f"{current} / {total}"

    def _operator_recent_log_text(self) -> str:
        entries = list(getattr(self, "logs", []))[:3]
        if not entries:
            return "暂无"
        return "；".join(
            f"{entry.get('time', '-')} {entry.get('result', '-')} {entry.get('action', '-')}"
            for entry in entries
        )

    def _sync_operator_mic_button(self) -> None:
        if not hasattr(self, "operator_mic_btn") or not hasattr(self, "mic_toggle_btn"):
            return
        self.operator_mic_btn.setText(self.mic_toggle_btn.text())
        self.operator_mic_btn.setEnabled(self.mic_toggle_btn.isEnabled())

    @staticmethod
    def _operator_parse_percent(value: str) -> int | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value))
        if not match:
            return None
        return max(0, min(100, int(round(float(match.group(1))))))

    @staticmethod
    def _operator_fmt(value: float | int | str) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return str(int(number)) if number.is_integer() else f"{number:.1f}"
