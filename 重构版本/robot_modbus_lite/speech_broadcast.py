"""Speech delivery adapter for operator broadcasts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .broadcast_queue import BroadcastMessage


class SpeechSink(Protocol):
    """Output target for spoken operator messages."""

    def speak(self, text: str) -> None:
        ...


@dataclass(frozen=True)
class SpeechDeliveryResult:
    success: bool
    delivered_seq: tuple[int, ...] = ()
    error: str = ""


class CallableSpeechSink:
    """Wraps a plain callable as a speech sink."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def speak(self, text: str) -> None:
        self._callback(text)


class Pyttsx3SpeechSink:
    """Optional local TTS sink backed by pyttsx3."""

    def __init__(self, engine: object | None = None) -> None:
        self._engine = engine

    def speak(self, text: str) -> None:
        engine = self._engine
        if engine is None:
            try:
                import pyttsx3  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError("未安装 pyttsx3，无法使用本地 TTS 播报。") from exc
            engine = pyttsx3.init()
            self._engine = engine
        engine.say(text)
        engine.runAndWait()


class SpeechBroadcastDeliveryService:
    """Delivers queued broadcast messages to a configured speech sink."""

    def __init__(self, sink: SpeechSink | None = None) -> None:
        self.sink = sink

    def deliver(self, messages: list[BroadcastMessage] | tuple[BroadcastMessage, ...]) -> SpeechDeliveryResult:
        if not messages:
            return SpeechDeliveryResult(success=True)
        if self.sink is None:
            return SpeechDeliveryResult(success=False, error="未配置语音播报输出接口。")
        delivered: list[int] = []
        for message in messages:
            try:
                self.sink.speak(message.text)
            except Exception as exc:
                return SpeechDeliveryResult(success=False, delivered_seq=tuple(delivered), error=str(exc))
            delivered.append(int(message.seq))
        return SpeechDeliveryResult(success=True, delivered_seq=tuple(delivered))
