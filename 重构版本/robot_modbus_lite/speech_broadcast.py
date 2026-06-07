"""Speech delivery adapter for operator broadcasts."""

from __future__ import annotations

from collections import OrderedDict
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

    _FIXED_CACHE_TEXTS = {
        "请确认执行。",
        "已取消。",
        "执行完成。",
        "正在处理。",
        "请补充目标位置。",
        "当前正在执行，请稍候。",
        "安全检查未通过，请查看屏幕。",
        "缺少唤醒词，请说小正或小兵。",
        "详情已显示在屏幕上。",
    }

    def __init__(
        self,
        client: object | None = None,
        player: Callable[[bytes, int], None] | None = None,
        sample_rate: int = 24000,
        stop_player: Callable[[], None] | None = None,
        stream_player_factory: Callable[[int], object] | None = None,
    ) -> None:
        self._client = client
        self._player = player
        self._stop_player = stop_player or self._default_stop_player
        self._stream_player_factory = stream_player_factory
        self._sample_rate = int(sample_rate)
        self._is_speaking = False
        self._cancel_requested = False
        self._pcm_cache: OrderedDict[tuple[str, str, int, str, int], bytes] = OrderedDict()
        self._pcm_cache_max_items = 32
        self.usage_events: list[dict[str, object]] = []

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
            if self._can_use_pcm_cache(clean, client):
                pcm, cache_hit = self._cached_or_synthesize_pcm(clean, client)
                if pcm and not self._cancel_requested:
                    self._play_pcm(client, pcm)
                self._record_usage(clean, client, pcm_bytes=len(pcm or b""), cache_hit=cache_hit)
                return
            stream_synthesize = getattr(client, "stream_synthesize_text", None)
            if callable(stream_synthesize):
                pcm_bytes = self._stream_play_text(client, clean)
                self._record_usage(clean, client, pcm_bytes=pcm_bytes, cache_hit=False)
                return
            pcm = client.synthesize_text(clean)
            if pcm and not self._cancel_requested:
                self._play_pcm(client, pcm)
            self._record_usage(clean, client, pcm_bytes=len(pcm or b""), cache_hit=False)
        finally:
            self._is_speaking = False

    def stop(self) -> None:
        self._cancel_requested = True
        self._is_speaking = False
        try:
            self._stop_player()
        except Exception:
            pass

    def _stream_play_text(self, client: object, text: str) -> int:
        stream_synthesize = getattr(client, "stream_synthesize_text")
        total_bytes = 0
        with self._open_stream_player(client) as stream_player:
            write = getattr(stream_player, "write", None)
            if not callable(write):
                raise RuntimeError("豆包流式播放器缺少 write(pcm) 方法。")

            def play_chunk(pcm: bytes) -> None:
                nonlocal total_bytes
                if pcm and not self._cancel_requested:
                    total_bytes += len(pcm)
                    write(pcm)

            stream_synthesize(text, play_chunk)
        return total_bytes

    def _can_use_pcm_cache(self, text: str, client: object) -> bool:
        return text in self._FIXED_CACHE_TEXTS and callable(getattr(client, "synthesize_text", None))

    def _cached_or_synthesize_pcm(self, text: str, client: object) -> tuple[bytes, bool]:
        key = self._pcm_cache_key(text, client)
        cached = self._pcm_cache.get(key)
        if cached is not None:
            self._pcm_cache.move_to_end(key)
            return cached, True
        pcm = client.synthesize_text(text)
        if pcm:
            self._pcm_cache[key] = bytes(pcm)
            self._pcm_cache.move_to_end(key)
            while len(self._pcm_cache) > self._pcm_cache_max_items:
                self._pcm_cache.popitem(last=False)
        return bytes(pcm or b""), False

    def _pcm_cache_key(self, text: str, client: object) -> tuple[str, str, int, str, int]:
        config = getattr(client, "config", None)
        speaker = str(getattr(config, "speaker", "") or "")
        sample_rate = int(getattr(config, "tts_sample_rate", self._sample_rate) or self._sample_rate)
        return (text, speaker, sample_rate, self._audio_format(client), 1)

    def _record_usage(self, text: str, client: object, *, pcm_bytes: int, cache_hit: bool) -> None:
        config = getattr(client, "config", None)
        self.usage_events.append(
            {
                "type": "tts",
                "text_len": len(text),
                "text_utf8_bytes": len(text.encode("utf-8")),
                "pcm_bytes": int(pcm_bytes),
                "speaker": str(getattr(config, "speaker", "") or ""),
                "sample_rate": int(getattr(config, "tts_sample_rate", self._sample_rate) or self._sample_rate),
                "audio_format": self._audio_format(client),
                "session_count": 0 if cache_hit else 1,
                "cache_hit": bool(cache_hit),
                "interrupted": bool(self._cancel_requested),
            }
        )

    def _play_pcm(self, client: object, pcm: bytes) -> None:
        if self._player is not None:
            self._player(pcm, self._sample_rate)
            return
        self._default_player(pcm, self._sample_rate, self._audio_format(client))

    def _open_stream_player(self, client: object):
        if self._stream_player_factory is not None:
            return self._stream_player_factory(self._sample_rate)
        return self._default_stream_player_factory(self._sample_rate, self._audio_format(client))

    @staticmethod
    def _audio_format(client: object) -> str:
        config = getattr(client, "config", None)
        return str(getattr(config, "tts_audio_format", "pcm") or "pcm").strip() or "pcm"

    @staticmethod
    def _default_player(pcm: bytes, sample_rate: int, audio_format: str = "pcm") -> None:
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("未安装 numpy，无法播放豆包 TTS 音频。") from exc
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("未安装 sounddevice，无法播放豆包 TTS 音频。") from exc
        dtype = np.int16 if str(audio_format).lower() == "pcm_s16le" else np.float32
        audio = np.frombuffer(pcm, dtype=dtype)
        sd.play(audio, samplerate=sample_rate, blocking=True)

    @staticmethod
    def _default_stream_player_factory(sample_rate: int, audio_format: str = "pcm"):
        dtype = "int16" if str(audio_format).lower() == "pcm_s16le" else "float32"
        return _SoundDeviceRawOutput(sample_rate, dtype=dtype)

    @staticmethod
    def _default_stop_player() -> None:
        try:
            import sounddevice as sd
        except ImportError:
            return
        sd.stop()


