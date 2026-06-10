from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PendingConfirmSyncResult:
    plan: Any
    deadline_sec: float


def sync_pending_confirm_plan(
    bridge: Any,
    *,
    thread_id: str,
    plan: Any,
    now_seconds: Callable[[], float],
    timeout_seconds: Callable[[], float],
) -> PendingConfirmSyncResult:
    clean_thread_id = str(thread_id or "operator-ui")
    if plan is None:
        bridge.clear_pending_confirm(thread_id=clean_thread_id)
        return PendingConfirmSyncResult(plan=None, deadline_sec=0.0)

    deadline_sec = float(now_seconds()) + float(timeout_seconds())
    bridge.set_pending_confirm(plan, thread_id=clean_thread_id, expires_at=deadline_sec)
    return PendingConfirmSyncResult(plan=plan, deadline_sec=deadline_sec)
