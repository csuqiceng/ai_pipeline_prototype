"""Speech delivery adapter for operator broadcasts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .broadcast_queue import BroadcastMessage


class SpeechSink(Protocol):
    """Output target for spoken operator messages."""

    @property
    def is_speaking(self) -> bool:
        ...

    def speak(self, text: str) -> None:
        ...

    def stop(self) -> None:
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

    @property
    def is_speaking(self) -> bool:
        return False

    def speak(self, text: str) -> None:
        self._callback(text)

    def stop(self) -> None:
        return None


class Pyttsx3SpeechSink:
    """Optional local TTS sink backed by pyttsx3."""

    def __init__(self, engine: object | None = None) -> None:
        self._engine = engine
        self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def speak(self, text: str) -> None:
        engine = self._engine
        if engine is None:
            try:
                import pyttsx3  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError("未安装 pyttsx3，无法使用本地 TTS 播报。") from exc
            engine = pyttsx3.init()
            self._engine = engine
        self._is_speaking = True
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            self._is_speaking = False

    def stop(self) -> None:
        engine = self._engine
        stop = getattr(engine, "stop", None)
        if callable(stop):
            stop()


class WindowsSapiSpeechSink:
    """Windows SAPI sink.

    A fresh COM voice is created for each utterance. In practice this is more
    reliable for repeated GUI timer deliveries than reusing a pyttsx3 engine.
    """

    def __init__(self, dispatch_factory: Callable[[str], object] | None = None) -> None:
        self._dispatch_factory = dispatch_factory
        self._is_speaking = False
        self._voice: object | None = None

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @staticmethod
    def available() -> bool:
        try:
            import win32com.client  # type: ignore[import-untyped]  # noqa: F401
        except Exception:
            return False
        return True

    def _dispatch(self, name: str) -> object:
        if self._dispatch_factory is not None:
            return self._dispatch_factory(name)
        try:
            import win32com.client  # type: ignore[import-untyped]
        except Exception as exc:
            raise RuntimeError("未安装 win32com，无法使用 Windows SAPI 播报。") from exc
        return win32com.client.Dispatch(name)

    def speak(self, text: str) -> None:
        voice = self._dispatch("SAPI.SpVoice")
        self._voice = voice
        speak = getattr(voice, "Speak", None)
        if not callable(speak):
            raise RuntimeError("Windows SAPI 语音接口不可用。")
        self._is_speaking = True
        try:
            speak(text)
        finally:
            self._is_speaking = False
            self._voice = None

    def stop(self) -> None:
        voice = self._voice
        speak = getattr(voice, "Speak", None)
        if callable(speak):
            try:
                speak("", 2)
            except TypeError:
                speak("")


class SpeechBroadcastDeliveryService:
    """Delivers queued broadcast messages to a configured speech sink."""

    def __init__(self, sink: SpeechSink | None = None) -> None:
        self.sink = sink

    def deliver(
        self,
        messages: list[BroadcastMessage] | tuple[BroadcastMessage, ...],
        *,
        should_continue: Callable[[], bool] | None = None,
    ) -> SpeechDeliveryResult:
        if not messages:
            return SpeechDeliveryResult(success=True)
        if self.sink is None:
            return SpeechDeliveryResult(success=False, error="未配置语音播报输出接口。")
        delivered: list[int] = []
        for message in messages:
            if should_continue is not None and not should_continue():
                return SpeechDeliveryResult(success=True, delivered_seq=tuple(delivered))
            try:
                self.sink.speak(message.text)
            except Exception as exc:
                return SpeechDeliveryResult(success=False, delivered_seq=tuple(delivered), error=str(exc))
            delivered.append(int(message.seq))
        return SpeechDeliveryResult(success=True, delivered_seq=tuple(delivered))
