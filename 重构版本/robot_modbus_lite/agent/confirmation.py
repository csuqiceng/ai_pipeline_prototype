"""Confirmation lifecycle for restricted Agent command drafts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from robot_modbus_lite.agent.drafts import CommandDraft, draft_to_query_record
from robot_modbus_lite.models import QueryRecord


class DraftStatus(str, Enum):
    WAITING_CONFIRMATION = "waiting_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PRECHECK_FAILED = "precheck_failed"


class ConfirmationError(RuntimeError):
    """Raised when a draft cannot transition through confirmation."""


@dataclass(frozen=True)
class DraftSession:
    draft: CommandDraft
    created_at: float
    expires_at: float
    status_signature: str
    safety_signature: str
    status: DraftStatus = DraftStatus.WAITING_CONFIRMATION
    precheck_failure: dict[str, Any] | None = None

    @property
    def draft_id(self) -> str:
        return self.draft.draft_id


class ConfirmationAgent:
    """Render confirmations and enforce one-use draft confirmation."""

    def __init__(self, *, timeout_sec: float = 60.0) -> None:
        self.timeout_sec = max(float(timeout_sec), 0.001)
        self._sessions: dict[str, DraftSession] = {}

    def render_confirmation_text(self, draft: CommandDraft) -> str:
        if draft.func_id in {8, 102, 108, 112}:
            return self._render_func108(draft)
        if draft.func_id in {109, 110}:
            label = "并行延时" if draft.func_id == 110 else "阻塞延时"
            return "\n".join(
                (
                    f"【复述确认】Func{draft.func_id} {label}",
                    f"将执行{label} {draft.params['delay_sec']} 秒（{_source_label(draft, 'delay_sec')}）。",
                    f"原始指令：{draft.raw_text}",
                )
            )
        if draft.func_id == 120:
            action = "打开" if int(draft.params["io_action"]) == 1 else "关闭"
            return "\n".join(
                (
                    "【复述确认】Func120 IO控制",
                    f"将{action} IO-{draft.params['io_no']}（{_source_label(draft, 'io_no')}）。",
                    f"原始指令：{draft.raw_text}",
                )
            )
        if draft.func_id in {106, 107}:
            label = "关节轴点动" if draft.func_id == 106 else "虚拟轴点动"
            return "\n".join(
                (
                    f"【复述确认】Func{draft.func_id} {label}",
                    (
                        f"轴号={draft.params['axis_no']}（{_source_label(draft, 'axis_no')}）  "
                        f"目标/步长={draft.params['pos_val']}（{_source_label(draft, 'pos_val')}）"
                    ),
                    (
                        f"速度={draft.params['spd_pct']}%（{_source_label(draft, 'spd_pct')}）  "
                        f"加速度={draft.params['acc_pct']}%（{_source_label(draft, 'acc_pct')}）  "
                        f"减速度={draft.params['dec_pct']}%（{_source_label(draft, 'dec_pct')}）"
                    ),
                    f"原始指令：{draft.raw_text}",
                    "确认执行？",
                )
            )
        return f"【复述确认】Func{draft.func_id}\n原始指令：{draft.raw_text}"

    def begin(
        self,
        draft: CommandDraft,
        *,
        now: float,
        status_signature: str,
        safety_signature: str,
    ) -> DraftSession:
        session = DraftSession(
            draft=draft,
            created_at=float(now),
            expires_at=float(now) + self.timeout_sec,
            status_signature=str(status_signature),
            safety_signature=str(safety_signature),
        )
        self._sessions[draft.draft_id] = session
        return session

    def confirm(
        self,
        draft_id: str,
        *,
        now: float,
        status_signature: str,
        safety_signature: str,
    ) -> QueryRecord:
        session = self._require_session(draft_id)
        self._ensure_waiting(session)
        if float(now) > session.expires_at:
            self._set_status(draft_id, DraftStatus.EXPIRED)
            raise ConfirmationError("草稿已过期，请重新生成。")
        if str(status_signature) != session.status_signature or str(safety_signature) != session.safety_signature:
            self._set_status(draft_id, DraftStatus.EXPIRED)
            raise ConfirmationError("控制器状态已变化，请重新预检后再确认。")

        confirmed = replace(session.draft, confirmed=True)
        self._sessions[draft_id] = replace(session, draft=confirmed, status=DraftStatus.CONFIRMED)
        return draft_to_query_record(confirmed)

    def reject(self, draft_id: str) -> None:
        session = self._require_session(draft_id)
        self._ensure_waiting(session)
        self._set_status(draft_id, DraftStatus.REJECTED)

    def expire(self, draft_id: str) -> None:
        session = self._require_session(draft_id)
        self._ensure_waiting(session)
        self._set_status(draft_id, DraftStatus.EXPIRED)

    def mark_precheck_failed(self, draft_id: str, precheck_result: dict[str, Any]) -> None:
        session = self._require_session(draft_id)
        self._ensure_waiting(session)
        self._sessions[draft_id] = replace(
            session,
            status=DraftStatus.PRECHECK_FAILED,
            precheck_failure=dict(precheck_result),
        )

    def get_status(self, draft_id: str) -> DraftStatus | None:
        session = self._sessions.get(draft_id)
        return None if session is None else session.status

    def get_session(self, draft_id: str) -> DraftSession | None:
        return self._sessions.get(str(draft_id))

    def _require_session(self, draft_id: str) -> DraftSession:
        session = self._sessions.get(draft_id)
        if session is None:
            raise ConfirmationError(f"未找到草稿: {draft_id}")
        return session

    def _ensure_waiting(self, session: DraftSession) -> None:
        if session.status == DraftStatus.PRECHECK_FAILED:
            raise ConfirmationError("预检失败，请修改参数后重新生成草稿。")
        if session.status != DraftStatus.WAITING_CONFIRMATION:
            raise ConfirmationError(f"草稿已结束: {session.status.value}")

    def _set_status(self, draft_id: str, status: DraftStatus) -> None:
        self._sessions[draft_id] = replace(self._sessions[draft_id], status=status)

    @staticmethod
    def _render_func108(draft: CommandDraft) -> str:
        p = draft.params
        precheck = draft.precheck_result or {}
        valid = precheck.get("valid")
        if valid is True:
            precheck_line = f"安全预检：通过，{precheck.get('summary', '无补充说明')}"
        elif valid is False:
            precheck_line = f"安全预检：未通过，{precheck.get('summary', '无补充说明')}"
        else:
            precheck_line = "安全预检：未执行"
        return "\n".join(
            (
                _linear_motion_title(draft.func_id),
                (
                    f"X={p['target_x']}（{_source_label(draft, 'target_x')}）  "
                    f"Y={p['target_y']}（{_source_label(draft, 'target_y')}）  "
                    f"Z={p['target_z']}（{_source_label(draft, 'target_z')}）"
                ),
                (
                    f"RX={p['target_rx']}°（{_source_label(draft, 'target_rx')}）  "
                    f"RY={p['target_ry']}°（{_source_label(draft, 'target_ry')}）  "
                    f"RZ={p['target_rz']}°（{_source_label(draft, 'target_rz')}）"
                ),
                (
                    f"速度={p['spd_pct']}%（{_source_label(draft, 'spd_pct')}）  "
                    f"加速度={p['acc_pct']}%（{_source_label(draft, 'acc_pct')}）  "
                    f"减速度={p['dec_pct']}%（{_source_label(draft, 'dec_pct')}）"
                ),
                _position_mode_line(draft),
                precheck_line,
                "确认执行？",
            )
        )


def _source_label(draft: CommandDraft, key: str) -> str:
    source = draft.param_sources.get(key, "")
    return {
        "specified": "指定",
        "inherited": "继承当前",
        "incremental": "增量计算",
        "controller": "继承安全参数",
        "default": "默认",
        "system": "系统",
    }.get(source, source or "未知")


def _linear_motion_title(func_id: int) -> str:
    if int(func_id) == 112:
        return "【复述确认】Func112 连续路径运动"
    if int(func_id) in {8, 102}:
        return f"【复述确认】Func{int(func_id)} 绝对运动"
    return "【复述确认】Func108 直线插补/PTP"


def _position_mode_line(draft: CommandDraft) -> str:
    params = draft.params
    try:
        incremental = int(float(params.get("position_increment", 0) or 0)) == 1
    except (TypeError, ValueError):
        incremental = False
    if not incremental:
        try:
            incremental = int(float(params.get("fuzzy_pos", 0) or 0)) == 1
        except (TypeError, ValueError):
            incremental = False
    return "模式：增量定位" if incremental else "模式：绝对定位"
