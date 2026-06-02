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


class DoubaoSpeechSink:
    """Doubao TTS sink backed by realtime dialogue TTS audio."""

    def __init__(
        self,
        client: object | None = None,
        player: Callable[[bytes, int], None] | None = None,
        sample_rate: int = 24000,
        stop_player: Callable[[], None] | None = None,
        stream_player_factory: Callable[[int], object] | None = None,
    ) -> None:
        self._client = client
        self._player = player or self._default_player
        self._stop_player = stop_player or self._default_stop_player
        self._stream_player_factory = stream_player_factory or self._default_stream_player_factory
        self._sample_rate = int(sample_rate)
        self._is_speaking = False
        self._cancel_requested = False

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def speak(self, text: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        self._cancel_requested = False
        self._is_speaking = True
        try:
            client = self._client
            if client is None:
                from .doubao_voice_client import DoubaoVoiceClient

                client = DoubaoVoiceClient()
                self._client = client
            stream_synthesize = getattr(client, "stream_synthesize_text", None)
            if callable(stream_synthesize):
                self._stream_play_text(client, clean)
                return
            pcm = client.synthesize_text(clean)
            if pcm and not self._cancel_requested:
                self._player(pcm, self._sample_rate)
        finally:
            self._is_speaking = False

    def stop(self) -> None:
        self._cancel_requested = True
        self._is_speaking = False
        try:
            self._stop_player()
        except Exception:
            pass

    def _stream_play_text(self, client: object, text: str) -> None:
        stream_synthesize = getattr(client, "stream_synthesize_text")
        with self._stream_player_factory(self._sample_rate) as stream_player:
            write = getattr(stream_player, "write", None)
            if not callable(write):
                raise RuntimeError("豆包流式播放器缺少 write(pcm) 方法。")

            def play_chunk(pcm: bytes) -> None:
                if pcm and not self._cancel_requested:
                    write(pcm)

            stream_synthesize(text, play_chunk)

    @staticmethod
    def _default_player(pcm: bytes, sample_rate: int) -> None:
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("未安装 numpy，无法播放豆包 TTS 音频。") from exc
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("未安装 sounddevice，无法播放豆包 TTS 音频。") from exc
        audio = np.frombuffer(pcm, dtype=np.float32)
        sd.play(audio, samplerate=sample_rate, blocking=True)

    @staticmethod
    def _default_stream_player_factory(sample_rate: int):
        return _SoundDeviceRawOutput(sample_rate)

    @staticmethod
    def _default_stop_player() -> None:
        try:
            import sounddevice as sd
        except ImportError:
            return
        sd.stop()


class _SoundDeviceRawOutput:
    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = int(sample_rate)
        self._stream = None

    def __enter__(self):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("未安装 sounddevice，无法流式播放豆包 TTS 音频。") from exc
        self._stream = sd.RawOutputStream(samplerate=self._sample_rate, channels=1, dtype="float32")
        self._stream.start()
        return self

    def write(self, pcm: bytes) -> None:
        if self._stream is None:
            return
        self._stream.write(pcm)

    def __exit__(self, *_args) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()


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
