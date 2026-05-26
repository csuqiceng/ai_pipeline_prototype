"""Operator-facing Qt page for the natural-language robot interface."""

from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
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

from .atomic_capabilities import atomic_capability_summary, atomic_capability_rows
from .alarm_monitor import AlarmMonitor
from .broadcast_queue import BroadcastMessage, BroadcastQueue
from .command_intent_adapter import command_intent_from_plan
from .dashboard import DashboardCache
from .dashboard_query import DashboardQueryService
from .emergency_channel import EmergencyChannel
from .engineer_voice_commands import (
    EXECUTION_POLICY_LABELS,
    EngineerVoiceCommandSpec,
    engineer_voice_capability_summary,
    match_engineer_voice_command,
)
from .dialog_logger import DialogLogger
from .interaction_archiver import InteractionArchiveWriter
from .json_schema import DeviceSnapshot
from .models import FlowDefinition, QueryRecord
from .response_builder import ResponseBuilder, ResponseMessage
from .safety_precheck import SafetyPrecheckService
from .safety_suggestion import SafetySuggestionService
from .semantic_response_policy import policy_for_plan
from .speech_broadcast import (
    Pyttsx3SpeechSink,
    SpeechBroadcastDeliveryService,
    SpeechDeliveryResult,
    WindowsSapiSpeechSink,
)
from .motion_plan import MotionPlanService
from .operator_voice_commands import OperatorVoiceCommandSpec, match_operator_voice_command
from .permission_service import PermissionDenied
from .process_precheck import ProcessPrecheckService
from .query_table import save_query_table_json
from .system_config import save_system_config


@dataclass(frozen=True)
class OperatorSceneState:
    current: str | None = None
    previous: str | None = None
    reason: str = ""
    changed_at: float = 0.0


