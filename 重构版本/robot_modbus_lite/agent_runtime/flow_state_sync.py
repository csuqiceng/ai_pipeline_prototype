from __future__ import annotations

from typing import Any


def sync_pending_flow_draft(bridge: Any, *, thread_id: str, draft: Any) -> Any:
    stored = dict(draft) if isinstance(draft, dict) else draft
    if isinstance(stored, dict):
        bridge.set_flow_draft(stored, thread_id=str(thread_id or "operator-ui"))
    else:
        bridge.clear_flow_draft(thread_id=str(thread_id or "operator-ui"))
    return stored
