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
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QStackedWidget,
)

from .atomic_capabilities import atomic_capability_summary, atomic_capability_rows
from .agent.context_builder import AgentContextBuilder
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
from .exceptions import BackgroundTaskError
from .dialog_logger import DialogLogger
from .interaction_archiver import InteractionArchiveWriter
from .json_schema import DeviceSnapshot
from .models import FlowDefinition, QueryRecord
from .response_builder import ResponseBuilder, ResponseMessage
from .safety_precheck import SafetyPrecheckService
from .safety_suggestion import SafetySuggestionService
from .semantic_response_policy import policy_for_plan
from .speech_broadcast import (
    DoubaoSpeechSink,
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
from .voice_wake_words import configured_wake_words, strip_wake_word_from_compact


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
        self._operator_pending_interruption_text = ""
        self._operator_executing_interruption_text = False
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
        self._operator_refresh_pending = False
        self._operator_last_refresh_sec = 0.0

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
        bar.setFixedWidth(self._scaled(268))
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
        sidebar.setFixedWidth(self._scaled(336))
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
            ("流程执行", self._operator_show_execution),
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
        self.operator_pending_flow_title = QLabel("待确认草案")
        self.operator_pending_flow_title.setObjectName("operatorSceneSubtitle")
        self.operator_pending_flow_browser = QTextBrowser()
        self.operator_pending_flow_browser.setObjectName("operatorRecentBrowser")
        self.operator_pending_flow_browser.setOpenExternalLinks(False)
        self.operator_pending_flow_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_pending_flow_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.operator_pending_flow_browser.setMaximumHeight(142)
        self.operator_pending_flow_title.setVisible(False)
        self.operator_pending_flow_browser.setVisible(False)
        self.operator_idle_subtitle = QLabel("最近操作")
        self.operator_idle_subtitle.setObjectName("operatorSceneSubtitle")
        self.operator_recent_browser = QTextBrowser()
        self.operator_recent_browser.setObjectName("operatorRecentBrowser")
        self.operator_recent_browser.setOpenExternalLinks(False)
        self.operator_recent_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_recent_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.operator_idle_title)
        layout.addWidget(self.operator_pending_flow_title)
        layout.addWidget(self.operator_pending_flow_browser)
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
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.operator_execute_title = QLabel("设备执行中")
        self.operator_execute_title.setObjectName("operatorSceneTitle")
        self.operator_execute_detail = QLabel("-")
        self.operator_execute_detail.setObjectName("operatorSceneSubtitle")
        self.operator_execute_detail.setWordWrap(True)
        self.operator_execute_detail.setMaximumHeight(58)
        self.operator_execute_detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.operator_execute_progress = QProgressBar()
        self.operator_execute_progress.setObjectName("operatorProgress")
        self.operator_execute_progress.setRange(0, 100)
        self.operator_execute_progress.setFormat("估算 %p%")
        self.operator_execute_timeline_scroll = QScrollArea()
        self.operator_execute_timeline_scroll.setObjectName("operatorFlowTimelineScroll")
        self.operator_execute_timeline_scroll.setWidgetResizable(True)
        self.operator_execute_timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.operator_execute_timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_execute_timeline_scroll.setMinimumHeight(180)
        self.operator_execute_timeline_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.operator_execute_timeline_content = QFrame()
        self.operator_execute_timeline_content.setObjectName("operatorFlowTimelineContent")
        self.operator_execute_timeline_layout = QVBoxLayout(self.operator_execute_timeline_content)
        self.operator_execute_timeline_layout.setContentsMargins(2, 2, 2, 2)
        self.operator_execute_timeline_layout.setSpacing(8)
        self.operator_execute_timeline_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.operator_execute_timeline_scroll.setWidget(self.operator_execute_timeline_content)
        self.operator_execute_step_widgets = {}
        self.operator_execute_step_progress_bars = {}
        self._operator_execute_timeline_signature = None
        self.operator_execute_position = QLabel("-")
        self.operator_execute_position.setObjectName("operatorMetricLarge")
        self.operator_execute_position.setWordWrap(True)
        self.operator_execute_position.setMaximumHeight(34)
        self.operator_execute_position.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.operator_execute_title)
        layout.addWidget(self.operator_execute_detail)
        layout.addWidget(self.operator_execute_progress)
        layout.addWidget(self.operator_execute_timeline_scroll, 1)
        layout.addWidget(self.operator_execute_position)
        return scene

    def _build_operator_confirm_scene(self) -> QWidget:
        scene = QFrame()
        scene.setObjectName("operatorScene")
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.operator_confirm_title = QLabel("等待确认执行")
        self.operator_confirm_title.setObjectName("operatorSceneTitle")
        self.operator_confirm_detail = QTextBrowser()
        self.operator_confirm_detail.setObjectName("operatorRecentBrowser")
        self.operator_confirm_detail.setOpenExternalLinks(False)
        self.operator_confirm_detail.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.operator_confirm_detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.operator_confirm_detail.setHtml("<p>当前没有需要确认的指令。</p>")
        button_row = QHBoxLayout()
        for text, slot in [
            ("确认执行", self._operator_confirm_execute),
            ("采纳安全建议", self._operator_accept_suggestion),
            ("取消指令", self._operator_cancel_confirm),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            btn.clicked.connect(slot)
            if text == "采纳安全建议":
                self.operator_accept_suggestion_btn = btn
            button_row.addWidget(btn)
        layout.addWidget(self.operator_confirm_title)
        layout.addWidget(self.operator_confirm_detail, 1)
        layout.addLayout(button_row)
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
            ("开启会话", self._operator_toggle_microphone_recording, ""),
            ("清空", self._operator_clear_text, ""),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("operatorActionButton")
            if klass:
                btn.setProperty("klass", klass)
            btn.clicked.connect(slot)
            input_row.addWidget(btn)
            if text == "开启会话":
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
        self._operator_schedule_refresh()

    def _append_log_entry(self, entry: dict[str, Any]) -> None:
        super()._append_log_entry(entry)
        self._operator_add_chat_from_log(entry)
        self._operator_schedule_refresh()

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
        query_target = str(getattr(actions[0], "target", "") or "")
        if query_target == "status_query" and self._operator_text_asks_execution_completion(getattr(plan, "raw_text", "")):
            answer_text = self._operator_execution_monitor_query_text()
            self._operator_set_pending_confirm_plan(None)
            self._operator_scene_override = "query"
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text=answer_text,
                    priority="normal" if "失败" not in answer_text else "high",
                    context_id="agent:execution_monitor",
                )
            )
            self._operator_publish_ai_answer_for_speech(answer_text)
            self._operator_archive_execution_result(result="answered", final_text=answer_text)
            return True
        if query_target in {"alarm_query", "status_query"}:
            answer_text = self._operator_agent_alarm_query_text()
            self._operator_set_pending_confirm_plan(None)
            self._operator_scene_override = "query"
            self._operator_publish_response(
                ResponseMessage(
                    kind="result",
                    text=answer_text,
                    priority="high" if "无法" in answer_text or "超限" in answer_text or "急停" in answer_text else "normal",
                    context_id=f"agent:{query_target}",
                )
            )
            self._operator_publish_ai_answer_for_speech(answer_text)
            self._operator_archive_execution_result(result="answered", final_text=answer_text)
            return True
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

    @staticmethod
    def _operator_text_asks_execution_completion(text: object) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(phrase in compact for phrase in ("执行完成了吗", "运动完成了吗", "完成了吗", "结束了吗", "执行完了吗"))

    def _operator_execution_monitor_query_text(self) -> str:
        from .agent.execution_monitor import ExecutionMonitorAgent

        snapshot = getattr(self, "_operator_last_execution_monitor_snapshot", None)
        now_provider = getattr(self, "_operator_now_seconds", None)
        now = now_provider() if callable(now_provider) else time.time()
        return ExecutionMonitorAgent().answer_completion_query(snapshot, now=now)

    def _operator_agent_alarm_query_text(self) -> str:
        from .agent.alarm_explanation import AlarmExplanationAgent

        result = AlarmExplanationAgent().explain(
            long34=self._operator_int_attr("six_long34", default=0),
            long36=self._operator_int_attr("six_long36", default=0),
            long38=self._operator_int_attr("six_long38", fallback="long38_raw", default=0),
            axis_status=tuple(getattr(self, "axis_status", ()) or ()),
            current_func=self._operator_current_func_num_for_agent(),
            safety_values=self._operator_alarm_query_safety_values(),
            hardware_values=self._operator_alarm_query_hardware_values(),
        )
        text = str(result.get("summary", "") or "当前报警状态暂不可用。")
        detail = str(result.get("detail", "") or "").strip()
        suggestions = [str(item).strip() for item in result.get("suggestions", []) if str(item).strip()]
        if detail:
            text += f" {detail}"
        if suggestions:
            text += " 建议：" + " ".join(suggestions[:3])
        return text

    def _operator_alarm_query_safety_values(self) -> dict[str, Any]:
        axis_ranges = getattr(self, "axis_ranges", None)
        boundary: dict[str, Any] = {}
        try:
            snapshot = self._operator_dashboard_snapshot_dict()
            boards = snapshot.get("boards", {}) if isinstance(snapshot, dict) else {}
            boundary = boards.get("safety_boundary", {}) or {}
        except Exception:
            boundary = {}
        return {
            "safe_r_min": getattr(axis_ranges, "safe_r_min", None),
            "safe_r_max": getattr(axis_ranges, "safe_r_max", None),
            "safe_z_min": getattr(axis_ranges, "safe_z_min", None),
            "safe_z_max": getattr(axis_ranges, "safe_z_max", None),
            "safe_speed_max": getattr(axis_ranges, "safe_speed_max", None),
            "safe_acc_max": getattr(axis_ranges, "safe_acc_max", None),
            "safe_dec_max": getattr(axis_ranges, "safe_dec_max", None),
            "current_r": self._operator_float_attr("current_r", fallback="robot_r")
            if self._operator_float_attr("current_r", fallback="robot_r") is not None
            else boundary.get("current_r"),
            "current_z": self._operator_float_attr("current_z", fallback="robot_z")
            if self._operator_float_attr("current_z", fallback="robot_z") is not None
            else boundary.get("current_z"),
        }

    def _operator_alarm_query_hardware_values(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        try:
            snapshot = self._operator_dashboard_snapshot_dict()
        except Exception:
            snapshot = {}
        hardware = snapshot.get("hardware", {}) if isinstance(snapshot, dict) else {}
        connection = snapshot.get("connection", {}) if isinstance(snapshot, dict) else {}
        servo_value = hardware.get("servo_enable", getattr(self, "servo_enable", None)) if isinstance(hardware, dict) else getattr(self, "servo_enable", None)
        axis_alarm_flags = (
            hardware.get("axis_alarm_flags", getattr(self, "axis_alarm_flags", None))
            if isinstance(hardware, dict)
            else getattr(self, "axis_alarm_flags", None)
        )
        axis_enabled = (
            hardware.get("axis_enabled", getattr(self, "axis_enabled", None))
            if isinstance(hardware, dict)
            else getattr(self, "axis_enabled", None)
        )
        any_axis_moving = (
            hardware.get("any_axis_moving", None)
            if isinstance(hardware, dict)
            else None
        )
        if any_axis_moving is None:
            any_axis_moving = str(getattr(self, "motion_percent", "") or "") == "运动中"
        return {
            "servo_enabled": servo_value,
            "ethercat_initialized": str(connection.get("controller", "")).lower() == "online"
            if isinstance(connection, dict) and connection
            else None,
            "axis_alarm_flags": axis_alarm_flags,
            "axis_enabled": axis_enabled,
            "any_axis_moving": any_axis_moving,
        }

    def _operator_current_func_num_for_agent(self) -> int | None:
        explicit = self._operator_int_attr("current_func_num", default=None)
        if explicit is not None:
            return explicit
        text = str(getattr(self, "current_func_text", "") or "")
        match = re.search(r"Func\s*(\d+)", text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _operator_int_attr(self, name: str, *, fallback: str | None = None, default: int | None = 0) -> int | None:
        value = getattr(self, name, None)
        if value is None and fallback:
            value = getattr(self, fallback, None)
        parsed = self._operator_float_or_none(value)
        return default if parsed is None else int(parsed)

    def _operator_float_attr(self, name: str, *, fallback: str | None = None) -> float | None:
        value = getattr(self, name, None)
        if value is None and fallback:
            value = getattr(self, fallback, None)
        return self._operator_float_or_none(value)

    @staticmethod
    def _operator_float_or_none(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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
        if self._operator_plan_requires_confirmation(plan):
            self._operator_set_pending_confirm_plan(plan)
            self._operator_scene_override = "confirm"
            text = "等待安全确认，请说“确认执行”或点击确认执行。"
            detail = self._operator_confirm_detail_text()
            chat_text = detail if detail and detail != "当前没有需要确认的风险。" else text
            self._set_nlp_execute_busy(False)
            self.status_label.setText(text)
            self._operator_add_chat_message("assistant", chat_text, kind="warn")
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
            self._operator_publish_ai_answer_for_speech(text)
            self._append_log("自然语言", "未识别动作", "失败", text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="unknown", final_text=text)
            self._refresh_operator_view()
            return True
        first_type = str(getattr(actions[0], "action_type", "") or "")
        semantic_level = int(getattr(plan, "semantic_level", 0) or 0)
        if self._operator_handle_llm_context_intent_plan(plan):
            return True
        if first_type == "compound_plan":
            draft = getattr(plan, "flow_draft", {}) or {}
            steps = tuple(draft.get("steps", ()) or ()) if isinstance(draft, dict) else ()
            step_lines = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))
            step_machine_text = self._operator_compound_step_machine_text(draft)
            reason = str(getattr(plan, "reason", "") or f"已生成复合指令草案：{len(steps)} 步。")
            safe_to_execute = bool(draft.get("safe_to_execute")) if isinstance(draft, dict) else False
            if safe_to_execute:
                self._operator_pending_flow_draft = dict(draft)
            text = reason
            if step_machine_text:
                text = f"{text}\n{step_machine_text}"
            if step_lines:
                text = f"{reason}\n{step_lines}"
                if step_machine_text:
                    text = f"{reason}\n{step_machine_text}\n{step_lines}"
            if safe_to_execute:
                text = f"{text}\n已生成可执行复合流程草案。可说“确认执行”开始逐步执行，或说“取消草案”。"
            else:
                text = f"{text}\n当前不会自动执行复合计划，请拆成单条指令逐条确认。"
            self._set_nlp_execute_busy(False)
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(reason))
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text)
            if hasattr(self, "_operator_publish_ai_answer_for_speech"):
                self._operator_publish_ai_answer_for_speech(text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="compound_plan_draft", final_text=text)
            if hasattr(self, "_append_log"):
                self._append_log("Agent", "复合指令草案", "提示", text)
            self._refresh_operator_view()
            return True
        if first_type == "agent_blocked":
            reason = str(getattr(plan, "reason", "") or getattr(actions[0], "reason", "") or "Agent 安全预检未通过。").strip()
            failed_messages: list[str] = []
            flow_draft = getattr(plan, "flow_draft", {}) or {}
            if isinstance(flow_draft, dict):
                precheck = flow_draft.get("precheck_result") or {}
                if isinstance(precheck, dict):
                    for item in tuple(precheck.get("items", ()) or ()):
                        if isinstance(item, dict) and item.get("status") == "fail":
                            message = str(item.get("message") or "").strip()
                            if message:
                                failed_messages.append(message)
            detail = "；".join(failed_messages[:3])
            text = reason if not detail else f"{reason}失败项：{detail}"
            self._set_nlp_execute_busy(False)
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(text))
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text, kind="warn")
            if hasattr(self, "_operator_publish_ai_answer_for_speech"):
                self._operator_publish_ai_answer_for_speech(text)
            if hasattr(self, "_operator_archive_execution_result"):
                self._operator_archive_execution_result(result="blocked", final_text=text)
            if hasattr(self, "_append_log"):
                self._append_log("Agent", "安全预检阻断", "阻断", text)
            self._refresh_operator_view()
            return True
        if first_type in {"unknown", "chat"} and semantic_level == 1:
            self._operator_update_flow_creation_followup_state(plan)
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
            self._operator_publish_ai_answer_for_speech(text)
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
            if isinstance(draft, dict) and draft:
                preview = self._operator_flow_draft_preview_text(draft, include_params=True)
                text = f"{reason}\n{preview}"
            else:
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

    def _operator_handle_llm_context_intent_plan(self, plan) -> bool:
        intent = self._operator_llm_context_intent_from_plan(plan)
        if not intent:
            return False
        kind = str(intent.get("kind") or "").strip()
        if kind == "flow_create":
            return self._operator_apply_llm_flow_create_intent(intent)
        if kind == "flow_append_step":
            return self._operator_apply_llm_flow_append_step_intent(intent)
        if kind == "flow_modify_step":
            return self._operator_apply_llm_flow_modify_step_intent(intent)
        if kind == "flow_list":
            return self._operator_apply_llm_flow_list_intent(intent)
        if kind == "flow_query":
            return self._operator_apply_llm_flow_query_intent(intent)
        if kind == "confirm_modify":
            return self._operator_apply_llm_confirm_modify_intent(intent)
        if kind == "dashboard_query":
            return self._operator_apply_llm_dashboard_query_intent(intent)
        if kind == "command_candidate":
            return self._operator_apply_llm_command_candidate_intent(intent, plan)
        if kind == "suggestion":
            return self._operator_apply_llm_suggestion_intent(intent)
        return False

    @staticmethod
    def _operator_llm_context_intent_from_plan(plan) -> dict[str, Any]:
        draft = getattr(plan, "flow_draft", {}) or {}
        if not isinstance(draft, dict):
            return {}
        intent = draft.get("llm_context_intent")
        return dict(intent) if isinstance(intent, dict) else {}

    def _operator_apply_llm_flow_create_intent(self, intent: dict[str, Any]) -> bool:
        flow_name = str(intent.get("flow_name") or intent.get("target_flow") or "").strip()
        if not flow_name:
            reply = str(intent.get("suggested_reply") or "请先告诉我要创建的流程名称。").strip()
            self._operator_publish_context_intent_reply(reply, category="流程创建理解")
            return True
        draft = {
            "flow_name": flow_name,
            "expanded_steps": [],
            "positions": self._operator_position_registry_draft_items(),
            "needs_precheck": True,
        }
        self._operator_pending_flow_draft = draft
        reply = str(intent.get("suggested_reply") or "").strip()
        if not reply:
            reply = f"已开始创建流程“{flow_name}”。"
        reply = self._operator_flow_create_guidance_text(flow_name, reply)
        self._operator_publish_context_intent_reply(reply, category="流程创建理解")
        return True

    @staticmethod
    def _operator_flow_create_guidance_text(flow_name: str, reply: str) -> str:
        clean = str(reply or "").strip() or f"已开始创建流程“{flow_name}”。"
        if "怎么添加步骤" in clean and "保存并执行" in clean:
            return clean
        guidance = (
            "\n\n怎么添加步骤：继续说每一步要做什么，系统会先生成草案，不会直接执行。"
            "\n例如："
            "\n1. 移动到位置A，X100 Y0 Z800，速度50%"
            "\n2. 等待2秒"
            "\n3. 输出1打开"
            "\n添加完后可以说“查看流程”，确认没问题再说“保存并执行”。"
        )
        return f"{clean}{guidance}"

    def _operator_apply_llm_flow_append_step_intent(self, intent: dict[str, Any]) -> bool:
        flow_name = str(intent.get("target_flow") or intent.get("flow_name") or "").strip()
        if not flow_name:
            current = str(getattr(self, "current_flow_name", "") or "").strip()
            if current:
                flow_name = current
        step_hint = str(intent.get("step_hint") or intent.get("text") or intent.get("suggested_step") or "").strip()
        if not flow_name or not step_hint:
            return False
        local_text = f"在{flow_name}流程后面添加一步{step_hint}"
        if self._operator_handle_saved_flow_edit_request(local_text):
            return True
        reply = str(intent.get("suggested_reply") or "").strip()
        if reply:
            self._operator_publish_context_intent_reply(reply, category="流程编辑理解")
            return True
        return False

    def _operator_apply_llm_flow_modify_step_intent(self, intent: dict[str, Any]) -> bool:
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(draft, dict) or not draft:
            reply = str(intent.get("suggested_reply") or "当前没有正在编辑的流程草案，请先创建或选择流程。").strip()
            self._operator_publish_context_intent_reply(reply, category="流程编辑理解")
            return True
        step_index = self._operator_llm_step_index(intent)
        field = str(intent.get("field") or intent.get("param") or "").strip().lower()
        value_text = str(intent.get("value_text") or intent.get("value") or "").strip()
        text = self._operator_flow_modify_text_from_llm_field(step_index, field, value_text)
        if text and self._operator_handle_pending_flow_draft_edit(text):
            return True
        reply = str(intent.get("suggested_reply") or "已理解为流程修改，但还缺少步骤编号、参数名或参数值。").strip()
        self._operator_publish_context_intent_reply(reply, category="流程编辑理解")
        return True

    def _operator_apply_llm_flow_query_intent(self, intent: dict[str, Any]) -> bool:
        flow_name = str(intent.get("target_flow") or intent.get("flow_name") or "").strip()
        if not flow_name:
            flow_name = str(getattr(self, "current_flow_name", "") or "").strip()
        if not flow_name:
            return False
        draft = self._operator_flow_draft_from_saved_flow(flow_name)
        if draft is None:
            reply = f"没有找到流程“{flow_name}”。"
            self._operator_publish_context_intent_reply(reply, category="流程查询")
            return True
        text = self._operator_flow_draft_preview_text(draft, include_params=True).replace(
            "当前待确认流程草案：",
            "流程 ",
            1,
        )
        self._operator_publish_context_intent_reply(text, category="流程查询")
        return True

    def _operator_apply_llm_flow_list_intent(self, intent: dict[str, Any]) -> bool:
        return self._operator_handle_flow_list_query(str(intent.get("query_text") or intent.get("text") or "有哪些流程"))

    def _operator_apply_llm_confirm_modify_intent(self, intent: dict[str, Any]) -> bool:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if plan is None:
            return False
        field = str(intent.get("field") or intent.get("param") or "").strip().lower()
        value_text = str(intent.get("value_text") or intent.get("value") or "").strip()
        text = self._operator_confirm_modify_text_from_llm_field(field, value_text)
        if text and self._operator_handle_pending_confirm_modify(text):
            return True
        reply = str(intent.get("suggested_reply") or "").strip()
        if reply:
            self._operator_publish_context_intent_reply(reply, category="确认阶段理解")
            return True
        return False

    def _operator_apply_llm_dashboard_query_intent(self, intent: dict[str, Any]) -> bool:
        query_text = str(intent.get("query_text") or intent.get("question") or intent.get("text") or "").strip()
        if not query_text:
            query_text = str(intent.get("suggested_reply") or "").strip()
        if query_text and self._operator_handle_dashboard_query(query_text):
            return True
        reply = str(intent.get("suggested_reply") or "当前状态查询无法直接匹配，请换一种问法，例如“现在急停状态怎么样”。").strip()
        self._operator_publish_context_intent_reply(reply, category="状态查询理解")
        return True

    def _operator_apply_llm_command_candidate_intent(self, intent: dict[str, Any], source_plan) -> bool:
        candidate_text = str(intent.get("candidate_text") or intent.get("text") or "").strip()
        if not candidate_text:
            reply = str(intent.get("suggested_reply") or "已识别为候选控制指令，但缺少可重新解析的命令文本。").strip()
            self._operator_publish_context_intent_reply(reply, category="候选指令理解")
            return True
        raw_text = str(getattr(source_plan, "raw_text", "") or "").strip()
        if not raw_text:
            actions = tuple(getattr(source_plan, "actions", ()) or ())
            if actions:
                raw_text = str(getattr(actions[0], "raw_text", "") or "")
        if not self._operator_is_wake_command(raw_text):
            reply = "候选控制指令缺少“小正或小兵”唤醒词，未执行。请带唤醒词重新下发生产指令。"
            self._operator_publish_context_intent_reply(reply, category="候选指令唤醒词校验")
            return True
        candidate_plan = self._operator_try_agent_orchestrator_plan(candidate_text)
        if candidate_plan is None:
            reply = str(intent.get("suggested_reply") or "已识别为候选控制指令，但本地安全解析未通过，请补充目标和参数。").strip()
            self._operator_publish_context_intent_reply(reply, category="候选指令解析")
            return True
        self._execute_nlp_plan(candidate_plan)
        return True

    def _operator_apply_llm_suggestion_intent(self, intent: dict[str, Any]) -> bool:
        reply = str(intent.get("suggested_reply") or intent.get("text") or "").strip()
        if not reply:
            reply = "已收到你的需求。建议先明确要查询、修改流程，还是执行控制指令。"
        self._operator_publish_context_intent_reply(reply, category="上下文建议")
        return True

    @staticmethod
    def _operator_confirm_modify_text_from_llm_field(field: str, value_text: str) -> str:
        if not value_text:
            return ""
        aliases = {
            "spd": "速度",
            "speed": "速度",
            "spd_pct": "速度",
            "acc": "加速度",
            "accel": "加速度",
            "acceleration": "加速度",
            "acc_pct": "加速度",
            "dec": "减速度",
            "decel": "减速度",
            "deceleration": "减速度",
            "dec_pct": "减速度",
            "step": "步长",
            "pos_val": "步长",
        }
        label = aliases.get(field)
        if not label:
            return ""
        return f"{label}改为{value_text}"

    @staticmethod
    def _operator_llm_step_index(intent: dict[str, Any]) -> int:
        raw = intent.get("step_index")
        if raw is None:
            raw = intent.get("step_no")
        if raw is None:
            raw = intent.get("step")
        return OperatorUiMixin._operator_parse_step_index_text(str(raw or ""))

    @staticmethod
    def _operator_flow_modify_text_from_llm_field(step_index: int, field: str, value_text: str) -> str:
        if step_index <= 0 or not value_text:
            return ""
        aliases = {
            "spd": "速度",
            "speed": "速度",
            "spd_pct": "速度",
            "acc": "加速度",
            "accel": "加速度",
            "acceleration": "加速度",
            "acc_pct": "加速度",
            "dec": "减速度",
            "decel": "减速度",
            "deceleration": "减速度",
            "dec_pct": "减速度",
            "delay": "延时",
            "delay_sec": "延时",
        }
        label = aliases.get(field)
        if not label:
            return ""
        return f"第{step_index}步{label}改成{value_text}"

    def _operator_publish_context_intent_reply(self, text: str, *, category: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        self._set_nlp_execute_busy(False)
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(clean))
        if getattr(self, "_operator_streaming_chat_active", False):
            self._operator_finish_streaming_chat_response(clean)
        elif hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", clean)
        if hasattr(self, "_operator_publish_ai_answer_for_speech"):
            self._operator_publish_ai_answer_for_speech(clean)
        if hasattr(self, "_append_log"):
            self._append_log("Agent", category, "成功", clean)
        if hasattr(self, "_operator_archive_execution_result"):
            self._operator_archive_execution_result(result="context_intent", final_text=clean)
        self._refresh_operator_view()

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
        prepared_text = self._operator_prepare_pending_flow_creation_followup_text(text)
        if prepared_text != text and hasattr(self, "nlp_input_edit") and hasattr(self.nlp_input_edit, "setPlainText"):
            self.nlp_input_edit.setPlainText(prepared_text)
        text = prepared_text
        if self._handle_operator_ui_command(text):
            return
        if self._operator_reject_new_action_while_busy(text):
            return
        processing_hint_started = self._operator_maybe_begin_agent_processing_response(text)
        if processing_hint_started and self._operator_schedule_agent_plan_background(text, mode="parse"):
            return
        agent_plan = self._operator_try_agent_orchestrator_plan(text)
        if agent_plan is not None:
            self._set_nlp_parse_busy(True)
            self._set_nlp_result_plan(agent_plan)
            first_action = agent_plan.actions[0] if agent_plan.actions else SimpleNamespace(action_type="unknown", target=None)
            if hasattr(self, "status_label"):
                self.status_label.setText(
                    f"解析完成: {len(agent_plan.actions)} 步 / {first_action.action_type} / {first_action.target or '-'}"
                )
            if hasattr(self, "_append_log"):
                self._append_log(
                    "自然语言",
                    "解析文本",
                    "成功" if agent_plan.actions and agent_plan.actions[0].action_type != "unknown" else "失败",
                    f"{agent_plan.source} | {len(agent_plan.actions)}步 | {agent_plan.reason}",
                )
            self._set_nlp_parse_busy(False)
            return
        if processing_hint_started:
            self._operator_cancel_streaming_chat_response()
        super()._parse_nlp_text()

    def _execute_nlp_text(self) -> None:
        text = self.nlp_input_edit.toPlainText().strip() if hasattr(self, "nlp_input_edit") else ""
        prepared_text = self._operator_prepare_pending_flow_creation_followup_text(text)
        if prepared_text != text and hasattr(self, "nlp_input_edit") and hasattr(self.nlp_input_edit, "setPlainText"):
            self.nlp_input_edit.setPlainText(prepared_text)
        text = prepared_text
        if self._handle_operator_ui_command(text):
            return
        if self._operator_reject_new_action_while_busy(text):
            return
        self._operator_set_pending_confirm_plan(None)
        processing_hint_started = self._operator_maybe_begin_agent_processing_response(text)
        if processing_hint_started and self._operator_schedule_agent_plan_background(text, mode="execute"):
            return
        agent_plan = self._operator_try_agent_orchestrator_plan(text)
        if agent_plan is not None:
            self._set_nlp_execute_busy(True)
            self._set_nlp_result_plan(agent_plan)
            self._execute_nlp_plan(agent_plan)
            return
        if processing_hint_started:
            self._operator_cancel_streaming_chat_response()
        super()._execute_nlp_text()

    def _operator_schedule_agent_plan_background(self, text: str, *, mode: str) -> bool:
        runner = getattr(self, "_run_in_background", None)
        if not callable(runner):
            return False
        if getattr(self, "_operator_agent_parse_running", False):
            return False
        self._operator_agent_parse_running = True
        if mode == "parse":
            self._set_nlp_parse_busy(True)
        else:
            self._set_nlp_execute_busy(True)

        def work():
            return self._operator_try_agent_orchestrator_plan(text)

        def done(result) -> None:
            self._operator_agent_parse_running = False
            if isinstance(result, BackgroundTaskError):
                if hasattr(self, "_append_log"):
                    self._append_log("Agent", "后台解析", "失败", str(result))
                result = None
            self._operator_apply_agent_plan_background_result(result, mode=mode)

        runner(work, done)
        return True

    def _operator_apply_agent_plan_background_result(self, agent_plan, *, mode: str) -> None:
        if agent_plan is None:
            self._operator_cancel_streaming_chat_response()
            if mode == "parse":
                self._set_nlp_parse_busy(False)
                super()._parse_nlp_text()
            else:
                self._set_nlp_execute_busy(False)
                super()._execute_nlp_text()
            return
        self._set_nlp_result_plan(agent_plan)
        if mode == "parse":
            first_action = agent_plan.actions[0] if agent_plan.actions else SimpleNamespace(action_type="unknown", target=None)
            if hasattr(self, "status_label"):
                self.status_label.setText(
                    f"解析完成: {len(agent_plan.actions)} 步 / {first_action.action_type} / {first_action.target or '-'}"
                )
            if hasattr(self, "_append_log"):
                self._append_log(
                    "自然语言",
                    "解析文本",
                    "成功" if agent_plan.actions and agent_plan.actions[0].action_type != "unknown" else "失败",
                    f"{agent_plan.source} | {len(agent_plan.actions)}步 | {agent_plan.reason}",
                )
            self._set_nlp_parse_busy(False)
            return
        self._execute_nlp_plan(agent_plan)

    def _operator_try_restricted_agent_plan(self, text: str):
        if not self._operator_should_try_restricted_agent(text):
            return None
        try:
            service = self._operator_restricted_agent_service()
            result = service.parse(text)
            from .agent.plan_adapter import AgentPlanAdapter

            return AgentPlanAdapter().to_voice_plan(result)
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("Agent", "受限Agent解析", "失败", str(exc))
            return None

    def _operator_try_agent_orchestrator_plan(self, text: str):
        try:
            from .agent.atomic_template import AtomicTemplateAgent
            from .agent.chat_explanation import ChatExplanationAgent
            from .agent.dashboard_query import DashboardQueryAgent
            from .agent.flow_draft import FlowDraftAgent
            from .agent.llm_fallback import LlmFallbackAgent
            from .agent.memory_setting import MemorySettingAgent
            from .agent.orchestrator import AgentOrchestrator
            from .agent.plan_adapter import AgentPlanAdapter
            from .agent.position_memory import PositionMemoryAgent
            from .agent.position_query import PositionQueryAgent
            from .agent.registered_flow import RegisteredFlowAgent

            restricted_service = None
            if self._operator_restricted_agent_enabled():
                restricted_service = self._operator_restricted_agent_service()
            result = AgentOrchestrator(
                restricted_service=restricted_service,
                chat_agent=ChatExplanationAgent(),
                position_query_agent=PositionQueryAgent(lookup=self._operator_agent_position_lookup),
                memory_setting_agent=self._operator_agent_memory_setting_agent(MemorySettingAgent),
                position_memory_agent=PositionMemoryAgent(),
                atomic_template_agent=self._operator_agent_atomic_template_agent(AtomicTemplateAgent),
                dashboard_query_agent=DashboardQueryAgent(),
                flow_draft_agent=self._operator_agent_flow_draft_agent(FlowDraftAgent),
                registered_flow_agent=self._operator_agent_registered_flow_agent(RegisteredFlowAgent),
                llm_fallback_agent=self._operator_agent_llm_fallback_agent(LlmFallbackAgent),
                llm_fallback_enabled=self._operator_agent_llm_fallback_enabled(),
            ).handle(text)
            if result.kind == "fallback_legacy":
                self._operator_log_agent_orchestrator_fallback(result)
                return None
            if result.kind in {"restricted_agent", "compound_plan_draft", "unsupported_compound"}:
                return AgentPlanAdapter().to_voice_plan(result.payload)
            return AgentPlanAdapter().to_voice_plan(result)
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("Agent", "统一Agent解析", "失败", str(exc))
            return None

    def _operator_restricted_agent_enabled(self) -> bool:
        return bool(getattr(getattr(self, "axis_ranges", None), "restricted_agent_enabled", False))

    def _operator_agent_llm_fallback_enabled(self) -> bool:
        check = getattr(self, "nlp_use_deepseek_check", None)
        if check is None or not hasattr(check, "isChecked"):
            return False
        try:
            return bool(check.isChecked()) and getattr(self, "_deepseek_client", None) is not None
        except Exception:
            return False

    def _operator_agent_llm_fallback_agent(self, agent_cls):
        client = getattr(self, "_deepseek_client", None)
        if client is None:
            return None
        return agent_cls(client=client, context_provider=self._operator_deepseek_runtime_context)

    def _operator_log_agent_orchestrator_fallback(self, result) -> None:
        if not hasattr(self, "_append_log"):
            return
        payload = getattr(result, "payload", None) or {}
        understanding = payload.get("understanding") or {}
        detail = (
            f"reason={payload.get('reason', '')}; "
            f"intent={understanding.get('intent', '')}; "
            f"func_id={understanding.get('func_id')}; "
            f"confidence={understanding.get('confidence', 0.0)}; "
            f"needs_model={payload.get('needs_model', False)}; "
            f"clarification={understanding.get('clarification', '')}"
        )
        self._append_log("Agent", "统一Agent交回旧路径", "提示", detail)

    @staticmethod
    def _operator_compound_step_machine_text(draft) -> str:
        if not isinstance(draft, dict):
            return ""
        machine = draft.get("step_machine")
        if not isinstance(machine, dict):
            return ""
        status = str(machine.get("status") or "")
        current_text = str(machine.get("current_step_text") or "").strip()
        steps = tuple(machine.get("steps") or ())
        total = len(steps)
        current_index = int(machine.get("current_index") or 0)
        if status == "waiting_step_confirmation" and current_text and total > 0:
            return f"当前等待确认第 {current_index + 1}/{total} 步：{current_text}"
        if status in {"blocked", "failed"}:
            reason = str(machine.get("reason") or "复合计划已停止。").strip()
            return reason
        if status == "completed":
            return "复合计划所有步骤已完成。"
        return ""

    def _operator_agent_memory_setting_agent(self, agent_cls):
        memory = getattr(self, "_atomic_memory", None)
        if memory is None:
            return None
        return agent_cls(memory=memory, save_callback=lambda _memory: self._save_atomic_memory())

    def _operator_agent_atomic_template_agent(self, agent_cls):
        memory = getattr(self, "_atomic_memory", None)
        if memory is None:
            return None
        try:
            memory.position_registry = self._position_registry()
        except Exception:
            pass
        return agent_cls(memory=memory)

    def _operator_agent_flow_draft_agent(self, agent_cls):
        parse_func = getattr(self, "_operator_agent_flow_draft_parse", None)
        if callable(parse_func):
            return agent_cls(parse_func=parse_func)
        build_adapter = getattr(self, "_build_voice_nlp_adapter", None)
        if not callable(build_adapter):
            return None

        def parse(text: str):
            return build_adapter().parse(text, use_deepseek=False)

        return agent_cls(parse_func=parse)

    def _operator_agent_registered_flow_agent(self, agent_cls):
        parse_func = getattr(self, "_operator_agent_registered_flow_parse", None)
        if callable(parse_func):
            return agent_cls(parse_func=parse_func)
        build_adapter = getattr(self, "_build_voice_nlp_adapter", None)
        if not callable(build_adapter):
            return None

        def parse(text: str):
            return build_adapter().parse(text, use_deepseek=False)

        return agent_cls(parse_func=parse)

    def _operator_agent_position_lookup(self, name: str):
        if not hasattr(self, "_position_registry"):
            return None
        registry = self._position_registry()
        entry = registry.get(name) if registry is not None and hasattr(registry, "get") else None
        return getattr(entry, "pose", None) if entry is not None else None

    def _operator_should_try_restricted_agent(self, text: str) -> bool:
        if not bool(getattr(getattr(self, "axis_ranges", None), "restricted_agent_enabled", False)):
            return False
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if compact in {"急停", "暂停", "继续", "恢复", "报警复位", "复位", "取消当前动作", "取消当前任务", "停止当前动作", "停止当前任务"}:
            return True
        if "报警" in compact and any(word in compact for word in ("什么", "查询", "说明", "原因", "当前", "状态")):
            return True
        if any(phrase in compact for phrase in ("为什么不能动", "为何不能动", "怎么不能动", "不能动了吗")):
            return True
        if any(phrase in compact for phrase in ("运动完成了吗", "执行完成了吗", "完成了吗", "结束了吗")):
            return True
        if any(word in compact for word in ("当前状态", "系统状态", "设备状态", "运行状态", "现在状态")):
            return True
        if re.search(r"(?:等待|延时|暂停)\d+(?:\.\d+)?(?:秒|s|毫秒|ms)", compact, flags=re.IGNORECASE):
            return True
        if re.search(r"(?:io|IO|输出|y|Y)\d+(?:开|打开|关|关闭|on|off)", compact, flags=re.IGNORECASE):
            return True
        if re.search(r"J[1-6](?:转到|到|绝对|正转|反转|负转|逆时针|回退)-?\d+(?:\.\d+)?(?:度|°)?", compact, flags=re.IGNORECASE):
            return True
        if re.search(r"(?:RX|RY|RZ|rx|ry|rz)(?:正转|反转|负转|逆时针|回退)-?\d+(?:\.\d+)?(?:度|°)?", compact, flags=re.IGNORECASE):
            return True
        wake_words = tuple(word for word in configured_wake_words() if word)
        has_wake_word = any(word in compact for word in wake_words)
        if has_wake_word and re.search(
            r"(?:前进|后退|左移|右移|上升|下降|向前|向后|向左|向右|向上|向下)(?:移动)?-?\d+(?:\.\d+)?(?:毫米|mm)?",
            compact,
            flags=re.IGNORECASE,
        ):
            return True
        if re.search(r"(?:向左|左移|向右|右移|向前|前进|向后|后退|升高|下降|降低|向上|向下)(?:移动)?-?\d+(?:\.\d+)?(?:毫米|mm)?", compact, flags=re.IGNORECASE):
            return True
        return bool(re.search(r"(?:RX|RY|RZ|X|Y|Z)-?\d+(?:\.\d+)?", compact, flags=re.IGNORECASE))

    def _operator_restricted_agent_service(self):
        service = getattr(self, "_restricted_agent_service", None)
        if service is not None:
            return service
        from .agent.parameter_completion import ControllerSnapshot
        from .agent.pose_angle import PoseAngleSafetyChecker
        from .agent.safety_review import SafetyReviewAgent
        from .agent.service import RestrictedAgentService

        def controller_snapshot_provider() -> ControllerSnapshot:
            pose = self._operator_current_pose_tuple()
            axis_ranges = getattr(self, "axis_ranges", None)
            return ControllerSnapshot(
                current_pose={
                    "target_x": float(pose[0]),
                    "target_y": float(pose[1]),
                    "target_z": float(pose[2]),
                    "target_rx": float(pose[3]),
                    "target_ry": float(pose[4]),
                    "target_rz": float(pose[5]),
                },
                safety_params={
                    "spd_pct": float(getattr(axis_ranges, "safe_speed_max", 50.0) or 50.0),
                    "acc_pct": float(getattr(axis_ranges, "safe_acc_max", 50.0) or 50.0),
                    "dec_pct": float(getattr(axis_ranges, "safe_dec_max", 50.0) or 50.0),
                },
                is_moving=self._operator_restricted_agent_is_moving(),
                read_ok=True,
            )

        service = RestrictedAgentService(
            controller_snapshot_provider=controller_snapshot_provider,
            runtime_snapshot_provider=lambda: self._operator_dashboard_snapshot_dict(refresh=True),
            safety_review_agent=SafetyReviewAgent(
                l1_service=SafetyPrecheckService(self.axis_ranges, max_sphere_radius=0.0),
                motion_plan_service=self._operator_agent_motion_plan_service(),
                pose_angle_checker=PoseAngleSafetyChecker(self._operator_agent_pose_angle_limits()),
            ),
            status_signature_provider=self._operator_restricted_agent_status_signature,
            safety_signature_provider=self._operator_restricted_agent_safety_signature,
            clock=self._operator_now_seconds,
            confirm_timeout_sec=self._operator_confirm_timeout_seconds(),
            start_pose_provider=self._operator_current_pose_tuple,
        )
        self._restricted_agent_service = service
        return service

    def _operator_agent_motion_plan_service(self) -> MotionPlanService:
        return MotionPlanService(
            engine=getattr(self, "operator_kinematics_engine", None),
            joint_limits=tuple(getattr(getattr(self, "axis_ranges", None), "joint_limits", ()) or ()),
            progress_callback=getattr(self, "_operator_publish_l2_progress", None),
        )

    def _operator_agent_pose_angle_limits(self) -> dict[str, float]:
        values = getattr(self, "controller_pose_angle_limits", None)
        if isinstance(values, dict) and values:
            return {
                "pose_upper_angle": self._operator_float_or_none(values.get("pose_upper_angle")) or 90.0,
                "pose_lower_angle": self._operator_float_or_none(values.get("pose_lower_angle")) or 90.0,
                "pose_cw_angle": self._operator_float_or_none(values.get("pose_cw_angle")) or 90.0,
                "pose_ccw_angle": self._operator_float_or_none(values.get("pose_ccw_angle")) or 90.0,
            }
        return {
            "pose_upper_angle": 90.0,
            "pose_lower_angle": 90.0,
            "pose_cw_angle": 90.0,
            "pose_ccw_angle": 90.0,
        }

    def _operator_restricted_agent_is_moving(self) -> bool:
        motion_percent = str(getattr(self, "motion_percent", "") or "")
        busy = str(getattr(self, "busy", "") or "")
        run_state = str(getattr(self, "run_state", "") or "")
        return bool(
            motion_percent == "运动中"
            or busy == "运行中"
            or run_state == "运行中"
            or getattr(self, "nlp_sequence_running", False)
            or getattr(self, "flow_running", False)
        )

    def _operator_restricted_agent_status_signature(self) -> str:
        return "|".join(
            str(value)
            for value in (
                getattr(self, "busy", ""),
                getattr(self, "motion_percent", ""),
                getattr(self, "current_func_text", ""),
                getattr(self, "alarm_code", ""),
                getattr(self, "estop_active", ""),
                getattr(self, "pause_active", ""),
            )
        )

    def _operator_restricted_agent_safety_signature(self) -> str:
        axis_ranges = getattr(self, "axis_ranges", None)
        return "|".join(
            str(value)
            for value in (
                getattr(axis_ranges, "safe_speed_max", ""),
                getattr(axis_ranges, "safe_acc_max", ""),
                getattr(axis_ranges, "safe_dec_max", ""),
                getattr(axis_ranges, "safe_r_min", ""),
                getattr(axis_ranges, "safe_r_max", ""),
                getattr(axis_ranges, "safe_z_min", ""),
                getattr(axis_ranges, "safe_z_max", ""),
            )
        )

    def _operator_update_flow_creation_followup_state(self, plan) -> None:
        raw_text = str(getattr(plan, "raw_text", "") or "")
        reason = str(getattr(plan, "reason", "") or "")
        if self._operator_text_asks_to_create_flow(raw_text) or self._operator_text_requests_flow_details(reason):
            self._operator_pending_flow_creation_followup = True

    def _operator_prepare_pending_flow_creation_followup_text(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean or not bool(getattr(self, "_operator_pending_flow_creation_followup", False)):
            return clean
        compact = re.sub(r"\s+", "", clean)
        if not self._operator_text_looks_like_flow_detail_answer(compact):
            return clean
        self._operator_pending_flow_creation_followup = False
        if any(wake_word and wake_word in compact for wake_word in configured_wake_words()):
            return clean
        return f"小正，创建流程，{clean}"

    @staticmethod
    def _operator_text_asks_to_create_flow(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(keyword in compact for keyword in ("创建流程", "新建流程", "创建新草案", "创建草案", "新建草案", "我要创建流程", "我要创建新草案"))

    @staticmethod
    def _operator_text_requests_flow_details(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(keyword in compact for keyword in ("请描述流程", "流程内容", "流程步骤", "有哪些步骤", "请提供以下信息", "草案名称", "步骤顺序"))

    @staticmethod
    def _operator_text_looks_like_flow_detail_answer(compact: str) -> bool:
        text = str(compact or "")
        if not text:
            return False
        if OperatorUiMixin._operator_text_looks_like_flow_draft_detail_answer(text):
            return True
        has_flow = "流程" in text or "草案" in text
        has_sequence = any(keyword in text for keyword in ("先", "然后", "再", "最后", "接着", "之后"))
        has_motion = any(keyword in text for keyword in ("移动", "位置", "抓取", "放置", "点头", "打开", "关闭"))
        return has_flow and has_sequence and has_motion

    def _clear_nlp_text(self) -> None:
        super()._clear_nlp_text()
        if hasattr(self, "operator_command_edit"):
            self.operator_command_edit.clear()
        self._operator_scene_override = None
        self._operator_set_pending_confirm_plan(None)
        self._operator_pending_interruption_text = ""
        self._refresh_operator_view()

    def _operator_parse_text(self) -> None:
        if not self._operator_push_text_to_nlp():
            return
        self._operator_scene_override = None
        self._parse_nlp_text()

    def _operator_execute_text(self) -> None:
        text = self.operator_command_edit.text().strip() if hasattr(self, "operator_command_edit") else ""
        if not text and hasattr(self, "nlp_input_edit"):
            text = self.nlp_input_edit.toPlainText().strip()
        if not self._operator_submit_nlp_text(text, input_mode="text", add_user_message=True):
            return
        if hasattr(self, "operator_command_edit"):
            self.operator_command_edit.clear()
        if hasattr(self, "nlp_input_edit"):
            self.nlp_input_edit.clear()

    def _operator_clear_text(self) -> None:
        self._clear_nlp_text()

    def _operator_toggle_microphone_recording(self) -> None:
        starting = not bool(getattr(self, "_voice_session_active", False))
        started_at_sec = self._operator_now_seconds()
        if starting:
            self._start_voice_session()
        else:
            self._stop_voice_session()
        if starting:
            delay_ms = self._operator_elapsed_ms_since(started_at_sec)
            self._operator_last_voice_receipt_delay_ms = delay_ms
            self._operator_last_voice_receipt_sla_passed = delay_ms <= self._operator_ack_limit_ms("voice")
        self._sync_operator_mic_button()

    def _operator_handle_voice_session_text(self, text: str) -> None:
        command = str(text or "").strip()
        if not command:
            return
        self._operator_execute_voice_session_text(command)

    def _operator_execute_voice_session_text(self, text: str) -> None:
        command = str(text or "").strip()
        if not command:
            return
        self._operator_submit_nlp_text(command, input_mode="voice", add_user_message=False)

    def _operator_submit_nlp_text(self, text: str, *, input_mode: str, add_user_message: bool) -> bool:
        command = str(text or "").strip()
        if not command:
            self._show_warning("输入为空", "请输入自然语言文本。")
            return False
        interrupter = getattr(self, "_operator_interrupt_current_speech_for_user_input", None)
        if callable(interrupter):
            interrupter()
        self._operator_scene_override = None
        if hasattr(self, "operator_voice_label"):
            label = "语音输入" if input_mode == "voice" else "文本输入"
            self.operator_voice_label.setText(f"{label}: {command}")
        if add_user_message and hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("user", command)
            self._operator_last_user_text = command
        archive = getattr(self, "_operator_archive_text_input", None)
        if callable(archive):
            archive(command)
        if not hasattr(self, "nlp_input_edit"):
            self._show_warning("输入为空", "自然语言输入控件未初始化。")
            return False
        self.nlp_input_edit.setPlainText(command)
        self._execute_nlp_text()
        self.nlp_input_edit.clear()
        return True

    def _operator_begin_voice_recognition_status(self) -> None:
        self._operator_voice_recognition_status_index = None
        self._operator_voice_recognition_status_label = None

    def _operator_update_voice_recognition_status(self, text: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        if not hasattr(self, "_operator_chat_messages"):
            self._operator_chat_messages = []
        if not hasattr(self, "_operator_chat_thinking_steps"):
            self._operator_chat_thinking_steps = [[] for _ in self._operator_chat_messages]
        if not hasattr(self, "_operator_chat_thinking_meta"):
            self._operator_chat_thinking_meta = [{} for _ in self._operator_chat_messages]
        index = getattr(self, "_operator_voice_recognition_status_index", None)
        if not isinstance(index, int) or index < 0 or index >= len(self._operator_chat_messages):
            self._operator_chat_messages.append(("user", clean))
            self._operator_chat_thinking_steps.append([])
            self._operator_chat_thinking_meta.append({"voice_recognition_status": True})
            self._operator_voice_recognition_status_index = len(self._operator_chat_messages) - 1
        else:
            self._operator_chat_messages[index] = ("user", clean)
            if index < len(self._operator_chat_thinking_steps):
                self._operator_chat_thinking_steps[index] = []
            if index < len(self._operator_chat_thinking_meta):
                self._operator_chat_thinking_meta[index] = {"voice_recognition_status": True}
            label = getattr(self, "_operator_voice_recognition_status_label", None)
            if label is not None and hasattr(label, "setText"):
                try:
                    label.setText(clean)
                    self._operator_last_user_text = clean
                    self._operator_chat_autoscroll_pending = True
                    self._operator_scroll_chat_to_bottom()
                    return
                except Exception:
                    self._operator_voice_recognition_status_label = None
        self._operator_last_user_text = clean
        self._operator_chat_autoscroll_pending = True
        self._render_operator_chat()

    def _operator_finish_voice_recognition_status(self, text: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            self._operator_clear_voice_recognition_status()
            return
        index = getattr(self, "_operator_voice_recognition_status_index", None)
        if isinstance(index, int) and 0 <= index < len(getattr(self, "_operator_chat_messages", [])):
            self._operator_chat_messages[index] = ("user", clean)
            if index < len(getattr(self, "_operator_chat_thinking_steps", [])):
                self._operator_chat_thinking_steps[index] = []
            if index < len(getattr(self, "_operator_chat_thinking_meta", [])):
                self._operator_chat_thinking_meta[index] = {}
            self._operator_voice_recognition_status_index = None
            self._operator_voice_recognition_status_label = None
            self._operator_last_user_text = clean
            self._operator_chat_autoscroll_pending = True
            self._render_operator_chat()
            return
        self._operator_voice_recognition_status_index = None
        self._operator_add_chat_message("user", clean)
        self._operator_last_user_text = clean

    def _operator_clear_voice_recognition_status(self) -> None:
        index = getattr(self, "_operator_voice_recognition_status_index", None)
        self._operator_voice_recognition_status_index = None
        self._operator_voice_recognition_status_label = None
        if not isinstance(index, int):
            return
        messages = getattr(self, "_operator_chat_messages", [])
        if index < 0 or index >= len(messages):
            return
        self._operator_chat_messages[index:index + 1] = []
        if hasattr(self, "_operator_chat_thinking_steps"):
            self._operator_chat_thinking_steps[index:index + 1] = []
        if hasattr(self, "_operator_chat_thinking_meta"):
            self._operator_chat_thinking_meta[index:index + 1] = []
        self._operator_chat_autoscroll_pending = True
        self._render_operator_chat()

    def _operator_render_voice_recognition_status(self) -> None:
        self._operator_chat_autoscroll_pending = True
        self._render_operator_chat()

    def _operator_voice_receipt_sla_result(self) -> dict[str, object]:
        delay_ms = int(getattr(self, "_operator_last_voice_receipt_delay_ms", 0) or 0)
        limit_ms = self._operator_ack_limit_ms("voice")
        return {
            "ack_delay_ms": delay_ms,
            "ack_limit_ms": limit_ms,
            "ack_sla_passed": delay_ms <= limit_ms,
        }

    def _operator_voice_recording_active(self) -> bool:
        if getattr(self, "_voice_session_active", False):
            return True
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
        if getattr(self, "_operator_executing_interruption_text", False):
            return False
        if not self._operator_execution_or_pause_active():
            return False
        if self._operator_is_wake_command(text):
            return self._operator_begin_busy_interruption(text)
        if getattr(self, "flow_running", False) and self._operator_handle_busy_chat_text(text):
            return True
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

    def _operator_is_wake_command(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(wake_word and wake_word in compact for wake_word in configured_wake_words())

    def _operator_handle_busy_chat_text(self, text: str) -> bool:
        command = str(text or "").strip()
        if not command:
            return False
        if self._operator_is_wake_command(command):
            return False
        use_deepseek = False
        if hasattr(self, "nlp_use_deepseek_check"):
            try:
                use_deepseek = bool(self.nlp_use_deepseek_check.isChecked())
            except Exception:
                use_deepseek = False
        chat_delta_callback = (
            self._operator_streaming_chat_delta_callback()
            if use_deepseek and hasattr(self, "_operator_streaming_chat_delta_callback")
            else None
        )
        if hasattr(self, "_operator_maybe_begin_streaming_chat_for_text"):
            if self._operator_maybe_begin_streaming_chat_for_text(command, use_deepseek=use_deepseek):
                messages = getattr(self, "_operator_chat_messages", [])
                metas = getattr(self, "_operator_chat_thinking_meta", [])
                if messages and messages[-1][0] == "assistant":
                    self._operator_busy_chat_stream_index = len(messages) - 1
                    if metas:
                        metas[-1]["busy_flow_chat"] = True
        if hasattr(self, "status_label"):
            self.status_label.setText("当前流程继续执行，正在处理闲聊。")

        def work():
            return self._build_voice_nlp_adapter().parse(
                command,
                use_deepseek=use_deepseek,
                chat_delta_callback=chat_delta_callback,
            )

        def on_result(result):
            if isinstance(result, Exception):
                text_out = f"闲聊处理失败：{result}"
                if hasattr(self, "status_label"):
                    self.status_label.setText(text_out)
                if hasattr(self, "_operator_add_chat_message"):
                    self._operator_add_chat_message("assistant", text_out)
                if hasattr(self, "_append_log"):
                    self._append_log("自然语言", "忙碌闲聊", "失败", str(result))
                return
            self._operator_reply_busy_chat_plan(result)

        runner = getattr(self, "_run_in_background", None)
        if callable(runner):
            runner(work, on_result)
        else:
            on_result(work())
        return True

    def _operator_reply_busy_chat_plan(self, plan) -> None:
        actions = tuple(getattr(plan, "actions", ()) or ())
        first_type = str(getattr(actions[0], "action_type", "") or "") if actions else ""
        reason = str(getattr(plan, "reason", "") or "").strip()
        if first_type == "chat":
            text = reason or "我在，当前流程会继续执行。"
            result = "chat"
        elif first_type == "query":
            if self._operator_answer_query_plan(plan):
                return
            text = reason or "当前流程继续执行，查询结果暂不可用。"
            result = "answered"
        else:
            text = reason or "当前流程正在执行。闲聊不会影响流程；如需切换新命令，请先说“小正或小兵”加具体指令。"
            if "生产指令缺少" in text:
                text = "当前流程正在执行。闲聊不会影响流程；如需切换新命令，请说“小正或小兵”加具体指令。"
            result = "chat"
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(text))
        if first_type == "chat" and self._operator_replace_busy_streaming_chat_response(text):
            pass
        elif hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", text)
        self._operator_publish_ai_answer_for_speech(text)
        if hasattr(self, "_operator_archive_execution_result"):
            self._operator_archive_execution_result(result=result, final_text=text)
        if hasattr(self, "_append_log"):
            self._append_log("自然语言", "忙碌闲聊", "成功", text)
        if hasattr(self, "_operator_schedule_refresh"):
            self._operator_schedule_refresh()

    def _operator_replace_busy_streaming_chat_response(self, text: str) -> bool:
        clean_text = str(text or "").strip()
        if not clean_text:
            return False
        if getattr(self, "_operator_streaming_chat_active", False):
            self._operator_finish_streaming_chat_response(clean_text)
            return True
        index = getattr(self, "_operator_busy_chat_stream_index", None)
        messages = getattr(self, "_operator_chat_messages", None)
        if not isinstance(index, int) or not isinstance(messages, list):
            return False
        if index < 0 or index >= len(messages):
            self._operator_busy_chat_stream_index = None
            return False
        role, _old_text = messages[index]
        if role != "assistant":
            self._operator_busy_chat_stream_index = None
            return False
        messages[index] = ("assistant", clean_text)
        steps = getattr(self, "_operator_chat_thinking_steps", None)
        if isinstance(steps, list):
            while len(steps) <= index:
                steps.append([])
            steps[index] = self._operator_streaming_chat_final_steps()
        metas = getattr(self, "_operator_chat_thinking_meta", [])
        if isinstance(metas, list):
            while len(metas) <= index:
                metas.append({})
            started = float(metas[index].get("started_sec", getattr(self, "_operator_streaming_chat_started_sec", self._operator_now_seconds())) or self._operator_now_seconds())
            elapsed_sec = max(0, int(self._operator_now_seconds() - started))
            metas[index] = {"active": False, "elapsed_sec": elapsed_sec}
        self._operator_busy_chat_stream_index = None
        self._operator_pending_streaming_chat_final_text = ""
        self._operator_streaming_chat_text = clean_text
        self._operator_streaming_chat_render_pending = False
        self._operator_chat_autoscroll_pending = True
        if hasattr(self, "_render_operator_chat"):
            self._render_operator_chat()
        return True

    def _operator_begin_busy_interruption(self, text: str) -> bool:
        command = str(text or "").strip()
        if not command:
            return False
        self._operator_pending_interruption_text = command
        self._operator_set_pending_confirm_plan(None)
        self._operator_scene_override = "execute"
        handler = getattr(self, "_handle_system_action", None)
        if callable(handler):
            handler("sys_pause")
        prompt = (
            "当前流程已暂停。检测到新的指令。"
            "如果要继续原来的流程，请说“继续当前流程”；"
            "如果要放弃原来的流程并处理新指令，请说“清除上一次流程并执行新的流程”。"
        )
        message = ResponseMessage(
            kind="warn",
            text=prompt,
            priority="high",
            context_id="operator:busy_interruption_choice",
        )
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText("当前流程已暂停，等待用户选择。")
        if hasattr(self, "_operator_archive_execution_result"):
            self._operator_archive_execution_result(result="paused_for_new_command", final_text=prompt)
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", "新指令打断流程", "等待选择", command)
        if hasattr(self, "_refresh_operator_view"):
            self._refresh_operator_view()
        return True

    def _operator_handle_pending_interruption_command(self, text: str) -> bool:
        pending = str(getattr(self, "_operator_pending_interruption_text", "") or "").strip()
        if not pending:
            return False
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False

        continue_phrases = {
            "继续当前流程",
            "继续原流程",
            "继续上一次流程",
            "恢复当前流程",
            "恢复原流程",
            "恢复上一次流程",
        }
        execute_new_phrases = {
            "清除上一次流程并执行新的流程",
            "清除上一次流程执行新的流程",
            "清除上一次的流程并执行新的流程",
            "清除上一次的流程执行新的流程",
            "放弃当前流程并执行新的流程",
            "停止当前流程并执行新的流程",
            "执行新的流程",
            "执行新流程",
            "执行新命令",
            "确认执行新的流程",
            "确认执行新流程",
        }
        cancel_phrases = {
            "取消新指令",
            "取消新的指令",
            "取消切换",
            "不执行新指令",
        }

        if any(phrase in compact for phrase in continue_phrases):
            self._operator_pending_interruption_text = ""
            handler = getattr(self, "_handle_system_action", None)
            if callable(handler):
                handler("sys_resume")
            text_out = "已取消新指令，继续当前流程。"
            self._operator_publish_interruption_choice_response(text_out, result="resumed_current_flow", log_action="继续当前流程")
            return True

        if any(phrase in compact for phrase in execute_new_phrases):
            self._operator_pending_interruption_text = ""
            self._operator_stop_interrupted_current_execution()
            text_out = "已停止上一次流程，开始处理新的指令。"
            self._operator_publish_interruption_choice_response(text_out, result="accepted_new_command", log_action="执行新指令")
            self._operator_execute_interruption_text(pending)
            return True

        if any(phrase in compact for phrase in cancel_phrases):
            self._operator_pending_interruption_text = ""
            text_out = "已取消新指令。当前流程仍处于暂停状态，请说“继续当前流程”恢复。"
            self._operator_publish_interruption_choice_response(text_out, result="cancelled_new_command", log_action="取消新指令")
            return True

        return False

    def _operator_publish_interruption_choice_response(self, text: str, *, result: str, log_action: str) -> None:
        message = ResponseMessage(
            kind="result",
            text=text,
            priority="normal",
            context_id=f"operator:busy_interruption:{result}",
        )
        if hasattr(self, "_operator_publish_response"):
            self._operator_publish_response(message)
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        if hasattr(self, "_operator_archive_execution_result"):
            self._operator_archive_execution_result(result=result, final_text=text)
        if hasattr(self, "_append_log"):
            self._append_log("用户页面", log_action, "成功", text)
        if hasattr(self, "_refresh_operator_view"):
            self._refresh_operator_view()

    def _operator_stop_interrupted_current_execution(self) -> None:
        if bool(getattr(self, "flow_running", False)):
            stopper = getattr(self, "_stop_flow", None)
            if callable(stopper):
                stopper()
                return
        stopper = getattr(self, "_operator_stop_current", None)
        if callable(stopper):
            stopper()
            return
        handler = getattr(self, "_handle_system_action", None)
        if callable(handler):
            handler("sys_cancel")

    def _operator_execute_interruption_text(self, text: str) -> None:
        command = str(text or "").strip()
        if not command:
            return
        if hasattr(self, "nlp_input_edit"):
            self.nlp_input_edit.setPlainText(command)
        previous = bool(getattr(self, "_operator_executing_interruption_text", False))
        self._operator_executing_interruption_text = True
        try:
            self._execute_nlp_text()
        finally:
            self._operator_executing_interruption_text = previous

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
                device_snapshot=self._operator_device_snapshot_for_archive(refresh_dashboard=False),
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

    def _operator_device_snapshot_for_archive(self, *, refresh_dashboard: bool = True) -> dict[str, Any]:
        try:
            snapshot_dict = self._operator_dashboard_snapshot_dict(refresh=refresh_dashboard)
        except TypeError:
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

    def _operator_show_execution(self) -> None:
        self._operator_scene_override = "execute"
        self._refresh_operator_view()

    def _operator_go_home(self) -> None:
        self._operator_scene_override = None
        self._refresh_operator_view()

    def _operator_no_pending_confirm(self) -> None:
        reason = self._operator_current_blocking_summary()
        text = "当前没有待确认的执行计划，未执行。"
        if reason:
            text = f"{text}{reason}"
        if hasattr(self, "status_label"):
            self.status_label.setText("当前没有待确认的执行计划，未执行。")
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", text)
        if hasattr(self, "_operator_archive_execution_result"):
            self._operator_archive_execution_result(result="blocked", final_text=text)
        self._append_log("用户页面", "安全确认", "提示", text)
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
        if plan is None and self._operator_prepare_current_compound_step_confirmation():
            return
        if not self._operator_plan_is_executable(plan):
            self._operator_no_pending_confirm()
            return
        if self._operator_plan_is_agent_draft(plan):
            self._operator_confirm_agent_draft(plan)
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
        self._operator_mark_compound_step_confirmed(plan)
        self.status_label.setText("确认收到，开始执行。")
        self._operator_add_chat_message("assistant", "确认收到，开始执行。")
        self._operator_archive_execution_result(result="accepted", final_text="确认收到，开始执行。")
        self._append_log("用户页面", "确认执行", "成功", getattr(plan, "reason", "已确认执行"))
        confirmed_plan = replace(plan, requires_confirmation=False)
        test_executor = self.__dict__.get("_execute_nlp_plan")
        if callable(test_executor):
            test_executor(confirmed_plan)
            return
        from .nlp_mixin import NlpMixin

        NlpMixin._execute_nlp_plan(self, confirmed_plan)

    def _operator_prepare_current_compound_step_confirmation(self) -> bool:
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(draft, dict) or draft.get("agent_kind") != "compound_plan_draft":
            return False
        if not bool(draft.get("safe_to_execute")):
            return False
        machine = draft.get("step_machine")
        if not isinstance(machine, dict):
            return False
        if str(machine.get("status") or "") != "waiting_step_confirmation":
            return False
        steps = draft.get("expanded_steps")
        if not isinstance(steps, list) or not steps:
            return False
        try:
            index = int(machine.get("current_index") or 0)
        except (TypeError, ValueError):
            index = 0
        if index < 0 or index >= len(steps):
            return False
        step = steps[index]
        if not isinstance(step, dict):
            return False
        plan = self._operator_compound_step_confirmation_plan(draft, step, index)
        if plan is None:
            return False
        self._operator_set_pending_confirm_plan(plan)
        self._operator_scene_override = "confirm"
        text = f"复合指令第 {index + 1}/{len(steps)} 步等待确认：{step.get('description') or step.get('action') or machine.get('current_step_text') or '-'}"
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        self._operator_add_chat_message("assistant", f"{text}\n请确认执行当前步骤，或取消指令。")
        self._operator_archive_execution_result(result="compound_step_waiting_confirmation", final_text=text)
        self._append_log("Agent", "复合指令步骤确认", "提示", text)
        self._refresh_operator_view()
        return True

    def _operator_compound_step_confirmation_plan(self, draft: dict[str, Any], step: dict[str, Any], index: int):
        from .voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan

        try:
            func_id = int(float(step.get("func_id") or step.get("func_num") or 0))
        except (TypeError, ValueError):
            func_id = 0
        params = step.get("params")
        if func_id <= 0 or not isinstance(params, dict):
            return None
        flow_name = str(draft.get("flow_name") or draft.get("flowName") or "compound").strip() or "compound"
        safe_name = re.sub(r"[^\w\u4e00-\u9fff]+", "_", flow_name).strip("_") or "compound"
        target = f"{safe_name}_step_{index + 1}"
        record = QueryRecord(
            query_key=target,
            func_num=func_id,
            params=dict(params),
            keywords=f"{flow_name} 复合指令 第{index + 1}步",
            description=str(step.get("description") or step.get("action") or f"{flow_name} 第{index + 1}步"),
            safety_level=int(float(step.get("safety_level", 5) or 5)),
        )
        return VoiceNlpPlan(
            actions=(
                VoiceNlpAction(
                    "atomic_template",
                    target,
                    "compound_step",
                    str(draft.get("raw_text") or ""),
                    f"复合指令第 {index + 1} 步等待确认。",
                ),
            ),
            source="compound_step",
            raw_text=str(draft.get("raw_text") or ""),
            reason=f"复合指令第 {index + 1} 步等待确认。",
            semantic_level=3,
            semantic_label="复合指令步骤执行层",
            requires_precheck=False,
            requires_confirmation=True,
            atomic_records={target: record},
            flow_draft={
                "agent_kind": "compound_step_confirmation",
                "compound_plan_id": draft.get("plan_id"),
                "compound_step_index": index,
                "compound_step_total": len(draft.get("expanded_steps") or []),
                "compound_flow_name": flow_name,
            },
        )

    def _operator_update_compound_step_result(self, *, ok: bool, reason: str = "") -> bool:
        draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(draft, dict) or draft.get("agent_kind") != "compound_plan_draft":
            return False
        machine = draft.get("step_machine")
        if not isinstance(machine, dict):
            return False
        steps = machine.get("steps")
        if not isinstance(steps, (list, tuple)) or not steps:
            return False
        try:
            index = int(machine.get("current_index") or 0)
        except (TypeError, ValueError):
            index = 0
        if index < 0 or index >= len(steps):
            return False
        updated_steps = [dict(step) for step in steps if isinstance(step, dict)]
        if len(updated_steps) != len(steps):
            return False
        detail = str(reason or "").strip()
        total = len(updated_steps)
        if not ok:
            updated_steps[index]["status"] = "failed"
            updated_steps[index]["reason"] = detail or "当前步骤执行失败。"
            machine.update(
                {
                    "status": "failed",
                    "current_index": index,
                    "reason": updated_steps[index]["reason"],
                    "steps": updated_steps,
                }
            )
            text = f"复合指令停止在第 {index + 1}/{total} 步：{updated_steps[index].get('text') or '-'}。原因：{updated_steps[index]['reason']}"
            if hasattr(self, "status_label"):
                self.status_label.setText(text)
            self._operator_add_chat_message("assistant", text, kind="warn")
            self._operator_archive_execution_result(result="compound_step_failed", final_text=text)
            self._append_log("Agent", "复合指令步骤执行", "失败", text)
            self._refresh_operator_view()
            return True

        updated_steps[index]["status"] = "completed"
        updated_steps[index].pop("reason", None)
        next_index = index + 1
        if next_index >= total:
            machine.update(
                {
                    "status": "completed",
                    "current_index": index,
                    "reason": "",
                    "steps": updated_steps,
                }
            )
            text = f"复合指令执行完成：共完成 {total} 步。"
            if hasattr(self, "status_label"):
                self.status_label.setText(text)
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="compound_completed", final_text=text)
            self._append_log("Agent", "复合指令完成", "成功", text)
            self._refresh_operator_view()
            return True

        updated_steps[next_index]["status"] = "waiting_confirmation"
        machine.update(
            {
                "status": "waiting_step_confirmation",
                "current_index": next_index,
                "current_step_text": updated_steps[next_index].get("text") or "",
                "reason": "",
                "steps": updated_steps,
            }
        )
        text = f"复合指令第 {index + 1}/{total} 步已完成。当前等待确认第 {next_index + 1}/{total} 步：{updated_steps[next_index].get('text') or '-'}。"
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        self._operator_add_chat_message("assistant", text)
        self._operator_archive_execution_result(result="compound_step_completed", final_text=text)
        self._append_log("Agent", "复合指令步骤执行", "成功", text)
        self._refresh_operator_view()
        return True

    def _operator_mark_compound_step_confirmed(self, plan) -> bool:
        draft = getattr(plan, "flow_draft", None)
        if not isinstance(draft, dict) or draft.get("agent_kind") != "compound_step_confirmation":
            return False
        compound_draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(compound_draft, dict):
            return False
        machine = compound_draft.get("step_machine")
        if not isinstance(machine, dict):
            return False
        steps = machine.get("steps")
        if not isinstance(steps, (list, tuple)) or not steps:
            return False
        try:
            index = int(draft.get("compound_step_index") if draft.get("compound_step_index") is not None else machine.get("current_index") or 0)
        except (TypeError, ValueError):
            index = 0
        if index < 0 or index >= len(steps):
            return False
        updated_steps = [dict(step) for step in steps if isinstance(step, dict)]
        if len(updated_steps) != len(steps):
            return False
        updated_steps[index]["status"] = "confirmed"
        machine.update(
            {
                "status": "step_confirmed",
                "current_index": index,
                "current_step_text": updated_steps[index].get("text") or machine.get("current_step_text") or "",
                "steps": updated_steps,
            }
        )
        self._operator_active_compound_step_target = str(getattr(tuple(getattr(plan, "actions", ()) or ())[0], "target", "") or "")
        return True

    def _operator_confirm_agent_draft(self, plan) -> None:
        draft_id = self._operator_agent_draft_id(plan)
        service = getattr(self, "_restricted_agent_service", None)
        if not draft_id or service is None or not hasattr(service, "confirm"):
            text = "Agent 待确认草稿不可用，未执行。请重新输入指令。"
            if hasattr(self, "status_label"):
                self.status_label.setText(text)
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="blocked", final_text=text)
            self._append_log("用户页面", "Agent确认执行", "拒绝", text)
            self._refresh_operator_view()
            return
        try:
            record = service.confirm(draft_id)
        except Exception as exc:
            text = f"Agent 草稿确认失败：{exc}"
            if hasattr(self, "status_label"):
                self.status_label.setText(text)
            self._operator_add_chat_message("assistant", text)
            self._operator_archive_execution_result(result="blocked", final_text=text)
            self._append_log("用户页面", "Agent确认执行", "拒绝", text)
            self._refresh_operator_view()
            return

        self._operator_set_pending_confirm_plan(None)
        self._operator_scene_override = None
        self._set_nlp_execute_busy(True)
        self.status_label.setText("确认收到，开始执行。")
        self._operator_add_chat_message("assistant", "确认收到，开始执行。")
        self._operator_archive_execution_result(result="accepted", final_text="确认收到，开始执行。")
        self._append_log("用户页面", "Agent确认执行", "成功", getattr(plan, "reason", "已确认执行"))
        self._execute_nlp_plan(self._operator_agent_record_to_execution_plan(plan, record))

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
        runtime = self._operator_runtime_blocking_summary()
        if runtime:
            return runtime
        return ""

    def _operator_runtime_blocking_summary(self) -> str:
        alarm_code = str(getattr(self, "alarm_code", "") or "").strip()
        alarm_text = str(getattr(self, "alarm_text", "") or "").strip()
        if alarm_code and alarm_code not in {"0", "ERR_000"}:
            detail = f"，{alarm_text}" if alarm_text and alarm_text not in {"系统正常", "无报警"} else ""
            return f"当前处于报警状态，报警码 {alarm_code}{detail}，请先处理报警。"
        if alarm_text and "报警" in alarm_text and alarm_text not in {"无报警", "系统正常", "报警：无", "报警: 无"}:
            code_text = f"报警码 {alarm_code}，" if alarm_code else ""
            return f"当前处于报警状态，{code_text}{alarm_text}，请先处理报警。"
        if bool(getattr(self, "estop_active", False)):
            return "当前急停已触发，请先确认现场安全并解除急停。"
        if bool(getattr(self, "pause_active", False)):
            return "当前系统处于暂停状态，请先恢复后再执行。"
        ready = getattr(self, "ready", None)
        if ready is False:
            return "当前控制器未就绪，请先恢复控制器就绪状态。"
        return ""

    def _operator_cancel_confirm(self) -> None:
        if getattr(self, "_operator_pending_confirm_plan", None) is not None:
            if self._operator_cancel_compound_step_confirmation():
                return
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

    def _operator_cancel_compound_step_confirmation(self) -> bool:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        draft = getattr(plan, "flow_draft", None)
        if not isinstance(draft, dict) or draft.get("agent_kind") != "compound_step_confirmation":
            return False
        self._operator_set_pending_confirm_plan(None)
        self._operator_pending_flow_draft = None
        self._operator_scene_override = None
        text = "已取消复合指令，后续步骤不会继续执行。"
        if hasattr(self, "status_label"):
            self.status_label.setText(text)
        self._operator_add_chat_message("assistant", text)
        self._operator_archive_execution_result(result="compound_cancelled", final_text=text)
        self._append_log("用户页面", "取消复合指令", "成功", text)
        self._refresh_operator_view()
        return True

    def _handle_operator_ui_command(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False

        if self._operator_handle_pending_interruption_command(text):
            return True
        if self._operator_handle_running_control_text(text):
            return True
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
        if self._operator_handle_saved_flow_edit_request(text):
            return True
        if self._operator_handle_pending_flow_draft_command(text):
            return True
        if self._operator_handle_pending_flow_draft_query(text):
            return True
        if self._operator_handle_flow_list_query(text):
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

    def _operator_handle_running_control_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        compact = compact.strip("，。！？,.!?")
        if not compact:
            return False
        if not (
            bool(getattr(self, "flow_running", False))
            or bool(getattr(self, "nlp_sequence_running", False))
            or self._operator_execution_or_pause_active()
        ):
            return False
        if compact in {"暂停", "暂停一下", "先暂停", "暂停当前", "暂停流程"}:
            self._handle_system_action("sys_pause")
            text_out = "当前任务已发送暂停指令。"
            if hasattr(self, "status_label"):
                self.status_label.setText(text_out)
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "运行中暂停", "成功", "无唤醒词高优先级控制")
            self._refresh_operator_view()
            return True
        if compact in {"停止", "停止当前", "停止当前动作", "停止当前任务", "取消", "取消当前", "取消当前动作", "取消当前任务"}:
            self._operator_stop_current()
            text_out = "当前任务已发送停止/取消指令。"
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "运行中停止", "成功", "无唤醒词高优先级控制")
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
        match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*步.*(?:加速度|加速|acc).*?(\d+(?:\.\d+)?)\s*%?", compact, re.IGNORECASE)
        if match:
            step_id = self._operator_parse_step_index_text(match.group(1))
            acc = float(match.group(2))
            if step_id > 0:
                result = service.edit_step_params(step_id, {"acc_pct": acc})
        if result is None:
            match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*步.*(?:减速度|减速|dec).*?(\d+(?:\.\d+)?)\s*%?", compact, re.IGNORECASE)
            if match:
                step_id = self._operator_parse_step_index_text(match.group(1))
                dec = float(match.group(2))
                if step_id > 0:
                    result = service.edit_step_params(step_id, {"dec_pct": dec})
        if result is None:
            match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*步.*速度.*?(\d+(?:\.\d+)?)\s*%?", compact)
            if match:
                step_id = self._operator_parse_step_index_text(match.group(1))
                speed = float(match.group(2))
                if step_id > 0:
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
        if result is None and re.search(r"删除.*(最后|末尾|最后一步|末尾一步)", compact):
            steps = draft.get("expanded_steps")
            if isinstance(steps, list) and steps:
                result = service.delete_step(len(steps))
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

    @staticmethod
    def _operator_parse_step_index_text(text: str) -> int:
        clean = str(text or "").strip()
        if not clean:
            return 0
        try:
            return int(clean)
        except ValueError:
            pass
        digits = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if clean in digits:
            return digits[clean]
        if clean == "十":
            return 10
        if clean.startswith("十") and len(clean) == 2 and clean[1] in digits:
            return 10 + digits[clean[1]]
        if clean.endswith("十") and len(clean) == 2 and clean[0] in digits:
            return digits[clean[0]] * 10
        if "十" in clean:
            left, right = clean.split("十", 1)
            tens = digits.get(left, 1 if left == "" else 0)
            ones = digits.get(right, 0)
            return tens * 10 + ones if tens else 0
        return 0

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

    def _operator_handle_saved_flow_edit_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        if not any(keyword in compact for keyword in ("后面添加", "后面再添", "后面加", "末尾添加", "最后添加")):
            return False
        if not any(keyword in compact for keyword in ("流程", "这个")):
            return False
        flow_name = self._operator_extract_flow_name_for_edit(compact)
        if not flow_name:
            return False
        draft = self._operator_flow_draft_from_saved_flow(flow_name)
        if draft is None:
            return False
        step = self._operator_build_append_flow_step_from_text(draft, compact)
        if step is None and any(keyword in compact.lower() for keyword in ("移动到位置", "到位置", "移动")):
            step = self._operator_missing_move_step_from_text(draft, compact)
        if step is None:
            return False
        service = self._operator_execution_plan_service()
        service.set_pending_flow_draft(draft)
        result = service.append_step(step)
        updated = service.pending_flow_draft()
        if updated is None:
            return False
        merged = dict(draft)
        merged["expanded_steps"] = updated.get("expanded_steps", [])
        merged["flow_name"] = updated.get("flow_name", flow_name)
        merged["needs_precheck"] = True
        self._operator_pending_flow_draft = merged
        service.set_pending_flow_draft(merged)
        clarification = service.current_clarification()
        if clarification is not None:
            message = f"已进入流程“{flow_name}”编辑，准备追加一步。{clarification.question}"
        else:
            message = f"{result.message}。当前流程草案尚未保存/执行。"
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(message))
        if hasattr(self, "_operator_add_chat_message"):
            self._operator_add_chat_message("assistant", message)
        self._append_log("自然语言", "已保存流程编辑", "成功", message)
        self._refresh_operator_view()
        return True

    def _operator_extract_flow_name_for_edit(self, compact: str) -> str:
        service = getattr(self, "service", None)
        names: list[str] = []
        if service is not None and hasattr(service, "list_flow_names"):
            try:
                names = [str(name) for name in service.list_flow_names()]
            except Exception:
                names = []
        for name in sorted(names, key=len, reverse=True):
            clean = re.sub(r"\s+", "", name)
            if clean and clean in compact:
                return name
        current = str(getattr(self, "current_flow_name", "") or "").strip()
        if current and ("这个" in compact or re.sub(r"\s+", "", current) in compact):
            return current
        return ""

    def _operator_flow_draft_from_saved_flow(self, flow_name: str) -> dict[str, Any] | None:
        service = getattr(self, "service", None)
        if service is None:
            return None
        steps: list[dict[str, Any]] = []
        entry = None
        if hasattr(service, "get_flow_entry"):
            try:
                entry = service.get_flow_entry(flow_name)
            except Exception:
                entry = None
        if entry is not None and hasattr(entry, "steps"):
            for index, step in enumerate(getattr(entry, "steps", ()) or (), start=1):
                params = dict(getattr(step, "params", {}) or {})
                query_key = str(params.get("query_key") or "").strip()
                record = getattr(self, "table", {}).get(query_key) if query_key else None
                if record is not None:
                    params = dict(getattr(record, "params", {}) or {})
                    action = str(getattr(record, "description", "") or query_key)
                    func_id = int(getattr(record, "func_num", 0) or 0)
                    description = str(getattr(record, "description", "") or query_key)
                else:
                    action = str(getattr(step, "action", "") or "")
                    func_id = int(getattr(step, "func_id", 0) or 0)
                    description = str(getattr(step, "description", "") or "")
                steps.append(
                    {
                        "step_id": index,
                        "action": action,
                        "func_id": func_id,
                        "position_name": str(getattr(step, "position_name", "") or ""),
                        "description": description,
                        "params": params,
                    }
                )
        if not steps and hasattr(service, "get_flow"):
            try:
                flow = service.get_flow(flow_name)
            except Exception:
                flow = None
            for index, key in enumerate(getattr(flow, "steps", ()) or (), start=1):
                record = getattr(self, "table", {}).get(key)
                if record is None:
                    continue
                steps.append(
                    {
                        "step_id": index,
                        "action": str(getattr(record, "description", "") or key),
                        "func_id": int(getattr(record, "func_num", 0) or 0),
                        "description": str(getattr(record, "description", "") or key),
                        "params": dict(getattr(record, "params", {}) or {}),
                    }
                )
        if not steps:
            return None
        return {
            "flow_name": flow_name,
            "expanded_steps": steps,
            "positions": self._operator_position_registry_draft_items(),
        }

    def _operator_position_registry_draft_items(self) -> list[dict[str, Any]]:
        try:
            registry = self._position_registry() if hasattr(self, "_position_registry") else None
            entries = list(registry.list_all()) if registry is not None and hasattr(registry, "list_all") else []
        except Exception:
            entries = []
        items: list[dict[str, Any]] = []
        for entry in entries:
            name = str(getattr(entry, "name", "") or "").strip()
            pose = getattr(entry, "pose", None)
            if name and isinstance(pose, (list, tuple)) and len(pose) >= 6:
                items.append({"name": name, "pose": list(pose[:6])})
        return items

    def _operator_missing_move_step_from_text(self, draft: dict, compact: str):
        from .execution_plan import ExecutionStep

        match = re.search(r"位置([a-zA-Z0-9_\-\u4e00-\u9fff]+)", compact, re.IGNORECASE)
        label = match.group(1) if match else "待补充位置"
        default_speed = self._operator_flow_draft_default_speed(draft)
        return ExecutionStep(
            step_id=0,
            action="move_position",
            func_id=108,
            params={
                "spd_pct": default_speed,
                "acc_pct": default_speed,
                "dec_pct": default_speed,
                "move_type": 0,
            },
            target_label=str(label).upper() if len(str(label)) == 1 else str(label),
            description=f"移动到位置{label}",
        )

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
            for keyword in (
                "保存并执行",
                "确认并执行",
                "确认执行",
                "执行这个流程",
                "运行这个流程",
                "保存后执行",
                "开始执行",
            )
        ) or compact in {
            "执行",
            "运行",
            "确认",
            "确认执行",
            "开始",
            "开始吧",
            "执行吧",
            "运行吧",
            "可以执行",
            "可以运行",
            "就这样执行",
            "按这个执行",
            "照这个执行",
        }
        save_only = execute_after_save or any(
            keyword in compact for keyword in ("确认保存", "保存流程", "保存草案", "保存这个流程", "确认草案")
        )
        if not save_only:
            return False
        if (
            execute_after_save
            and draft.get("agent_kind") == "compound_plan_draft"
            and bool(draft.get("safe_to_execute"))
        ):
            if self._operator_prepare_current_compound_step_confirmation():
                return True
        ok, detail, flow_name = self._operator_save_flow_draft(draft)
        if not ok:
            if not draft.get("expanded_steps"):
                self._operator_pending_flow_draft = None
            self.status_label.setText(detail)
            if hasattr(self, "_operator_add_chat_message"):
                self._operator_add_chat_message("assistant", detail)
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
        if self._operator_text_looks_like_flow_draft_detail_answer(compact):
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

    @staticmethod
    def _operator_text_looks_like_flow_draft_detail_answer(compact: str) -> bool:
        text = str(compact or "")
        if not text:
            return False
        has_name_assignment = any(keyword in text for keyword in ("草案名称为", "流程名称为", "名字叫", "名称叫"))
        has_step_assignment = any(keyword in text for keyword in ("步骤为", "步骤是", "步骤包括", "步骤如下", "然后步骤"))
        has_motion_description = any(keyword in text for keyword in ("从", "移动到", "到位置", "再移动", "抓取", "放置"))
        return (has_name_assignment and (has_step_assignment or has_motion_description)) or (
            has_step_assignment and has_motion_description
        )

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
        if "缺少" in answer and "唤醒词" in answer:
            self._append_log("自然语言", "缺少唤醒词", "提示", answer)
        else:
            self._append_log("自然语言", "上下文查询", "成功", answer)
        self._refresh_operator_view()
        return True

    def _operator_context_answer(self, text: str) -> str:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return ""
        wake_required_answer = self._operator_missing_wake_word_flow_execution_answer(compact)
        if wake_required_answer:
            return wake_required_answer
        if self._operator_text_looks_like_existing_flow_execution_request(compact):
            return ""
        if self._operator_text_looks_like_flow_creation_request(compact):
            return ""
        axis_position_answer = self._operator_axis_position_context_answer(compact)
        if axis_position_answer:
            return axis_position_answer
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
        if self._operator_text_looks_like_existing_flow_execution_request(compact_text):
            return ""
        if not any(keyword in compact_text for keyword in ("流程", "信息", "详情", "步骤", "看看", "看下", "查询")):
            return ""
        flow = self._operator_find_registered_flow_for_text(compact_text)
        if flow is None:
            return ""
        return self._operator_flow_entry_preview_text(flow, include_params=True)

    def _operator_missing_wake_word_flow_execution_answer(self, compact_text: str) -> str:
        compact = re.sub(r"\s+", "", compact_text or "")
        if not compact:
            return ""
        stripped = strip_wake_word_from_compact(compact)
        if stripped != compact:
            return ""
        if not any(verb in compact for verb in ("执行", "开始", "运行", "启动", "直行")):
            return ""
        flow = self._operator_find_registered_flow_for_text(compact)
        if flow is None:
            return ""
        name = str(getattr(flow, "name", "") or "").strip()
        if not name:
            return ""
        return (
            f"生产执行指令缺少“小正或小兵”唤醒词，未执行。"
            f"如需执行流程，请说“小正，执行{name}流程”或“小兵，执行{name}流程”。"
            "没有触发机械手动作。"
        )

    def _operator_text_looks_like_existing_flow_execution_request(self, compact_text: str) -> bool:
        compact = re.sub(r"\s+", "", compact_text or "")
        if not compact:
            return False
        stripped = strip_wake_word_from_compact(compact)
        if stripped == compact:
            return False
        if not any(verb in stripped for verb in ("执行", "开始", "运行")):
            return False
        return self._operator_find_registered_flow_for_text(stripped) is not None

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

    def _operator_flow_entry_preview_text(self, flow: Any, *, include_params: bool = False) -> str:
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
            lines.append("步骤流：")
            for index, step in enumerate(steps[:12], start=1):
                record = self._operator_flow_step_record(step)
                source = record if record is not None else step
                action = str(getattr(source, "description", "") or getattr(source, "action", "") or "执行动作").strip()
                func_id = getattr(source, "func_num", getattr(source, "func_id", ""))
                position_name = str(getattr(source, "position_name", "") or "").strip()
                func_text = f"Func{int(float(func_id))}" if func_id not in ("", None, 0) else "Func?"
                position_text = f"  位置={position_name}" if position_name else ""
                lines.append(f"{index:02d}  {func_text}  {action}{position_text}")
                params = getattr(source, "params", None)
                if include_params and isinstance(params, dict) and params:
                    lines.extend(f"    {line}" for line in OperatorUiMixin._operator_format_flow_step_param_lines(params))
            if len(steps) > 12:
                lines.append(f"... 还有 {len(steps) - 12} 步未展开显示。")
        return "\n".join(lines)

    def _operator_flow_step_record(self, step: Any):
        query_key = ""
        if isinstance(step, str):
            query_key = step
        else:
            params = getattr(step, "params", None)
            if isinstance(params, dict):
                query_key = str(params.get("query_key") or "").strip()
            if not query_key:
                query_key = str(getattr(step, "query_key", "") or "").strip()
            if not query_key:
                action = str(getattr(step, "action", "") or "").strip()
                if action:
                    query_key = action
        if not query_key:
            return None
        table = getattr(self, "table", None)
        if isinstance(table, dict):
            return table.get(query_key)
        return None

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
        has_wake = any(keyword in compact_text for keyword in configured_wake_words())
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

    def _operator_axis_position_context_answer(self, compact_text: str) -> str:
        if not compact_text:
            return ""
        if not any(marker in compact_text for marker in ("各轴", "关节", "轴位置", "当前位置", "当前坐标")):
            return ""
        if not any(marker in compact_text for marker in ("多少", "是多少", "什么", "查询", "看下", "看一下", "位置", "坐标")):
            return ""
        snapshot = self._operator_dashboard_snapshot_dict() if hasattr(self, "_operator_dashboard_snapshot_dict") else {}
        data = snapshot.get("data") if isinstance(snapshot, dict) and isinstance(snapshot.get("data"), dict) else snapshot
        if not isinstance(data, dict):
            data = {}
        joint_values = data.get("dpos_j") or data.get("joints") or data.get("joint_position")
        cart_values = data.get("dpos_c") or data.get("position") or data.get("cartesian")
        parts: list[str] = []
        if isinstance(joint_values, (list, tuple)) and len(joint_values) >= 6:
            joints = "，".join(
                f"J{index}={self._operator_compact_number(value)}"
                for index, value in enumerate(joint_values[:6], start=1)
            )
            parts.append(f"关节位置：{joints}。")
        if isinstance(cart_values, dict):
            cart_values = [cart_values.get(key) for key in ("x", "y", "z", "rx", "ry", "rz")]
        if isinstance(cart_values, (list, tuple)) and len(cart_values) >= 3:
            parts.append(
                "当前位置："
                f"X={self._operator_compact_number(cart_values[0])}，"
                f"Y={self._operator_compact_number(cart_values[1])}，"
                f"Z={self._operator_compact_number(cart_values[2])}。"
            )
        return "".join(parts)

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

    def _operator_handle_flow_list_query(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact or "流程" not in compact:
            return False
        list_markers = (
            "有哪些流程",
            "有什么流程",
            "流程有哪些",
            "流程列表",
            "所有流程",
            "全部流程",
            "保存了哪些流程",
            "总共有多少个流程",
            "有多少个流程",
            "多少个流程",
            "几个流程",
        )
        if not any(marker in compact for marker in list_markers):
            return False
        service = getattr(self, "service", None)
        if service is None or not hasattr(service, "list_flow_names"):
            return False
        try:
            names = [str(name) for name in service.list_flow_names()]
        except Exception:
            names = []
        if not names:
            answer = "当前没有已保存流程。可以说“创建流程，名字叫测试”开始创建。"
        else:
            lines = [f"当前共有 {len(names)} 个流程："]
            lines.extend(f"{index}. {name}" for index, name in enumerate(names, start=1))
            first = names[0]
            lines.append(f"可以说“查看{first}流程”查看步骤，或说“小正，执行{first}流程”执行。")
            answer = "\n".join(lines)
        self._operator_publish_context_intent_reply(answer, category="流程列表查询")
        return True

    def _operator_deepseek_runtime_context(self) -> str:
        scene_state = getattr(self, "_operator_scene_state", OperatorSceneState())
        current_scene = str(getattr(scene_state, "current", "") or "").strip()
        last_result = self._operator_last_execution_result_text()
        if last_result and "还没有可报告" in last_result:
            last_result = ""
        position_lines: list[str] = []
        try:
            registry = self._position_registry() if hasattr(self, "_position_registry") else None
            entries = list(registry.list_all()) if registry is not None and hasattr(registry, "list_all") else []
        except Exception:
            entries = []
        if entries:
            for entry in entries[:8]:
                x, y, z, rx, ry, rz = getattr(entry, "pose", (0, 0, 0, 0, 0, 0))
                position_lines.append(
                    f"{entry.name}=({self._operator_compact_number(x)}, {self._operator_compact_number(y)}, "
                    f"{self._operator_compact_number(z)}, {self._operator_compact_number(rx)}, "
                    f"{self._operator_compact_number(ry)}, {self._operator_compact_number(rz)})"
                )
        return AgentContextBuilder().build_text(
            current_scene=current_scene,
            pending_confirm_plan=getattr(self, "_operator_pending_confirm_plan", None),
            pending_flow_draft=getattr(self, "_operator_pending_flow_draft", None),
            recent_messages=tuple(getattr(self, "_operator_chat_messages", []) or ()),
            current_flow_text=self._operator_current_flow_context_answer(),
            last_execution_result=last_result,
            last_execution_state=self._operator_last_execution_state_after_text(),
            position_lines=tuple(position_lines),
        )

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

    def _refresh_operator_pending_flow_status(self) -> None:
        if not hasattr(self, "operator_pending_flow_browser"):
            return
        draft = getattr(self, "_operator_pending_flow_draft", None)
        text = self._operator_pending_flow_status_text(draft) if isinstance(draft, dict) else ""
        visible = bool(text)
        if hasattr(self, "operator_pending_flow_title"):
            self.operator_pending_flow_title.setVisible(visible)
        self.operator_pending_flow_browser.setVisible(visible)
        if not visible:
            self.operator_pending_flow_browser.clear()
            return
        if self.operator_pending_flow_browser.toPlainText() != text:
            self.operator_pending_flow_browser.setPlainText(text)

    @staticmethod
    def _operator_pending_flow_status_text(draft: dict[str, Any] | None) -> str:
        if not isinstance(draft, dict) or not draft:
            return ""
        flow_name = str(draft.get("flow_name") or draft.get("flowName") or "未命名流程").strip() or "未命名流程"
        steps = draft.get("expanded_steps")
        step_count = len(steps) if isinstance(steps, list) else 0
        status = "待重新预检" if draft.get("needs_precheck") else "等待确认"
        lines = [
            "待确认流程草案",
            f"流程名：{flow_name}",
            f"步骤数：{step_count}",
            f"状态：{status}",
            "下一步：可说“确认保存”“保存并执行”“重新预检”或“取消草案”。",
            "完整步骤和参数已显示在对话中。",
        ]
        return "\n".join(lines)

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
            lines.append("步骤流：")
            for index, step in enumerate(step_items, start=1):
                if not isinstance(step, dict):
                    lines.append(f"{index:02d}  非结构化步骤")
                    continue
                description = str(step.get("description") or step.get("action") or "").strip()
                func_id = step.get("func_id") or step.get("func_num")
                position_name = str(step.get("position_name") or "").strip()
                func_text = f"Func{int(float(func_id))}" if func_id else "Func?"
                position_text = f"  位置={position_name}" if position_name else ""
                lines.append(f"{index:02d}  {func_text}  {description or '执行动作'}{position_text}")
                params = step.get("params")
                if include_params and isinstance(params, dict) and params:
                    lines.extend(f"    {line}" for line in OperatorUiMixin._operator_format_flow_step_param_lines(params))
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

    @staticmethod
    def _operator_format_flow_step_param_lines(params: dict[str, Any]) -> list[str]:
        def has_any(keys: tuple[str, ...]) -> bool:
            return any(key in params for key in keys)

        def value(key: str) -> str:
            return OperatorUiMixin._operator_compact_number(params.get(key))

        used: set[str] = set()
        lines: list[str] = []
        target_keys = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
        if has_any(target_keys):
            labels = (
                ("target_x", "X"),
                ("target_y", "Y"),
                ("target_z", "Z"),
                ("target_rx", "RX"),
                ("target_ry", "RY"),
                ("target_rz", "RZ"),
            )
            parts = [f"{label} {key}={value(key)}" for key, label in labels if key in params]
            if parts:
                lines.append("目标  " + "  ".join(parts))
                used.update(key for key, _ in labels)
        jog_keys = ("axis_no", "pos_val", "io_no", "io_action", "delay_sec")
        if has_any(jog_keys):
            labels = (
                ("axis_no", "axis_no"),
                ("pos_val", "pos_val"),
                ("io_no", "io_no"),
                ("io_action", "io_action"),
                ("delay_sec", "delay_sec"),
            )
            parts = [f"{label}={value(key)}" for key, label in labels if key in params]
            if parts:
                lines.append("动作  " + "  ".join(parts))
                used.update(key for key, _ in labels)
        motion_keys = ("spd_pct", "acc_pct", "dec_pct", "move_type")
        if has_any(motion_keys):
            labels = (
                ("spd_pct", "速度"),
                ("acc_pct", "加速度"),
                ("dec_pct", "减速度"),
                ("move_type", "move_type"),
            )
            parts = []
            for key, label in labels:
                if key not in params:
                    continue
                suffix = "%" if key in {"spd_pct", "acc_pct", "dec_pct"} else ""
                parts.append(f"{label} {key}={value(key)}{suffix}" if key != "move_type" else f"{key}={value(key)}")
            if parts:
                lines.append("运动  " + "  ".join(parts))
                used.update(key for key, _ in labels)
        flag_keys = ("stop_cmd", "fuzzy_pos", "fuzzy_spd", "fuzzy_acc", "fuzzy_dec")
        if has_any(flag_keys):
            parts = [f"{key}={value(key)}" for key in flag_keys if key in params]
            if parts:
                lines.append("标志  " + "  ".join(parts))
                used.update(flag_keys)
        other = [f"{key}={OperatorUiMixin._operator_compact_number(params[key])}" for key in sorted(params) if key not in used]
        if other:
            lines.append("其他  " + "  ".join(other))
        return lines or ["参数：-"]

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
        if self._operator_pending_confirm_query_requested(compact):
            text_out = self._operator_pending_confirm_params_text(plan)
            if not text_out:
                return False
            if hasattr(self, "status_label"):
                self.status_label.setText(self._operator_footer_status_text(text_out))
            self._operator_add_chat_message("assistant", text_out)
            self._operator_publish_ai_answer_for_speech(text_out)
            self._append_log("用户页面", "确认阶段参数查询", "成功", text_out)
            self._refresh_operator_view()
            return True
        acc_match = re.search(r"(?:加速度|加速|acc)(?:改成|改为|设为|设置为|到)?(\d+(?:\.\d+)?)%?", compact, re.IGNORECASE)
        if acc_match:
            acc = max(5.0, min(150.0, float(acc_match.group(1))))
            changed = self._operator_update_pending_confirm_records(plan, {"acc_pct": acc})
            if not changed:
                return self._operator_reject_pending_confirm_param_modify("加速度")
            self._operator_sync_compound_step_params_from_confirm_plan(plan)
            text_out = f"已将待确认计划加速度调整为{acc:g}%。请再次确认执行。"
            self._operator_prepare_plan_prechecks(plan)
            self.status_label.setText(text_out)
            self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "确认阶段修改参数", "成功", text_out)
            self._refresh_operator_view()
            return True
        dec_match = re.search(r"(?:减速度|减速|dec)(?:改成|改为|设为|设置为|到)?(\d+(?:\.\d+)?)%?", compact, re.IGNORECASE)
        if dec_match:
            dec = max(5.0, min(150.0, float(dec_match.group(1))))
            changed = self._operator_update_pending_confirm_records(plan, {"dec_pct": dec})
            if not changed:
                return self._operator_reject_pending_confirm_param_modify("减速度")
            self._operator_sync_compound_step_params_from_confirm_plan(plan)
            text_out = f"已将待确认计划减速度调整为{dec:g}%。请再次确认执行。"
            self._operator_prepare_plan_prechecks(plan)
            self.status_label.setText(text_out)
            self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "确认阶段修改参数", "成功", text_out)
            self._refresh_operator_view()
            return True
        speed_match = re.search(r"(?:速度|速率|spd)(?:改成|改为|设为|设置为|到)?(\d+(?:\.\d+)?)%?", compact, re.IGNORECASE)
        if speed_match:
            speed = max(5.0, min(150.0, float(speed_match.group(1))))
            changed = self._operator_update_pending_confirm_records(
                plan,
                {"spd_pct": speed, "acc_pct": speed, "dec_pct": speed},
            )
            if not changed:
                return self._operator_reject_pending_confirm_param_modify("速度")
            self._operator_sync_compound_step_params_from_confirm_plan(plan)
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
                return self._operator_reject_pending_confirm_param_modify("步长")
            self._operator_sync_compound_step_params_from_confirm_plan(plan)
            text_out = f"已将待确认计划步长调整为{step:g}。请再次确认执行。"
            self._operator_prepare_plan_prechecks(plan)
            self.status_label.setText(text_out)
            self._operator_add_chat_message("assistant", text_out)
            self._append_log("用户页面", "确认阶段修改参数", "成功", text_out)
            self._refresh_operator_view()
            return True
        return False

    def _operator_reject_pending_confirm_param_modify(self, param_label: str) -> bool:
        label = str(param_label or "该").strip()
        text_out = (
            f"当前待确认步骤不包含{label}参数，未修改右侧待确认计划。"
            "如果要修改其他步骤，请说“取消指令”后重新生成流程，或明确说“第几步参数改为多少”。"
        )
        if hasattr(self, "status_label"):
            self.status_label.setText(self._operator_footer_status_text(text_out))
        self._operator_add_chat_message("assistant", text_out)
        self._append_log("用户页面", "确认阶段修改参数", "失败", text_out)
        self._refresh_operator_view()
        return True

    @staticmethod
    def _operator_pending_confirm_query_requested(compact: str) -> bool:
        return any(
            keyword in compact
            for keyword in (
                "当前参数",
                "运动参数",
                "待确认参数",
                "现在参数",
                "参数是哪些",
                "参数是什么",
                "这个计划",
                "待确认计划",
            )
        ) and not any(keyword in compact for keyword in ("改为", "改成", "设为", "设置为"))

    def _operator_pending_confirm_params_text(self, plan) -> str:
        records = getattr(plan, "atomic_records", {}) or {}
        lines = ["当前待确认计划参数："]
        found = False
        for key, record in records.items():
            params = getattr(record, "params", None)
            if not isinstance(params, dict) or not params:
                continue
            desc = str(getattr(record, "description", "") or key or "步骤")
            func_id = getattr(record, "func_num", None)
            title = f"{desc}"
            if func_id is not None:
                title += f" Func{func_id}"
            lines.append(f"- {title}：{self._operator_format_params_inline(params)}")
            found = True
        draft = getattr(plan, "flow_draft", None)
        if not found and isinstance(draft, dict):
            params = draft.get("params")
            if isinstance(params, dict) and params:
                func_id = draft.get("func_id") or draft.get("func_num")
                title = f"Func{func_id}" if func_id is not None else "Agent草案"
                lines.append(f"- {title}：{self._operator_format_params_inline(params)}")
                found = True
        if not found:
            return ""
        lines.append("请确认执行、继续修改参数，或取消指令。")
        return "\n".join(lines)

    def _operator_sync_compound_step_params_from_confirm_plan(self, plan) -> bool:
        draft = getattr(plan, "flow_draft", None)
        if not isinstance(draft, dict) or draft.get("agent_kind") != "compound_step_confirmation":
            return False
        compound_draft = getattr(self, "_operator_pending_flow_draft", None)
        if not isinstance(compound_draft, dict):
            return False
        steps = compound_draft.get("expanded_steps")
        if not isinstance(steps, list):
            return False
        try:
            index = int(draft.get("compound_step_index") or 0)
        except (TypeError, ValueError):
            index = 0
        if index < 0 or index >= len(steps) or not isinstance(steps[index], dict):
            return False
        records = getattr(plan, "atomic_records", {}) or {}
        record = next(iter(records.values()), None)
        params = getattr(record, "params", None)
        if not isinstance(params, dict):
            return False
        step_params = steps[index].get("params")
        if not isinstance(step_params, dict):
            step_params = {}
            steps[index]["params"] = step_params
        step_params.update(dict(params))
        compound_draft["needs_precheck"] = True
        return True

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
        draft = getattr(plan, "flow_draft", None)
        if isinstance(draft, dict):
            params = draft.get("params")
            if isinstance(params, dict):
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
            return True
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
        elif spec.action == "show_execution":
            self._set_workspace_mode("operator")
            self._operator_show_execution()
            self._operator_reply_ui_command_success("已显示流程执行页面。")
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

    def _operator_dashboard_snapshot_dict(self, *, refresh: bool = True) -> dict[str, Any]:
        if hasattr(self, "operator_dashboard_cache"):
            if refresh:
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
        now = self._operator_now_seconds()
        if self._operator_user_is_typing_command():
            last = float(getattr(self, "_operator_last_dashboard_cache_refresh_sec", 0.0) or 0.0)
            if now - last < 0.2:
                return getattr(self.operator_dashboard_cache, "snapshot", None)
        self._operator_last_dashboard_cache_refresh_sec = now
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
        self._operator_replace_pending_speech()
        self._operator_stop_current_speech_best_effort()
        self._operator_current_spoken_text = spoken_text
        self._operator_recent_spoken_text = spoken_text
        self._operator_recent_spoken_until_sec = self._operator_now_seconds() + 12.0
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

    def _operator_replace_pending_speech(self) -> int:
        generation = int(getattr(self, "_operator_speech_generation", 0) or 0) + 1
        self._operator_speech_generation = generation
        self._operator_current_spoken_text = ""
        queue = getattr(self, "operator_broadcast_queue", None)
        if queue is not None:
            messages = queue.messages_since(0)
            if messages:
                self._operator_last_delivered_broadcast_seq = max(message.seq for message in messages)
        return generation

    def _operator_stop_current_speech_best_effort(self) -> None:
        sink = getattr(self, "operator_speech_sink", None)
        stop = getattr(sink, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def _operator_interrupt_current_speech_for_user_input(self) -> None:
        self._operator_replace_pending_speech()
        self._operator_stop_current_speech_best_effort()

    def _operator_stop_current_speech_for_user_voice_only(self) -> None:
        self._operator_stop_current_speech_best_effort()

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
            "six_axis:",
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
        speech_generation = int(getattr(self, "_operator_speech_generation", 0) or 0)
        executor = getattr(self, "_operator_speech_executor", None)
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operator-tts")
            self._operator_speech_executor = executor
        self._operator_speech_async_busy = True
        try:
            future = executor.submit(
                service.deliver,
                tuple(speech_pending),
                should_continue=lambda: int(getattr(self, "_operator_speech_generation", 0) or 0) == speech_generation,
            )
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
        return isinstance(sink, (Pyttsx3SpeechSink, WindowsSapiSpeechSink, DoubaoSpeechSink))

    def _operator_enable_local_tts(self, *, engine: object | None = None):
        if engine is None and WindowsSapiSpeechSink.available():
            sink = WindowsSapiSpeechSink()
        else:
            sink = Pyttsx3SpeechSink(engine=engine)
        self.operator_speech_sink = sink
        return sink

    def _operator_configure_tts_from_settings(self):
        if not bool(getattr(getattr(self, "axis_ranges", None), "operator_tts_enabled", False)):
            self.operator_speech_sink = None
            return None
        import os

        from .env_loader import load_local_env_file

        load_local_env_file()
        provider = os.environ.get("VOICE_TTS_PROVIDER", "local").strip().lower()
        if provider == "doubao":
            self.operator_speech_sink = DoubaoSpeechSink()
            return self.operator_speech_sink
        return self._operator_enable_local_tts()

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
        return bool(actions) and all(
            getattr(action, "action_type", "unknown")
            in {"template", "atomic_template", "flow", "system", "memory", "agent_draft"}
            for action in actions
        )

    @staticmethod
    def _operator_plan_requires_precheck(plan) -> bool:
        if OperatorUiMixin._operator_plan_is_agent_draft(plan):
            return False
        actions = tuple(getattr(plan, "actions", ()) or ())
        if any(getattr(action, "action_type", "") in {"template", "atomic_template", "flow"} for action in actions):
            return True
        return bool(policy_for_plan(plan).requires_precheck)

    @staticmethod
    def _operator_plan_requires_confirmation(plan) -> bool:
        if OperatorUiMixin._operator_plan_is_agent_draft(plan):
            return True
        actions = tuple(getattr(plan, "actions", ()) or ())
        if any(getattr(action, "action_type", "") in {"template", "flow"} for action in actions):
            return True
        return bool(policy_for_plan(plan).requires_confirmation)

    @staticmethod
    def _operator_plan_is_agent_draft(plan) -> bool:
        actions = tuple(getattr(plan, "actions", ()) or ())
        return bool(actions) and all(getattr(action, "action_type", "") == "agent_draft" for action in actions)

    @staticmethod
    def _operator_agent_draft_id(plan) -> str:
        draft = getattr(plan, "flow_draft", {}) or {}
        draft_id = str(draft.get("draft_id") or "").strip() if isinstance(draft, dict) else ""
        if draft_id:
            return draft_id
        actions = tuple(getattr(plan, "actions", ()) or ())
        if not actions:
            return ""
        return str(getattr(actions[0], "target", "") or "").strip()

    @staticmethod
    def _operator_agent_record_to_execution_plan(source_plan, record: QueryRecord):
        from .voice_nlp_adapter import VoiceNlpAction, VoiceNlpPlan

        return VoiceNlpPlan(
            actions=(
                VoiceNlpAction(
                    "atomic_template",
                    record.query_key,
                    "restricted_agent",
                    str(getattr(source_plan, "raw_text", "") or ""),
                    "Agent 草稿已确认，转入现有执行链路。",
                ),
            ),
            source="restricted_agent",
            raw_text=str(getattr(source_plan, "raw_text", "") or ""),
            reason="Agent 草稿已确认，转入现有执行链路。",
            semantic_level=3,
            semantic_label="常规生产执行层",
            requires_precheck=False,
            requires_confirmation=False,
            nlp_engine="restricted_agent",
            atomic_records={record.query_key: record},
        )

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
            self._configure_ui_scale()
            self._resize_to_fit_screen(620, 820)
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
            self._configure_ui_scale()
            self._resize_to_fit_screen(1380, 860)
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
        self._operator_refresh_pending = False
        self._operator_last_refresh_sec = self._operator_now_seconds()
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
        user_typing = self._operator_user_is_typing_command()
        if user_typing:
            self._operator_request_scene(self._operator_desired_scene(), reason="operator_refresh_typing")
            self._sync_operator_mic_button()
            return
        self._refresh_operator_axis_labels()
        self._refresh_operator_scene_content(detail)
        self._refresh_operator_pending_flow_status()
        refresh_heavy_panels = self._operator_heavy_panels_refresh_due()
        if refresh_heavy_panels:
            self._refresh_operator_recent_events()
        self._refresh_operator_dialog_labels()
        if refresh_heavy_panels:
            self._refresh_operator_full_status()
            self._operator_last_heavy_panel_refresh_sec = self._operator_now_seconds()
        self._sync_operator_mic_button()
        self._operator_publish_periodic_reassurance_if_needed()

        scene = self._operator_desired_scene()
        self._operator_request_scene(scene, reason="operator_refresh")

    def _operator_user_is_typing_command(self) -> bool:
        edit = getattr(self, "operator_command_edit", None)
        if edit is None:
            return False
        try:
            if not edit.hasFocus():
                return False
        except Exception:
            return False
        try:
            return bool(str(edit.text() or "").strip())
        except Exception:
            return False

    def _operator_schedule_refresh(self, *, max_rate_hz: float = 10.0) -> None:
        if not hasattr(self, "operator_scene_stack"):
            return
        if bool(getattr(self, "_operator_refresh_pending", False)):
            return
        if self._operator_execution_scene_active():
            max_rate_hz = min(float(max_rate_hz), 4.0)
        now = self._operator_now_seconds()
        last = float(getattr(self, "_operator_last_refresh_sec", 0.0) or 0.0)
        min_interval = 1.0 / max(1.0, float(max_rate_hz))
        delay_ms = max(0, int((min_interval - max(0.0, now - last)) * 1000))
        self._operator_refresh_pending = True
        QTimer.singleShot(delay_ms, self._refresh_operator_view)

    def _operator_heavy_panels_refresh_due(self) -> bool:
        if not self._operator_execution_scene_active():
            return True
        interval = self._operator_heavy_panel_refresh_interval_seconds()
        last = float(getattr(self, "_operator_last_heavy_panel_refresh_sec", 0.0) or 0.0)
        return self._operator_now_seconds() - last >= interval

    @staticmethod
    def _operator_heavy_panel_refresh_interval_seconds() -> float:
        return 1.0

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
        state = (title, value, bool(active))
        if getattr(label, "_operator_badge_state", None) == state:
            return
        setattr(label, "_operator_badge_state", state)
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
        self._refresh_operator_execute_timeline(state_detail, progress=progress)
        self.operator_execute_position.setText(
            f"位置 X:{self.robot_x} Y:{self.robot_y} Z:{self.robot_z} R:{self.robot_r}"
        )

        self.operator_confirm_title.setText("等待确认执行")
        self._operator_set_confirm_detail_html(self._operator_confirm_detail_html())
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
            items = self._operator_flow_execution_timeline_items()
            if items:
                current = next((item for item in items if item["status"] == "current"), items[-1])
                return f"流程：{self._operator_flow_execution_name()}\n步骤：{current['index']} / {len(items)}\n当前：{current['label']}"
            return f"流程：{self.current_flow_name or '-'}"
        if getattr(self, "nlp_sequence_running", False):
            total = len(getattr(self, "_nlp_pending_actions", []))
            current = min(getattr(self, "_nlp_pending_index", 0) + 1, total) if total else 0
            return f"自然语言动作 {current} / {total}"
        return state_detail

    def _operator_flow_execution_name(self) -> str:
        flow_name = str(getattr(self, "current_flow_name", "") or "").strip()
        if not flow_name and hasattr(self, "flow_combo"):
            try:
                flow_name = str(self.flow_combo.currentText() or "").strip()
            except Exception:
                flow_name = ""
        return flow_name

    def _operator_flow_execution_steps(self) -> list[Any]:
        flow_name = self._operator_flow_execution_name()
        if not flow_name:
            return []
        try:
            flow = self.service.get_flow(flow_name)
            return list(getattr(flow, "steps", ()) or ())
        except Exception:
            return []

    def _operator_flow_execution_timeline_items(self) -> list[dict[str, object]]:
        steps = self._operator_flow_execution_steps()
        if not steps:
            return []
        current_index = min(max(int(getattr(self, "flow_step_index", 0) or 0), 0), max(len(steps) - 1, 0))
        items = []
        for index, step in enumerate(steps, start=1):
            zero_index = index - 1
            status = "current" if zero_index == current_index else "done" if zero_index < current_index else "pending"
            items.append(
                {
                    "index": index,
                    "status": status,
                    "label": self._operator_flow_step_display_label(step),
                    "key": str(step or ""),
                }
            )
        return items

    def _operator_flow_step_display_label(self, step: Any) -> str:
        record = self._operator_flow_step_record(step)
        source = record if record is not None else step
        label = str(getattr(source, "description", "") or getattr(source, "action", "") or "").strip()
        if label:
            return label
        if isinstance(step, str):
            return step
        query_key = str(getattr(source, "query_key", "") or "").strip()
        return query_key or "执行动作"

    @staticmethod
    def _operator_compact_flow_step_label(text: str, *, limit: int = 44) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(clean) <= limit:
            return clean or "执行动作"
        return clean[: max(1, limit - 1)].rstrip() + "…"

    def _refresh_operator_execute_timeline(self, state_detail: str, *, progress: int | None) -> None:
        if not hasattr(self, "operator_execute_timeline_layout"):
            return
        if getattr(self, "flow_running", False):
            items = self._operator_flow_execution_timeline_items()
            signature = (
                "flow",
                self._operator_flow_execution_name(),
                tuple((int(item["index"]), str(item["status"]), str(item["label"])) for item in items),
            )
            if signature == getattr(self, "_operator_execute_timeline_signature", None):
                self._operator_update_current_flow_progress(progress)
                current_index = int(getattr(self, "flow_step_index", 0) or 0) + 1
                self._operator_schedule_current_flow_step_scroll_if_needed(current_index)
                return
            self._operator_execute_timeline_signature = signature
            self._operator_clear_layout(self.operator_execute_timeline_layout)
            self.operator_execute_step_widgets = {}
            self.operator_execute_step_progress_bars = {}
            for item in items:
                widget = self._build_operator_flow_step_card(item, progress=progress)
                step_index = int(item["index"])
                self.operator_execute_step_widgets[step_index] = widget
                self.operator_execute_timeline_layout.addWidget(widget)
            current_index = int(getattr(self, "flow_step_index", 0) or 0) + 1
            self._operator_schedule_current_flow_step_scroll_if_needed(current_index)
            return
        self._operator_last_visible_flow_step_index = None
        detail = str(state_detail or "-").strip() or "-"
        signature = ("status", detail)
        if signature == getattr(self, "_operator_execute_timeline_signature", None):
            return
        self._operator_execute_timeline_signature = signature
        self._operator_clear_layout(self.operator_execute_timeline_layout)
        self.operator_execute_step_widgets = {}
        self.operator_execute_step_progress_bars = {}
        self.operator_execute_timeline_layout.addWidget(self._build_operator_execute_info_card(detail))

    @staticmethod
    def _operator_clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _operator_update_current_flow_progress(self, progress: int | None) -> None:
        bars = getattr(self, "operator_execute_step_progress_bars", {}) or {}
        current_index = int(getattr(self, "flow_step_index", 0) or 0) + 1
        bar = bars.get(current_index)
        if bar is None:
            return
        bar.setValue(max(0, min(100, int(progress or 0))))

    def _operator_schedule_current_flow_step_scroll_if_needed(self, step_index: int) -> None:
        try:
            current_index = int(step_index)
        except (TypeError, ValueError):
            return
        if current_index <= 0:
            return
        if getattr(self, "_operator_last_visible_flow_step_index", None) == current_index:
            return
        self._operator_last_visible_flow_step_index = current_index
        for delay_ms in (0, 60, 160, 320):
            QTimer.singleShot(delay_ms, lambda index=current_index: self._operator_scroll_current_flow_step(index))

    def _build_operator_flow_step_card(self, item: dict[str, object], *, progress: int | None) -> QWidget:
        status = str(item.get("status") or "pending")
        step_index = int(item.get("index") or 0)
        label_text = self._operator_compact_flow_step_label(str(item.get("label") or "执行动作"))
        card = QFrame()
        card.setObjectName("operatorFlowStepCard")
        card.setProperty("status", status)
        height = 82 if status == "current" else 66
        card.setMinimumHeight(height)
        card.setMaximumHeight(height)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(7)

        dot = QLabel("✓" if status == "done" else "↻" if status == "current" else "○")
        dot.setObjectName("operatorFlowStepDot")
        dot.setProperty("status", status)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setFixedSize(30, 30)
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(3)
        title = QLabel(f"步骤 {step_index}")
        title.setObjectName("operatorFlowStepTitle")
        title.setProperty("status", status)
        title.setMaximumHeight(20)
        body = QLabel(label_text)
        body.setObjectName("operatorFlowStepBody")
        body.setWordWrap(True)
        body.setMaximumHeight(36)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        text_box.addWidget(title)
        text_box.addWidget(body)
        if status == "current":
            bar = QProgressBar()
            bar.setObjectName("operatorFlowStepProgress")
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, int(progress or 0))))
            bar.setTextVisible(False)
            self.operator_execute_step_progress_bars[step_index] = bar
            text_box.addWidget(bar)
        layout.addLayout(text_box, 1)
        return card

    def _build_operator_execute_info_card(self, text: str) -> QWidget:
        card = QFrame()
        card.setObjectName("operatorFlowStepCard")
        card.setProperty("status", "current")
        card.setMaximumHeight(96)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 9)
        title = QLabel("当前执行")
        title.setObjectName("operatorFlowStepTitle")
        body = QLabel(text)
        body.setObjectName("operatorFlowStepBody")
        body.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(body)
        return card

    def _operator_scroll_current_flow_step(self, step_index: int) -> None:
        widgets = getattr(self, "operator_execute_step_widgets", None)
        if widgets is None:
            widgets = getattr(self, "_operator_execute_step_widgets", {})
        widget = widgets.get(int(step_index))
        scroll = getattr(self, "operator_execute_timeline_scroll", None)
        if widget is None or scroll is None:
            return
        ensure = getattr(scroll, "ensureWidgetVisible", None)
        if callable(ensure):
            ensure(widget, 0, 12)
        try:
            bar = scroll.verticalScrollBar()
            viewport = scroll.viewport()
            y = int(widget.y())
            height = int(widget.height())
            viewport_height = int(viewport.height())
            target = y + height // 2 - viewport_height // 2
            target = max(int(bar.minimum()), min(int(bar.maximum()), target))
            bar.setValue(target)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return

    def _operator_confirm_detail_html(self) -> str:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if plan is None:
            return self._operator_confirm_html_shell("<p class='muted'>当前没有需要确认的指令。</p>")
        flow_draft = getattr(plan, "flow_draft", None)
        if isinstance(flow_draft, dict) and flow_draft.get("agent_kind") == "waiting_confirmation":
            return self._operator_agent_confirm_detail_html(plan, flow_draft)
        return self._operator_plain_confirm_detail_html()

    def _operator_set_confirm_detail_html(self, html_text: str) -> None:
        browser = getattr(self, "operator_confirm_detail", None)
        if browser is None:
            return
        signature = self._operator_confirm_detail_signature()
        previous_signature = getattr(self, "_operator_confirm_html_signature", None)
        previous_html = getattr(self, "_operator_confirm_html_text", None)
        same_confirm_context = signature == previous_signature
        if same_confirm_context and previous_html == html_text:
            return
        scroll_value = 0
        scroll_bar = None
        if same_confirm_context:
            try:
                scroll_bar = browser.verticalScrollBar()
                scroll_value = int(scroll_bar.value())
            except (AttributeError, TypeError, ValueError, RuntimeError):
                scroll_bar = None
                scroll_value = 0
        browser.setHtml(html_text)
        self._operator_confirm_html_signature = signature
        self._operator_confirm_html_text = html_text

        def restore_scroll() -> None:
            bar = scroll_bar
            if bar is None:
                try:
                    bar = browser.verticalScrollBar()
                except (AttributeError, RuntimeError):
                    return
            try:
                target = scroll_value if same_confirm_context else 0
                target = max(int(bar.minimum()), min(int(bar.maximum()), int(target)))
                bar.setValue(target)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                return

        QTimer.singleShot(0, restore_scroll)

    def _operator_confirm_detail_signature(self) -> str:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if plan is None:
            return "none"
        flow_draft = getattr(plan, "flow_draft", None)
        if isinstance(flow_draft, dict):
            draft_id = str(flow_draft.get("draft_id") or flow_draft.get("plan_id") or "").strip()
            if draft_id:
                return draft_id
        actions = tuple(getattr(plan, "actions", ()) or ())
        if actions:
            first = actions[0]
            target = str(getattr(first, "target", "") or "")
            action_type = str(getattr(first, "action_type", "") or "")
            if target or action_type:
                return f"{action_type}:{target}"
        return str(id(plan))

    def _operator_agent_confirm_detail_html(self, plan, draft: dict[str, object]) -> str:
        confirmation_text = str(draft.get("confirmation_text") or "").strip()
        title = self._operator_confirmation_title(confirmation_text, draft)
        params = draft.get("params") if isinstance(draft.get("params"), dict) else {}
        sources = draft.get("param_sources") if isinstance(draft.get("param_sources"), dict) else {}
        precheck = draft.get("precheck_result") if isinstance(draft.get("precheck_result"), dict) else {}

        body_parts = [
            f"<div class='subtitle'>{html.escape(title)}</div>",
            "<div class='hint'>请核对目标位置、姿态、运动参数和安全预检结果，确认后将写入控制器。</div>",
        ]
        position_rows = self._operator_confirm_param_rows(
            params,
            sources,
            (
                ("X", "target_x", "mm"),
                ("Y", "target_y", "mm"),
                ("Z", "target_z", "mm"),
            ),
        )
        if position_rows:
            body_parts.append(self._operator_confirm_section_html("目标位置", position_rows))
        pose_rows = self._operator_confirm_param_rows(
            params,
            sources,
            (
                ("RX", "target_rx", "°"),
                ("RY", "target_ry", "°"),
                ("RZ", "target_rz", "°"),
            ),
        )
        if pose_rows:
            body_parts.append(self._operator_confirm_section_html("姿态", pose_rows))
        motion_rows = self._operator_confirm_param_rows(
            params,
            sources,
            (
                ("速度", "spd_pct", "%"),
                ("加速度", "acc_pct", "%"),
                ("减速度", "dec_pct", "%"),
            ),
        )
        if motion_rows:
            body_parts.append(self._operator_confirm_section_html("运动参数", motion_rows))
        body_parts.append(self._operator_confirm_precheck_html(precheck))
        timeout_text = self._operator_confirm_timeout_text()
        if timeout_text:
            body_parts.append(
                f"<div class='timeout'>{html.escape(timeout_text.replace('确认有效期: ', '确认有效期：'))}</div>"
            )
        suggestion_lines = self._operator_confirm_suggestion_lines(plan)
        if suggestion_lines:
            body_parts.append(self._operator_confirm_note_html("可采纳安全建议", suggestion_lines))
        body_parts.append(self._operator_confirm_mode_hint_html())
        return self._operator_confirm_html_shell("".join(body_parts))

    def _operator_plain_confirm_detail_html(self) -> str:
        lines = [line.strip() for line in self._operator_confirm_detail_text().splitlines() if line.strip()]
        if not lines:
            return self._operator_confirm_html_shell("<p class='muted'>当前没有需要确认的指令。</p>")
        title = html.escape(lines[0])
        paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in lines[1:])
        return self._operator_confirm_html_shell(
            f"<div class='subtitle'>{title}</div>{paragraphs}{self._operator_confirm_mode_hint_html()}"
        )

    def _operator_confirm_precheck_html(self, precheck: dict[str, object]) -> str:
        valid = precheck.get("valid")
        summary = str(precheck.get("summary") or "无补充说明。")
        if valid is True:
            badge = "<span class='badge ok'>通过</span>"
            line = f"{badge}<span>L1 安全检查：{html.escape(summary)}</span>"
        elif valid is False:
            badge = "<span class='badge bad'>未通过</span>"
            line = f"{badge}<span>L1 安全检查：{html.escape(summary)}</span>"
        else:
            badge = "<span class='badge warn'>未执行</span>"
            line = f"{badge}<span>L1 安全检查：{html.escape(summary)}</span>"
        l2_text = self._operator_confirm_l2_decision_text()
        rows = [f"<div class='checkrow'>{line}</div>"]
        if l2_text:
            rows.append(f"<div class='checkrow'><span class='badge warn'>提示</span><span>{html.escape(l2_text)}</span></div>")
        return "<div class='section'><div class='section-title'>安全预检</div>" + "".join(rows) + "</div>"

    def _operator_confirm_param_rows(
        self,
        params: dict[str, object],
        sources: dict[str, object],
        fields: tuple[tuple[str, str, str], ...],
    ) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for label, key, unit in fields:
            if key not in params:
                continue
            rows.append(
                (
                    label,
                    self._operator_confirm_value_text(params.get(key), unit=unit),
                    self._operator_confirm_source_label(str(sources.get(key, "") or "")),
                )
            )
        return rows

    @staticmethod
    def _operator_confirm_value_text(value: object, *, unit: str) -> str:
        try:
            numeric = float(value)
            text = f"{numeric:.1f}"
        except (TypeError, ValueError):
            text = str(value)
        return f"{text} {unit}".strip()

    @staticmethod
    def _operator_confirm_source_label(source: str) -> str:
        return {
            "specified": "指定",
            "inherited": "继承当前",
            "incremental": "增量计算",
            "controller": "继承安全参数",
            "default": "默认",
            "system": "系统",
        }.get(source, source or "未知")

    @staticmethod
    def _operator_confirmation_title(confirmation_text: str, draft: dict[str, object]) -> str:
        for line in confirmation_text.splitlines():
            clean = line.strip()
            if clean:
                return clean.replace("【复述确认】", "")
        func_id = draft.get("func_id")
        return f"Func{func_id} 待确认指令" if func_id is not None else "待确认指令"

    @staticmethod
    def _operator_confirm_section_html(title: str, rows: list[tuple[str, str, str]]) -> str:
        body = "".join(
            "<tr>"
            f"<td class='name'>{html.escape(name)}</td>"
            f"<td class='value'>{html.escape(value)}</td>"
            f"<td class='source'>{html.escape(source)}</td>"
            "</tr>"
            for name, value, source in rows
        )
        return (
            "<div class='section'>"
            f"<div class='section-title'>{html.escape(title)}</div>"
            "<table class='param-table' cellspacing='0' cellpadding='0'>"
            f"{body}"
            "</table>"
            "</div>"
        )

    @staticmethod
    def _operator_confirm_note_html(title: str, lines: list[str]) -> str:
        items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
        return f"<div class='section'><div class='section-title'>{html.escape(title)}</div><ul>{items}</ul></div>"

    @staticmethod
    def _operator_confirm_mode_hint_html() -> str:
        return (
            "<div class='section mode-hint'>"
            "<div class='section-title'>当前模式：等待确认</div>"
            "<p>可以说：确认执行、取消指令、速度改为50%、加速度改为50%、现在的运动参数是哪些</p>"
            "</div>"
        )

    @staticmethod
    def _operator_confirm_html_shell(body: str) -> str:
        return f"""
        <html>
        <head>
        <style>
            body {{
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                color: #122033;
                font-size: 13px;
                line-height: 1.35;
                margin: 0;
            }}
            .subtitle {{
                font-size: 16px;
                font-weight: 700;
                margin: 0 0 4px 0;
            }}
            .hint, .muted {{
                color: #667085;
                margin-bottom: 10px;
            }}
            .section {{
                margin-top: 10px;
                padding-top: 8px;
                border-top: 1px solid #e5e7eb;
            }}
            .section-title {{
                font-weight: 700;
                color: #334155;
                margin-bottom: 5px;
            }}
            .param-table {{
                width: 100%;
            }}
            .param-table td {{
                padding: 3px 4px;
                vertical-align: middle;
            }}
            .name {{
                width: 34px;
                color: #475467;
                font-weight: 700;
            }}
            .value {{
                color: #101828;
                font-weight: 700;
            }}
            .source {{
                color: #667085;
                text-align: right;
                white-space: nowrap;
            }}
            .checkrow {{
                margin: 4px 0;
            }}
            .badge {{
                display: inline-block;
                min-width: 34px;
                padding: 1px 5px;
                margin-right: 6px;
                border-radius: 4px;
                font-weight: 700;
            }}
            .ok {{ color: #067647; background: #ecfdf3; }}
            .warn {{ color: #b54708; background: #fffaeb; }}
            .bad {{ color: #b42318; background: #fef3f2; }}
            .timeout {{
                margin-top: 12px;
                color: #344054;
                font-weight: 700;
            }}
            p {{
                margin: 4px 0;
            }}
            .confirm-bottom-spacer {{
                height: 56px;
            }}
        </style>
        </head>
        <body>{body}<div class="confirm-bottom-spacer"></div></body>
        </html>
        """

    def _operator_confirm_detail_text(self) -> str:
        plan = getattr(self, "_operator_pending_confirm_plan", None)
        if not self._operator_plan_is_executable(plan):
            return "当前没有需要确认的风险。"
        if self._operator_plan_is_agent_draft(plan):
            draft = getattr(plan, "flow_draft", {}) or {}
            confirmation_text = ""
            if isinstance(draft, dict):
                confirmation_text = str(draft.get("confirmation_text") or "").strip()
            parts = [confirmation_text or str(getattr(plan, "reason", "") or "Agent 草稿等待确认。")]
            timeout_text = self._operator_confirm_timeout_text()
            if timeout_text:
                parts.append(timeout_text)
            return "\n\n".join(part for part in parts if part)
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

    def _operator_add_chat_message(self, role: str, text: str, *, scroll_to_bottom: bool = True, kind: str = "") -> None:
        clean_text = (text or "").strip()
        if not clean_text:
            return
        if not hasattr(self, "_operator_chat_messages"):
            self._operator_chat_messages = []
        if not hasattr(self, "_operator_chat_thinking_steps"):
            self._operator_chat_thinking_steps = [[] for _ in self._operator_chat_messages]
        if not hasattr(self, "_operator_chat_thinking_meta"):
            self._operator_chat_thinking_meta = [{} for _ in self._operator_chat_messages]
        if role == "assistant" and self._operator_replace_current_streaming_chat_message(clean_text):
            return
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
        message_index = len(self._operator_chat_messages) - 1
        if self._operator_append_chat_row_if_possible(message_index):
            if scroll_to_bottom:
                self._operator_scroll_chat_to_bottom()
            return
        self._render_operator_chat()

    def _operator_append_chat_row_if_possible(self, message_index: int) -> bool:
        if not getattr(self, "_operator_chat_rendered", False):
            return False
        layout = getattr(self, "operator_chat_layout", None)
        if layout is None:
            return False
        messages = getattr(self, "_operator_chat_messages", [])
        if message_index < 0 or message_index >= len(messages):
            return False
        role, text = messages[message_index]
        steps = getattr(self, "_operator_chat_thinking_steps", [])
        metas = getattr(self, "_operator_chat_thinking_meta", [])
        thinking_steps = steps[message_index] if message_index < len(steps) else []
        thinking_meta = metas[message_index] if message_index < len(metas) else {}
        try:
            row = self._build_operator_chat_row(
                role,
                text,
                thinking_steps=thinking_steps,
                thinking_meta=thinking_meta,
                message_index=message_index,
            )
            count = layout.count()
            insert_at = count
            if count > 0:
                last_item = layout.itemAt(count - 1)
                if last_item is not None and last_item.widget() is None:
                    insert_at = count - 1
            layout.insertWidget(insert_at, row)
            return True
        except Exception:
            return False

    def _operator_current_streaming_chat_index(self, *, include_inactive: bool = False) -> int | None:
        messages = getattr(self, "_operator_chat_messages", None)
        if not isinstance(messages, list) or not messages:
            return None
        candidates = (getattr(self, "_operator_streaming_chat_message_index", None),)
        metas = getattr(self, "_operator_chat_thinking_meta", [])
        for candidate in candidates:
            if isinstance(candidate, int) and 0 <= candidate < len(messages) and messages[candidate][0] == "assistant":
                if include_inactive or getattr(self, "_operator_streaming_chat_active", False):
                    return candidate
                if isinstance(metas, list) and candidate < len(metas) and bool(metas[candidate].get("active")):
                    return candidate
        if isinstance(metas, list):
            for index in range(min(len(messages), len(metas)) - 1, -1, -1):
                if messages[index][0] == "assistant" and bool(metas[index].get("active")):
                    return index
        return None

    def _operator_replace_current_streaming_chat_message(self, text: str) -> bool:
        index = self._operator_current_streaming_chat_index()
        if index is None:
            return False
        self._operator_complete_streaming_chat_response(text, message_index=index)
        return True

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

    def _operator_maybe_begin_agent_processing_response(self, text: str) -> bool:
        if getattr(self, "_operator_streaming_chat_active", False):
            return True
        try:
            enabled = bool(self._operator_agent_llm_fallback_enabled())
        except Exception:
            enabled = False
        if not enabled:
            return False
        self._operator_begin_agent_processing_response(text)
        self._operator_process_pending_ui_events()
        return True

    def _operator_begin_agent_processing_response(self, text: str) -> None:
        self._operator_begin_streaming_chat_response(
            initial_steps=["正在理解上下文", "读取当前对话和流程状态", "等待 AI 上下文解释"],
            final_steps=["识别上下文意图", "本地安全策略复核", "生成可执行前提示，未直接控制机械手"],
        )
        if hasattr(self, "status_label"):
            self.status_label.setText("正在结合上下文理解指令，请稍候。")

    @staticmethod
    def _operator_process_pending_ui_events() -> None:
        try:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:
            pass

    @staticmethod
    def _operator_text_looks_like_streaming_chat(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False
        compact = strip_wake_word_from_compact(compact)
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

    def _operator_begin_streaming_chat_response(
        self,
        *,
        initial_steps: list[str] | None = None,
        final_steps: list[str] | None = None,
    ) -> None:
        self._operator_streaming_chat_active = True
        self._operator_streaming_chat_text = ""
        self._operator_streaming_chat_pending_chars = []
        self._operator_streaming_chat_finalizing = False
        self._operator_streaming_chat_final_text = ""
        self._operator_streaming_chat_char_timer_active = False
        self._operator_streaming_chat_started_sec = self._operator_now_seconds()
        self._operator_streaming_chat_last_render_sec = 0.0
        self._operator_streaming_chat_render_pending = False
        self._operator_streaming_chat_initial_steps_override = list(initial_steps) if initial_steps else None
        self._operator_streaming_chat_final_steps_override = list(final_steps) if final_steps else None
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
            self._operator_streaming_chat_message_index = len(self._operator_chat_messages) - 1
        else:
            self._operator_chat_messages.append(("assistant", ""))
            self._operator_chat_thinking_steps.append(self._operator_streaming_chat_initial_steps())
            self._operator_chat_thinking_meta.append(initial_meta)
            self._operator_streaming_chat_message_index = len(self._operator_chat_messages) - 1
        self._operator_pending_streaming_chat_final_text = ""
        self._operator_chat_messages = self._operator_chat_messages[-80:]
        self._operator_chat_thinking_steps = self._operator_chat_thinking_steps[-80:]
        self._operator_chat_thinking_meta = self._operator_chat_thinking_meta[-80:]
        self._operator_streaming_chat_message_index = min(
            len(self._operator_chat_messages) - 1,
            int(getattr(self, "_operator_streaming_chat_message_index", len(self._operator_chat_messages) - 1) or 0),
        )
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
        batch_size = self._operator_streaming_chat_typewriter_batch_size()
        chars = [str(pending.pop(0)) for _ in range(min(batch_size, len(pending)))]
        current = str(getattr(self, "_operator_streaming_chat_text", "") or "") + "".join(chars)
        self._operator_streaming_chat_text = current
        if not hasattr(self, "_operator_chat_messages") or not self._operator_chat_messages:
            self._operator_chat_messages = [("assistant", current)]
            self._operator_chat_thinking_steps = [self._operator_streaming_chat_initial_steps()]
            self._operator_chat_thinking_meta = [{"active": True, "started_sec": self._operator_now_seconds()}]
            self._operator_streaming_chat_message_index = 0
        else:
            index = self._operator_current_streaming_chat_index()
            if index is None:
                index = len(self._operator_chat_messages) - 1
                self._operator_streaming_chat_message_index = index
            self._operator_chat_messages[index] = ("assistant", current)
        label = getattr(self, "_operator_streaming_chat_content_label", None)
        if label is not None and hasattr(label, "setText"):
            try:
                label.setText(self._operator_chat_display_text("assistant", current))
                if hasattr(label, "setVisible"):
                    label.setVisible(True)
                self._operator_chat_autoscroll_pending = True
                self._operator_scroll_chat_to_bottom_throttled()
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
        self._operator_scroll_chat_to_bottom_throttled()
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

    def _operator_streaming_chat_initial_steps(self) -> list[str]:
        override = getattr(self, "_operator_streaming_chat_initial_steps_override", None)
        if isinstance(override, list) and override:
            return [str(item) for item in override]
        return ["正在思考", "识别为普通问答", "检索本地资料"]

    def _operator_streaming_chat_final_steps(self) -> list[str]:
        override = getattr(self, "_operator_streaming_chat_final_steps_override", None)
        if isinstance(override, list) and override:
            return [str(item) for item in override]
        return ["识别为普通问答", "基于本地资料整理回答", "AI 生成回答，未触发机械手动作"]

    @staticmethod
    def _operator_streaming_chat_render_interval_seconds() -> float:
        return 0.06

    @staticmethod
    def _operator_streaming_chat_typewriter_interval_seconds() -> float:
        return 0.025

    @staticmethod
    def _operator_streaming_chat_typewriter_batch_size() -> int:
        return 4

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

    def _operator_complete_streaming_chat_response(self, final_text: str, *, message_index: int | None = None) -> None:
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
        index = message_index
        if index is None:
            index = self._operator_current_streaming_chat_index(include_inactive=True)
        if index is None and self._operator_chat_messages:
            index = len(self._operator_chat_messages) - 1
        if clean_text:
            if index is None:
                self._operator_chat_messages.append(("assistant", clean_text))
                index = len(self._operator_chat_messages) - 1
            else:
                self._operator_chat_messages[index] = ("assistant", clean_text)
            if not hasattr(self, "_operator_chat_thinking_steps"):
                self._operator_chat_thinking_steps = [[] for _ in self._operator_chat_messages]
            if not hasattr(self, "_operator_chat_thinking_meta"):
                self._operator_chat_thinking_meta = [{} for _ in self._operator_chat_messages]
            while len(self._operator_chat_thinking_steps) <= index:
                self._operator_chat_thinking_steps.append([])
            while len(self._operator_chat_thinking_meta) <= index:
                self._operator_chat_thinking_meta.append({})
            self._operator_chat_thinking_steps[index] = self._operator_streaming_chat_final_steps()
            started = float(
                self._operator_chat_thinking_meta[index].get(
                    "started_sec",
                    getattr(self, "_operator_streaming_chat_started_sec", self._operator_now_seconds()),
                )
                or self._operator_now_seconds()
            )
            elapsed_sec = max(0, int(self._operator_now_seconds() - started))
            self._operator_chat_thinking_meta[index] = {"active": False, "elapsed_sec": elapsed_sec}
        else:
            if index is not None:
                self._operator_chat_messages[index:index + 1] = []
                if hasattr(self, "_operator_chat_thinking_steps"):
                    self._operator_chat_thinking_steps[index:index + 1] = []
                if hasattr(self, "_operator_chat_thinking_meta"):
                    self._operator_chat_thinking_meta[index:index + 1] = []
        self._operator_streaming_chat_message_index = None
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
        if self._operator_handle_compound_step_log(entry):
            return
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
            if self._operator_should_suppress_outer_flow_completion(entry):
                return
            self._operator_archive_execution_from_log(entry, message.text)
            self._operator_publish_response(message)
            self._operator_route_voice_recognition_from_log(entry)
            self._operator_note_flow_completion_response(entry)
            return
        if category == "自然语言":
            if action == "动作序列完成" and result == "成功":
                self._operator_add_chat_message("assistant", detail or "执行完成。")
                return
            if action == "动作序列终止":
                self._operator_add_chat_message("assistant", f"执行失败：{detail or '动作序列已终止'}")
                return

    def _operator_handle_compound_step_log(self, entry: dict[str, Any]) -> bool:
        if str(entry.get("category", "")) != "自然语言":
            return False
        action = str(entry.get("action", "") or "")
        result = str(entry.get("result", "") or "")
        detail = str(entry.get("detail", "") or "")
        if "compound_step" not in detail:
            return False
        if not action.startswith("动作序列第"):
            return False
        if result == "成功" or action.endswith("成功"):
            return self._operator_update_compound_step_result(ok=True, reason=detail)
        if result == "失败" or action.endswith("失败"):
            return self._operator_update_compound_step_result(ok=False, reason=detail)
        return False

    def _operator_note_flow_completion_response(self, entry: dict[str, Any]) -> None:
        if str(entry.get("category", "")) != "流程":
            return
        action = str(entry.get("action", ""))
        if not action.startswith("流程完成 "):
            return
        if str(entry.get("result", "")) != "成功":
            return
        self._operator_last_flow_completion_response_sec = self._operator_now_seconds()

    def _operator_should_suppress_outer_flow_completion(self, entry: dict[str, Any]) -> bool:
        if str(entry.get("category", "")) != "自然语言":
            return False
        if str(entry.get("action", "")) != "动作序列完成":
            return False
        if str(entry.get("result", "")) != "成功":
            return False
        detail = str(entry.get("detail", "") or "")
        if "共执行 1 步" not in detail:
            return False
        last_flow_sec = float(getattr(self, "_operator_last_flow_completion_response_sec", 0.0) or 0.0)
        if last_flow_sec <= 0:
            return False
        return self._operator_now_seconds() - last_flow_sec <= 3.0

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

    def _operator_scroll_chat_to_bottom_throttled(self) -> None:
        now = self._operator_now_seconds()
        last = float(getattr(self, "_operator_last_streaming_chat_scroll_sec", 0.0) or 0.0)
        if now - last < 0.15:
            return
        self._operator_last_streaming_chat_scroll_sec = now
        self._operator_scroll_chat_to_bottom()

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

        display_text = self._operator_chat_display_text(role, text)
        content = QLabel(display_text)
        content.setObjectName("operatorChatText")
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if is_user:
            content.setStyleSheet("color: #111827;")
        if is_user and bool((thinking_meta or {}).get("voice_recognition_status")):
            content.setMinimumWidth(220)
            content.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            if message_index is not None:
                self._operator_voice_recognition_status_label = content
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
    def _operator_chat_display_text(role: str, text: str) -> str:
        clean = str(text or "")
        if role != "assistant" or not clean.strip():
            return clean
        return OperatorUiMixin._operator_format_flow_steps_for_display(clean)

    @staticmethod
    def _operator_format_flow_steps_for_display(text: str) -> str:
        clean = str(text or "").replace("**", "").strip()
        compact = re.sub(r"\s+", "", clean)
        if "流程" not in compact and "草案" not in compact:
            return clean
        if not re.search(r"\d+[\.、]\s*\S+", clean):
            return clean

        suffix_patterns = (
            "请问是否需要",
            "请确认",
            "请通过",
            "当前仅生成",
            "可说",
        )
        suffix_start = len(clean)
        for marker in suffix_patterns:
            pos = clean.find(marker)
            if pos > 0:
                suffix_start = min(suffix_start, pos)
        body = clean[:suffix_start].strip()
        suffix = clean[suffix_start:].strip()

        step_matches = list(re.finditer(r"(?<!\w)(\d+)[\.、]\s*", body))
        if len(step_matches) < 2:
            return clean

        intro = body[: step_matches[0].start()].strip()
        intro = intro.rstrip("：:")
        steps: list[tuple[str, str]] = []
        for index, match in enumerate(step_matches):
            next_start = step_matches[index + 1].start() if index + 1 < len(step_matches) else len(body)
            step_text = body[match.end() : next_start].strip()
            step_text = step_text.rstrip("；;，,")
            if step_text:
                steps.append((match.group(1), step_text))
        if len(steps) < 2:
            return clean

        lines: list[str] = []
        if intro:
            lines.append(intro)
            lines.append("")
        for number, step_text in steps:
            lines.append(f"步骤 {number}")
            lines.append(step_text)
            lines.append("")
        if suffix:
            lines.append(suffix)
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

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
        if not hasattr(self, "operator_mic_btn"):
            return
        active = bool(getattr(self, "_voice_session_active", False))
        self.operator_mic_btn.setText("结束会话" if active else "开启会话")
        self.operator_mic_btn.setEnabled(True)
        if hasattr(self, "mic_toggle_btn"):
            self.mic_toggle_btn.setText("结束会话" if active else "开始录音")

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