class OperatorUiMixin:
    """Build and update the simplified operator page."""

    def _build_operator_page(self) -> QWidget:
        """Build the user-facing page described by the companion UI spec."""
        self._operator_scene_override: str | None = None
        self._operator_compact = False
        self._operator_previous_geometry = None
        self._operator_fullscreen_geometry = None
        self._operator_current_scene = None
        self._operator_scene_state = OperatorSceneState()
        self._operator_scene_before_alarm = None
        self._operator_pending_confirm_plan = None
        self._operator_pending_flow_draft = None
        self._operator_pending_engineer_voice_spec: EngineerVoiceCommandSpec | None = None
        self._operator_pending_engineer_voice_created_at_sec: float | None = None
        self._operator_chat_messages: list[tuple[str, str]] = self._operator_initial_chat_messages()
        self._operator_chat_thinking_steps: list[list[str]] = [[] for _ in self._operator_chat_messages]
        self._operator_chat_thinking_meta: list[dict[str, object]] = [{} for _ in self._operator_chat_messages]
        self._operator_last_user_text = ""
        self._operator_last_response_text = "系统在线"
        self._operator_chat_rendered = False
        self._operator_chat_autoscroll_pending = False
        self._operator_full_status_html = ""
        self._operator_recent_events_html = ""
        self.operator_broadcast_queue = BroadcastQueue()
        self.operator_response_builder = ResponseBuilder()
        self.operator_speech_sink = None
        self._operator_configure_tts_from_settings()
        self.operator_emergency_channel = EmergencyChannel(getattr(self.axis_ranges, "emergency_codes", None))
        self.operator_dashboard_cache = self._operator_make_dashboard_cache()
        self._operator_last_broadcast_seq = 0
        self._operator_last_delivered_broadcast_seq = 0
        self._operator_tts_failure_count = 0
        self._operator_tts_retry_after_sec = 0.0
        self._operator_pending_confirm_deadline_sec = 0.0
        self._operator_last_precheck_result = None
        self._operator_last_motion_plan_result = None
        self._operator_last_process_precheck_result = None
        self._operator_l3_progress_percent = 0
        self._operator_l3_progress_text = ""
        self._operator_dashboard_broadcast_state = None
        self._operator_last_periodic_reassurance_sec = 0.0

        page = QFrame()
        page.setObjectName("operatorPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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
        self.operator_refresh_timer.setInterval(self._operator_view_refresh_interval_ms())
        self.operator_refresh_timer.timeout.connect(self._refresh_operator_view)
        self.operator_refresh_timer.start()
        self.operator_dashboard_timer = QTimer(self)
        self.operator_dashboard_timer.setInterval(self._operator_dashboard_refresh_interval_ms())
        self.operator_dashboard_timer.timeout.connect(self._operator_refresh_dashboard_cache)
        self.operator_dashboard_timer.start()
        self.operator_alarm_timer = QTimer(self)
        self.operator_alarm_timer.setInterval(self._operator_alarm_refresh_interval_ms())
        self.operator_alarm_timer.timeout.connect(self._operator_refresh_alarm_monitor)
        self.operator_alarm_timer.start()
        self.operator_speech_timer = QTimer(self)
        self.operator_speech_timer.setInterval(1000)
        self.operator_speech_timer.timeout.connect(self._operator_auto_deliver_broadcasts)
        self.operator_speech_timer.start()
        self._operator_refresh_dashboard_cache()
        self._refresh_operator_view()
        return page

    def _operator_make_dashboard_cache(self) -> DashboardCache:
        return DashboardCache(
            refresh_ms=self._operator_dashboard_refresh_interval_ms(),
            stale_after_ms=self._operator_dashboard_stale_after_ms(),
        )

    def _operator_dashboard_refresh_interval_ms(self) -> int:
        return max(1, int(getattr(getattr(self, "axis_ranges", None), "operator_dashboard_refresh_ms", 50)))

    def _operator_alarm_refresh_interval_ms(self) -> int:
        return max(1, int(getattr(getattr(self, "axis_ranges", None), "operator_alarm_refresh_ms", 50)))

    def _operator_view_refresh_interval_ms(self) -> int:
        return max(1, int(getattr(getattr(self, "axis_ranges", None), "operator_view_refresh_ms", 500)))

    def _operator_dashboard_stale_after_ms(self) -> int:
        return max(1, int(getattr(getattr(self, "axis_ranges", None), "dashboard_stale_after_ms", 1000)))

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

        flags_card = QFrame()
        flags_card.setObjectName("operatorStatusCard")
        flags_layout = QVBoxLayout(flags_card)
        flags_layout.setContentsMargins(12, 10, 12, 10)
        flags_layout.setSpacing(8)
        safety_title = QLabel("安全状态")
        safety_title.setObjectName("operatorSidebarTitle")
        flags_layout.addWidget(safety_title)
        safety_grid = QGridLayout()
        safety_grid.setHorizontalSpacing(6)
        safety_grid.setVerticalSpacing(6)
        self.operator_estop_badge = QLabel("急停\n关")
        self.operator_pause_badge = QLabel("暂停\n关")
        self.operator_alarm_badge = QLabel("报警\n无")
        for idx, badge in enumerate([self.operator_estop_badge, self.operator_pause_badge, self.operator_alarm_badge]):
            badge.setObjectName("operatorStatusBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            safety_grid.addWidget(badge, 0, idx)
        flags_layout.addLayout(safety_grid)
        layout.addWidget(flags_card)

        axis_card = QFrame()
        axis_card.setObjectName("operatorStatusCard")
        axis_layout = QVBoxLayout(axis_card)
        axis_layout.setContentsMargins(12, 10, 12, 10)
        axis_layout.setSpacing(6)
        axis_title = QLabel("实时位置")
        axis_title.setObjectName("operatorSidebarTitle")
        axis_layout.addWidget(axis_title)
        joint_title = QLabel("关节")
        joint_title.setObjectName("operatorSmallLabel")
        axis_layout.addWidget(joint_title)
        joint_grid = QGridLayout()
        joint_grid.setHorizontalSpacing(10)
        joint_grid.setVerticalSpacing(4)
        self.operator_joint_labels = []
        for idx in range(6):
            label = QLabel(f"J{idx + 1}\n-")
            label.setObjectName("operatorPoseCell")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.operator_joint_labels.append(label)
            joint_grid.addWidget(label, idx // 3, idx % 3)
        axis_layout.addLayout(joint_grid)
        pose_title = QLabel("坐标")
        pose_title.setObjectName("operatorSmallLabel")
        axis_layout.addWidget(pose_title)
        pose_grid = QGridLayout()
        pose_grid.setHorizontalSpacing(6)
        pose_grid.setVerticalSpacing(6)
        self.operator_pose_labels = {}
        for idx, key in enumerate(["X", "Y", "Z", "RX", "RY", "RZ"]):
            label = QLabel(f"{key}\n-")
            label.setObjectName("operatorPoseCell")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.operator_pose_labels[key] = label
            pose_grid.addWidget(label, idx // 3, idx % 3)
        axis_layout.addLayout(pose_grid)
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
            ("停止当前", self._operator_stop_current, ""),
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
        ]):
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            btn.clicked.connect(slot)
            quick_layout.addWidget(btn, idx // 2, idx % 2)
            if text == "全屏":
                self.operator_fullscreen_btn = btn
        self.operator_tts_check = QCheckBox("语音播报")
        self.operator_tts_check.setObjectName("operatorToolCheck")
        self.operator_tts_check.setChecked(bool(getattr(self.axis_ranges, "operator_tts_enabled", False)))
        self.operator_tts_check.toggled.connect(self._operator_on_tts_toggled)
        quick_layout.addWidget(self.operator_tts_check, 2, 0, 1, 2)
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
            if text == "采纳建议":
                self.operator_accept_suggestion_btn = btn
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
        ack_btn = QPushButton("确认报警")
        ack_btn.setObjectName("operatorActionButton")
        ack_btn.clicked.connect(self._operator_acknowledge_alarm)
        reset_btn = QPushButton("复位")
        reset_btn.setObjectName("operatorActionButton")
        reset_btn.setProperty("klass", "green")
        reset_btn.clicked.connect(lambda: self._handle_system_action("alarm_reset"))
        layout.addWidget(self.operator_alarm_title)
        layout.addWidget(self.operator_alarm_detail)
        alarm_buttons = QHBoxLayout()
        alarm_buttons.addWidget(ack_btn)
        alarm_buttons.addWidget(reset_btn)
        alarm_buttons.addStretch(1)
        layout.addLayout(alarm_buttons)
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
            ("发送", self._operator_execute_text, "green"),
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
            ("停止当前", self._operator_stop_current, ""),
            ("完整状态", self._operator_show_full_status, ""),
            ("小窗口", self._operator_toggle_compact, ""),
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
        self._operator_archive_nlp_result(plan)
        if getattr(self, "nlp_sequence_running", False):
            self._operator_set_pending_confirm_plan(None)
            self._operator_scene_override = None
            self._operator_last_precheck_result = None
            self._operator_last_motion_plan_result = None
            self._operator_last_process_precheck_result = None
        elif self._operator_answer_query_plan(plan):
            pass
        elif self._operator_plan_is_executable(plan):
            if self._operator_plan_requires_precheck(plan):
                self._operator_prepare_plan_prechecks(plan)
            else:
                self._operator_last_precheck_result = None
                self._operator_last_motion_plan_result = None
                self._operator_last_process_precheck_result = None
            if self._operator_plan_requires_confirmation(plan):
                self._operator_set_pending_confirm_plan(plan)
                self._operator_scene_override = "confirm"
                self._operator_add_chat_message("assistant", self._operator_plan_chat_text(plan))
            else:
                self._operator_set_pending_confirm_plan(None)
                self._operator_scene_override = None
                self._operator_add_chat_message("assistant", self._operator_plan_recognized_text(plan))
        else:
            self._operator_set_pending_confirm_plan(None)
            self._operator_scene_override = None
            self._operator_last_precheck_result = None
            self._operator_last_motion_plan_result = None
            self._operator_last_process_precheck_result = None
            self._operator_add_chat_message("assistant", self._operator_nonexecutable_plan_chat_text(plan))
        self._refresh_operator_view()

    @staticmethod
    def _operator_nonexecutable_plan_chat_text(plan) -> str:
        actions = tuple(getattr(plan, "actions", ()) or ())
        first_type = str(getattr(actions[0], "action_type", "") or "") if actions else ""
        reason = str(getattr(plan, "reason", "") or "").strip()
        if first_type == "chat" and reason:
            return reason
        return f"我没有识别到可执行动作。{reason}"

    def _operator_answer_query_plan(self, plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions or getattr(actions[0], "action_type", "") != "query":
            return False
        if getattr(actions[0], "target", "") == "atomic_capabilities":
            answer_text = self._operator_atomic_capability_answer_text()
            self._operator_set_pending_confirm_plan(None)
            self._operator_scene_override = "query"
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text=answer_text,
                    priority="normal",
                    context_id="atomic:capability_query",
                )
            )
            self._operator_publish_ai_answer_for_speech(answer_text)
            self._operator_archive_execution_result(result="answered", final_text=answer_text)
            return True
        answer = DashboardQueryService().answer(
            str(getattr(plan, "raw_text", "") or getattr(actions[0], "raw_text", "") or ""),
            self._operator_dashboard_snapshot_dict(),
        )
        if answer is None:
            return False
        self._operator_set_pending_confirm_plan(None)
        self._operator_scene_override = "query"
        self._operator_publish_response(
            ResponseMessage(
                kind="result",
                text=answer.text,
                priority=answer.priority,
                context_id=f"dashboard_query:{answer.board_key}",
            )
        )
        self._operator_publish_ai_answer_for_speech(answer.text)
        self._operator_archive_execution_result(result="answered", final_text=answer.text)
        return True

    def _set_nlp_parse_busy(self, busy: bool) -> None:
        super()._set_nlp_parse_busy(busy)
        if busy:
            message = self.operator_response_builder.receipt(input_mode="text")
            self._operator_publish_response(message)
        self._operator_scene_override = None
        self._refresh_operator_view()

    def _set_nlp_execute_busy(self, busy: bool) -> None:
        super()._set_nlp_execute_busy(busy)
        if busy:
            self._operator_set_pending_confirm_plan(None)
        self._operator_scene_override = None
        self._refresh_operator_view()

    def _execute_nlp_plan(self, plan) -> None:
        if self._operator_handle_flow_draft_plan(plan):
            return
        if getattr(self, "_operator_streaming_chat_active", False):
            self._operator_cancel_streaming_chat_response()
        if self._operator_plan_requires_confirmation(plan) and getattr(self, "_operator_pending_confirm_plan", None) is plan:
            text = "等待安全确认，请说“确认执行”或点击确认执行。"
            self._set_nlp_execute_busy(False)
            self.status_label.setText(text)
            self._operator_add_chat_message("assistant", text, kind="warn")
            self._append_log("用户页面", "等待确认", "提示", text)
            self._refresh_operator_view()
            return
        emergency_actions = [
            action
            for action in tuple(getattr(plan, "actions", ()) or ())
            if getattr(action, "action_type", "") == "system" and getattr(action, "target", "") == "sys_estop"
        ]
        if emergency_actions:
            self._set_nlp_execute_busy(False)
            raw_text = str(getattr(plan, "raw_text", "") or "")
            if self._operator_handle_emergency_text(raw_text):
                return
            message = self.operator_response_builder.alert("急停类系统动作必须使用“急停 授权码 急停”格式，当前未执行。")
            self._operator_publish_response(message)
            self.status_label.setText(message.text)
            self._append_log("应急", "应急编码校验", "拒绝", f"missing_code | {raw_text}")
            self._refresh_operator_view()
            return
        super()._execute_nlp_plan(plan)

    def _operator_handle_flow_draft_plan(self, plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions:
            reason = str(getattr(plan, "reason", "") or "未识别到可执行动作。").strip()
            text = self._operator_unknown_plan_chat_text(reason)
            self._set_nlp_execute_busy(False)
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(text))
            if getattr(self, "_operator_streaming_chat_active", False):
                self._operator_finish_streaming_chat_response(text)
            elif hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text)
            self._append_log("自然语言", "未识别动作", "失败", text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="unknown", final_text=text)
            self._refresh_operator_view()
            return True
        first_type = str(getattr(actions[0], "action_type", "") or "")
        semantic_level = int(getattr(plan, "semantic_level", 0) or 0)
        if first_type in {"unknown", "chat"} and semantic_level == 1:
            reason = str(getattr(plan, "reason", "") or "闲聊咨询，未触发控制动作。")
            text = reason if first_type == "chat" else f"{reason}。没有触发机械手动作。"
            self._set_nlp_execute_busy(False)
            self.status_label.setText(self._operator_footer_status_text(text))
            if first_type == "chat" and getattr(self, "_operator_streaming_chat_active", False):
                self._operator_finish_streaming_chat_response(text)
            elif hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text)
            self._operator_publish_ai_answer_for_speech(text)
            self._append_log("自然语言", "闲聊咨询", "成功", text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="chat", final_text=text)
            self._refresh_operator_view()
            return True
        if first_type == "unknown":
            reason = str(getattr(plan, "reason", "") or getattr(actions[0], "reason", "") or "未识别到可执行动作。").strip()
            text = self._operator_unknown_plan_chat_text(reason)
            self._set_nlp_execute_busy(False)
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(text))
            if getattr(self, "_operator_streaming_chat_active", False):
                self._operator_finish_streaming_chat_response(text)
            elif hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text)
            self._append_log("自然语言", "未识别动作", "失败", text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="unknown", final_text=text)
            self._refresh_operator_view()
            return True
        if first_type == "clarification":
            draft = getattr(plan, "flow_draft", {}) or {}
            if isinstance(draft, dict) and draft:
                self._operator_pending_flow_draft = dict(draft)
            text = str(getattr(plan, "reason", "") or "需要补充信息后才能生成流程草案。")
            self._set_nlp_execute_busy(False)
            self.status_label.setText(text)
            if getattr(self, "_operator_streaming_chat_active", False):
                self._operator_finish_streaming_chat_response(text)
            elif hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text)
            self._append_log("自然语言", "澄清提示", "提示", text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="clarification", final_text=text)
            self._refresh_operator_view()
            return True
        if first_type == "flow_draft":
            draft = getattr(plan, "flow_draft", {}) or {}
            if isinstance(draft, dict) and draft:
                self._operator_pending_flow_draft = dict(draft)
                service = self._operator_execution_plan_service()
                service.set_pending_flow_draft(draft)
                updated = service.pending_flow_draft()
                if updated is not None:
                    merged = dict(draft)
                    merged["expanded_steps"] = updated.get("expanded_steps", [])
                    merged["flow_name"] = updated.get("flow_name", merged.get("flow_name", ""))
                    self._operator_pending_flow_draft = merged
                clarification = service.current_clarification()
                if clarification is not None:
                    if isinstance(self._operator_pending_flow_draft, dict):
                        self._operator_pending_flow_draft["needs_precheck"] = True
                    question = str(clarification.question or "请补充流程参数。")
                    text = f"已生成流程草案，但还缺少关键参数。请补充：{question}"
                    self._set_nlp_execute_busy(False)
                    self.status_label.setText(text)
                    if getattr(self, "_operator_streaming_chat_active", False):
                        self._operator_finish_streaming_chat_response(text)
                    elif hasattr(self, "_operator_add_chat_message"):
                        self._operator_add_chat_message("assistant", text)
                    self._append_log("自然语言", "流程草案追问", "提示", text)
                    if hasattr(self, "_operator_archive_execution_result"):
                        self._operator_archive_execution_result(result="clarification", final_text=text)
                    self._refresh_operator_view()
                    return True
            steps = draft.get("expanded_steps") if isinstance(draft, dict) else None
            step_count = len(steps) if isinstance(steps, list) else 0
            reason = str(getattr(plan, "reason", "") or "已生成流程草案。")
            text = f"{reason}\n当前仅生成草案，不自动保存或执行。草案步骤数：{step_count} 步。可说“确认保存”或“保存并执行”。"
            notices = getattr(self._operator_execution_plan_service(), "default_notices", [])
            if notices:
                text += "\n" + "\n".join(str(item) for item in notices)
            self._set_nlp_execute_busy(False)
            self.status_label.setText(reason)
            if getattr(self, "_operator_streaming_chat_active", False):
                self._operator_finish_streaming_chat_response(text)
            elif hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text)
            self._append_log("自然语言", "流程草案", "提示", text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="flow_draft", final_text=text)
            self._refresh_operator_view()
            return True
        return False

    @staticmethod
    def _operator_unknown_plan_chat_text(reason: str) -> str:
        clean = str(reason or "未识别到可执行动作。").strip()
        if not clean:
            clean = "未识别到可执行动作。"
        if "没有触发机械手动作" in clean:
            return clean
        return clean.rstrip("。") + "。没有触发机械手动作。"

    def _parse_nlp_text(self) -> None:
        text = self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else ""
        if self._handle_operator_ui_command(text):
            return
        if self._operator_reject_new_action_while_busy(text):
            return
        super()._parse_nlp_text()

    def _execute_nlp_text(self) -> None:
        text = self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else ""
        if self._handle_operator_ui_command(text):
            return
        if self._operator_reject_new_action_while_busy(text):
            return
        self._operator_set_pending_confirm_plan(None)
        super()._execute_nlp_text()

    def _clear_nlp_text(self) -> None:
        super()._clear_nlp_text()
        if hasattr(self, "operator_command_edit"):
            self.operator_command_edit.clear()
        self._operator_scene_override = None
        self._operator_set_pending_confirm_plan(None)
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
        if hasattr(self, "operator_command_edit"):
            self.operator_command_edit.clear()
        if hasattr(self, "nlp_input_edit"):
            self.nlp_input_edit.clear()

    def _operator_clear_text(self) -> None:
        self._clear_nlp_text()

    def _operator_toggle_microphone_recording(self) -> None:
        starting = not self._operator_voice_recording_active()
        started_at_sec = self._operator_now_seconds()
        if starting:
            builder = getattr(self, "operator_response_builder", None) or ResponseBuilder()
            self.operator_response_builder = builder
            self._operator_publish_response(builder.receipt(input_mode="voice", context_id="voice:receipt"))
        self._toggle_microphone_recording()
        if starting:
            delay_ms = self._operator_elapsed_ms_since(started_at_sec)
            self._operator_last_voice_receipt_delay_ms = delay_ms
            self._operator_last_voice_receipt_sla_passed = delay_ms <= self._operator_ack_limit_ms("voice")
        self._sync_operator_mic_button()

    def _operator_voice_receipt_sla_result(self) -> dict[str, object]:
        delay_ms = int(getattr(self, "_operator_last_voice_receipt_delay_ms", 0) or 0)
        limit_ms = self._operator_ack_limit_ms("voice")
        return {
            "ack_delay_ms": delay_ms,
            "ack_limit_ms": limit_ms,
            "ack_sla_passed": delay_ms <= limit_ms,
        }

    def _operator_voice_recording_active(self) -> bool:
        if getattr(self, "_local_voice_streaming", False):
            return True
        if getattr(self, "_proxy_mic_capturing", False):
            return True
        process = getattr(self, "_mic_process", None)
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:
            return False

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
        self._operator_archive_text_input(text)
        return True

    def _operator_reject_new_action_while_busy(self, text: str) -> bool:
        if not self._operator_execution_or_pause_active():
            return False
        message = ResponseMessage(
            kind="alert",
            text="当前任务未完成，已拒绝新的动作指令。可查询进度、暂停、继续、停止流程或使用应急编码。",
            priority="high",
            context_id="operator:new_action_blocked_while_busy",
        )
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText("当前任务未完成，已拒绝新的动作指令。")
        self._operator_archive_execution_result(result="blocked", final_text=message.text)
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "忙碌拒绝新指令", "拒绝", text)
        if hasattr(self, "_refresh_operator_view"):
            self._refresh_operator_view()
        return True

    def _operator_execution_or_pause_active(self) -> bool:
        return bool(
            getattr(self, "nlp_sequence_running", False)
            or getattr(self, "flow_running", False)
            or getattr(self, "busy", "") in {"运行中", "暂停"}
            or getattr(self, "run_state", "") in {"运行中", "暂停"}
        )

    def _operator_archive_text_input(self, text: str) -> dict[str, Any] | None:
        try:
            writer = self._operator_interaction_writer()
            record = writer.append_input_record(
                source="text",
                raw_text=text,
                device_snapshot=self._operator_device_snapshot_for_archive(),
                scene_state=self._operator_scene_state_payload(),
            )
            self._operator_last_interaction_record_id = record.msg_id
            self._operator_last_interaction_start_sec = self._operator_now_seconds()
            return record.to_dict()
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "交互记录归档", "失败", str(exc))
            return None

    def _operator_archive_voice_input(
        self,
        text: str,
        *,
        asr_confidence: float | None = None,
    ) -> dict[str, Any] | None:
        try:
            writer = self._operator_interaction_writer()
            record = writer.append_input_record(
                source="voice",
                raw_text=text,
                asr_confidence=asr_confidence,
                device_snapshot=self._operator_device_snapshot_for_archive(),
                scene_state=self._operator_scene_state_payload(),
            )
            self._operator_last_interaction_record_id = record.msg_id
            self._operator_last_interaction_start_sec = self._operator_now_seconds()
            return record.to_dict()
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "语音交互记录归档", "失败", str(exc))
            return None

    @staticmethod
    def _operator_asr_confidence_from_log(entry: dict[str, Any]) -> float | None:
        for key in ("asr_confidence", "voice_asr_confidence", "confidence"):
            value = entry.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _operator_interaction_archive_path(self):
        log_dir = getattr(self, "_log_dir", None)
        if log_dir is None:
            log_dir = getattr(self, "runtime_root", None) / "data" / "exported_logs"
        return log_dir / f"interaction_session_{getattr(self, 'session_id', 'session')}.jsonl"

    def _operator_dialog_log_dir(self):
        log_dir = getattr(self, "_log_dir", None)
        if log_dir is None:
            log_dir = getattr(self, "runtime_root", None) / "data" / "exported_logs"
        return log_dir / "dialogs"

    def _operator_dialog_logger(self) -> DialogLogger:
        return DialogLogger(self._operator_dialog_log_dir())

    def _operator_interaction_writer(self) -> InteractionArchiveWriter:
        return InteractionArchiveWriter(
            path=self._operator_interaction_archive_path(),
            session_id=str(getattr(self, "session_id", "session")),
            dialog_logger=self._operator_dialog_logger(),
        )

    def _operator_device_snapshot_for_archive(self) -> dict[str, Any]:
        snapshot_dict = self._operator_dashboard_snapshot_dict()
        try:
            dashboard_snapshot = DashboardCache().snapshot
            dashboard_snapshot = replace(
                dashboard_snapshot,
                ts=str(snapshot_dict.get("ts", dashboard_snapshot.ts)),
                position=dict(snapshot_dict.get("position", {})),
                safety=dict(snapshot_dict.get("safety", {})),
                motion=dict(snapshot_dict.get("motion", {})),
                connection=dict(snapshot_dict.get("connection", {})),
                hardware=dict(snapshot_dict.get("hardware", {})),
                refresh_ms=int(snapshot_dict.get("refresh_ms", dashboard_snapshot.refresh_ms)),
                boards=dict(snapshot_dict.get("boards", {})),
            )
            return DeviceSnapshot.from_dashboard_snapshot(dashboard_snapshot).to_dict()
        except Exception:
            return snapshot_dict

    def _operator_archive_nlp_result(self, plan) -> bool:
        msg_id = getattr(self, "_operator_last_interaction_record_id", "")
        if not msg_id:
            return False
        try:
            writer = self._operator_interaction_writer()
            return writer.update_nlp_result(msg_id, self._operator_nlp_result_payload(plan))
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "NLP结果归档", "失败", str(exc))
            return False

    def _operator_nlp_result_payload(self, plan) -> dict[str, Any]:
        actions = tuple(getattr(plan, "actions", ()) or ())
        first = actions[0] if actions else None
        action_type = getattr(first, "action_type", "unknown") if first else "unknown"
        target = getattr(first, "target", None) if first else None
        record = self._operator_record_for_action(plan, first) if first is not None else None
        params = dict(getattr(record, "params", {}) or {}) if record is not None else {}
        func_id = getattr(record, "func_num", None) if record is not None else None
        intent = "command" if action_type in {"template", "atomic_template", "flow", "system"} else "unknown"
        semantic_policy = policy_for_plan(plan)
        return {
            "semantic_level": semantic_policy.semantic_level,
            "semantic_label": semantic_policy.semantic_label,
            "response_deadline_ms": semantic_policy.result_deadline_ms,
            "progress_interval_ms": semantic_policy.progress_interval_ms,
            "requires_precheck": semantic_policy.requires_precheck,
            "requires_confirmation": semantic_policy.requires_confirmation,
            "priority": semantic_policy.priority,
            "intent": intent,
            "func_id": func_id,
            "params": params,
            "risk_level": params.get("atomic_risk_level"),
            "risk_reason": params.get("atomic_risk_reason"),
            "confidence": 0.8 if intent == "command" else 0.0,
            "engine": str(getattr(plan, "nlp_engine", "") or getattr(plan, "source", "") or "unknown"),
            "tokens": list(getattr(plan, "tokens", ()) or ()),
            "action_type": action_type,
            "target": target,
            "reason": str(getattr(plan, "reason", "") or ""),
            "command_intent": command_intent_from_plan(
                plan,
                table=getattr(self, "table", {}),
                source="text",
            ).to_dict(),
        }

    def _operator_archive_safety_check(self) -> bool:
        msg_id = getattr(self, "_operator_last_interaction_record_id", "")
        if not msg_id:
            return False
        try:
            writer = self._operator_interaction_writer()
            return writer.update_record(msg_id, {"safety_check": self._operator_safety_check_payload()})
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "安全检查归档", "失败", str(exc))
            return False

    def _operator_safety_check_payload(self) -> dict[str, Any]:
        l1 = getattr(self, "_operator_last_precheck_result", None)
        l2 = getattr(self, "_operator_last_motion_plan_result", None)
        l3 = getattr(self, "_operator_last_process_precheck_result", None)
        results = [result for result in (l1, l2, l3) if isinstance(result, dict)]
        if any(result.get("status") == "fail" for result in results):
            status = "fail"
        elif results and all(result.get("status") == "pass" for result in results):
            status = "pass"
        else:
            status = "warn" if results else "pending"
        warnings = [
            str(result.get("suggestion"))
            for result in results
            if result.get("status") == "unavailable" and result.get("suggestion")
        ]
        return {
            "pc_precheck": status,
            "pc_precheck_detail": {
                "l1": dict(l1 or {}),
                "l2": dict(l2 or {}),
                "l3": dict(l3 or {}),
            },
            "controller_check": "pending",
            "controller_check_func": None,
            "warnings": warnings,
        }

    def _operator_archive_execution_result(
        self,
        *,
        result: str,
        final_text: str,
        exec_duration_ms: int = 0,
        execution_detail: dict[str, Any] | None = None,
    ) -> bool:
        msg_id = getattr(self, "_operator_last_interaction_record_id", "")
        if not msg_id:
            return False
        current_execution = self._operator_current_interaction_execution()
        detail = execution_detail or {}
        modbus_write = dict(detail.get("modbus_write") or current_execution.get("modbus_write") or {})
        state_before = dict(detail.get("state_before") or current_execution.get("state_before") or self._operator_current_interaction_device_snapshot())
        state_after = dict(detail.get("state_after") or self._operator_device_snapshot_for_archive())
        existing_duration_ms = int(current_execution.get("exec_duration_ms", 0) or 0)
        effective_exec_duration_ms = int(exec_duration_ms) if int(exec_duration_ms) > 0 else existing_duration_ms
        response = self._operator_current_interaction_response()
        if not response.get("ack"):
            response["ack"] = "收到，正在处理。"
            response["ack_delay_ms"] = 0
        response["final"] = final_text
        response["final_delay_ms"] = self._operator_interaction_elapsed_ms(fallback_ms=int(exec_duration_ms))
        try:
            writer = self._operator_interaction_writer()
            return writer.update_record(
                msg_id,
                {
                    "execution": {
                        "modbus_write": modbus_write,
                        "state_before": state_before,
                        "state_after": state_after,
                        "result": result,
                        "exec_duration_ms": effective_exec_duration_ms,
                    },
                    "response": response,
                },
            )
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "执行结果归档", "失败", str(exc))
            return False

    def _operator_archive_engineer_voice_command(
        self,
        spec: EngineerVoiceCommandSpec,
        *,
        raw_text: str,
        result: str,
        final_text: str,
    ) -> bool:
        if not hasattr(self, "_log_dir") and getattr(self, "runtime_root", None) is None:
            return False
        if not getattr(self, "_operator_last_interaction_record_id", "") or getattr(self, "_operator_last_engineer_voice_raw_text", None) != raw_text:
            try:
                writer = self._operator_interaction_writer()
                record = writer.append_input_record(
                    source="engineer_voice",
                    raw_text=raw_text,
                    device_snapshot=self._operator_device_snapshot_for_archive(),
                    scene_state=self._operator_scene_state_payload(),
                )
                self._operator_last_engineer_voice_raw_text = raw_text
                self._operator_last_interaction_record_id = record.msg_id
                self._operator_last_interaction_start_sec = self._operator_now_seconds()
            except Exception as exc:
                if hasattr(self, "_append_log"):
                    self._append_log("归档", "工程师语音交互归档", "失败", str(exc))
                return False
        msg_id = getattr(self, "_operator_last_interaction_record_id", "")
        if not msg_id:
            return False
        nlp_result = {
            "semantic_level": 4,
            "semantic_label": "系统管理层",
            "intent": "engineer_command",
            "func_id": None,
            "params": {
                "button_label": spec.button_label,
                "section": spec.section,
                "danger_level": spec.danger_level,
            },
            "confidence": 1.0,
            "engine": "engineer_voice_commands",
            "action_type": spec.action,
            "target": spec.button_label,
            "reason": "工程师页语音等价指令",
        }
        try:
            writer = self._operator_interaction_writer()
            writer.update_nlp_result(msg_id, nlp_result)
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "工程师语音NLP归档", "失败", str(exc))
        return self._operator_archive_execution_result(
            result=result,
            final_text=final_text,
            execution_detail={
                "modbus_write": {},
                "engineer_voice": {
                    "section": spec.section,
                    "button_label": spec.button_label,
                    "action": spec.action,
                    "danger_level": spec.danger_level,
                },
            },
        )

    def _operator_current_interaction_execution(self) -> dict[str, Any]:
        payload = self._operator_current_interaction_payload()
        execution = payload.get("execution", {}) if isinstance(payload, dict) else {}
        return dict(execution) if isinstance(execution, dict) else {}

    def _operator_current_interaction_device_snapshot(self) -> dict[str, Any]:
        payload = self._operator_current_interaction_payload()
        snapshot = payload.get("device_snapshot", {}) if isinstance(payload, dict) else {}
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def _operator_archive_response_ack(self, ack_text: str) -> bool:
        msg_id = getattr(self, "_operator_last_interaction_record_id", "")
        if not msg_id:
            return False
        response = self._operator_current_interaction_response()
        response["ack"] = ack_text
        response["ack_delay_ms"] = self._operator_interaction_elapsed_ms()
        response["ack_limit_ms"] = self._operator_ack_limit_ms("text")
        response["ack_sla_passed"] = response["ack_delay_ms"] <= response["ack_limit_ms"]
        try:
            writer = self._operator_interaction_writer()
            return writer.update_record(msg_id, {"response": response})
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("归档", "回执归档", "失败", str(exc))
            return False

    def _operator_current_interaction_response(self) -> dict[str, Any]:
        payload = self._operator_current_interaction_payload()
        response = payload.get("response", {}) if isinstance(payload, dict) else {}
        if not isinstance(response, dict):
            response = {}
        return {
            "ack": str(response.get("ack", "") or ""),
            "ack_delay_ms": int(response.get("ack_delay_ms", 0) or 0),
            "ack_limit_ms": int(response.get("ack_limit_ms", 0) or 0),
            "ack_sla_passed": bool(response.get("ack_sla_passed", False)),
            "final": str(response.get("final", "") or ""),
            "final_delay_ms": int(response.get("final_delay_ms", 0) or 0),
        }

    def _operator_current_interaction_payload(self) -> dict[str, Any]:
        msg_id = getattr(self, "_operator_last_interaction_record_id", "")
        if not msg_id:
            return {}
        path = self._operator_interaction_archive_path()
        if not path.exists():
            return {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("msg_id") == msg_id:
                return payload
        return {}

    def _operator_interaction_elapsed_ms(self, *, fallback_ms: int = 0) -> int:
        start = getattr(self, "_operator_last_interaction_start_sec", None)
        if start is None:
            return int(fallback_ms)
        return self._operator_elapsed_ms_since(float(start))

    def _operator_elapsed_ms_since(self, started_at_sec: float) -> int:
        elapsed = max(0.0, self._operator_now_seconds() - float(started_at_sec))
        return int(round(elapsed * 1000))

    @staticmethod
    def _operator_ack_limit_ms(input_mode: str) -> int:
        if input_mode == "emergency":
            return 30
        if input_mode == "voice":
            return 200
        return 50

    def _operator_stop_current(self) -> None:
        if getattr(self, "flow_running", False):
            self._handle_system_action("sys_cancel")
            self._stop_flow()
            self.status_label.setText("已发送取消当前任务命令。")
            self._append_log("用户页面", "停止当前任务", "成功", "已发送 Func104 取消当前函数")
            self._refresh_operator_view()
            return
        if self._operator_execution_or_pause_active():
            self._handle_system_action("sys_cancel")
            self.status_label.setText("已发送取消当前任务命令。")
            self._append_log("用户页面", "停止当前任务", "成功", "已发送 Func104 取消当前函数")
            self._refresh_operator_view()
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

    def _operator_set_pending_confirm_plan(self, plan) -> None:
        self._operator_pending_confirm_plan = plan
        if plan is None:
            self._operator_pending_confirm_deadline_sec = 0.0
            return
        self._operator_pending_confirm_deadline_sec = self._operator_now_seconds() + self._operator_confirm_timeout_seconds()

    def _operator_confirm_timeout_seconds(self) -> float:
        configured = getattr(getattr(self, "axis_ranges", None), "operator_confirm_timeout_sec", 60.0)
        try:
            return max(1.0, float(configured))
        except (TypeError, ValueError):
            return 60.0

    def _operator_reject_expired_pending_confirm(self) -> bool:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if plan is None:
            return False
        deadline = float(getattr(self, "_operator_pending_confirm_deadline_sec", 0.0) or 0.0)
        if deadline <= 0 or self._operator_now_seconds() <= deadline:
            return False
        self._operator_expire_pending_confirm()
        return True

    def _operator_expire_pending_confirm(self, *, refresh: bool = True) -> None:
        self._operator_set_pending_confirm_plan(None)
        self._operator_scene_override = None
        text = "安全确认已超时，已取消待执行计划。请重新输入指令。"
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        self._operator_add_chat_message("assistant", text)
        self._operator_archive_execution_result(result="blocked", final_text=text)
        self._append_log("用户页面", "安全确认超时", "拒绝", text)
        if refresh:
            self._refresh_operator_view()

    def _operator_clear_expired_pending_confirm_for_refresh(self) -> None:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if plan is None:
            return
        deadline = float(getattr(self, "_operator_pending_confirm_deadline_sec", 0.0) or 0.0)
        if deadline > 0 and self._operator_now_seconds() > deadline:
            self._operator_expire_pending_confirm(refresh=False)

    def _operator_confirm_execute(self) -> None:
        if self._operator_reject_expired_pending_confirm():
            return
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if not self._operator_plan_is_executable(plan):
            self._operator_no_pending_confirm()
            return
        precheck = getattr(self, "_operator_last_precheck_result", None)
        if isinstance(precheck, dict) and precheck.get("status") == "fail":
            text = self._operator_precheck_summary(precheck)
            self.status_label.setText("L1预检未通过，已拒绝执行。")
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="blocked", final_text=text)
            self._append_log("安全预检", "确认执行", "拒绝", text)
            self._refresh_operator_view()
            return
        motion_plan = getattr(self, "_operator_last_motion_plan_result", None)
        if self._operator_l2_should_block(motion_plan):
            text = self._operator_l2_summary(motion_plan)
            self.status_label.setText("L2运动规划预演未通过，已拒绝执行。")
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="blocked", final_text=text)
            self._append_log("运动预演", "确认执行", "拒绝", text)
            self._refresh_operator_view()
            return
        process_precheck = getattr(self, "_operator_last_process_precheck_result", None)
        if self._operator_l3_should_block(process_precheck):
            text = self._operator_l3_summary(process_precheck)
            self.status_label.setText("L3流程预演未通过，已拒绝执行。")
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="blocked", final_text=text)
            self._append_log("流程预演", "确认执行", "拒绝", text)
            self._refresh_operator_view()
            return
        self._operator_set_pending_confirm_plan(None)
        self._operator_scene_override = None
        self._set_nlp_execute_busy(True)
        self.status_label.setText("确认收到，开始执行。")
        self._operator_add_chat_message("assistant", "确认收到，开始执行。")
        self._operator_archive_execution_result(result="accepted", final_text="确认收到，开始执行。")
        self._append_log("用户页面", "确认执行", "成功", getattr(plan, "reason", "已确认执行"))
        self._execute_nlp_plan(plan)

    def _operator_accept_suggestion(self) -> None:
        if self._operator_reject_expired_pending_confirm():
            return
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if not self._operator_plan_is_executable(plan):
            self._operator_no_pending_confirm()
            return
        suggested_plan = self._operator_apply_l1_suggestion_to_plan(plan)
        if suggested_plan is None:
            suggested_plan = self._operator_apply_l2_avoidance_suggestion_to_plan(plan)
        if suggested_plan is None:
            text = self._operator_current_blocking_summary() or "当前没有可自动改写的安全建议。"
            self.status_label.setText("当前没有可自动改写的安全建议，未执行原计划。")
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="blocked", final_text=text)
            self._append_log("用户页面", "采纳建议", "拒绝", text)
            self._refresh_operator_view()
            return
        self._operator_set_pending_confirm_plan(suggested_plan)
        self._operator_prepare_plan_prechecks(suggested_plan)
        self._append_log("用户页面", "采纳建议", "成功", self._operator_current_blocking_summary() or "已采纳安全建议")
        self._operator_confirm_execute()

    def _operator_current_blocking_summary(self) -> str:
        precheck = getattr(self, "_operator_last_precheck_result", None)
        if isinstance(precheck, dict) and precheck.get("status") == "fail":
            return self._operator_precheck_summary(precheck)
        motion_plan = getattr(self, "_operator_last_motion_plan_result", None)
        if self._operator_l2_should_block(motion_plan):
            return self._operator_l2_summary(motion_plan)
        process_precheck = getattr(self, "_operator_last_process_precheck_result", None)
        if self._operator_l3_should_block(process_precheck):
            return self._operator_l3_summary(process_precheck)
        return ""

    def _operator_cancel_confirm(self) -> None:
        if getattr(self, "_operator_pending_confirm_plan", None) is not None:
            self._operator_set_pending_confirm_plan(None)
            self._operator_scene_override = None
            text = "已取消待确认的执行计划。"
            self.status_label.setText(text)
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="cancelled", final_text=text)
            self._append_log("用户页面", "取消确认", "成功", "已取消待确认计划")
            self._refresh_operator_view()
            return
        self._operator_stop_current()

    def _handle_operator_ui_command(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False

        if self._operator_handle_emergency_text(text):
            return True
        if self._operator_handle_progress_query(text):
            return True
        if self._operator_handle_pending_confirm_modify(text):
            return True
        if self._operator_handle_pending_clarification_answer(text):
            return True
        if self._operator_handle_pending_flow_draft_edit(text):
            return True
        if self._operator_handle_pending_flow_draft_command(text):
            return True
        if self._operator_handle_pending_flow_draft_query(text):
            return True
        if self._operator_handle_context_query(text):
            return True
        if self._operator_handle_dashboard_query(text):
            return True
        if self._operator_handle_engineer_confirm_query(text):
            return True
        if self._operator_handle_atomic_capability_query(text):
            return True
        if self._operator_handle_engineer_voice_capability_query(text):
            return True
        if self._operator_handle_pending_engineer_voice_command(text):
            return True
        if self._operator_handle_voice_command_spec(text):
            return True
        if self._operator_handle_engineer_voice_command_spec(text):
            return True

        def has_any(*keywords: str) -> bool:
            return any(keyword in compact for keyword in keywords)

        if has_any("切换到用户页面", "打开用户页面", "用户页面", "操作页面"):
            self._set_workspace_mode("operator")
            self._operator_reply_ui_command_success("已切换到用户页面。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("切换到工程师", "工程师页面", "工程师模式"):
            self._set_workspace_mode("engineer")
            self._operator_reply_ui_command_success("已切换到工程师页面。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("显示完整状态", "完整状态", "状态看板", "七类看板"):
            self._set_workspace_mode("operator")
            self._operator_show_full_status()
            self._operator_reply_ui_command_success("已显示完整状态看板。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("回到主界面", "返回主界面", "主界面", "待机画面"):
            self._set_workspace_mode("operator")
            self._operator_go_home()
            self._operator_reply_ui_command_success("已回到主界面。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("退出全屏", "恢复窗口", "普通窗口"):
            self._set_workspace_mode("operator")
            self._operator_restore_normal_window()
            self._operator_reply_ui_command_success("已退出全屏。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("全屏", "放大界面"):
            self._set_workspace_mode("operator")
            self._operator_show_fullscreen()
            self._operator_reply_ui_command_success("已进入全屏。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("小窗口", "缩小界面"):
            self._set_workspace_mode("operator")
            if not getattr(self, "_operator_compact", False):
                self._operator_toggle_compact()
            self._operator_reply_ui_command_success("已切换到小窗口。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("上一条"):
            self._operator_scroll_recent(-1)
            self._operator_reply_ui_command_success("已切换到上一条记录。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("下一条"):
            self._operator_scroll_recent(1)
            self._operator_reply_ui_command_success("已切换到下一条记录。")
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("显示安全参数", "安全参数"):
            self._set_workspace_mode("operator")
            self._operator_scene_override = "query"
            self._operator_reply_ui_command_success("已显示完整状态中的安全参数看板。")
            self._refresh_operator_view()
            self._append_log("用户页面", "界面语音指令", "成功", text)
            return True
        if has_any("显示标定画面", "标定画面"):
            self._set_workspace_mode("engineer")
            self._show_page(1)
            self._operator_reply_ui_command_success("当前 Qt 后台页面包含系统参数和模板维护。")
            self._append_log("用户页面", "界面语音指令", "提示", "已切换工程师后台页")
            return True

        return False

    def _operator_reply_ui_command_success(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", text)

    def _operator_execution_plan_service(self):
        from .execution_plan_service import ExecutionPlanService

        service = getattr(self, "_execution_plan_service", None)
        if service is None:
            service = ExecutionPlanService()
            self._execution_plan_service = service
        return service

    def _operator_handle_pending_clarification_answer(self, text: str) -> bool:
        service = self._operator_execution_plan_service()
        if service.current_clarification() is None:
            return False
        result = service.apply_clarification_answer(text)
        if not result.applied:
            message = result.message
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(message))
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", message)
            self._append_log("自然语言", "追问回答", "失败", message)
            self._refresh_operator_view()
            return True
        draft = getattr(self, "_operator_pending_flow_draft", None)
        updated = service.pending_flow_draft()
        if isinstance(draft, dict) and updated is not None:
            merged = dict(draft)
            merged["expanded_steps"] = updated.get("expanded_steps", [])
            merged["flow_name"] = updated.get("flow_name", merged.get("flow_name", ""))
            merged["needs_precheck"] = True
            self._operator_pending_flow_draft = merged
        message = f"{result.message}。当前流程草案已更新，保存或执行前需要重新预检。"
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(message))
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", message)
        self._append_log("自然语言", "追问回答", "成功", message)
        self._refresh_operator_view()
        return True

    def _operator_handle_pending_flow_draft_edit(self, text: str) -> bool:
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(draft, dict) or not draft:
            return False
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        service = self._operator_execution_plan_service()
        service.set_pending_flow_draft(draft)
        result = None
        match = re.search(r"第\s*(\d+)\s*步.*速度.*?(\d+(?:\.\d+)?)\s*%?", compact)
        if match:
            step_id = int(match.group(1))
            speed = float(match.group(2))
            result = service.edit_step_params(
                step_id,
                {"spd_pct": speed, "acc_pct": speed, "dec_pct": speed},
            )
        if result is None:
            match = re.search(r"(第|步骤)\s*(\d+)\s*步?.*延时.*?(\d+(?:\.\d+)?)\s*(秒|s|毫秒|ms)?", compact, re.I)
            if match:
                step_id = int(match.group(2))
                value = float(match.group(3))
                unit = (match.group(4) or "秒").lower()
                delay_sec = value / 1000.0 if unit in ("毫秒", "ms") else value
                result = service.edit_step_params(step_id, {"delay_sec": delay_sec})
        if result is None:
            match = re.search(r"删除.*(第|步骤)\s*(\d+)\s*步?", compact)
            if match:
                result = service.delete_step(int(match.group(2)))
        if result is None:
            match = re.search(r"(整体|全部|所有).*速度.*?(\d+(?:\.\d+)?)\s*%?", compact)
            if match:
                result = service.edit_all_speed(float(match.group(2)))
        if result is None:
            match = re.search(r"(最后|末尾|后面).*加.*步(.+)", compact)
            if match:
                step = self._operator_build_append_flow_step_from_text(draft, match.group(2))
                if step is None:
                    return False
                result = service.append_step(step)
        if result is None and re.search(r"(撤销|恢复上一步|取消刚才的修改)", compact):
            result = service.undo()
        if result is None:
            return False
        if not result.ok:
            message = result.message
            if hasattr(self, "status_label"):
                self.status_label.setText(message)
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", message)
            self._append_log("自然语言", "流程草案编辑", "失败", message)
            self._refresh_operator_view()
            return True
        updated = service.pending_flow_draft()
        if updated is not None:
            merged = dict(draft)
            merged["expanded_steps"] = updated.get("expanded_steps", [])
            merged["flow_name"] = updated.get("flow_name", merged.get("flow_name", ""))
            merged["needs_precheck"] = True
            self._operator_pending_flow_draft = merged
        message = f"{result.message}。当前流程草案尚未保存/执行。"
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(message))
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", message)
        self._append_log("自然语言", "流程草案编辑", "成功", message)
        self._refresh_operator_view()
        return True

    def _operator_build_append_flow_step_from_text(self, draft: dict, text: str):
        from .execution_plan import ExecutionStep

        compact = re.sub(r"\s+", "", text or "").lower()
        if not compact:
            return None
        positions = draft.get("positions")
        if not isinstance(positions, list):
            positions = []
        selected = None
        for item in positions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name and name.lower() in compact:
                selected = item
                break
        if selected is None and any(alias in compact for alias in ("休息姿态", "0位", "零位")):
            selected = {"name": "休息姿态", "pose": [900.0, 0.0, 1000.0, 0.0, 0.0, 0.0]}
        if selected is None:
            return None
        pose = list(selected.get("pose") or [])
        if len(pose) < 6:
            return None
        default_speed = self._operator_flow_draft_default_speed(draft)
        name = str(selected.get("name") or "").strip()
        params = {
            "target_x": float(pose[0]),
            "target_y": float(pose[1]),
            "target_z": float(pose[2]),
            "target_rx": float(pose[3]),
            "target_ry": float(pose[4]),
            "target_rz": float(pose[5]),
            "spd_pct": default_speed,
            "acc_pct": default_speed,
            "dec_pct": default_speed,
            "move_type": 0,
        }
        return ExecutionStep(
            step_id=0,
            action="move_position",
            func_id=108,
            params=params,
            target_label=name,
            description=f"移动到{name}",
        )

    @staticmethod
    def _operator_flow_draft_default_speed(draft: dict) -> float:
        steps = draft.get("expanded_steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                params = step.get("params")
                if isinstance(params, dict) and "spd_pct" in params:
                    try:
                        return float(params["spd_pct"])
                    except (TypeError, ValueError):
                        continue
        return 50.0

    def _operator_handle_pending_flow_draft_command(self, text: str) -> bool:
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(draft, dict) or not draft:
            return False
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        if any(keyword in compact for keyword in ("取消草案", "放弃草案", "不要保存", "取消流程草案")):
            self._operator_pending_flow_draft = None
            message = "已取消当前流程草案。"
            self.status_label.setText(message)
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", message)
            self._append_log("自然语言", "流程草案取消", "成功", message)
            self._refresh_operator_view()
            return True
        if any(keyword in compact for keyword in ("重新预检", "开始预检", "检查草案", "预检草案", "重跑预检")):
            plan = self._operator_flow_draft_precheck_plan(draft)
            if plan is None:
                message = "当前流程草案无法生成预检计划，请先补齐步骤参数。"
                self.status_label.setText(message)
                if hasattr(self, "_operator_add_chat_message"):
                    self._operator_add_chat_message("assistant", message)
                self._append_log("自然语言", "流程草案预检", "失败", message)
                self._refresh_operator_view()
                return True
            self._operator_prepare_plan_prechecks(plan)
            draft["needs_precheck"] = False
            message = "已对当前流程草案重新预检。"
            self.status_label.setText(message)
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", message)
            self._append_log("自然语言", "流程草案预检", "成功", message)
            self._refresh_operator_view()
            return True
        execute_after_save = any(
            keyword in compact
            for keyword in ("保存并执行", "确认并执行", "确认执行", "执行这个流程", "运行这个流程", "保存后执行")
        )
        save_only = execute_after_save or any(
            keyword in compact for keyword in ("确认保存", "保存流程", "保存草案", "保存这个流程", "确认草案")
        )
        if not save_only:
            return False
        ok, detail, flow_name = self._operator_save_flow_draft(draft)
        if not ok:
            self.status_label.setText(detail)
            if hasattr(self, "_show_warning"):
                self._show_warning("流程草案保存失败", detail)
            self._append_log("自然语言", "流程草案保存", "失败", detail)
            self._refresh_operator_view()
            return True
        self._operator_pending_flow_draft = None
        self.current_flow_name = flow_name
        if hasattr(self, "flow_combo"):
            try:
                if self.flow_combo.findText(flow_name) < 0:
                    self.flow_combo.addItem(flow_name)
                self.flow_combo.setCurrentText(flow_name)
            except Exception:
                pass
        if hasattr(self, "_refresh_flow_combo"):
            self._refresh_flow_combo()
            if hasattr(self, "flow_combo"):
                self.flow_combo.setCurrentText(flow_name)
        if execute_after_save and hasattr(self, "_start_flow"):
            message = f"已保存流程草案：{flow_name}，开始执行。"
            self.status_label.setText(message)
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", message)
            self._append_log("自然语言", "流程草案保存并执行", "成功", detail)
            self._start_flow()
            return True
        message = f"已保存流程草案：{flow_name}。"
        self.status_label.setText(message)
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", message)
        self._append_log("自然语言", "流程草案保存", "成功", detail)
        self._refresh_operator_view()
        return True

    def _operator_handle_pending_flow_draft_query(self, text: str) -> bool:
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(draft, dict) or not draft:
            return False
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        query_keywords = (
            "什么样的流程",
            "流程是什么",
            "这个流程",
            "小流程",
            "看下流程",
            "看看流程",
            "查看流程",
            "流程草案",
            "草案步骤",
            "步骤是什么",
            "有哪些步骤",
            "然后呢",
            "然后",
            "接下来",
            "接下来呢",
            "下一步",
            "下一步呢",
            "后续呢",
        )
        if not any(keyword in compact for keyword in query_keywords):
            return False
        include_params = any(keyword in compact for keyword in ("参数", "坐标", "xy", "xyz", "具体"))
        answer = self._operator_flow_draft_preview_text(draft, include_params=include_params)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer))
        if getattr(self, "_operator_streaming_chat_active", False):
            self._operator_finish_streaming_chat_response(answer)
        elif hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", answer)
        self._operator_publish_ai_answer_for_speech(answer)
        self._append_log("自然语言", "流程草案查询", "成功", answer)
        self._refresh_operator_view()
        return True

    def _operator_handle_context_query(self, text: str) -> bool:
        answer = self._operator_context_answer(text)
        if not answer:
            return False
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer))
        if getattr(self, "_operator_streaming_chat_active", False):
            self._operator_finish_streaming_chat_response(answer)
        elif hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", answer)
        self._operator_publish_ai_answer_for_speech(answer)
        self._append_log("自然语言", "上下文查询", "成功", answer)
        self._refresh_operator_view()
        return True

    def _operator_context_answer(self, text: str) -> str:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return ""
        if self._operator_text_looks_like_flow_creation_request(compact):
            return ""
        status_answer = self._operator_device_status_context_answer(compact)
        if status_answer:
            return status_answer
        if any(keyword in compact for keyword in ("执行结果", "运行结果", "上次执行", "刚才执行", "刚刚执行")):
            return self._operator_last_execution_result_text()
        flow_answer = self._operator_registered_flow_context_answer(compact)
        if flow_answer:
            return flow_answer
        if self._operator_text_looks_like_position_context_query(compact):
            position_answer = self._operator_position_context_answer(compact)
            if position_answer:
                return position_answer
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if isinstance(draft, dict) and draft and any(keyword in compact for keyword in ("草案", "方案", "流程")):
            return self._operator_flow_draft_preview_text(draft, include_params=True)
        if any(keyword in compact for keyword in ("流程", "方案")) and any(
            keyword in compact for keyword in ("具体", "详情", "参数", "步骤", "什么样")
        ):
            return self._operator_current_flow_context_answer()
        return ""

    def _operator_registered_flow_context_answer(self, compact_text: str) -> str:
        if not any(keyword in compact_text for keyword in ("流程", "信息", "详情", "步骤", "看看", "看下", "查询")):
            return ""
        flow = self._operator_find_registered_flow_for_text(compact_text)
        if flow is None:
            return ""
        return self._operator_flow_entry_preview_text(flow, include_params=True)

    def _operator_find_registered_flow_for_text(self, compact_text: str):
        service = getattr(self, "service", None)
        candidates = []
        registry = getattr(service, "flow_registry", None)
        if registry is not None and hasattr(registry, "list_all"):
            try:
                candidates.extend(registry.list_all())
            except Exception:
                pass
        flows = getattr(service, "flows", None)
        if isinstance(flows, dict):
            candidates.extend(flows.values())
        best = None
        best_len = 0
        for item in candidates:
            name = str(getattr(item, "name", "") or "").strip()
            if not name:
                continue
            compact_name = re.sub(r"\s+", "", name)
            if compact_name and compact_name in compact_text and len(compact_name) > best_len:
                best = item
                best_len = len(compact_name)
        return best

    @staticmethod
    def _operator_flow_entry_preview_text(flow: Any, *, include_params: bool = False) -> str:
        name = str(getattr(flow, "name", "") or "未命名流程").strip() or "未命名流程"
        description = str(getattr(flow, "description", "") or "").strip()
        confirmed = "是" if bool(getattr(flow, "confirmed", False)) else "否"
        version = getattr(flow, "version", "")
        steps = list(getattr(flow, "steps", ()) or ())
        lines = [f"流程 {name}：共 {len(steps)} 步，已确认：{confirmed}。"]
        if version not in ("", None):
            lines[0] = f"流程 {name}：共 {len(steps)} 步，版本 {version}，已确认：{confirmed}。"
        if description:
            lines.append(f"说明：{description}")
        if steps:
            lines.append("步骤：")
            for index, step in enumerate(steps[:12], start=1):
                action = str(getattr(step, "description", "") or getattr(step, "action", "") or "执行动作").strip()
                func_id = getattr(step, "func_id", "")
                position_name = str(getattr(step, "position_name", "") or "").strip()
                suffix_parts = []
                if func_id not in ("", None, 0):
                    suffix_parts.append(f"Func{int(float(func_id))}")
                if position_name:
                    suffix_parts.append(f"位置 {position_name}")
                suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
                lines.append(f"{index}. {action}{suffix}")
                params = getattr(step, "params", None)
                if include_params and isinstance(params, dict) and params:
                    lines.append(f"   参数：{OperatorUiMixin._operator_format_params_inline(params)}")
            if len(steps) > 12:
                lines.append(f"... 还有 {len(steps) - 12} 步未展开显示。")
        return "\n".join(lines)

    @staticmethod
    def _operator_text_looks_like_flow_creation_request(compact_text: str) -> bool:
        if not compact_text:
            return False
        has_flow = "流程" in compact_text or "小流程" in compact_text
        has_create = any(
            keyword in compact_text
            for keyword in ("编写", "创建", "生成", "新建", "写一下", "做一个", "打个")
        )
        has_sequence = any(keyword in compact_text for keyword in ("先", "再", "然后", "接着", "之后"))
        has_wake = any(keyword in compact_text for keyword in ("小正", "小郑", "校正"))
        return has_flow and has_wake and (has_create or has_sequence)

    @staticmethod
    def _operator_text_looks_like_position_context_query(compact_text: str) -> bool:
        if not compact_text:
            return False
        if not any(keyword in compact_text for keyword in ("位置", "位", "坐标", "xy", "XY", "xyz", "XYZ", "参数")):
            return False
        query_markers = (
            "参数",
            "坐标",
            "xy",
            "XY",
            "xyz",
            "XYZ",
            "具体",
            "什么",
            "是多少",
            "我问",
            "问的",
            "看下",
            "看一下",
            "查询",
            "查一下",
        )
        return any(marker in compact_text for marker in query_markers)

    def _operator_device_status_context_answer(self, compact_text: str) -> str:
        if not compact_text:
            return ""
        status_markers = ("状态", "下位机", "设备", "机械手")
        query_markers = ("现在", "当前", "什么", "怎样", "怎么样", "如何", "是否", "正常吗")
        if not any(marker in compact_text for marker in status_markers):
            return ""
        if not any(marker in compact_text for marker in query_markers):
            return ""
        snapshot = self._operator_dashboard_snapshot_dict() if hasattr(self, "_operator_dashboard_snapshot_dict") else {}
        data = snapshot.get("data") if isinstance(snapshot, dict) and isinstance(snapshot.get("data"), dict) else snapshot
        if not isinstance(data, dict):
            data = {}
        boards = data.get("boards") if isinstance(data.get("boards"), dict) else {}
        device = boards.get("device_status") if isinstance(boards.get("device_status"), dict) else {}
        communication = boards.get("communication_faults") if isinstance(boards.get("communication_faults"), dict) else {}
        merged = dict(data)
        merged.update({k: v for k, v in device.items() if k not in merged or merged.get(k) in (None, "", "-")})
        if "ecat_ok" not in merged and "ecat_ok" in communication:
            merged["ecat_ok"] = communication.get("ecat_ok")
        system_state = merged.get("system_state", "-")
        current_func = merged.get("func_id_current", "-")
        ready = "是" if bool(merged.get("ready", False)) else "否"
        estop = "开" if bool(merged.get("estop", False)) else "关"
        pause = "开" if bool(merged.get("pause", False)) else "关"
        alarm = "有" if bool(merged.get("alarm", False)) else "无"
        alarm_code = str(merged.get("alarm_code", "-") or "-")
        ecat_ok = "正常" if bool(merged.get("ecat_ok", True)) else "异常"
        position = self._operator_format_state_after_position(merged)
        position_text = f"当前位置：X={position[1:-1].split(', ')[0]}，Y={position[1:-1].split(', ')[1]}，Z={position[1:-1].split(', ')[2]}。" if position else ""
        return (
            f"当前下位机状态：{system_state}。"
            f"当前函数：{current_func}。通讯：{ecat_ok}，就绪：{ready}；"
            f"急停：{estop}，暂停：{pause}，报警：{alarm}，报警码：{alarm_code}。"
            f"{position_text}"
        )

    def _operator_position_context_answer(self, compact_text: str) -> str:
        try:
            registry = self._position_registry() if hasattr(self, "_position_registry") else None
        except Exception:
            registry = None
        entries = []
        if registry is not None and hasattr(registry, "list_all"):
            try:
                entries = list(registry.list_all())
            except Exception:
                entries = []
        if not entries:
            return self._operator_table_position_context_answer(compact_text)
        normalized_text = compact_text.lower()
        selected = None
        for entry in entries:
            name = str(getattr(entry, "name", "") or "").strip()
            if name and name.lower() in normalized_text:
                selected = entry
                break
        if selected is None and len(entries) == 1 and (
            any(keyword in compact_text for keyword in ("home", "Home", "HOME"))
            or any(keyword in compact_text for keyword in ("坐标", "参数", "xy", "xyz", "XYZ", "具体"))
        ):
            selected = entries[0]
        if selected is None:
            table_answer = self._operator_table_position_context_answer(compact_text)
            if table_answer:
                return table_answer
            return ""
        x, y, z, rx, ry, rz = getattr(selected, "pose", (0, 0, 0, 0, 0, 0))
        spd = getattr(selected, "spd", 50)
        move_type = getattr(selected, "move_type", 0)
        locked = "是" if bool(getattr(selected, "locked", False)) else "否"
        return (
            f"位置 {selected.name} 的参数："
            f"x={self._operator_compact_number(x)}，y={self._operator_compact_number(y)}，"
            f"z={self._operator_compact_number(z)}，rx={self._operator_compact_number(rx)}，"
            f"ry={self._operator_compact_number(ry)}，rz={self._operator_compact_number(rz)}；"
            f"速度={spd}%，move_type={move_type}，locked={locked}。"
        )

    def _operator_table_position_context_answer(self, compact_text: str) -> str:
        table = getattr(self, "table", None)
        if not isinstance(table, dict):
            return ""
        normalized_text = compact_text.lower()
        selected = None
        for key, record in table.items():
            name = str(getattr(record, "query_key", key) or key).strip()
            keywords = str(getattr(record, "keywords", "") or "")
            candidates = [name, *[part.strip() for part in re.split(r"[\s,，;；]+", keywords) if part.strip()]]
            if any(candidate and candidate.lower() in normalized_text for candidate in candidates):
                selected = record
                break
        if selected is None:
            return ""
        params = dict(getattr(selected, "params", {}) or {})
        if not params:
            return ""
        x = params.get("target_x", params.get("x", 0))
        y = params.get("target_y", params.get("y", 0))
        z = params.get("target_z", params.get("z", 0))
        rx = params.get("target_rx", params.get("rx", 0))
        ry = params.get("target_ry", params.get("ry", 0))
        rz = params.get("target_rz", params.get("rz", 0))
        spd = params.get("spd_pct", params.get("speed", 50))
        move_type = params.get("move_type", 0)
        name = str(getattr(selected, "query_key", "") or "未知位置")
        return (
            f"位置 {name} 的参数："
            f"x={self._operator_compact_number(x)}，y={self._operator_compact_number(y)}，"
            f"z={self._operator_compact_number(z)}，rx={self._operator_compact_number(rx)}，"
            f"ry={self._operator_compact_number(ry)}，rz={self._operator_compact_number(rz)}；"
            f"速度={self._operator_compact_number(spd)}%，move_type={move_type}。"
        )

    def _operator_last_execution_result_text(self) -> str:
        row = self._operator_last_execution_record()
        if not row:
            return "当前会话还没有可报告的机械手执行结果。"
        execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
        result = str(execution.get("result") or "")
        raw_text = str((row.get("input") or {}).get("raw_text") or "-")
        final = str((row.get("response") or {}).get("final") or "-")
        label = {
            "success": "成功",
            "failure": "失败",
            "warning": "警告",
            "blocked": "已阻止",
            "accepted": "已接受",
            "cancelled": "已取消",
        }.get(result, result)
        return f"最近一次执行结果：{label}。指令：{raw_text}。系统反馈：{final}"

    def _operator_last_execution_record(self) -> dict[str, Any]:
        try:
            path = self._operator_interaction_archive_path()
        except Exception:
            return {}
        if path is None or not path.exists():
            return {}
        current_msg_id = str(getattr(self, "_operator_last_interaction_record_id", "") or "")
        ignored = {"pending", "chat", "answered", "flow_draft"}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return {}
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("msg_id") == current_msg_id:
                continue
            execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
            result = str(execution.get("result") or "")
            if not result or result in ignored:
                continue
            return row
        return {}

    def _operator_last_execution_state_after_text(self) -> str:
        row = self._operator_last_execution_record()
        if not row:
            return ""
        execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
        state_after = execution.get("state_after") if isinstance(execution.get("state_after"), dict) else {}
        data = state_after.get("data") if isinstance(state_after.get("data"), dict) else state_after
        if not isinstance(data, dict) or not data:
            return ""
        result = str(execution.get("result") or "-")
        label = {
            "success": "成功",
            "failure": "失败",
            "warning": "警告",
            "blocked": "已阻止",
            "accepted": "已接受",
            "cancelled": "已取消",
        }.get(result, result)
        final = str((row.get("response") or {}).get("final") or "-")
        system_state = data.get("system_state", "-")
        current_func = data.get("func_id_current", "-")
        ready = "是" if bool(data.get("ready", False)) else "否"
        estop = "开" if bool(data.get("estop", False)) else "关"
        pause = "开" if bool(data.get("pause", False)) else "关"
        alarm = "有" if bool(data.get("alarm", False)) else "无"
        alarm_code = str(data.get("alarm_code", "-") or "-")
        position = self._operator_format_state_after_position(data)
        position_text = f"，位置={position}" if position else ""
        return (
            "最近一次执行后状态："
            f"结果={label}，反馈={final}，system_state={system_state}，"
            f"func_id_current={current_func}，ready={ready}，急停={estop}，暂停={pause}，"
            f"报警={alarm}，alarm_code={alarm_code}{position_text}。"
        )

    def _operator_format_state_after_position(self, data: dict[str, Any]) -> str:
        values = data.get("dpos_c")
        if not isinstance(values, (list, tuple)) or len(values) < 3:
            values = data.get("position")
            if isinstance(values, dict):
                values = [values.get("x"), values.get("y"), values.get("z")]
        if not isinstance(values, (list, tuple)) or len(values) < 3:
            return ""
        return (
            f"({self._operator_compact_number(values[0])}, "
            f"{self._operator_compact_number(values[1])}, "
            f"{self._operator_compact_number(values[2])})"
        )

    def _operator_current_flow_context_answer(self) -> str:
        flow_name = str(getattr(self, "current_flow_name", "") or "").strip()
        service = getattr(self, "service", None)
        if not flow_name and service is not None and hasattr(service, "list_flow_names"):
            names = [str(name) for name in service.list_flow_names()]
            if len(names) == 1:
                flow_name = names[0]
            elif names:
                flow_name = names[-1]
        if not flow_name or service is None or not hasattr(service, "get_flow"):
            return ""
        try:
            flow = service.get_flow(flow_name)
        except Exception:
            return ""
        steps = list(getattr(flow, "steps", ()) or ())
        lines = [f"当前流程 {flow_name}，共 {len(steps)} 步。"]
        for index, key in enumerate(steps[:8], start=1):
            record = getattr(self, "table", {}).get(key)
            if record is None:
                lines.append(f"{index}. {key}")
                continue
            params = self._operator_format_params_inline(getattr(record, "params", {}) or {})
            lines.append(f"{index}. {getattr(record, 'description', '') or key}（Func{record.func_num}，{params}）")
        if len(steps) > 8:
            lines.append(f"... 还有 {len(steps) - 8} 步未展开显示。")
        return "\n".join(lines)

    def _operator_deepseek_runtime_context(self) -> str:
        parts: list[str] = []
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if isinstance(draft, dict) and draft:
            parts.append(self._operator_flow_draft_preview_text(draft, include_params=True))
        dialogue_text = self._operator_recent_dialogue_context()
        if dialogue_text:
            parts.append(dialogue_text)
        flow_text = self._operator_current_flow_context_answer()
        if flow_text:
            parts.append(flow_text)
        last_result = self._operator_last_execution_result_text()
        if last_result and "还没有可报告" not in last_result:
            parts.append(last_result)
        last_state = self._operator_last_execution_state_after_text()
        if last_state:
            parts.append(last_state)
        try:
            registry = self._position_registry() if hasattr(self, "_position_registry") else None
            entries = list(registry.list_all()) if registry is not None and hasattr(registry, "list_all") else []
        except Exception:
            entries = []
        if entries:
            position_lines = []
            for entry in entries[:8]:
                x, y, z, rx, ry, rz = getattr(entry, "pose", (0, 0, 0, 0, 0, 0))
                position_lines.append(
                    f"{entry.name}=({self._operator_compact_number(x)}, {self._operator_compact_number(y)}, "
                    f"{self._operator_compact_number(z)}, {self._operator_compact_number(rx)}, "
                    f"{self._operator_compact_number(ry)}, {self._operator_compact_number(rz)})"
                )
            parts.append("位置库：" + "；".join(position_lines))
        return "\n".join(part for part in parts if part).strip()

    def _operator_recent_dialogue_context(self, *, limit: int = 6, max_chars: int = 160) -> str:
        messages = getattr(self, "_operator_chat_messages", None)
        if not isinstance(messages, list) or not messages:
            return ""
        lines: list[str] = []
        for item in messages[-max(1, limit):]:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            role = str(item[0] or "").strip().lower()
            text = re.sub(r"\s+", " ", str(item[1] or "")).strip()
            if not text:
                continue
            label = "用户" if role == "user" else "AI" if role == "assistant" else role or "消息"
            if len(text) > max_chars:
                text = text[:max_chars].rstrip() + "..."
            lines.append(f"{label}：{text}")
        if not lines:
            return ""
        return "最近对话：\n" + "\n".join(lines)

    @staticmethod
    def _operator_flow_draft_preview_text(draft: dict[str, Any], *, include_params: bool = False) -> str:
        flow_name = str(draft.get("flow_name") or draft.get("flowName") or "未命名流程").strip() or "未命名流程"
        steps = draft.get("expanded_steps")
        step_items = steps if isinstance(steps, list) else []
        positions = draft.get("positions")
        position_items = positions if isinstance(positions, list) else []
        lines = [
            f"当前待确认流程草案：{flow_name}，共 {len(step_items)} 步，尚未保存/执行。",
        ]
        if draft.get("needs_precheck"):
            lines.append("状态：草案已修改，保存或执行前需要重新预检。")
        if position_items:
            pose_lines = []
            for item in position_items[:4]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                pose = item.get("pose")
                if name and isinstance(pose, list):
                    pose_text = ", ".join(OperatorUiMixin._operator_compact_number(value) for value in pose[:6])
                    pose_lines.append(f"{name}=({pose_text})")
            if pose_lines:
                lines.append("位置：" + "；".join(pose_lines))
        if step_items:
            lines.append("步骤：")
            for index, step in enumerate(step_items[:8], start=1):
                if not isinstance(step, dict):
                    lines.append(f"{index}. 非结构化步骤")
                    continue
                description = str(step.get("description") or step.get("action") or "").strip()
                func_id = step.get("func_id") or step.get("func_num")
                position_name = str(step.get("position_name") or "").strip()
                suffix_parts = []
                if func_id:
                    suffix_parts.append(f"Func{int(float(func_id))}")
                if position_name:
                    suffix_parts.append(f"位置 {position_name}")
                suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
                lines.append(f"{index}. {description or '执行动作'}{suffix}")
                params = step.get("params")
                if include_params and isinstance(params, dict) and params:
                    lines.append(f"   参数：{OperatorUiMixin._operator_format_params_inline(params)}")
            if len(step_items) > 8:
                lines.append(f"... 还有 {len(step_items) - 8} 步未展开显示。")
        lines.append("可说“确认保存”保存草案，或说“保存并执行”。")
        return "\n".join(lines)

    @staticmethod
    def _operator_compact_number(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.3f}".rstrip("0").rstrip(".")

    @staticmethod
    def _operator_format_params_inline(params: dict[str, Any]) -> str:
        parts = []
        for key in sorted(params):
            value = params[key]
            if isinstance(value, (int, float, str)):
                value_text = OperatorUiMixin._operator_compact_number(value)
            else:
                value_text = str(value)
            parts.append(f"{key}={value_text}")
        return "，".join(parts) if parts else "-"

    def _operator_save_flow_draft(self, draft: dict[str, Any]) -> tuple[bool, str, str]:
        flow_name = str(draft.get("flow_name") or draft.get("flowName") or "").strip()
        if not flow_name:
            return False, "流程草案缺少流程名称。", ""
        steps = draft.get("expanded_steps")
        if not isinstance(steps, list) or not steps:
            return False, f"流程草案'{flow_name}'没有可保存的展开步骤。", flow_name
        actor = "engineer"
        if hasattr(self, "_current_permission_actor"):
            actor = self._current_permission_actor()
        elif hasattr(self, "_authenticated_role"):
            actor = str(getattr(self, "_authenticated_role", None) or "engineer")
        registry = self._position_registry() if hasattr(self, "_position_registry") else None
        if registry is not None:
            for item in draft.get("positions") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                pose = item.get("pose")
                if not name or not isinstance(pose, list) or len(pose) < 6:
                    continue
                try:
                    ok, message = registry.set_position(
                        name,
                        tuple(float(value) for value in pose[:6]),
                        spd=int(float(item.get("spd", 50) or 50)),
                        move_type=int(float(item.get("move_type", 0) or 0)),
                        created_by=actor,
                    )
                except PermissionDenied as exc:
                    return False, str(exc), flow_name
                if not ok:
                    return False, message, flow_name
        query_keys: list[str] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                return False, f"流程草案'{flow_name}'第{index}步格式非法。", flow_name
            try:
                func_id = int(float(step.get("func_id") or step.get("func_num") or 0))
            except (TypeError, ValueError):
                func_id = 0
            params = step.get("params")
            if func_id <= 0 or not isinstance(params, dict):
                return False, f"流程草案'{flow_name}'第{index}步缺少函数号或参数。", flow_name
            step_id = int(float(step.get("step_id") or index))
            query_key = self._operator_flow_draft_query_key(flow_name, step_id)
            record = QueryRecord(
                query_key=query_key,
                func_num=func_id,
                params=dict(params),
                keywords=f"{flow_name} 流程草案 第{step_id}步",
                description=str(step.get("description") or step.get("action") or f"{flow_name} 第{step_id}步"),
                safety_level=int(float(step.get("safety_level", 5) or 5)),
            )
            self.table[query_key] = record
            if hasattr(self, "service") and hasattr(self.service, "table"):
                self.service.table[query_key] = record
            query_keys.append(query_key)
        query_path = getattr(self, "json_path", None)
        if query_path is not None:
            save_query_table_json(query_path, self.table)
        flow = FlowDefinition(name=flow_name, steps=tuple(query_keys), step_delay_ms=int(draft.get("step_delay_ms", 300)))
        self.service.save_flow(flow)
        return True, f"流程'{flow_name}'已保存，包含{len(query_keys)}步。", flow_name

    def _operator_flow_draft_precheck_plan(self, draft: dict[str, Any]):
        from .voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan

        flow_name = str(draft.get("flow_name") or draft.get("flowName") or "草案").strip() or "草案"
        steps = draft.get("expanded_steps")
        if not isinstance(steps, list) or not steps:
            return None
        safe_name = re.sub(r"[^\w\u4e00-\u9fff]+", "_", flow_name).strip("_") or "flow"
        temp_flow_name = f"__draft_precheck_{safe_name}"
        query_keys: list[str] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                return None
            try:
                func_id = int(float(step.get("func_id") or step.get("func_num") or 0))
            except (TypeError, ValueError):
                return None
            params = step.get("params")
            if func_id <= 0 or not isinstance(params, dict):
                return None
            step_id = int(float(step.get("step_id") or index))
            query_key = f"{temp_flow_name}:{step_id:02d}"
            record = QueryRecord(
                query_key=query_key,
                func_num=func_id,
                params=dict(params),
                keywords=f"{flow_name} 临时预检 第{step_id}步",
                description=str(step.get("description") or step.get("action") or f"{flow_name} 第{step_id}步"),
                safety_level=int(float(step.get("safety_level", 5) or 5)),
            )
            self.table[query_key] = record
            if hasattr(self, "service") and hasattr(self.service, "table"):
                self.service.table[query_key] = record
            query_keys.append(query_key)
        flow = FlowDefinition(name=temp_flow_name, steps=tuple(query_keys), step_delay_ms=int(draft.get("step_delay_ms", 300)))
        if hasattr(self, "service") and hasattr(self.service, "flows"):
            self.service.flows[temp_flow_name] = flow
        return VoiceNlpPlan(
            actions=(VoiceNlpAction("flow", temp_flow_name, "flow_draft", flow_name, "流程草案临时预检"),),
            source="flow_draft",
            raw_text=flow_name,
            reason="流程草案临时预检",
            semantic_level=3,
            semantic_label="流程草案编排层",
            requires_precheck=True,
            requires_confirmation=True,
        )

    @staticmethod
    def _operator_flow_draft_query_key(flow_name: str, step_id: int) -> str:
        safe_name = re.sub(r"[^\w\u4e00-\u9fff]+", "_", flow_name or "").strip("_") or "flow"
        return f"flowdraft:{safe_name}:{int(step_id):02d}"

    def _operator_handle_pending_confirm_modify(self, text: str) -> bool:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if plan is None:
            return False
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        speed_match = re.search(r"(?:速度|速率|spd)(?:改成|改为|设为|设置为|到)?(\d+(?:\.\d+)?)%?", compact, re.IGNORECASE)
        if speed_match:
            speed = max(5.0, min(150.0, float(speed_match.group(1))))
            changed = self._operator_update_pending_confirm_records(
                plan,
                {"spd_pct": speed, "acc_pct": speed, "dec_pct": speed},
            )
            if not changed:
                return False
            text_out = f"已将待确认计划速度调整为{speed:g}%，加速度和减速度同步为{speed:g}%。请再次确认执行。"
            self._operator_prepare_plan_prechecks(plan)
            self.status_label.setText(text_out)
            self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "确认阶段修改参数", "成功", text_out)
            self._refresh_operator_view()
            return True
        step_match = re.search(r"(?:步长|距离|幅度)(?:改成|改为|设为|设置为|到)?(\d+(?:\.\d+)?)(?:毫米|mm|度|°)?", compact, re.IGNORECASE)
        if step_match:
            step = max(0.1, float(step_match.group(1)))
            changed = self._operator_update_pending_confirm_records(plan, {"pos_val": step})
            if not changed:
                return False
            text_out = f"已将待确认计划步长调整为{step:g}。请再次确认执行。"
            self._operator_prepare_plan_prechecks(plan)
            self.status_label.setText(text_out)
            self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "确认阶段修改参数", "成功", text_out)
            self._refresh_operator_view()
            return True
        return False

    @staticmethod
    def _operator_update_pending_confirm_records(plan, updates: dict[str, float]) -> bool:
        records = getattr(plan, "atomic_records", {}) or {}
        changed = False
        for record in records.values():
            params = getattr(record, "params", None)
            if not isinstance(params, dict):
                continue
            for key, value in updates.items():
                if key in params:
                    params[key] = float(value)
                    changed = True
        return changed

    def _operator_handle_button_voice_command(self, text: str) -> bool:
        return self._operator_handle_voice_command_spec(text)

    def _operator_handle_voice_command_spec(self, text: str) -> bool:
        spec = match_operator_voice_command(text)
        if spec is None:
            return False
        return self._operator_execute_voice_command_spec(spec)

    def _operator_handle_engineer_voice_command_spec(self, text: str) -> bool:
        spec = match_engineer_voice_command(text)
        if spec is None:
            return False
        return self._operator_execute_engineer_voice_command_spec(spec, raw_text=text)

    def _operator_handle_pending_engineer_voice_command(self, text: str) -> bool:
        spec = getattr(self, "_operator_pending_engineer_voice_spec", None)
        if spec is None:
            return False
        compact = re.sub(r"\s+", "", text or "")
        if compact in {"确认工程师操作", "确认后台操作", "确认执行", "执行确认"}:
            return self._operator_confirm_engineer_voice_command()
        if compact in {"取消工程师操作", "取消后台操作", "取消执行", "取消计划"}:
            return self._operator_cancel_engineer_voice_command()
        return False

    def _operator_handle_engineer_confirm_query(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        keywords = (
            "待确认后台操作",
            "待确认工程师操作",
            "当前待确认",
            "工程师操作等我确认",
            "后台操作等我确认",
        )
        if not any(keyword in compact for keyword in keywords):
            return False
        spec = getattr(self, "_operator_pending_engineer_voice_spec", None)
        if spec is None:
            answer = "当前没有待确认的工程师语音操作。"
        else:
            answer = f"当前待确认的工程师语音操作是：{spec.button_label}。请说“确认工程师操作”继续，或说“取消工程师操作”。"
        message = ResponseMessage(
            kind="result",
            text=answer,
            priority="normal",
            context_id="engineer_confirm:query",
        )
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer))
        if hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", "待确认查询", "成功", answer)
        return True

    def _operator_handle_atomic_capability_query(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        keywords = (
            "支持哪些原子命令",
            "支持哪些二次原子",
            "二次原子函数能力",
            "原子命令能力",
            "原子函数清单",
        )
        if not any(keyword in compact for keyword in keywords):
            return False
        answer = self._operator_atomic_capability_answer_text()
        message = ResponseMessage(
            kind="result",
            text=answer,
            priority="normal",
            context_id="atomic:capability_query",
        )
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer))
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "原子能力查询", "成功", answer)
        return True

    @staticmethod
    def _operator_atomic_capability_answer_text() -> str:
        summary = atomic_capability_summary()
        rows = atomic_capability_rows()
        implemented_names = [str(row["name"]) for row in rows if row["status"] in {"implemented", "basic"}]
        guarded_names = [str(row["name"]) for row in rows if row["status"] == "guarded"]
        deferred_names = [str(row["name"]) for row in rows if row["status"] == "deferred"]
        return (
            "二次原子函数能力如下：\n"
            f"已实现/基础实现 {summary['implemented'] + summary['basic']} 项，例如 "
            f"{'、'.join(implemented_names[:8])}。\n"
            f"保护性拒绝 {summary['guarded']} 项：{'、'.join(guarded_names) or '-'}。\n"
            f"延期 {summary['deferred']} 项：{'、'.join(deferred_names) or '-'}。"
        )

    def _operator_handle_engineer_voice_capability_query(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        keywords = (
            "语音能控制哪些工程师操作",
            "哪些工程师操作能语音",
            "哪些不能语音执行",
            "工程师语音能力",
            "后台语音能力",
        )
        if not any(keyword in compact for keyword in keywords):
            return False
        summary = engineer_voice_capability_summary(limit_per_group=4)
        lines = ["工程师页语音能力如下："]
        for policy in ("direct", "confirm", "rejected", "listed_only"):
            item = summary[policy]
            examples = "、".join(str(value) for value in item["examples"]) or "-"
            lines.append(f"{EXECUTION_POLICY_LABELS[policy]}：{item['count']}项，例如 {examples}。")
        answer = "\n".join(lines)
        message = ResponseMessage(
            kind="result",
            text=answer,
            priority="normal",
            context_id="engineer_voice:capability_query",
        )
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer))
        if hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", "能力查询", "成功", answer)
        return True

    def _operator_queue_engineer_voice_command(self, spec: EngineerVoiceCommandSpec, *, raw_text: str) -> None:
        previous_spec = getattr(self, "_operator_pending_engineer_voice_spec", None)
        if previous_spec is not None and hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", previous_spec.button_label, "覆盖", f"被新操作覆盖: {spec.button_label}")
        self._operator_pending_engineer_voice_spec = spec
        self._operator_pending_engineer_voice_raw_text = raw_text
        self._operator_pending_engineer_voice_created_at_sec = self._operator_now_seconds()
        text = f"{spec.button_label} 等待确认。请说“确认工程师操作”继续，或说“取消工程师操作”。"
        self._operator_archive_engineer_voice_command(spec, raw_text=raw_text, result="waiting_confirmation", final_text=text)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(text))
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text=text,
                    priority="normal",
                    context_id=f"engineer_confirm:{spec.action}",
                )
            )
        if hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", spec.button_label, "等待确认", spec.action)

    def _operator_confirm_engineer_voice_command(self) -> bool:
        spec = getattr(self, "_operator_pending_engineer_voice_spec", None)
        if spec is None:
            return False
        if self._operator_engineer_voice_confirm_expired():
            self._operator_pending_engineer_voice_spec = None
            raw_text = str(getattr(self, "_operator_pending_engineer_voice_raw_text", "") or spec.button_label)
            self._operator_pending_engineer_voice_raw_text = ""
            self._operator_pending_engineer_voice_created_at_sec = None
            text = f"{spec.button_label} 确认已超时，已取消。请重新发起工程师操作。"
            self._operator_archive_engineer_voice_command(spec, raw_text=raw_text, result="timeout", final_text=text)
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(text))
            if hasattr(self, "_append_log"):
                self._append_log("工程师页语音指令", spec.button_label, "超时", text)
            return True
        self._operator_pending_engineer_voice_spec = None
        raw_text = str(getattr(self, "_operator_pending_engineer_voice_raw_text", "") or spec.button_label)
        self._operator_pending_engineer_voice_raw_text = ""
        self._operator_pending_engineer_voice_created_at_sec = None
        handled = self._operator_execute_confirmed_engineer_voice_command(spec)
        if handled:
            self._operator_archive_engineer_voice_command(
                spec,
                raw_text=raw_text,
                result="success",
                final_text=f"工程师语音操作已确认执行：{spec.button_label}。",
            )
        if handled and hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", spec.button_label, "成功", "已确认执行")
        return handled

    def _operator_cancel_engineer_voice_command(self) -> bool:
        spec = getattr(self, "_operator_pending_engineer_voice_spec", None)
        if spec is None:
            return False
        self._operator_pending_engineer_voice_spec = None
        raw_text = str(getattr(self, "_operator_pending_engineer_voice_raw_text", "") or spec.button_label)
        self._operator_pending_engineer_voice_raw_text = ""
        self._operator_pending_engineer_voice_created_at_sec = None
        if hasattr(self, "status_label"):
            self.status_label.setText("已取消工程师语音操作。")
        self._operator_archive_engineer_voice_command(spec, raw_text=raw_text, result="cancelled", final_text="已取消工程师语音操作。")
        if hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", spec.button_label, "取消", "已取消待确认操作")
        return True

    def _operator_execute_confirmed_engineer_voice_command(self, spec: EngineerVoiceCommandSpec) -> bool:
        action_map = {
            "save_system_config": "_save_system_config",
            "reload_system_config": "_reload_system_config",
            "save_template": "_save_record",
            "import_template": "_import_template_json",
            "delete_interp_point": "_delete_interp_point",
            "start_flow": "_start_flow",
            "step_flow": "_step_flow",
            "stop_flow": "_stop_flow",
            "reset_flow": "_reset_flow",
            "save_safe_point": "_save_safe_point",
            "save_avoidance_config": "_save_avoidance_config_only",
            "remove_flow_step": "_remove_flow_step",
            "save_flow": "_save_flow",
        }
        method_name = action_map.get(spec.action)
        if method_name is None:
            return False
        method = getattr(self, method_name, None)
        if method is None:
            return False
        method()
        return True

    def _operator_engineer_voice_confirm_expired(self) -> bool:
        created_at = getattr(self, "_operator_pending_engineer_voice_created_at_sec", None)
        if created_at is None:
            return False
        timeout_sec = float(getattr(getattr(self, "axis_ranges", None), "operator_confirm_timeout_sec", 60) or 60)
        return (self._operator_now_seconds() - created_at) > timeout_sec

    def _operator_engineer_voice_rejection_text(self, spec: EngineerVoiceCommandSpec) -> str:
        if spec.danger_level == "emergency":
            return f"{spec.button_label} 属于应急安全操作，请使用三段式应急编码或现场物理急停，当前未开放工程师页语音直接执行。"
        return f"{spec.button_label} 属于高风险后台操作，当前未开放语音直接执行，请使用手动操作和现场确认流程。"

    def _operator_execute_engineer_voice_command_spec(self, spec: EngineerVoiceCommandSpec, *, raw_text: str = "") -> bool:
        allowed_actions = {
            "show_run_page",
            "show_manage_page",
            "show_log_page",
            "show_json_preview",
            "show_system_params",
            "show_safe_points",
            "show_flow_manage",
            "read_feedback",
            "refresh_microphones",
            "refresh_logs",
        }
        if spec.action not in allowed_actions:
            if spec.danger_level == "confirm":
                self._operator_queue_engineer_voice_command(spec, raw_text=raw_text or spec.button_label)
                return True
            if spec.danger_level == "normal":
                if spec.action in {"parse_text", "execute_text", "record", "clear_text"}:
                    return False
                spoken_text = raw_text or spec.button_label
                text = f"{spoken_text} 已在工程师页语音清单中登记，但当前版本仅清单保留，尚未接入语音执行。"
                if hasattr(self, "_operator_publish_response"):
                    self._operator_publish_response(
                        ResponseMessage(
                            kind="result",
                            text=text,
                            priority="normal",
                            context_id=f"engineer_listed_only:{spec.action}",
                        )
                    )
                if hasattr(self, "status_label"):
                    self.status_label.setText(self._operator_footer_status_text(text))
                self._operator_archive_engineer_voice_command(spec, raw_text=raw_text or spec.button_label, result="unsupported", final_text=text)
                if hasattr(self, "_append_log"):
                    self._append_log("工程师页语音指令", spec.button_label, "未接入", text)
                return True
            text = self._operator_engineer_voice_rejection_text(spec)
            if hasattr(self, "_operator_publish_response"):
                self._operator_publish_response(
                    ResponseMessage(
                        kind="alert" if spec.danger_level == "emergency" else "result",
                        text=text,
                        priority="high" if spec.danger_level == "emergency" else "normal",
                        context_id=f"engineer_reject:{spec.action}",
                    )
                )
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(text))
            self._operator_archive_engineer_voice_command(spec, raw_text=raw_text or spec.button_label, result="blocked", final_text=text)
            if hasattr(self, "_append_log"):
                self._append_log("工程师页语音指令", spec.button_label, "拒绝", text)
            return True

        if spec.action == "show_run_page":
            self._operator_open_engineer_page(0, "运行页")
        elif spec.action == "show_manage_page":
            self._operator_open_engineer_page(1, "后台页")
        elif spec.action == "show_log_page":
            self._operator_open_engineer_page(2, "日志页")
        elif spec.action == "show_json_preview":
            self._operator_open_engineer_tab(0, "JSON预览")
        elif spec.action == "show_system_params":
            self._operator_open_engineer_tab(1, "系统参数")
        elif spec.action == "show_safe_points":
            self._operator_open_engineer_tab(2, "安全中间点")
        elif spec.action == "show_flow_manage":
            self._operator_open_engineer_tab(3, "流程管理")
        elif spec.action == "read_feedback":
            self._operator_open_engineer_page(0, "运行页")
            self._read_feedback()
        elif spec.action == "refresh_microphones":
            self._operator_open_engineer_page(0, "运行页")
            self._refresh_microphone_devices()
        elif spec.action == "refresh_logs":
            self._operator_open_engineer_page(2, "日志页")
            self._refresh_logs()
        else:
            return False

        if hasattr(self, "_append_log"):
            self._append_log("工程师页语音指令", spec.button_label, "成功", spec.button_label)
        self._operator_archive_engineer_voice_command(
            spec,
            raw_text=raw_text or spec.button_label,
            result="success",
            final_text=f"工程师语音操作已执行：{spec.button_label}。",
        )
        return True

    def _operator_open_engineer_page(self, index: int, label: str) -> None:
        self._set_workspace_mode("engineer")
        if hasattr(self, "_show_page"):
            self._show_page(index)
        if hasattr(self, "status_label"):
            self.status_label.setText(f"已打开工程师{label}。")

    def _operator_open_engineer_tab(self, index: int, label: str) -> None:
        self._operator_open_engineer_page(1, "后台页")
        tabs = getattr(self, "engineer_right_tabs", None)
        if tabs is not None:
            tabs.setCurrentIndex(index)
        if hasattr(self, "status_label"):
            self.status_label.setText(f"已打开工程师{label}。")

    def _operator_execute_voice_command_spec(self, spec: OperatorVoiceCommandSpec) -> bool:
        if spec.action == "parse_text":
            self._operator_parse_text()
        elif spec.action == "execute_text":
            self._operator_execute_text()
        elif spec.action == "clear_text":
            self._operator_clear_text()
        elif spec.action == "record":
            if getattr(self, "_operator_voice_route_active", False):
                return False
            self._operator_toggle_microphone_recording()
        elif spec.action == "tts_on":
            self._operator_set_tts_enabled(True)
        elif spec.action == "tts_off":
            self._operator_set_tts_enabled(False)
        elif spec.action == "confirm_execute":
            self._operator_confirm_execute()
        elif spec.action == "accept_suggestion":
            self._operator_accept_suggestion()
        elif spec.action == "cancel_confirm":
            self._operator_cancel_confirm()
        elif spec.action == "show_full_status":
            self._set_workspace_mode("operator")
            self._operator_show_full_status()
            self._operator_reply_ui_command_success("已显示完整状态看板。")
        elif spec.action == "go_home":
            self._set_workspace_mode("operator")
            self._operator_go_home()
            self._operator_reply_ui_command_success("已回到主界面。")
        elif spec.action == "enter_fullscreen":
            self._set_workspace_mode("operator")
            self._operator_show_fullscreen()
            self._operator_reply_ui_command_success("已进入全屏。")
        elif spec.action == "exit_fullscreen":
            self._set_workspace_mode("operator")
            self._operator_restore_normal_window()
            self._operator_reply_ui_command_success("已退出全屏。")
        elif spec.action == "compact_window":
            self._set_workspace_mode("operator")
            if not getattr(self, "_operator_compact", False):
                self._operator_toggle_compact()
            self._operator_reply_ui_command_success("已切换到小窗口。")
        elif spec.action == "pause":
            self._handle_system_action("sys_pause")
        elif spec.action == "resume":
            self._handle_system_action("sys_resume")
        elif spec.action == "stop_current":
            self._operator_stop_current()
        elif spec.action == "ack_alarm":
            self._operator_acknowledge_alarm()
        elif spec.action == "alarm_reset":
            self._handle_system_action("alarm_reset")
        else:
            return False
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "按钮语音指令", "成功", spec.button_label)
        return True

    def _operator_acknowledge_alarm(self) -> None:
        alarm_code = str(getattr(self, "alarm_code", "-") or "-")
        alarm_text = str(getattr(self, "alarm_text", "-") or "-")
        text = f"报警已确认，报警码 {alarm_code}，{alarm_text}。请按现场流程处理后再复位。"
        self._operator_publish_response(
            ResponseMessage(
                kind="alert",
                text=text,
                priority="high",
                context_id=f"alarm_ack:{alarm_code}",
            )
        )
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(text))
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "确认报警", "成功", f"{alarm_code} | {alarm_text}")

    def _operator_handle_progress_query(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        if not any(keyword in compact for keyword in ("进度", "到哪", "执行到哪", "预检到哪", "做到哪")):
            return False
        answer_text = self._operator_progress_query_text()
        message = ResponseMessage(
            kind="progress",
            text=answer_text,
            priority="normal",
            context_id="operator_progress:query",
        )
        self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer_text))
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "进度查询", "成功", text)
        return True

    def _operator_progress_query_text(self) -> str:
        scene = self._operator_desired_scene()
        if scene == "precheck":
            result = getattr(self, "_operator_last_precheck_result", None)
            if isinstance(result, dict):
                return f"预检已完成，L1状态 {result.get('status', 'unknown')}。"
            return "预检进行中，正在核验指令、设备状态和安全参数。"
        if scene == "confirm":
            l1 = getattr(self, "_operator_last_precheck_result", None)
            l2 = getattr(self, "_operator_last_motion_plan_result", None)
            l3 = getattr(self, "_operator_last_process_precheck_result", None)
            return (
                "预检已完成，等待安全确认。"
                f"L1={self._operator_result_status(l1)}，"
                f"L2={self._operator_result_status(l2)}，"
                f"L3={self._operator_result_status(l3)}。"
            )
        if scene == "execute":
            progress = self._operator_execution_progress()
            flow_progress = self._operator_flow_progress_text()
            task = self._operator_current_task_text()
            if progress is None:
                return f"当前正在执行：{task}。流程步骤 {flow_progress}，控制器仍在运行，暂时没有可靠百分比。"
            return f"当前执行进度约{progress}%，任务：{task}，流程步骤 {flow_progress}。"
        if scene == "alarm":
            return f"当前处于报警场景，报警码 {getattr(self, 'alarm_code', '-')}，请先处理报警。"
        if scene == "query":
            return "当前处于查询场景，已显示七类看板信息。"
        return "当前处于待机场景，没有正在执行的任务。"

    @staticmethod
    def _operator_result_status(result: object) -> str:
        return str(result.get("status", "未执行")) if isinstance(result, dict) else "未执行"

    def _operator_handle_dashboard_query(self, text: str) -> bool:
        answer = DashboardQueryService().answer(text, self._operator_dashboard_snapshot_dict())
        if answer is None:
            return False
        self._set_workspace_mode("operator")
        self._operator_scene_override = "query"
        message = ResponseMessage(
            kind="result",
            text=answer.text,
            priority=answer.priority,
            context_id=f"dashboard_query:{answer.board_key}",
        )
        self._operator_publish_response(message)
        self._operator_publish_ai_answer_for_speech(answer.text)
        self._operator_archive_execution_result(result="answered", final_text=answer.text)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(answer.text))
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "看板查询", "成功", f"{answer.board_key} | {text}")
        if hasattr(self, "_refresh_operator_view"):
            self._refresh_operator_view()
        return True

    def _operator_run_l1_precheck(self, plan) -> dict[str, Any]:
        if hasattr(self, "operator_dashboard_cache"):
            self.operator_dashboard_cache.update_from_source(self)
            snapshot = self.operator_dashboard_cache.to_dict()
        else:
            snapshot = {}
        service = SafetyPrecheckService(self.axis_ranges)
        result = service.run_l1(snapshot, self._operator_l1_plan_dict(plan))
        self._append_log(
            "安全预检",
            "L1预检",
            "成功" if result.get("status") == "pass" else "失败",
            self._operator_precheck_summary(result),
        )
        return result

    def _operator_l1_plan_dict(self, plan) -> dict[str, Any]:
        raw_text = str(getattr(plan, "raw_text", "") or "adhoc")
        plan_dict: dict[str, Any] = {"plan_id": raw_text}
        for action in tuple(getattr(plan, "actions", ()) or ()):
            if getattr(action, "action_type", "") not in {"template", "atomic_template"}:
                continue
            record = self._operator_record_for_action(plan, action)
            if record is None:
                continue
            pose = record.pose_tuple()
            if pose is not None:
                plan_dict["target"] = {"x": pose[0], "y": pose[1], "z": pose[2]}
            if getattr(record, "func_num", None) == 106:
                axis_no = record.int_param("axis_no")
                if 0 <= axis_no <= 5:
                    joints: list[float | None] = [None, None, None, None, None, None]
                    joints[axis_no] = record.float_param("pos_val")
                    plan_dict["target"] = {**plan_dict.get("target", {}), "joints": tuple(joints)}
            speed = {
                "spd_pct": record.spd_pct_value(),
                "acc_pct": record.acc_pct_value(),
                "dec_pct": record.dec_pct_value(),
            }
            plan_dict["speed"] = speed
            break
        return plan_dict

    def _operator_prepare_plan_prechecks(self, plan) -> None:
        self._operator_l3_progress_percent = 0
        self._operator_l3_progress_text = ""
        self._operator_publish_reassurance("正在进行安全预检", context_id="precheck:reassurance")
        self._operator_publish_precheck_progress("L1安全预检", 33, context_id="precheck:l1:progress")
        self._operator_last_precheck_result = self._operator_run_l1_precheck(plan)
        if isinstance(self._operator_last_precheck_result, dict) and self._operator_last_precheck_result.get("status") == "fail":
            self._operator_last_motion_plan_result = {
                "status": "skipped",
                "items": [],
                "suggestion": "L1安全预检未通过，已跳过L2运动预演和L3流程预演。",
            }
            self._operator_last_process_precheck_result = None
            self._operator_publish_precheck_progress("L1预检未通过，已停止预演", 100, context_id="precheck:complete")
            self._operator_archive_safety_check()
            return
        self._operator_publish_precheck_progress("L2运动预演", 66, context_id="precheck:l2:progress")
        self._operator_last_motion_plan_result = self._operator_run_l2_motion_plan(plan)
        self._operator_last_process_precheck_result = self._operator_run_l3_process_precheck(plan)
        self._operator_publish_precheck_progress("预检预演完成", 100, context_id="precheck:complete")
        self._operator_archive_safety_check()

    def _operator_publish_precheck_progress(self, stage: str, percent: int, *, context_id: str) -> None:
        builder = getattr(self, "operator_response_builder", None) or ResponseBuilder()
        self.operator_response_builder = builder
        self._operator_publish_response(builder.progress(context_id, stage=stage, percent=percent))

    def _operator_publish_reassurance(self, stage: str, *, context_id: str) -> None:
        builder = getattr(self, "operator_response_builder", None) or ResponseBuilder()
        self.operator_response_builder = builder
        device_status, communication_status = self._operator_reassurance_status_texts()
        self._operator_publish_response(
            builder.reassurance(
                stage,
                device_status=device_status,
                communication_status=communication_status,
                context_id=context_id,
            )
        )

    def _operator_publish_periodic_reassurance_if_needed(self) -> bool:
        if not self._operator_execution_or_pause_active():
            self._operator_last_periodic_reassurance_sec = 0.0
            return False
        now = self._operator_now_seconds()
        last = float(getattr(self, "_operator_last_periodic_reassurance_sec", 0.0) or 0.0)
        interval = self._operator_reassurance_interval_seconds()
        if last > 0 and now - last < interval:
            return False
        self._operator_last_periodic_reassurance_sec = now
        self._operator_publish_reassurance("当前任务仍在处理", context_id="operator:periodic_reassurance")
        return True

    def _operator_reassurance_interval_seconds(self) -> float:
        configured_ms = getattr(getattr(self, "axis_ranges", None), "operator_reassurance_interval_ms", None)
        if configured_ms is not None:
            try:
                return max(0.5, float(configured_ms) / 1000.0)
            except (TypeError, ValueError):
                pass
        return 2.0

    def _operator_reassurance_status_texts(self) -> tuple[str, str]:
        try:
            snapshot = self._operator_dashboard_snapshot_dict()
        except Exception:
            snapshot = {}
        boards = snapshot.get("boards", {}) if isinstance(snapshot, dict) else {}
        device = boards.get("device_status", {}) if isinstance(boards, dict) else {}
        communication = boards.get("communication_faults", {}) if isinstance(boards, dict) else {}
        has_alarm = bool(device.get("alarm") or device.get("estop"))
        is_paused = bool(device.get("pause"))
        if has_alarm:
            device_status = "异常"
        elif is_paused:
            device_status = "暂停"
        else:
            device_status = "正常"
        realtime_feedback = str(communication.get("realtime_feedback", "") or "")
        ecat_ok = communication.get("ecat_ok")
        if realtime_feedback == "stale":
            communication_status = "反馈过期"
        elif realtime_feedback == "offline" or ecat_ok is False:
            communication_status = "异常"
        else:
            communication_status = "正常"
        return device_status, communication_status

    def _operator_run_l3_process_precheck(self, plan) -> dict[str, Any] | None:
        for action in tuple(getattr(plan, "actions", ()) or ()):
            if getattr(action, "action_type", "") != "flow" or not getattr(action, "target", ""):
                continue
            service = getattr(self, "service", None)
            flow = getattr(service, "flows", {}).get(action.target)
            getter = getattr(service, "get_effective_flow", None)
            if flow is not None and callable(getter):
                flow = getter(action.target)
            if flow is None:
                return {
                    "status": "fail",
                    "flow_name": str(action.target),
                    "progress_percent": 0,
                    "items": [
                        {
                            "id": "missing_flow",
                            "level": "L3",
                            "label": "流程存在性",
                            "status": "fail",
                            "message": f"流程不存在: {action.target}。",
                        }
                    ],
                    "suggestion": "请先创建或选择有效流程。",
                }
            snapshot = self._operator_dashboard_snapshot_dict()
            axis_ranges = getattr(self, "axis_ranges", None)
            cumulative_error_limit = getattr(axis_ranges, "l3_cumulative_error_limit_mm", 0.0) if axis_ranges else 0.0
            result = ProcessPrecheckService(
                l1_runner=lambda source_snapshot, plan_dict: self._operator_l1_result_for_record_key(
                    str(plan_dict.get("plan_id", "")), source_snapshot
                ),
                l2_runner=self._operator_l2_result_for_record,
                progress_callback=self._operator_publish_l3_progress,
                min_step_delay_ms=getattr(axis_ranges, "l3_min_step_delay_ms", 0) if axis_ranges else 0,
                cumulative_error_limit_mm=float(cumulative_error_limit) if float(cumulative_error_limit or 0.0) > 0 else None,
            ).run_l3(flow=flow, table=getattr(self, "table", {}), snapshot=snapshot)
            if hasattr(self, "_append_log"):
                self._append_log(
                    "流程预演",
                    f"L3流程预演 {flow.name}",
                    "成功" if result.get("status") == "pass" else "失败",
                    self._operator_l3_summary(result),
                )
            return result
        return None

    def _operator_publish_l3_progress(self, event: dict[str, Any]) -> None:
        percent = int(event.get("percent", 0) or 0)
        current_step = int(event.get("current_step", 0) or 0)
        total_steps = int(event.get("total_steps", 0) or 0)
        step_key = str(event.get("step_key", "-") or "-")
        detail = str(event.get("message", "") or "").strip()
        if detail:
            if not detail.endswith(("。", "！", "？", ".", "!", "?")):
                detail += "。"
            text = f"流程预演进度 {percent}%，{detail}"
        else:
            text = f"流程预演进度 {percent}%，已完成第{current_step}/{total_steps}步：{step_key}。"
        self._operator_l3_progress_percent = percent
        self._operator_l3_progress_text = text
        if hasattr(self, "operator_precheck_title"):
            self.operator_precheck_title.setText("流程预演进行中")
        if hasattr(self, "operator_precheck_progress"):
            self.operator_precheck_progress.setRange(0, 100)
            self.operator_precheck_progress.setValue(percent)
            self.operator_precheck_progress.setFormat(f"L3预演 {percent}%")
        self._operator_publish_response(
            ResponseMessage(
                kind="progress",
                text=text,
                priority="normal",
                context_id=f"l3_progress:{event.get('flow_name', '-')}",
            )
        )

    def _operator_publish_l2_progress(self, event: dict[str, Any]) -> None:
        percent = max(0, min(100, int(event.get("percent", 0) or 0)))
        stage = str(event.get("stage", "-") or "-")
        detail = str(event.get("message", "") or stage).strip()
        if detail and not detail.endswith(("。", "！", "？", ".", "!", "?")):
            detail += "。"
        text = f"L2运动规划预演进度 {percent}%，{detail or '正在处理。'}"
        if hasattr(self, "operator_precheck_title"):
            self.operator_precheck_title.setText("运动规划预演进行中")
        if hasattr(self, "operator_precheck_progress"):
            self.operator_precheck_progress.setRange(0, 100)
            self.operator_precheck_progress.setValue(percent)
            self.operator_precheck_progress.setFormat(f"L2预演 {percent}%")
        self._operator_publish_response(
            ResponseMessage(
                kind="progress",
                text=text,
                priority="normal",
                context_id=f"l2_progress:{stage}",
            )
        )

    def _operator_dashboard_snapshot_dict(self) -> dict[str, Any]:
        if hasattr(self, "operator_dashboard_cache"):
            self._operator_refresh_dashboard_cache()
            snapshot = self.operator_dashboard_cache.to_dict()
        else:
            snapshot = {}
        forbidden_boxes = tuple(getattr(getattr(self, "axis_ranges", None), "l3_forbidden_boxes", ()) or ())
        if forbidden_boxes:
            snapshot.setdefault("workspace", {})["forbidden_boxes"] = [dict(box) for box in forbidden_boxes]
        return snapshot

    def _operator_refresh_dashboard_cache(self):
        if not hasattr(self, "operator_dashboard_cache"):
            self.operator_dashboard_cache = DashboardCache()
        snapshot = self.operator_dashboard_cache.update_from_source(self)
        self._operator_publish_dashboard_change_broadcasts(snapshot)
        return snapshot

    def _operator_refresh_alarm_monitor(self):
        if not hasattr(self, "operator_alarm_monitor"):
            self.operator_alarm_monitor = AlarmMonitor(interval_ms=self._operator_alarm_refresh_interval_ms())
        sample = self.operator_alarm_monitor.sample_from_source(self)
        self._operator_last_alarm_detection = sample.to_dict()
        return sample

    def _operator_publish_dashboard_change_broadcasts(self, snapshot) -> None:
        current = self._operator_dashboard_broadcast_state_from_snapshot(snapshot)
        previous = getattr(self, "_operator_dashboard_broadcast_state", None)
        self._operator_dashboard_broadcast_state = current
        if previous is None:
            return
        if current["alarm"] and (
            not previous.get("alarm") or current.get("alarm_code") != previous.get("alarm_code")
        ):
            alarm_text = getattr(snapshot, "safety", {}).get("alarm_text", "-")
            self._operator_publish_response(
                ResponseMessage(
                    kind="alert",
                    text=f"报警发生，报警码 {current['alarm_code']}，{alarm_text}。",
                    priority="high",
                    context_id=f"dashboard:alarm:{current['alarm_code']}",
                )
            )
        if previous.get("alarm") and not current["alarm"]:
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text="报警状态已解除，当前无报警。",
                    priority="normal",
                    context_id="dashboard:alarm:cleared",
                )
            )
        if current["estop"] and not previous.get("estop"):
            self._operator_publish_response(
                ResponseMessage(
                    kind="alert",
                    text="急停状态已触发，请确认现场安全。",
                    priority="high",
                    context_id="dashboard:estop:on",
                )
            )
        if previous.get("estop") and not current["estop"]:
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text="急停状态已解除，请确认现场安全后再继续。",
                    priority="normal",
                    context_id="dashboard:estop:off",
                )
            )
        if current["pause"] and not previous.get("pause"):
            self._operator_publish_response(
                ResponseMessage(
                    kind="alert",
                    text="系统已进入暂停状态。",
                    priority="normal",
                    context_id="dashboard:pause:on",
                )
            )
        if previous.get("pause") and not current["pause"]:
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text="系统已退出暂停状态。",
                    priority="normal",
                    context_id="dashboard:pause:off",
                )
            )
        if current.get("feedback_fresh") is False and previous.get("feedback_fresh", True):
            age = current.get("feedback_age_ms")
            age_text = f" {age}ms" if age not in (None, "", "-") else ""
            self._operator_publish_response(
                ResponseMessage(
                    kind="alert",
                    text=f"实时反馈数据已过期{age_text}，请确认控制器轮询和通讯状态。",
                    priority="high",
                    context_id="dashboard:feedback:stale",
                )
            )
        if (
            not current["ecat_ok"]
            and previous.get("ecat_ok")
            and current.get("realtime_feedback") != "stale"
        ):
            self._operator_publish_response(
                ResponseMessage(
                    kind="alert",
                    text="通讯状态异常，实时反馈已离线。",
                    priority="high",
                    context_id="dashboard:comm:offline",
                )
            )
        if current["ecat_ok"] and previous.get("ecat_ok") is False:
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text="通讯状态已恢复，实时反馈在线。",
                    priority="normal",
                    context_id="dashboard:comm:online",
                )
            )
        if current.get("axis_status_abnormal") and (
            not previous.get("axis_status_abnormal")
            or current.get("axis_status") != previous.get("axis_status")
        ):
            self._operator_publish_response(
                ResponseMessage(
                    kind="alert",
                    text=f"轴状态异常，当前轴状态 {self._format_sequence(current.get('axis_status', ()))}。",
                    priority="high",
                    context_id="dashboard:axis_status:abnormal",
                )
            )
        if current["channel_idle"] and previous.get("channel_idle") is False:
            position = self._format_sequence(current.get("dpos_c", ()))
            position_text = f"，当前位置 {position}" if position != "-" else ""
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text=(
                        "动作执行完成，控制器通道已空闲，"
                        f"函数 {previous.get('current_func') or current.get('current_func') or '-'}，"
                        f"结果 {current.get('result', '-')}{position_text}。"
                    ),
                    priority="normal",
                    context_id=f"dashboard:motion:completed:{previous.get('current_func') or '-'}",
                )
            )

    @staticmethod
    def _operator_dashboard_broadcast_state_from_snapshot(snapshot) -> dict[str, Any]:
        boards = getattr(snapshot, "boards", {}) or {}
        device_status = boards.get("device_status", {}) or {}
        action_feasibility = boards.get("action_feasibility", {}) or {}
        motion_limits = boards.get("motion_limits", {}) or {}
        communication = boards.get("communication_faults", {}) or {}
        axis_status = tuple(motion_limits.get("axis_status", ()) or ())
        return {
            "alarm": bool(device_status.get("alarm")),
            "estop": bool(device_status.get("estop")),
            "pause": bool(device_status.get("pause")),
            "ecat_ok": bool(communication.get("ecat_ok")),
            "realtime_feedback": str(communication.get("realtime_feedback", "unknown") or "unknown"),
            "feedback_fresh": bool(communication.get("feedback_fresh", communication.get("ecat_ok", True))),
            "feedback_age_ms": communication.get("feedback_age_ms"),
            "alarm_code": str(device_status.get("alarm_code", "-") or "-"),
            "channel_idle": bool(action_feasibility.get("channel_idle")),
            "current_func": str(action_feasibility.get("current_func", "-") or "-"),
            "result": str(action_feasibility.get("result", "-") or "-"),
            "motion_percent": str(motion_limits.get("motion_percent", "-") or "-"),
            "axis_status": axis_status,
            "axis_status_abnormal": any(str(value) not in {"0", "0.0", "-", ""} for value in axis_status),
            "dpos_c": tuple(device_status.get("dpos_c", ()) or ()),
        }

    def _operator_l1_result_for_record_key(self, record_key: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        record = getattr(self, "table", {}).get(record_key)
        if record is None:
            return {"status": "fail", "items": [{"message": f"模板不存在: {record_key}"}]}
        return self._operator_l1_result_for_record(record, snapshot)

    def _operator_l1_result_for_record(self, record, snapshot: dict[str, Any]) -> dict[str, Any]:
        service = SafetyPrecheckService(self.axis_ranges)
        plan = SimpleNamespace(
            raw_text=record.query_key,
            actions=(SimpleNamespace(action_type="template", target=record.query_key),),
        )
        return service.run_l1(snapshot, self._operator_l1_plan_dict(plan))

    def _operator_l2_result_for_record(self, record) -> dict[str, Any]:
        plan = SimpleNamespace(
            raw_text=record.query_key,
            actions=(SimpleNamespace(action_type="template", target=record.query_key),),
        )
        return self._operator_run_l2_motion_plan(plan)

    def _operator_l2_target_pose(self, plan) -> tuple[float, float, float, float, float, float] | None:
        for action in tuple(getattr(plan, "actions", ()) or ()):
            if getattr(action, "action_type", "") not in {"template", "atomic_template"}:
                continue
            record = self._operator_record_for_action(plan, action)
            if record is None:
                continue
            pose = record.pose_tuple()
            if pose is not None:
                return tuple(float(value) for value in pose)
        return None

    def _operator_current_pose_tuple(self) -> tuple[float, float, float, float, float, float]:
        rotation = [part.strip() for part in str(getattr(self, "robot_r", "0/0/0")).replace(",", "/").split("/")]
        values = [
            self._operator_float_or_zero(getattr(self, "robot_x", 0.0)),
            self._operator_float_or_zero(getattr(self, "robot_y", 0.0)),
            self._operator_float_or_zero(getattr(self, "robot_z", 0.0)),
            self._operator_float_or_zero(rotation[0] if len(rotation) > 0 else 0.0),
            self._operator_float_or_zero(rotation[1] if len(rotation) > 1 else 0.0),
            self._operator_float_or_zero(rotation[2] if len(rotation) > 2 else 0.0),
        ]
        return tuple(values)  # type: ignore[return-value]

    @staticmethod
    def _operator_float_or_zero(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _operator_apply_l1_suggestion_to_plan(self, plan):
        plan_dict = self._operator_l1_plan_dict(plan)
        suggestion = SafetySuggestionService(self.axis_ranges).suggest(plan_dict)
        if not suggestion.get("available"):
            return None
        adjusted = suggestion.get("adjusted_plan", {})
        actions = list(getattr(plan, "actions", ()) or ())
        if not actions:
            return None
        first = actions[0]
        if getattr(first, "action_type", "") != "template" or not getattr(first, "target", None):
            return None
        record = getattr(self, "table", {}).get(first.target)
        if record is None:
            return None
        params = dict(record.params)
        target = adjusted.get("target", {})
        if isinstance(target, dict):
            if "x" in target:
                params["target_x"] = float(target["x"])
            if "y" in target:
                params["target_y"] = float(target["y"])
            if "z" in target:
                params["target_z"] = float(target["z"])
            if getattr(record, "func_num", None) == 106 and "joints" in target:
                axis_no = record.int_param("axis_no")
                joint_value = self._operator_target_joint_value(target.get("joints"), axis_no)
                if joint_value is not None:
                    params["pos_val"] = joint_value
        speed = adjusted.get("speed", {})
        if isinstance(speed, dict):
            for key in ("spd_pct", "acc_pct", "dec_pct"):
                if key in speed:
                    params[key] = float(speed[key])
        suggested_key = f"__operator_suggested_{record.query_key}"
        self.table[suggested_key] = type(record)(
            query_key=suggested_key,
            func_num=record.func_num,
            params=params,
            keywords=record.keywords,
            description=f"{record.description or record.query_key}（采纳安全建议）",
            safety_level=record.safety_level,
        )
        actions[0] = replace(first, target=suggested_key, reason="采纳 L1 安全建议")
        return replace(
            plan,
            actions=tuple(actions),
            reason=f"{getattr(plan, 'reason', '')}；采纳安全建议：" + "；".join(suggestion.get("messages", [])),
        )

    def _operator_apply_l2_avoidance_suggestion_to_plan(self, plan):
        if not self._operator_l2_should_block(getattr(self, "_operator_last_motion_plan_result", None)):
            return None
        actions = list(getattr(plan, "actions", ()) or ())
        if len(actions) != 1:
            return None
        first = actions[0]
        if getattr(first, "action_type", "") != "template" or not getattr(first, "target", None):
            return None
        record = getattr(self, "table", {}).get(first.target)
        if record is None or getattr(record, "func_num", None) != 108:
            return None
        safe_point = self._operator_active_safe_point_for_suggestion()
        if safe_point is None:
            return None
        safe_key = f"__operator_safe_{safe_point.name}_{record.query_key}"
        self.table[safe_key] = self._operator_safe_point_record_for_suggestion(safe_point, safe_key, record)
        flow_name = f"__operator_avoid_{record.query_key}"
        flow = FlowDefinition(name=flow_name, steps=(safe_key, record.query_key), step_delay_ms=1000)
        service = getattr(self, "service", None)
        if service is None or not hasattr(service, "flows"):
            return None
        service.flows[flow_name] = flow
        self.current_flow_name = flow_name
        if hasattr(self, "flow_combo"):
            try:
                if self.flow_combo.findText(flow_name) < 0:
                    self.flow_combo.addItem(flow_name)
                self.flow_combo.setCurrentText(flow_name)
            except Exception:
                pass
        actions[0] = replace(first, action_type="flow", target=flow_name, reason=f"采纳 L2 中间点建议: {safe_point.name}")
        return replace(
            plan,
            actions=tuple(actions),
            reason=f"{getattr(plan, 'reason', '')}；采纳中间点建议：先经过 {safe_point.name}，再执行 {record.query_key}",
        )

    def _operator_active_safe_point_for_suggestion(self):
        config = getattr(self, "avoidance_config", None)
        safe_points = getattr(config, "safe_points", {}) if config is not None else {}
        if not safe_points:
            return None
        key = getattr(self, "current_safe_point_key", None)
        if key and key in safe_points:
            return safe_points[key]
        return safe_points[sorted(safe_points)[0]]

    @staticmethod
    def _operator_safe_point_record_for_suggestion(safe_point, query_key: str, target_record: QueryRecord) -> QueryRecord:
        return QueryRecord(
            query_key=query_key,
            func_num=108,
            params={
                "target_x": float(safe_point.x),
                "target_y": float(safe_point.y),
                "target_z": float(safe_point.z),
                "target_rx": float(safe_point.rx),
                "target_ry": float(safe_point.ry),
                "target_rz": float(safe_point.rz),
                "spd_pct": float(safe_point.speed_percent),
                "acc_pct": float(safe_point.acc_percent),
                "dec_pct": float(safe_point.acc_percent),
                "stop_cmd": 0,
                "fuzzy_pos": 0,
                "fuzzy_spd": 0,
                "fuzzy_acc": 0,
                "fuzzy_dec": 0,
                "move_type": 0,
            },
            keywords=str(safe_point.name),
            description=f"采纳建议中间点：{safe_point.name}",
            safety_level=getattr(target_record, "safety_level", 5),
        )

    @staticmethod
    def _operator_target_joint_value(joints: object, axis_no: int) -> float | None:
        value: object = None
        if isinstance(joints, (list, tuple)) and 0 <= axis_no < len(joints):
            value = joints[axis_no]
        elif isinstance(joints, dict):
            for key in (axis_no, str(axis_no), f"j{axis_no + 1}", f"J{axis_no + 1}"):
                if key in joints:
                    value = joints[key]
                    break
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _operator_precheck_summary(self, result: dict[str, Any] | None) -> str:
        if not isinstance(result, dict):
            return "L1预检尚未执行。"
        failed = [item for item in result.get("items", []) if item.get("status") != "pass"]
        if not failed:
            return "L1预检通过。"
        lines = ["L1预检未通过："]
        for item in failed[:5]:
            label = str(item.get("label", "-"))
            message = str(item.get("message", "-"))
            lines.append(f"- {label}：{message}")
        return "\n".join(lines)

    def _operator_precheck_check_texts(self, result: dict[str, Any] | None) -> list[str]:
        if not isinstance(result, dict):
            return ["指令接收: 等待", "设备状态检查: 等待", "安全参数检查: 等待", "运动规划预演: 等待"]
        items = result.get("items", [])
        status_by_id = {str(item.get("id", "")): item.get("status") for item in items}
        device_ids = {"estop", "alarm", "paused", "controller", "realtime_feedback", "channel_idle"}
        safety_ids = {
            "current_r_range",
            "current_z_range",
            "target_x_range",
            "target_y_range",
            "target_z_range",
            "speed_pct",
            "acc_pct",
            "dec_pct",
        }
        device_ok = all(status_by_id.get(item_id) == "pass" for item_id in device_ids if item_id in status_by_id)
        safety_ok = all(status_by_id.get(item_id) == "pass" for item_id in safety_ids if item_id in status_by_id)
        for item in items:
            if item.get("status") == "pass":
                continue
            label = str(item.get("label", ""))
            if any(keyword in label for keyword in ("目标", "软限位", "速度", "加速度", "减速度", "当前 R", "当前 Z")):
                safety_ok = False
            else:
                device_ok = False
        l2_result = getattr(self, "_operator_last_motion_plan_result", None)
        l2_status = "未接入"
        if isinstance(l2_result, dict):
            status = l2_result.get("status")
            if status == "pass":
                l2_status = "通过"
            elif status == "fail":
                l2_status = "未通过"
            elif status == "unavailable":
                l2_status = "不可用"
            elif status == "skipped":
                l2_status = "已跳过"
        return [
            "指令接收: 已收到",
            f"设备状态检查: {'通过' if device_ok else '未通过'}",
            f"安全参数检查: {'通过' if safety_ok else '未通过'}",
            f"运动规划预演: {l2_status}",
        ]

    def _operator_run_l2_motion_plan(self, plan) -> dict[str, Any]:
        pose = self._operator_l2_target_pose(plan) if plan is not None else None
        if pose is None:
            return {
                "status": "unavailable",
                "selected_fstatus": None,
                "joints": (),
                "items": [],
                "suggestion": "未找到可用于 L2 预演的目标位姿。",
            }
        engine = getattr(self, "operator_kinematics_engine", None)
        result = MotionPlanService(
            engine=engine,
            progress_callback=self._operator_publish_l2_progress,
        ).plan(target_pose=pose, start_pose=self._operator_current_pose_tuple())
        self._operator_last_motion_plan_result = result
        if hasattr(self, "_append_log"):
            self._append_log(
                "运动预演",
                "L2运动规划",
                "成功" if result.get("status") == "pass" else "提示" if result.get("status") == "unavailable" else "失败",
                self._operator_l2_summary(result),
            )
        return result

    @staticmethod
    def _operator_l2_should_block(result: dict[str, Any] | None) -> bool:
        return isinstance(result, dict) and result.get("status") == "fail"

    @staticmethod
    def _operator_l2_summary(result: dict[str, Any] | None) -> str:
        if not isinstance(result, dict):
            return "L2运动规划预演尚未执行。"
        if result.get("status") == "pass":
            return f"L2运动规划预演通过，FSTATUS={result.get('selected_fstatus')}。"
        if result.get("status") == "unavailable":
            return str(result.get("suggestion") or "L2运动规划预演不可用。")
        if result.get("need_midpoint") and result.get("midpoint_pose") is not None:
            return f"{result.get('suggestion') or 'L2运动规划需要中点绕行。'} 建议中点={result.get('midpoint_pose')}。"
        failed = [item for item in result.get("items", []) if item.get("status") != "pass"]
        if not failed:
            return str(result.get("suggestion") or "L2运动规划预演未通过。")
        lines = ["L2运动规划预演未通过："]
        for item in failed[:5]:
            lines.append(f"- {item.get('label', '-')}：{item.get('message', '-')}")
        return "\n".join(lines)

    @staticmethod
    def _operator_l3_should_block(result: dict[str, Any] | None) -> bool:
        return isinstance(result, dict) and result.get("status") == "fail"

    @staticmethod
    def _operator_l3_summary(result: dict[str, Any] | None) -> str:
        if not isinstance(result, dict):
            return "L3流程预演尚未执行。"
        if result.get("status") == "pass":
            return f"L3流程预演通过，流程={result.get('flow_name', '-')}。"
        failed = [item for item in result.get("items", []) if item.get("status") == "fail"]
        if not failed:
            return str(result.get("suggestion") or "L3流程预演未通过。")
        lines = ["L3流程预演未通过："]
        for item in failed[:5]:
            lines.append(f"- {item.get('label', '-')}：{item.get('message', '-')}")
        midpoint_suggestions = result.get("midpoint_suggestions") or ()
        if midpoint_suggestions:
            lines.append("流程级中点建议：")
            for item in midpoint_suggestions[:3]:
                lines.append(
                    f"- 第{item.get('step_index', '-')}步 {item.get('step_key', '-')} "
                    f"建议中点={item.get('midpoint_pose', '-')}。"
                )
        return "\n".join(lines)

    def _operator_handle_emergency_text(self, text: str) -> bool:
        started_at_sec = self._operator_now_seconds()
        channel = getattr(self, "operator_emergency_channel", None)
        if channel is None:
            channel = EmergencyChannel()
            self.operator_emergency_channel = channel
        decision = channel.evaluate(text)
        if not decision.matched:
            return False

        delay_ms = self._operator_elapsed_ms_since(started_at_sec)
        self._operator_last_emergency_ack_delay_ms = delay_ms
        self._operator_last_emergency_ack_sla_passed = delay_ms <= self._operator_ack_limit_ms("emergency")
        self._operator_publish_response(
            ResponseMessage(
                kind="alert" if decision.authorized else "result",
                text=decision.message,
                priority="high",
                context_id=f"emergency:{decision.reason}",
            )
        )
        self.status_label.setText(decision.message)
        self._append_log(
            "应急",
            "应急编码校验",
            "成功" if decision.authorized else "拒绝",
            f"{decision.reason} | {text}",
        )
        if decision.authorized and decision.action_key:
            self._handle_system_action(decision.action_key)
        self._refresh_operator_view()
        return True

    def _operator_publish_response(self, message: ResponseMessage) -> BroadcastMessage | None:
        queue = getattr(self, "operator_broadcast_queue", None)
        if queue is None:
            queue = BroadcastQueue()
            self.operator_broadcast_queue = queue
        dedupe_key = self._operator_broadcast_dedupe_key(message)
        if dedupe_key:
            published = queue.publish_once(
                kind=message.kind,
                text=message.text,
                priority=message.priority,
                context_id=message.context_id,
                dedupe_key=dedupe_key,
                dedupe_window_seconds=self._operator_broadcast_dedupe_window_seconds(),
            )
        else:
            published = queue.publish(
                kind=message.kind,
                text=message.text,
                priority=message.priority,
                context_id=message.context_id,
            )
        if published is None:
            return None
        self._operator_last_broadcast_seq = published.seq
        if self._operator_should_show_broadcast_in_chat(published):
            self._operator_add_chat_message("assistant", published.text)
        if message.kind == "receipt":
            self._operator_archive_response_ack(published.text)
        return published

    def _operator_publish_ai_answer_for_speech(self, text: str) -> BroadcastMessage | None:
        spoken_text = str(text or "").strip()
        if not spoken_text:
            return None
        queue = getattr(self, "operator_broadcast_queue", None)
        if queue is None:
            queue = BroadcastQueue()
            self.operator_broadcast_queue = queue
        published = queue.publish_once(
            kind="result",
            text=spoken_text,
            priority="normal",
            context_id="chat:ai_answer",
            dedupe_key=f"chat:ai_answer:{spoken_text}",
            dedupe_window_seconds=self._operator_broadcast_dedupe_window_seconds(),
        )
        if published is not None:
            self._operator_last_broadcast_seq = published.seq
        return published

    @staticmethod
    def _operator_should_show_broadcast_in_chat(message: BroadcastMessage) -> bool:
        context = str(getattr(message, "context_id", "") or "")
        automatic_prefixes = (
            "connection:",
            "feedback:",
            "alarm_ack:",
            "dashboard:",
            "deepseek:",
            "operator_scene:",
            "system_state:",
            "motion_state:",
            "axis_status:",
        )
        automatic_contexts = {
            "operator:periodic_reassurance",
            "precheck:reassurance",
            "precheck:l1:progress",
            "precheck:l2:progress",
            "precheck:complete",
        }
        if context in automatic_contexts:
            return False
        return not context.startswith(automatic_prefixes)

    @staticmethod
    def _operator_initial_chat_messages() -> list[tuple[str, str]]:
        return []

    @staticmethod
    def _operator_broadcast_dedupe_key(message: ResponseMessage) -> str | None:
        if message.kind not in {"alert", "result"}:
            return None
        context = message.context_id or message.text
        return f"{message.kind}:{context}"

    def _operator_pending_broadcasts_for_delivery(self, last_seq: int) -> list[BroadcastMessage]:
        queue = getattr(self, "operator_broadcast_queue", None)
        if queue is None:
            return []
        return queue.messages_since_for_delivery(last_seq)

    def _operator_filter_broadcasts_for_speech(self, messages: list[BroadcastMessage]) -> list[BroadcastMessage]:
        if not getattr(self, "_authenticated_role", ""):
            return []
        return [
            message
            for message in messages
            if str(getattr(message, "context_id", "") or "") == "chat:ai_answer"
        ]

    def _operator_consume_pending_broadcasts_for_delivery(self) -> list[BroadcastMessage]:
        last_seq = int(getattr(self, "_operator_last_delivered_broadcast_seq", 0) or 0)
        pending = self._operator_pending_broadcasts_for_delivery(last_seq)
        if pending:
            self._operator_last_delivered_broadcast_seq = max(message.seq for message in pending)
        return pending

    def _operator_deliver_pending_broadcasts_to_speech(self) -> SpeechDeliveryResult:
        last_seq = int(getattr(self, "_operator_last_delivered_broadcast_seq", 0) or 0)
        self._operator_last_delivered_broadcast_seq = last_seq
        pending = self._operator_pending_broadcasts_for_delivery(last_seq)
        speech_pending = self._operator_filter_broadcasts_for_speech(pending)
        service = SpeechBroadcastDeliveryService(sink=getattr(self, "operator_speech_sink", None))
        result = service.deliver(speech_pending)
        if not pending:
            return result
        if result.success:
            self._operator_last_delivered_broadcast_seq = max(message.seq for message in pending)
            if hasattr(self, "_append_log"):
                skipped = len(pending) - len(speech_pending)
                detail = f"已播报 {len(speech_pending)} 条"
                if skipped:
                    reason = "未登录过滤" if not getattr(self, "_authenticated_role", "") else "非AI回答过滤"
                    detail = f"{detail}，{reason} {skipped} 条"
                self._append_log("语音播报", "主动播报", "成功", detail)
            return result
        if hasattr(self, "_append_log"):
            self._append_log("语音播报", "主动播报", "失败", result.error or "语音播报失败")
        return result

    def _operator_deliver_pending_broadcasts_to_speech_async(self) -> SpeechDeliveryResult:
        if bool(getattr(self, "_operator_speech_async_busy", False)):
            return SpeechDeliveryResult(success=True)
        last_seq = int(getattr(self, "_operator_last_delivered_broadcast_seq", 0) or 0)
        self._operator_last_delivered_broadcast_seq = last_seq
        pending = self._operator_pending_broadcasts_for_delivery(last_seq)
        speech_pending = self._operator_filter_broadcasts_for_speech(pending)
        if not pending:
            return SpeechDeliveryResult(success=True)
        max_seq = max(message.seq for message in pending)
        if not speech_pending:
            self._operator_last_delivered_broadcast_seq = max_seq
            if hasattr(self, "_append_log"):
                skipped = len(pending)
                reason = "未登录过滤" if not getattr(self, "_authenticated_role", "") else "非AI回答过滤"
                self._append_log("语音播报", "主动播报", "成功", f"已播报 0 条，{reason} {skipped} 条")
            return SpeechDeliveryResult(success=True)
        sink = getattr(self, "operator_speech_sink", None)
        if sink is None:
            return SpeechDeliveryResult(success=False, error="未配置语音播报输出接口。")
        service = SpeechBroadcastDeliveryService(sink=sink)
        executor = getattr(self, "_operator_speech_executor", None)
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operator-tts")
            self._operator_speech_executor = executor
        self._operator_speech_async_busy = True
        try:
            future = executor.submit(service.deliver, tuple(speech_pending))
        except Exception as exc:
            self._operator_speech_async_busy = False
            return SpeechDeliveryResult(success=False, error=str(exc))

        def _clear_busy(_future) -> None:
            self._operator_speech_async_busy = False

        future.add_done_callback(_clear_busy)
        self._operator_last_delivered_broadcast_seq = max_seq
        if hasattr(self, "_append_log"):
            skipped = len(pending) - len(speech_pending)
            detail = f"已提交后台播报 {len(speech_pending)} 条"
            if skipped:
                reason = "未登录过滤" if not getattr(self, "_authenticated_role", "") else "非AI回答过滤"
                detail = f"{detail}，{reason} {skipped} 条"
            self._append_log("语音播报", "主动播报", "成功", detail)
        return SpeechDeliveryResult(success=True, delivered_seq=tuple(int(message.seq) for message in speech_pending))

    @staticmethod
    def _operator_should_deliver_speech_async(sink: object) -> bool:
        return isinstance(sink, (Pyttsx3SpeechSink, WindowsSapiSpeechSink))

    def _operator_enable_local_tts(self, *, engine: object | None = None):
        if engine is None and WindowsSapiSpeechSink.available():
            sink = WindowsSapiSpeechSink()
        else:
            sink = Pyttsx3SpeechSink(engine=engine)
        self.operator_speech_sink = sink
        return sink

    def _operator_configure_tts_from_settings(self):
        if bool(getattr(getattr(self, "axis_ranges", None), "operator_tts_enabled", False)):
            return self._operator_enable_local_tts()
        self.operator_speech_sink = None
        return None

    def _operator_auto_deliver_broadcasts(self) -> SpeechDeliveryResult | None:
        if not bool(getattr(getattr(self, "axis_ranges", None), "operator_tts_enabled", False)):
            return None
        now = self._operator_now_seconds()
        retry_after = float(getattr(self, "_operator_tts_retry_after_sec", 0.0) or 0.0)
        if now < retry_after:
            return None
        sink = getattr(self, "operator_speech_sink", None)
        if self._operator_should_deliver_speech_async(sink):
            result = self._operator_deliver_pending_broadcasts_to_speech_async()
        else:
            result = self._operator_deliver_pending_broadcasts_to_speech()
        if result.success:
            self._operator_tts_failure_count = 0
            self._operator_tts_retry_after_sec = 0.0
            return result
        self._operator_tts_failure_count = int(getattr(self, "_operator_tts_failure_count", 0) or 0) + 1
        delay = self._operator_tts_retry_delay_seconds()
        self._operator_tts_retry_after_sec = now + delay * (2 ** max(0, self._operator_tts_failure_count - 1))
        if self._operator_tts_failure_count >= self._operator_tts_max_failures():
            self.axis_ranges = replace(self.axis_ranges, operator_tts_enabled=False)
            self.operator_speech_sink = None
            if hasattr(self, "operator_tts_check"):
                self.operator_tts_check.setChecked(False)
            self._operator_notify_tts_auto_paused(result)
        return result

    def _operator_notify_tts_auto_paused(self, result: SpeechDeliveryResult) -> None:
        reason = (getattr(result, "error", "") or "语音播报输出接口不可用").strip()
        count = int(getattr(self, "_operator_tts_failure_count", 0) or 0)
        text = f"语音播报连续失败 {count} 次，已自动暂停。原因：{reason}。请检查扬声器或本地 TTS 配置后重新开启。"
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", text)
        if hasattr(self, "_append_log"):
            self._append_log("语音播报", "自动暂停", "失败", text)
        if hasattr(self, "_show_warning"):
            self._show_warning("语音播报已自动暂停", text)

    def _operator_broadcast_dedupe_window_seconds(self) -> float:
        return float(getattr(getattr(self, "axis_ranges", None), "broadcast_dedupe_window_sec", 5.0))

    def _operator_tts_retry_delay_seconds(self) -> float:
        return float(getattr(getattr(self, "axis_ranges", None), "tts_retry_delay_sec", 5.0))

    def _operator_tts_max_failures(self) -> int:
        return int(getattr(getattr(self, "axis_ranges", None), "tts_max_failures", 3))

    @staticmethod
    def _operator_now_seconds() -> float:
        return time.monotonic()

    def _operator_set_tts_enabled(self, enabled: bool) -> None:
        self.axis_ranges = replace(self.axis_ranges, operator_tts_enabled=bool(enabled))
        self._operator_tts_failure_count = 0
        self._operator_tts_retry_after_sec = 0.0
        self._operator_configure_tts_from_settings()
        path = getattr(self, "system_config_path", None)
        if path is not None:
            save_system_config(path, self.axis_ranges)
        if hasattr(self, "operator_tts_check"):
            self.operator_tts_check.setChecked(bool(enabled))
        text = "语音播报已启用。" if enabled else "语音播报已关闭。"
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "语音播报开关", "成功", text)

    def _operator_on_tts_toggled(self, checked: bool) -> None:
        self._operator_set_tts_enabled(bool(checked))

    @staticmethod
    def _operator_plan_is_executable(plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        return bool(actions) and all(getattr(action, "action_type", "unknown") in {"template", "atomic_template", "flow", "system", "memory"} for action in actions)

    @staticmethod
    def _operator_plan_requires_precheck(plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        if any(getattr(action, "action_type", "") in {"template", "atomic_template", "flow"} for action in actions):
            return True
        return bool(policy_for_plan(plan).requires_precheck)

    @staticmethod
    def _operator_plan_requires_confirmation(plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        if any(getattr(action, "action_type", "") in {"template", "flow"} for action in actions):
            return True
        return bool(policy_for_plan(plan).requires_confirmation)

    def _operator_record_for_action(self, plan, action):
        target = getattr(action, "target", None)
        if not target:
            return None
        if getattr(action, "action_type", "") == "atomic_template":
            record = getattr(plan, "atomic_records", {}).get(target)
            if record is not None:
                return record
        return getattr(self, "table", {}).get(target)

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
        self._operator_refresh_dashboard_cache()
        self._operator_clear_expired_pending_confirm_for_refresh()

        state_text, color, detail = self._compute_overall_state()
        self.operator_state_label.setText(f"● {state_text}")
        self.operator_state_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: 800;")
        alarm_active = self._operator_alarm_active()
        pause_active = bool(getattr(self, "pause_active", False)) or self.busy == "暂停" or self.run_state == "暂停"
        estop_active = bool(getattr(self, "estop_active", False)) or "急停" in f"{self.alarm_text} {self.run_state} {self.busy}"
        self._set_operator_badge(self.operator_estop_badge, "急停", "开" if estop_active else "关", estop_active)
        self._set_operator_badge(self.operator_pause_badge, "暂停", "开" if pause_active else "关", pause_active)
        self._set_operator_badge(self.operator_alarm_badge, "报警", "有" if alarm_active else "无", alarm_active)

        monitor_text = self.monitor_label.text() if hasattr(self, "monitor_label") else "未启动"
        comm_text = "正常" if monitor_text == "实时监控运行中" else monitor_text
        if hasattr(self, "operator_host_edit") and hasattr(self, "host_edit") and not self.operator_host_edit.hasFocus():
            self.operator_host_edit.setText(self.host_edit.text().strip())

        self.operator_current_label.setText(f"当前: {self._operator_current_task_text()}")
        self._refresh_operator_axis_labels()
        self._refresh_operator_scene_content(detail)
        self._refresh_operator_recent_events()
        self._refresh_operator_dialog_labels()
        self._refresh_operator_full_status()
        self._sync_operator_mic_button()
        self._operator_publish_periodic_reassurance_if_needed()

        scene = self._operator_desired_scene()
        self._operator_request_scene(scene, reason="operator_refresh")

    def _operator_request_scene(self, scene: str, reason: str = "operator_request_scene") -> None:
        try:
            self._operator_apply_scene(scene, reason=reason)
        except TypeError:
            self._operator_apply_scene(scene)

    def _operator_apply_scene(self, scene: str, reason: str = "operator_apply_scene") -> None:
        previous = getattr(self, "_operator_current_scene", None)
        if scene == "alarm" and previous not in {None, "alarm"}:
            self._operator_scene_before_alarm = previous
        if hasattr(self, "operator_scene_stack"):
            self.operator_scene_stack.setCurrentIndex(self._operator_scene_indexes.get(scene, 0))
        self._operator_current_scene = scene
        if previous is None:
            self._operator_scene_state = OperatorSceneState(
                current=scene,
                previous=None,
                reason="initial",
                changed_at=self._operator_now_seconds(),
            )
            return
        if previous == scene:
            return
        self._operator_scene_state = OperatorSceneState(
            current=scene,
            previous=previous,
            reason=reason,
            changed_at=self._operator_now_seconds(),
        )
        message_text = self._operator_scene_transition_text(scene)
        priority = "high" if scene == "alarm" else "normal"
        self._operator_publish_response(
            ResponseMessage(
                kind="alert" if scene == "alarm" else "progress",
                text=message_text,
                priority=priority,
                context_id=f"operator_scene:{scene}",
            )
        )
        if hasattr(self, "_append_log"):
            self._append_log(
                "用户页面",
                "场景切换",
                "成功",
                f"{previous} -> {scene}",
                extra=self._operator_scene_state_payload(),
            )

    def _operator_scene_state_payload(self) -> dict[str, Any]:
        state = getattr(self, "_operator_scene_state", OperatorSceneState())
        return {
            "current": state.current,
            "previous": state.previous,
            "reason": state.reason,
            "changed_at": state.changed_at,
        }

    @staticmethod
    def _operator_scene_transition_text(scene: str) -> str:
        return {
            "idle": "已回到待机场景，系统在线。",
            "precheck": "进入预检场景，正在核验指令和设备状态。",
            "execute": "进入执行场景，正在跟踪动作进度。",
            "confirm": "进入安全确认场景，请确认执行、采纳建议或取消。",
            "alarm": "进入报警场景，请查看报警信息并按现场流程处理。",
            "query": "进入查询场景，已显示看板信息。",
        }.get(scene, f"已切换到{scene}场景。")

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

    def _set_operator_badge(self, label: QLabel, title: str, value: str, active: bool) -> None:
        label.setText(f"{title}\n{value}")
        label.setProperty("active", "true" if active else "false")
        label.style().unpolish(label)
        label.style().polish(label)

    def _refresh_operator_axis_labels(self) -> None:
        joints = list(getattr(self, "robot_joints", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)))
        joints = joints[:6] + [0.0] * max(0, 6 - len(joints))
        for idx, label in enumerate(self.operator_joint_labels):
            label.setText(f"J{idx + 1}\n{self._operator_fmt(joints[idx])}")
        rotation = [part.strip() for part in str(self.robot_r).replace(",", "/").split("/")]
        rotation = rotation[:3] + ["-"] * max(0, 3 - len(rotation))
        pose_values = {
            "X": self.robot_x,
            "Y": self.robot_y,
            "Z": self.robot_z,
            "RX": rotation[0],
            "RY": rotation[1],
            "RZ": rotation[2],
        }
        for key, value in pose_values.items():
            if key in self.operator_pose_labels:
                self.operator_pose_labels[key].setText(f"{key}\n{value}")

    def _refresh_operator_scene_content(self, state_detail: str) -> None:
        if getattr(self, "nlp_parse_running", False):
            self.operator_precheck_title.setText("安全预检进行中")
            for label, text in zip(
                self.operator_precheck_checks,
                ["指令接收: 已收到", "设备状态检查: 进行中", "安全参数检查: 等待", "运动规划预演: 等待"],
            ):
                label.setText(text)
            self.operator_precheck_progress.setRange(0, 0)
        elif getattr(self, "_operator_last_precheck_result", None) is not None:
            result = getattr(self, "_operator_last_precheck_result", None)
            self.operator_precheck_title.setText("安全预检完成")
            for label, text in zip(self.operator_precheck_checks, self._operator_precheck_check_texts(result)):
                label.setText(text)
            self.operator_precheck_progress.setRange(0, 100)
            self.operator_precheck_progress.setValue(100 if result.get("status") == "pass" else 70)
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
        if hasattr(self, "operator_accept_suggestion_btn"):
            self.operator_accept_suggestion_btn.setEnabled(
                self._operator_accept_suggestion_available(getattr(self, "_operator_pending_confirm_plan", None))
            )

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
        parts = ["解析完成，等待确认执行:", "\n".join(rows), f"说明: {reason}"]
        timeout_text = self._operator_confirm_timeout_text()
        if timeout_text:
            parts.append(timeout_text)
        l2_decision = self._operator_confirm_l2_decision_text()
        if l2_decision:
            parts.append(l2_decision)
        atomic_risk_lines = self._operator_confirm_atomic_risk_lines(plan)
        if atomic_risk_lines:
            parts.append("原子风险:\n" + "\n".join(atomic_risk_lines))
        risk_lines = self._operator_confirm_risk_lines()
        if risk_lines:
            parts.append("风险项:\n" + "\n".join(risk_lines))
        suggestion_lines = self._operator_confirm_suggestion_lines(plan)
        if suggestion_lines:
            parts.append("可采纳建议:\n" + "\n".join(suggestion_lines))
        return "\n\n".join(part for part in parts if part)

    def _operator_confirm_timeout_text(self) -> str:
        deadline = float(getattr(self, "_operator_pending_confirm_deadline_sec", 0.0) or 0.0)
        if deadline <= 0:
            return ""
        remaining = max(0.0, deadline - self._operator_now_seconds())
        seconds = int(remaining) if remaining.is_integer() else int(remaining) + 1
        return f"确认有效期: 剩余 {seconds} 秒。"

    def _operator_confirm_atomic_risk_lines(self, plan) -> list[str]:
        lines: list[str] = []
        for action in tuple(getattr(plan, "actions", ()) or ()):
            if getattr(action, "action_type", "") != "atomic_template":
                continue
            record = self._operator_record_for_action(plan, action)
            params = dict(getattr(record, "params", {}) or {}) if record is not None else {}
            risk_level = str(params.get("atomic_risk_level") or "").strip()
            risk_reason = str(params.get("atomic_risk_reason") or "").strip()
            if not risk_level and not risk_reason:
                continue
            target = getattr(action, "target", "") or "-"
            if risk_level and risk_reason:
                lines.append(f"- {target}: {risk_level}，{risk_reason}")
            elif risk_level:
                lines.append(f"- {target}: {risk_level}")
            else:
                lines.append(f"- {target}: {risk_reason}")
            if len(lines) >= 6:
                break
        return lines

    def _operator_confirm_l2_decision_text(self) -> str:
        result = getattr(self, "_operator_last_motion_plan_result", None)
        if not isinstance(result, dict) or result.get("status") != "pass":
            return ""
        selected = result.get("selected_fstatus")
        if selected in (None, ""):
            return ""
        text = f"运动规划: L2通过，已选 FSTATUS={selected}。"
        rejected = result.get("rejected_fstatuses") or ()
        if rejected:
            text += " 已规避 FSTATUS: " + "、".join(str(item) for item in rejected) + "。"
        return text

    def _operator_confirm_risk_lines(self) -> list[str]:
        lines: list[str] = []
        for result in (
            getattr(self, "_operator_last_precheck_result", None),
            getattr(self, "_operator_last_motion_plan_result", None),
            getattr(self, "_operator_last_process_precheck_result", None),
        ):
            if not isinstance(result, dict) or result.get("status") != "fail":
                continue
            for item in result.get("items", []) or []:
                if not isinstance(item, dict) or item.get("status") == "pass":
                    continue
                label = str(item.get("label", "-"))
                message = str(item.get("message", "-"))
                lines.append(f"- {label}: {message}")
                if len(lines) >= 6:
                    return lines
        return lines

    def _operator_confirm_suggestion_lines(self, plan) -> list[str]:
        lines: list[str] = []
        axis_ranges = getattr(self, "axis_ranges", None)
        if axis_ranges is not None:
            try:
                suggestion = SafetySuggestionService(axis_ranges).suggest(self._operator_l1_plan_dict(plan))
            except Exception:
                suggestion = {}
            if suggestion.get("available"):
                lines.extend(f"- {message}" for message in suggestion.get("messages", [])[:6])
        l2_line = self._operator_l2_avoidance_suggestion_line(plan)
        if l2_line:
            lines.append(l2_line)
        return lines[:6]

    def _operator_l2_avoidance_suggestion_line(self, plan) -> str:
        if not self._operator_l2_should_block(getattr(self, "_operator_last_motion_plan_result", None)):
            return ""
        actions = list(getattr(plan, "actions", ()) or ())
        if len(actions) != 1:
            return ""
        first = actions[0]
        if getattr(first, "action_type", "") != "template" or not getattr(first, "target", None):
            return ""
        record = getattr(self, "table", {}).get(first.target)
        if record is None or getattr(record, "func_num", None) != 108:
            return ""
        safe_point = self._operator_active_safe_point_for_suggestion()
        if safe_point is None:
            return ""
        return f"- 增加安全中间点 {safe_point.name}，生成 {safe_point.name} -> {record.query_key} 临时流程"

    def _operator_accept_suggestion_available(self, plan) -> bool:
        return bool(self._operator_confirm_suggestion_lines(plan))

    def _operator_execution_progress(self) -> int | None:
        percent = self._operator_parse_percent(getattr(self, "motion_percent", ""))
        if percent is not None:
            return percent

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
            return 50

        if getattr(self, "nlp_sequence_running", False):
            total = len(getattr(self, "_nlp_pending_actions", []) or [])
            if total > 0:
                current = min(max(int(getattr(self, "_nlp_pending_index", 0)) + 1, 1), total)
                return int(round(current / total * 100))
            return 50

        if self.busy == "运行中" or self.run_state == "运行中" or getattr(self, "nlp_sequence_running", False):
            return 50
        return 0

    def _operator_execution_scene_active(self) -> bool:
        return bool(
            getattr(self, "nlp_sequence_running", False)
            or getattr(self, "flow_running", False)
            or self.busy in {"运行中", "暂停"}
            or self.run_state in {"运行中", "暂停"}
        )

    def _operator_restorable_scene_after_alarm(self, previous_scene: str | None) -> str | None:
        if not previous_scene:
            return None
        if previous_scene == "query":
            return "query"
        if previous_scene == "execute" and self._operator_execution_scene_active():
            return "execute"
        if previous_scene == "precheck" and getattr(self, "nlp_parse_running", False):
            return "precheck"
        if previous_scene == "confirm" and self._operator_plan_is_executable(
            getattr(self, "_operator_pending_confirm_plan", None)
        ):
            return "confirm"
        if previous_scene == "idle":
            return "idle"
        return None

    def _operator_desired_scene(self) -> str:
        if self._operator_alarm_active():
            return "alarm"
        previous_alarm_scene = None
        if getattr(self, "_operator_current_scene", None) == "alarm":
            previous_alarm_scene = getattr(self, "_operator_scene_before_alarm", None)
            self._operator_scene_before_alarm = None
        if self._operator_plan_is_executable(getattr(self, "_operator_pending_confirm_plan", None)):
            return "confirm"
        if getattr(self, "nlp_parse_running", False):
            return "precheck"
        if self._operator_execution_scene_active():
            return "execute"
        if previous_alarm_scene:
            restored_scene = self._operator_restorable_scene_after_alarm(previous_alarm_scene)
            if restored_scene:
                return restored_scene
        scene_override = getattr(self, "_operator_scene_override", None)
        if scene_override:
            return scene_override
        return "idle"

    def _refresh_operator_recent_events(self) -> None:
        if not hasattr(self, "operator_recent_browser"):
            return
        rows = []
        for entry in self._operator_recent_event_entries(limit=50):
            result = entry["result"]
            color = "#0f8a3b" if result == "成功" else "#b45309" if result == "警告" else "#b91c1c" if result == "失败" else "#334155"
            rows.append(
                "<div style='margin:0 0 8px 0;padding:8px 9px;border:1px solid #dbe4ee;background:#ffffff;'>"
                "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;'>"
                "<tr>"
                f"<td style='color:#0f172a;font-size:13px;font-weight:800;white-space:nowrap;'>{html.escape(entry['time'])}</td>"
                "<td align='right' style='white-space:nowrap;'>"
                f"<span style='color:#334155;background:#eef2f7;font-size:12px;font-weight:700;padding:1px 5px;'>{html.escape(entry['category'])}</span>"
                f"<span style='color:{color};font-size:12px;font-weight:900;margin-left:6px;'>{html.escape(result or '-')}</span>"
                "</td>"
                "</tr>"
                "</table>"
                f"<div style='margin-top:5px;color:#111827;font-size:13px;font-weight:900;'>{html.escape(entry['action'])}</div>"
                f"<pre style=\"margin:5px 0 0 0;color:#475569;font-size:12px;white-space:pre-wrap;font-family:Consolas,'Microsoft YaHei',monospace;\">{html.escape(entry['detail'])}</pre>"
                "</div>"
            )
        if not rows:
            rows.append("<div class='event'><b>待执行</b><br><span>暂无操作记录</span></div>")
        html_text = (
            "<style>"
            "body{font-family:'Microsoft YaHei',Arial,sans-serif;color:#0f172a;}"
            "</style>"
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

    def _operator_recent_event_entries(self, limit: int = 12) -> list[dict[str, str]]:
        events = []
        for entry in list(getattr(self, "logs", [])):
            if self._operator_recent_event_hidden(entry):
                continue
            events.append(self._operator_format_recent_entry(entry))
            if len(events) >= limit:
                break
        return events

    @staticmethod
    def _operator_recent_event_hidden(entry: dict[str, object]) -> bool:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        if category != "自然语言":
            return False
        return action in {"闲聊咨询", "DeepSeek问答"}

    def _operator_format_recent_entry(self, entry: dict[str, object]) -> dict[str, str]:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        result = str(entry.get("result", "")) or "-"
        detail = str(entry.get("detail", "")) or "-"
        time_text = str(entry.get("time", "-"))
        if action.startswith("系统命令 "):
            key = action.replace("系统命令 ", "", 1)
            action = self._operator_system_action_label(key)
        elif action.startswith("系统命令准备 "):
            key = action.replace("系统命令准备 ", "", 1)
            action = f"准备 {self._operator_system_action_label(key)}"
        if action == "实时状态变化":
            action = "状态更新"
            detail = self._operator_format_state_change_detail(detail)
        return {
            "time": time_text,
            "category": category or "-",
            "result": result,
            "action": action or "-",
            "detail": self._operator_format_recent_detail(detail),
        }

    def _operator_system_action_label(self, key: str) -> str:
        labels = {
            "sys_resume": "继续运行",
            "sys_pause": "暂停任务",
            "sys_estop": "急停",
            "sys_cancel": "取消当前任务",
            "alarm_reset": "报警复位",
        }
        return labels.get(key, key or "系统命令")

    def _operator_compact_state_change(self, detail: str) -> str:
        parts = [part.strip() for part in detail.split("|") if part.strip()]
        important = [part for part in parts if part.startswith(("系统状态", "忙闲", "报警"))]
        return self._operator_compact_detail(" | ".join(important or parts[:2]) or detail)

    def _operator_format_state_change_detail(self, detail: str) -> str:
        parts = [part.strip() for part in detail.split("|") if part.strip()]
        return "\n".join(parts) if parts else detail

    def _operator_format_recent_detail(self, detail: str) -> str:
        text = str(detail or "-").strip()
        if not text:
            return "-"
        for separator in [" | ", ", "]:
            if separator in text:
                parts = [part.strip() for part in text.split(separator) if part.strip()]
                if len(parts) > 1:
                    return "\n".join(parts)
        return text

    def _operator_compact_detail(self, detail: str, max_len: int = 72) -> str:
        text = " ".join(str(detail or "-").split())
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "..."

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
        if not hasattr(self, "_operator_chat_thinking_steps"):
            self._operator_chat_thinking_steps = [[] for _ in self._operator_chat_messages]
        if not hasattr(self, "_operator_chat_thinking_meta"):
            self._operator_chat_thinking_meta = [{} for _ in self._operator_chat_messages]
        if self._operator_chat_messages and self._operator_chat_messages[-1] == (role, clean_text):
            if scroll_to_bottom:
                self._operator_chat_autoscroll_pending = True
                self._operator_scroll_chat_to_bottom()
            if not getattr(self, "_operator_chat_rendered", False):
                self._render_operator_chat()
            return
        self._operator_chat_messages.append((role, clean_text))
        self._operator_chat_thinking_steps.append([])
        self._operator_chat_thinking_meta.append({})
        self._operator_chat_messages = self._operator_chat_messages[-80:]
        self._operator_chat_thinking_steps = self._operator_chat_thinking_steps[-80:]
        self._operator_chat_thinking_meta = self._operator_chat_thinking_meta[-80:]
        self._operator_chat_autoscroll_pending = scroll_to_bottom
        self._render_operator_chat()

    def _operator_streaming_chat_delta_callback(self):
        def callback(delta: str) -> None:
            text = str(delta or "")
            if not text:
                return

            def apply_delta() -> None:
                self._operator_append_streaming_chat_response(text)

            if not getattr(self, "_operator_streaming_chat_active", False):

                def begin_and_apply() -> None:
                    if not getattr(self, "_operator_streaming_chat_active", False):
                        self._operator_begin_streaming_chat_response()
                    self._operator_append_streaming_chat_response(text)

                if hasattr(self, "_run_on_main_thread"):
                    self._run_on_main_thread(begin_and_apply)
                else:
                    begin_and_apply()
                return
            if hasattr(self, "_run_on_main_thread"):
                self._run_on_main_thread(apply_delta)
            else:
                apply_delta()

        return callback

    def _operator_maybe_begin_streaming_chat_for_text(self, text: str, *, use_deepseek: bool) -> bool:
        if not use_deepseek:
            return False
        if getattr(self, "_operator_streaming_chat_active", False):
            return True
        if not self._operator_text_looks_like_streaming_chat(text):
            return False
        self._operator_begin_streaming_chat_response()
        return True

    @staticmethod
    def _operator_text_looks_like_streaming_chat(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        for wake_word in ("小正", "小郑", "校正"):
            if compact.startswith(wake_word):
                compact = compact[len(wake_word):].lstrip("，,。:：")
                break
        chat_keywords = (
            "你好",
            "您好",
            "你是谁",
            "介绍",
            "功能",
            "能做什么",
            "怎么用",
            "帮助",
            "有哪些",
            "什么",
            "如何",
            "看下",
            "看一下",
            "查询",
            "命令",
            "模板",
            "指令",
            "用法",
        )
        if any(keyword in compact for keyword in chat_keywords):
            return True
        production_verbs = ("去", "移动", "回零", "急停", "暂停", "继续", "执行", "保存", "上升", "下降", "左移", "右移")
        return not any(verb in compact for verb in production_verbs)

    def _operator_begin_streaming_chat_response(self) -> None:
        self._operator_streaming_chat_active = True
        self._operator_streaming_chat_text = ""
        self._operator_streaming_chat_pending_chars = []
        self._operator_streaming_chat_finalizing = False
        self._operator_streaming_chat_final_text = ""
        self._operator_streaming_chat_char_timer_active = False
        self._operator_streaming_chat_started_sec = self._operator_now_seconds()
        self._operator_streaming_chat_last_render_sec = 0.0
        self._operator_streaming_chat_render_pending = False
        if not hasattr(self, "_operator_chat_messages"):
            self._operator_chat_messages = []
        if not hasattr(self, "_operator_chat_thinking_steps"):
            self._operator_chat_thinking_steps = [[] for _ in self._operator_chat_messages]
        if not hasattr(self, "_operator_chat_thinking_meta"):
            self._operator_chat_thinking_meta = [{} for _ in self._operator_chat_messages]
        initial_meta = {"active": True, "started_sec": self._operator_streaming_chat_started_sec}
        pending_final = str(getattr(self, "_operator_pending_streaming_chat_final_text", "") or "")
        if (
            pending_final
            and self._operator_chat_messages
            and self._operator_chat_messages[-1] == ("assistant", pending_final)
        ):
            self._operator_chat_messages[-1] = ("assistant", "")
            self._operator_chat_thinking_steps[-1:] = [self._operator_streaming_chat_initial_steps()]
            self._operator_chat_thinking_meta[-1:] = [initial_meta]
        else:
            self._operator_chat_messages.append(("assistant", ""))
            self._operator_chat_thinking_steps.append(self._operator_streaming_chat_initial_steps())
            self._operator_chat_thinking_meta.append(initial_meta)
        self._operator_pending_streaming_chat_final_text = ""
        self._operator_chat_messages = self._operator_chat_messages[-80:]
        self._operator_chat_thinking_steps = self._operator_chat_thinking_steps[-80:]
        self._operator_chat_thinking_meta = self._operator_chat_thinking_meta[-80:]
        self._operator_chat_autoscroll_pending = True
        self._render_operator_chat()
        self._operator_streaming_chat_last_render_sec = self._operator_now_seconds()

    def _operator_append_streaming_chat_response(self, delta: str) -> None:
        if not getattr(self, "_operator_streaming_chat_active", False):
            self._operator_begin_streaming_chat_response()
        text = str(delta or "")
        if not text:
            return
        pending = getattr(self, "_operator_streaming_chat_pending_chars", None)
        if not isinstance(pending, list):
            pending = []
            self._operator_streaming_chat_pending_chars = pending
        pending.extend(text)
        self._operator_schedule_streaming_chat_char_flush()

    def _operator_schedule_streaming_chat_char_flush(self) -> None:
        if getattr(self, "_operator_streaming_chat_char_timer_active", False):
            return
        if not getattr(self, "_operator_streaming_chat_pending_chars", []):
            return
        self._operator_streaming_chat_char_timer_active = True
        try:
            QTimer.singleShot(
                int(self._operator_streaming_chat_typewriter_interval_seconds() * 1000),
                self._operator_flush_streaming_chat_char,
            )
        except Exception:
            self._operator_streaming_chat_char_timer_active = False

    def _operator_flush_streaming_chat_char(self) -> None:
        self._operator_streaming_chat_char_timer_active = False
        pending = getattr(self, "_operator_streaming_chat_pending_chars", [])
        if not pending:
            if getattr(self, "_operator_streaming_chat_finalizing", False):
                self._operator_complete_streaming_chat_response(
                    str(getattr(self, "_operator_streaming_chat_final_text", "") or getattr(self, "_operator_streaming_chat_text", ""))
                )
            return
        char = str(pending.pop(0))
        current = str(getattr(self, "_operator_streaming_chat_text", "") or "") + char
        self._operator_streaming_chat_text = current
        if not hasattr(self, "_operator_chat_messages") or not self._operator_chat_messages:
            self._operator_chat_messages = [("assistant", current)]
            self._operator_chat_thinking_steps = [self._operator_streaming_chat_initial_steps()]
            self._operator_chat_thinking_meta = [{"active": True, "started_sec": self._operator_now_seconds()}]
        else:
            self._operator_chat_messages[-1] = ("assistant", current)
        label = getattr(self, "_operator_streaming_chat_content_label", None)
        if label is not None and hasattr(label, "setText"):
            try:
                label.setText(current)
                if hasattr(label, "setVisible"):
                    label.setVisible(True)
                self._operator_chat_autoscroll_pending = True
                self._operator_scroll_chat_to_bottom()
                if pending:
                    self._operator_schedule_streaming_chat_char_flush()
                elif getattr(self, "_operator_streaming_chat_finalizing", False):
                    self._operator_complete_streaming_chat_response(
                        str(getattr(self, "_operator_streaming_chat_final_text", "") or current)
                    )
                return
            except Exception:
                self._operator_streaming_chat_content_label = None
        self._operator_chat_autoscroll_pending = True
        self._render_operator_chat()
        if pending:
            self._operator_schedule_streaming_chat_char_flush()
        elif getattr(self, "_operator_streaming_chat_finalizing", False):
            self._operator_complete_streaming_chat_response(
                str(getattr(self, "_operator_streaming_chat_final_text", "") or current)
            )

    def _operator_render_streaming_chat_throttled(self) -> None:
        now = self._operator_now_seconds()
        last = float(getattr(self, "_operator_streaming_chat_last_render_sec", 0.0) or 0.0)
        interval = self._operator_streaming_chat_render_interval_seconds()
        if now - last >= interval:
            self._operator_streaming_chat_last_render_sec = now
            self._operator_streaming_chat_render_pending = False
            self._render_operator_chat()
            return
        if getattr(self, "_operator_streaming_chat_render_pending", False):
            return
        self._operator_streaming_chat_render_pending = True
        try:
            QTimer.singleShot(int(interval * 1000), self._operator_flush_streaming_chat_render)
        except Exception:
            pass

    def _operator_flush_streaming_chat_render(self) -> None:
        if not getattr(self, "_operator_streaming_chat_render_pending", False):
            return
        self._operator_streaming_chat_render_pending = False
        self._operator_streaming_chat_last_render_sec = self._operator_now_seconds()
        self._render_operator_chat()

    @staticmethod
    def _operator_streaming_chat_thinking_hint() -> str:
        return "正在整理资料，请稍候..."

    @staticmethod
    def _operator_streaming_chat_initial_steps() -> list[str]:
        return ["正在思考", "识别为普通问答", "检索本地资料"]

    @staticmethod
    def _operator_streaming_chat_final_steps() -> list[str]:
        return ["识别为普通问答", "基于本地资料整理回答", "DeepSeek 生成回答，未触发机械手动作"]

    @staticmethod
    def _operator_streaming_chat_render_interval_seconds() -> float:
        return 0.06

    @staticmethod
    def _operator_streaming_chat_typewriter_interval_seconds() -> float:
        return 0.04

    @staticmethod
    def _operator_footer_status_text(text: str, max_len: int = 88) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= max_len:
            return compact
        return compact[: max_len - 3].rstrip() + "..."

    def _operator_finish_streaming_chat_response(self, final_text: str) -> None:
        clean_text = str(final_text or "").strip()
        if not getattr(self, "_operator_streaming_chat_active", False):
            self._operator_pending_streaming_chat_final_text = clean_text
            if clean_text:
                self._operator_add_chat_message("assistant", clean_text)
            return
        current = str(getattr(self, "_operator_streaming_chat_text", "") or "")
        pending = getattr(self, "_operator_streaming_chat_pending_chars", [])
        if not isinstance(pending, list):
            pending = []
            self._operator_streaming_chat_pending_chars = pending
        if clean_text and pending:
            combined = current + "".join(str(ch) for ch in pending)
            if clean_text.startswith(current) and combined != clean_text:
                pending[:] = list(clean_text[len(current):])
            self._operator_streaming_chat_finalizing = True
            self._operator_streaming_chat_final_text = clean_text
            self._operator_schedule_streaming_chat_char_flush()
            return
        self._operator_complete_streaming_chat_response(clean_text)

    def _operator_complete_streaming_chat_response(self, final_text: str) -> None:
        clean_text = str(final_text or "").strip()
        self._operator_streaming_chat_active = False
        self._operator_pending_streaming_chat_final_text = ""
        self._operator_streaming_chat_text = clean_text
        self._operator_streaming_chat_pending_chars = []
        self._operator_streaming_chat_finalizing = False
        self._operator_streaming_chat_final_text = ""
        self._operator_streaming_chat_char_timer_active = False
        if not hasattr(self, "_operator_chat_messages") or not self._operator_chat_messages:
            self._operator_chat_messages = []
            self._operator_chat_thinking_steps = []
            self._operator_chat_thinking_meta = []
        if clean_text:
            self._operator_chat_messages[-1:] = [("assistant", clean_text)]
            if not hasattr(self, "_operator_chat_thinking_steps"):
                self._operator_chat_thinking_steps = [[] for _ in self._operator_chat_messages]
            if not hasattr(self, "_operator_chat_thinking_meta"):
                self._operator_chat_thinking_meta = [{} for _ in self._operator_chat_messages]
            self._operator_chat_thinking_steps[-1:] = [self._operator_streaming_chat_final_steps()]
            started = float(getattr(self, "_operator_streaming_chat_started_sec", self._operator_now_seconds()) or self._operator_now_seconds())
            elapsed_sec = max(0, int(self._operator_now_seconds() - started))
            self._operator_chat_thinking_meta[-1:] = [{"active": False, "elapsed_sec": elapsed_sec}]
        else:
            self._operator_chat_messages[-1:] = []
            if hasattr(self, "_operator_chat_thinking_steps"):
                self._operator_chat_thinking_steps[-1:] = []
            if hasattr(self, "_operator_chat_thinking_meta"):
                self._operator_chat_thinking_meta[-1:] = []
        self._operator_chat_autoscroll_pending = True
        self._operator_streaming_chat_render_pending = False
        self._render_operator_chat()

    def _operator_cancel_streaming_chat_response(self) -> None:
        if not getattr(self, "_operator_streaming_chat_active", False):
            return
        self._operator_streaming_chat_active = False
        self._operator_streaming_chat_text = ""
        self._operator_streaming_chat_pending_chars = []
        self._operator_streaming_chat_finalizing = False
        self._operator_streaming_chat_final_text = ""
        self._operator_streaming_chat_char_timer_active = False
        self._operator_streaming_chat_render_pending = False
        self._operator_streaming_chat_content_label = None
        if (
            getattr(self, "_operator_chat_messages", None)
            and self._operator_chat_messages[-1] == ("assistant", "")
            and getattr(self, "_operator_chat_thinking_meta", None)
            and bool(self._operator_chat_thinking_meta[-1].get("active"))
        ):
            self._operator_chat_messages[-1:] = []
            self._operator_chat_thinking_steps[-1:] = []
            self._operator_chat_thinking_meta[-1:] = []
            self._render_operator_chat()

    def _operator_add_chat_from_log(self, entry: dict[str, Any]) -> None:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        result = str(entry.get("result", ""))
        detail = str(entry.get("detail", ""))
        message = self.operator_response_builder.from_log_entry(entry)
        if message is None:
            self._operator_archive_execution_from_log(entry, str(entry.get("detail", "") or ""))
            if category != "自然语言":
                return
        else:
            self._operator_archive_execution_from_log(entry, message.text)
            self._operator_publish_response(message)
            self._operator_route_voice_recognition_from_log(entry)
            return
        if category == "自然语言":
            if action == "动作序列完成" and result == "成功":
                self._operator_add_chat_message("assistant", detail or "执行完成。")
                return
            if action == "动作序列终止":
                self._operator_add_chat_message("assistant", f"执行失败：{detail or '动作序列已终止'}")
                return

    def _operator_route_voice_recognition_from_log(self, entry: dict[str, Any]) -> bool:
        if str(entry.get("category", "")) != "语音":
            return False
        if str(entry.get("action", "")) != "麦克风识别":
            return False
        if str(entry.get("result", "")) != "成功":
            return False
        text = str(entry.get("detail", "") or "").strip()
        if not text or text == "-":
            return False
        self._operator_archive_voice_input(text, asr_confidence=self._operator_asr_confidence_from_log(entry))
        previous = bool(getattr(self, "_operator_voice_route_active", False))
        self._operator_voice_route_active = True
        try:
            if self._handle_operator_ui_command(text):
                return True
            if hasattr(self, "operator_command_edit"):
                self.operator_command_edit.setText(text)
            if hasattr(self, "_operator_execute_text"):
                self._operator_execute_text()
                return True
            return False
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("语音", "识别文本路由", "失败", f"{type(exc).__name__}: {exc}")
            return False
        finally:
            self._operator_voice_route_active = previous

    def _operator_archive_execution_from_log(self, entry: dict[str, Any], response_text: str) -> bool:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        result = str(entry.get("result", ""))
        if self._operator_mark_execution_start_from_log(entry):
            return False
        execution_detail = self._operator_execution_detail_from_log(entry)
        exec_duration_ms = self._operator_execution_duration_from_log(entry)
        if category == "自然语言" and action == "动作序列完成" and result == "成功":
            return self._operator_archive_execution_result(result="success", final_text=response_text, exec_duration_ms=exec_duration_ms, execution_detail=execution_detail)
        if category == "自然语言" and action == "动作序列终止":
            return self._operator_archive_execution_result(result="failure", final_text=response_text, exec_duration_ms=exec_duration_ms, execution_detail=execution_detail)
        if category == "流程" and action.startswith("流程完成 ") and result == "成功":
            return self._operator_archive_execution_result(result="success", final_text=response_text, exec_duration_ms=exec_duration_ms, execution_detail=execution_detail)
        if category == "流程" and result == "失败":
            return self._operator_archive_execution_result(result="failure", final_text=response_text, exec_duration_ms=exec_duration_ms, execution_detail=execution_detail)
        if category == "六轴" and action.startswith("执行完成 ") and result == "成功":
            return self._operator_archive_execution_result(result="success", final_text=response_text, exec_duration_ms=exec_duration_ms, execution_detail=execution_detail)
        if category == "六轴" and action.startswith("完成+报警 "):
            return self._operator_archive_execution_result(result="warning", final_text=response_text, exec_duration_ms=exec_duration_ms, execution_detail=execution_detail)
        if category == "系统" and action.startswith("系统命令 "):
            return self._operator_archive_execution_result(
                result="success" if result == "成功" else "failure",
                final_text=response_text,
                exec_duration_ms=exec_duration_ms,
                execution_detail=execution_detail,
            )
        if category == "执行" and action.startswith("发送指令 "):
            return self._operator_archive_execution_result(
                result="success" if result == "成功" else "failure",
                final_text=response_text,
                exec_duration_ms=exec_duration_ms,
                execution_detail=execution_detail,
            )
        return False

    def _operator_mark_execution_start_from_log(self, entry: dict[str, Any]) -> bool:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        if not (
            (category == "执行" and action.startswith("发送准备 "))
            or (category == "系统" and action.startswith("系统命令准备 "))
        ):
            return False
        key = self._operator_execution_log_key(entry)
        start_ms = self._operator_log_monotonic_ms(entry)
        if not key or start_ms is None:
            return False
        starts = getattr(self, "_operator_execution_start_monotonic_ms", None)
        if not isinstance(starts, dict):
            starts = {}
            self._operator_execution_start_monotonic_ms = starts
        starts[key] = start_ms
        return True

    def _operator_execution_duration_from_log(self, entry: dict[str, Any]) -> int:
        key = self._operator_execution_log_key(entry)
        end_ms = self._operator_log_monotonic_ms(entry)
        starts = getattr(self, "_operator_execution_start_monotonic_ms", {})
        if not key or end_ms is None or not isinstance(starts, dict):
            return 0
        start_ms = starts.get(key)
        if start_ms is None:
            return 0
        try:
            return max(0, int(end_ms) - int(start_ms))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _operator_log_monotonic_ms(entry: dict[str, Any]) -> int | None:
        value = entry.get("monotonic_ms")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _operator_execution_log_key(entry: dict[str, Any]) -> str:
        category = str(entry.get("category", ""))
        action = str(entry.get("action", ""))
        if category == "执行":
            name = str(entry.get("query_key") or "")
            if not name:
                for prefix in ("发送准备 ", "发送指令 "):
                    if action.startswith(prefix):
                        name = action.replace(prefix, "", 1).strip()
                        break
            return f"execute:{name}" if name else ""
        if category == "系统":
            name = str(entry.get("system_action") or "")
            if not name:
                for prefix in ("系统命令准备 ", "系统命令 "):
                    if action.startswith(prefix):
                        name = action.replace(prefix, "", 1).strip()
                        break
            return f"system:{name}" if name else ""
        return ""

    @staticmethod
    def _operator_execution_detail_from_log(entry: dict[str, Any]) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        command_snapshot = entry.get("command_snapshot")
        if isinstance(command_snapshot, dict):
            detail["modbus_write"] = dict(command_snapshot)
        if isinstance(entry.get("state_before"), dict):
            detail["state_before"] = dict(entry["state_before"])
        if isinstance(entry.get("state_after"), dict):
            detail["state_after"] = dict(entry["state_after"])
        return detail

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

    def _operator_plan_recognized_text(self, plan) -> str:
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions:
            return f"我没有识别到可执行动作。{getattr(plan, 'reason', '')}"
        policy = policy_for_plan(plan)
        first = actions[0]
        action_type = getattr(first, "action_type", "-")
        target = getattr(first, "target", "") or "-"
        return f"已识别{policy.semantic_label}：{action_type} / {target}。"

    def _render_operator_chat(self) -> None:
        if not hasattr(self, "operator_chat_layout"):
            return
        while self.operator_chat_layout.count():
            item = self.operator_chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        thinking_steps = getattr(self, "_operator_chat_thinking_steps", [])
        thinking_meta = getattr(self, "_operator_chat_thinking_meta", [])
        for index, (role, text) in enumerate(getattr(self, "_operator_chat_messages", [])):
            steps = thinking_steps[index] if index < len(thinking_steps) else []
            meta = thinking_meta[index] if index < len(thinking_meta) else {}
            self.operator_chat_layout.addWidget(
                self._build_operator_chat_row(
                    role,
                    text,
                    thinking_steps=steps,
                    thinking_meta=meta,
                    message_index=index,
                )
            )
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

        def scroll(final: bool = False) -> None:
            bar = self.operator_chat_scroll.verticalScrollBar()
            if hasattr(self, "operator_chat_content"):
                self.operator_chat_content.adjustSize()
            bar.setValue(bar.maximum())
            if final:
                self._operator_chat_autoscroll_pending = False

        QTimer.singleShot(0, lambda: scroll(False))
        QTimer.singleShot(40, lambda: scroll(False))
        QTimer.singleShot(120, lambda: scroll(False))
        QTimer.singleShot(260, lambda: scroll(True))

    def _build_operator_chat_row(
        self,
        role: str,
        text: str,
        *,
        thinking_steps: list[str] | None = None,
        thinking_meta: dict[str, object] | None = None,
        message_index: int | None = None,
    ) -> QWidget:
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
        min_width, max_width = self._operator_chat_bubble_width_bounds(is_user=is_user)
        if min_width > 0:
            bubble.setMinimumWidth(min_width)
        bubble.setMaximumWidth(max_width)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 9, 12, 10)
        bubble_layout.setSpacing(4)

        if not is_user and thinking_steps:
            meta = thinking_meta or {}
            is_active = bool(meta.get("active"))
            elapsed = int(meta.get("elapsed_sec", 0) or 0)
            label = "正在思考..." if is_active else f"已思考（用时 {elapsed} 秒）"
            process_button = QPushButton(label)
            process_button.setObjectName("operatorThinkingToggle")
            process_button.setCheckable(True)
            process_button.setCursor(Qt.CursorShape.PointingHandCursor)
            process_detail = QLabel("\n".join(f"{idx}. {step}" for idx, step in enumerate(thinking_steps, start=1)))
            process_detail.setObjectName("operatorThinkingDetail")
            process_detail.setWordWrap(True)
            process_detail.setTextFormat(Qt.TextFormat.PlainText)
            process_button.setChecked(is_active)
            process_detail.setVisible(is_active)
            process_button.toggled.connect(process_detail.setVisible)
            bubble_layout.addWidget(process_button)
            bubble_layout.addWidget(process_detail)

        content = QLabel(text)
        content.setObjectName("operatorChatText")
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if is_user:
            content.setStyleSheet("color: #111827;")
        if not text:
            content.setVisible(False)
        if (
            not is_user
            and bool((thinking_meta or {}).get("active"))
            and message_index is not None
            and message_index == len(getattr(self, "_operator_chat_messages", [])) - 1
        ):
            self._operator_streaming_chat_content_label = content

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

    @staticmethod
    def _operator_chat_bubble_width_bounds(*, is_user: bool) -> tuple[int, int]:
        return (0, 760) if is_user else (300, 760)

    def _refresh_operator_full_status(self) -> None:
        if not hasattr(self, "operator_full_status_browser"):
            return
        snapshot = self._operator_dashboard_snapshot_dict()
        sections = [
            self._operator_html_section(title, rows)
            for title, rows in self._operator_full_status_sections_from_snapshot(snapshot)
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

    def _operator_full_status_sections_from_snapshot(self, snapshot: dict[str, Any]) -> list[tuple[str, list[tuple[str, object]]]]:
        boards = snapshot.get("boards", {}) if isinstance(snapshot, dict) else {}
        device = boards.get("device_status", {}) or {}
        feasibility = boards.get("action_feasibility", {}) or {}
        safety = boards.get("safety_boundary", {}) or {}
        motion = boards.get("motion_limits", {}) or {}
        process = boards.get("process_preview", {}) or {}
        adaptation = boards.get("process_adaptation", {}) or {}
        communication = boards.get("communication_faults", {}) or {}
        return [
            (
                "看板1 设备基础状态",
                [
                    ("刷新周期", f"{snapshot.get('refresh_ms', '-')}ms"),
                    ("系统状态", device.get("system_state", "-")),
                    ("急停/暂停/报警", f"{self._yes_no(device.get('estop'))} / {self._yes_no(device.get('pause'))} / {self._yes_no(device.get('alarm'))}"),
                    ("报警码", device.get("alarm_code", "-")),
                    ("MPOS关节", self._format_sequence(device.get("mpos_j", ()))),
                    ("DPOS关节", self._format_sequence(device.get("dpos_j", ()))),
                    ("MPOS空间", self._format_sequence(device.get("mpos_c", ()))),
                    ("DPOS空间", self._format_sequence(device.get("dpos_c", ()))),
                    ("当前R/Z", f"{device.get('r_current', '-')} / {device.get('z_current', '-')}"),
                ],
            ),
            (
                "看板2 动作执行可行性",
                [
                    ("通道空闲", self._yes_no(feasibility.get("channel_idle"))),
                    ("L1/L2预检", f"{feasibility.get('precheck_status', 'unknown')} / {feasibility.get('motion_status', 'unknown')}"),
                    ("当前函数", feasibility.get("current_func", "-")),
                    ("执行结果", feasibility.get("result", "-")),
                ],
            ),
            (
                "看板3 全域安全边界",
                [
                    ("安全R范围", self._format_range(safety.get("safe_r_range"))),
                    ("安全Z范围", self._format_range(safety.get("safe_z_range"))),
                    ("当前R/Z", f"{safety.get('current_r', '-')} / {safety.get('current_z', '-')}"),
                    ("X/Y/Z范围", f"{self._format_range(safety.get('x_range'))} / {self._format_range(safety.get('y_range'))} / {self._format_range(safety.get('z_range'))}"),
                    ("关节软限位", self._format_joint_limits(safety.get("joint_limits"))),
                ],
            ),
            (
                "看板4 运动极限参数",
                [
                    ("当前速度", motion.get("speed", "-")),
                    ("运动进度", motion.get("motion_percent", "-")),
                    ("速度上限", motion.get("safe_speed_max", "-")),
                    ("加速度/减速度上限", f"{motion.get('safe_acc_max', '-')} / {motion.get('safe_dec_max', '-')}"),
                    ("轴状态", self._format_sequence(motion.get("axis_status", ()))),
                    ("运动类型", self._format_sequence(motion.get("motion_type", ()))),
                ],
            ),
            (
                "看板5 工艺流程预演进度",
                [
                    ("流程状态", process.get("flow_status", "-")),
                    ("当前流程", process.get("current_flow_name", "-")),
                    ("当前步骤", process.get("flow_current_step", "-")),
                    ("预演进度", self._format_percent(process.get("progress_percent"))),
                    ("L3状态", process.get("l3_status", "unknown")),
                    ("风险摘要", self._format_sequence(process.get("risk_summary", ()))),
                ],
            ),
            (
                "看板6 工艺适配评估",
                [
                    ("L2状态", adaptation.get("l2_status", "unknown")),
                    ("FSTATUS", adaptation.get("fstatus", "-")),
                    ("奇异点", adaptation.get("singularity", "-")),
                    ("建议", adaptation.get("suggestion", "-")),
                ],
            ),
            (
                "看板7 通讯+设备故障诊断",
                [
                    ("通讯状态", "正常" if communication.get("ecat_ok") else "异常"),
                    ("控制器", communication.get("controller", "unknown")),
                    ("实时反馈", communication.get("realtime_feedback", "unknown")),
                    ("IO状态位", communication.get("io_status", "-")),
                    ("伺服使能", communication.get("servo_enable", "-")),
                ],
            ),
        ]

    def _operator_html_section(self, title: str, rows: list[tuple[str, object]]) -> str:
        table_rows = "".join(
            "<tr>"
            f"<th>{html.escape(label)}</th>"
            f"<td>{html.escape(str(value if value not in (None, '') else '-'))}</td>"
            "</tr>"
            for label, value in rows
        )
        return f"<div class='board'><div class='title'>{html.escape(title)}</div><table>{table_rows}</table></div>"

    @staticmethod
    def _yes_no(value: object) -> str:
        return "是" if bool(value) else "否"

    @staticmethod
    def _format_sequence(value: object) -> str:
        if isinstance(value, (list, tuple)):
            if not value:
                return "-"
            return " / ".join(str(item) for item in value)
        return str(value if value not in (None, "") else "-")

    @staticmethod
    def _format_range(value: object) -> str:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return f"{value[0]} ~ {value[1]}"
        return str(value if value not in (None, "") else "-")

    @staticmethod
    def _format_percent(value: object) -> str:
        if value in (None, "", "-"):
            return "-"
        text = str(value)
        return text if text.endswith("%") else f"{text}%"

    @staticmethod
    def _format_joint_limits(value: object) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            return "-"
        parts: list[str] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            parts.append(f"J{index}:{item[0]} ~ {item[1]}")
        return " / ".join(parts) if parts else "-"

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
        entries = self._operator_recent_event_entries(limit=3)
        if not entries:
            return "暂无"
        return "；".join(
            f"{entry['time']} {entry['category']} {entry['result']} {entry['action']}"
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
