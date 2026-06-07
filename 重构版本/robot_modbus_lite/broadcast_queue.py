"""Small in-memory queue for operator-facing broadcasts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Callable


@dataclass(frozen=True)
class BroadcastMessage:
    """A queued message to show in the operator chat or speak later."""

    seq: int
    kind: str
    text: str
    speech_text: str = ""
    priority: str = "normal"
    ts: str = ""
    context_id: str | None = None


class BroadcastQueue:
    """Maintains recent operator broadcasts with monotonically increasing seq."""

    def __init__(self, max_messages: int = 100, *, clock: Callable[[], float] | None = None) -> None:
        self.max_messages = max(1, int(max_messages))
        self._messages: list[BroadcastMessage] = []
        self._next_seq = 1
        self._clock = clock or time.monotonic
        self._last_dedupe_at: dict[str, float] = {}

    def publish(
        self,
        *,
        kind: str,
        text: str,
        speech_text: str = "",
        priority: str = "normal",
        context_id: str | None = None,
    ) -> BroadcastMessage:
        message = BroadcastMessage(
            seq=0,
            kind=kind,
            text=text,
            speech_text=speech_text,
            priority=priority,
            context_id=context_id,
        )
        return self.publish_message(message)

    def publish_message(self, message: BroadcastMessage) -> BroadcastMessage:
        published = BroadcastMessage(
            seq=self._next_seq,
            kind=message.kind,
            text=message.text,
            speech_text=message.speech_text,
            priority=message.priority,
            ts=message.ts or datetime.now().isoformat(timespec="milliseconds"),
            context_id=message.context_id,
        )
        self._next_seq += 1
        self._messages.append(published)
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]
        return published

    def publish_once(
        self,
        *,
        kind: str,
        text: str,
        speech_text: str = "",
        priority: str = "normal",
        context_id: str | None = None,
        dedupe_key: str,
        dedupe_window_seconds: float = 5.0,
    ) -> BroadcastMessage | None:
        now = float(self._clock())
        last_seen = self._last_dedupe_at.get(dedupe_key)
        if last_seen is not None and now - last_seen < float(dedupe_window_seconds):
            return None
        self._last_dedupe_at[dedupe_key] = now
        return self.publish(kind=kind, text=text, speech_text=speech_text, priority=priority, context_id=context_id)

    def messages_since(self, last_seq: int) -> list[BroadcastMessage]:
        return [message for message in self._messages if message.seq > last_seq]

    def messages_since_for_delivery(self, last_seq: int) -> list[BroadcastMessage]:
        pending = self.messages_since(last_seq)
        return sorted(pending, key=lambda message: (self._priority_rank(message.priority), message.seq))

    @staticmethod
    def _priority_rank(priority: str) -> int:
        ranks = {"high": 0, "normal": 1, "low": 2}
        return ranks.get(str(priority).lower(), 1)