class _SoundDeviceRawOutput:
    def __init__(self, sample_rate: int, *, dtype: str = "float32") -> None:
        self._sample_rate = int(sample_rate)
        self._dtype = str(dtype or "float32")
        self._stream = None

    def __enter__(self):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("未安装 sounddevice，无法流式播放豆包 TTS 音频。") from exc
        self._stream = sd.RawOutputStream(samplerate=self._sample_rate, channels=1, dtype=self._dtype)
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
        for group in self._mergeable_groups(tuple(messages)):
            if should_continue is not None and not should_continue():
                return SpeechDeliveryResult(success=True, delivered_seq=tuple(delivered))
            message = group[0]
            try:
                speech_text = self._group_speech_text(group)
                self.sink.speak(speech_text)
            except Exception as exc:
                return SpeechDeliveryResult(success=False, delivered_seq=tuple(delivered), error=str(exc))
            delivered.extend(int(item.seq) for item in group)
        return SpeechDeliveryResult(success=True, delivered_seq=tuple(delivered))

    @staticmethod
    def _can_merge(message: BroadcastMessage) -> bool:
        context = str(getattr(message, "context_id", "") or "")
        priority = str(getattr(message, "priority", "normal") or "normal").lower()
        return context == "chat:ai_answer" and priority in {"normal", "low"}

    @classmethod
    def _mergeable_groups(cls, messages: tuple[BroadcastMessage, ...]) -> list[tuple[BroadcastMessage, ...]]:
        groups: list[tuple[BroadcastMessage, ...]] = []
        pending: list[BroadcastMessage] = []
        for message in messages:
            if cls._can_merge(message):
                pending.append(message)
                continue
            if pending:
                groups.append(tuple(pending))
                pending = []
            groups.append((message,))
        if pending:
            groups.append(tuple(pending))
        return groups

    @staticmethod
    def _group_speech_text(messages: tuple[BroadcastMessage, ...]) -> str:
        parts = [str(getattr(message, "speech_text", "") or message.text).strip() for message in messages]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return " ".join(parts)
