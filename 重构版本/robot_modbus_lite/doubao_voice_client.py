"""Doubao realtime ASR/TTS client adapters."""

from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import os
import queue
import time
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import websockets

from . import doubao_realtime_protocol as protocol
from .nlp_standard_words import load_standard_words
from .voice_wake_words import configured_wake_words


class DoubaoVoiceError(RuntimeError):
    """Raised when Doubao voice configuration or runtime calls fail."""


@dataclass(frozen=True)
class DoubaoVoiceConfig:
    api_key: str
    resource_id: str = "volc.speech.dialog"
    app_key: str = "PlgvMymc7f3tQnJ6"
    ws_url: str = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
    speaker: str = "zh_male_yunzhou_jupiter_bigtts"
    input_sample_rate: int = 16000
    tts_sample_rate: int = 24000
    recv_timeout: int = 10
    end_smooth_window_ms: int = 1500
    stream_queue_max_chunks: int = 20
    dialog_model: str = "1.2.1.1"
    tts_audio_format: str = "pcm"
    tts_minimal_session: bool = False
    tts_use_chat_tts_text: bool = False
    enable_asr_twopass: bool = True

    @classmethod
    def from_env(cls) -> "DoubaoVoiceConfig":
        from .env_loader import load_local_env_file

        load_local_env_file()
        api_key = os.environ.get("DOUBAO_API_KEY", "").strip()
        if not api_key:
            raise DoubaoVoiceError("缺少环境变量 DOUBAO_API_KEY。")
        return cls(
            api_key=api_key,
            resource_id=os.environ.get("DOUBAO_RESOURCE_ID", "volc.speech.dialog").strip() or "volc.speech.dialog",
            app_key=os.environ.get("DOUBAO_APP_KEY", "PlgvMymc7f3tQnJ6").strip() or "PlgvMymc7f3tQnJ6",
            ws_url=os.environ.get("DOUBAO_WS_URL", cls.ws_url).strip() or cls.ws_url,
            speaker=os.environ.get("DOUBAO_SPEAKER", cls.speaker).strip() or cls.speaker,
            input_sample_rate=int(os.environ.get("DOUBAO_INPUT_SAMPLE_RATE", "16000")),
            tts_sample_rate=int(os.environ.get("DOUBAO_TTS_SAMPLE_RATE", "24000")),
            recv_timeout=int(os.environ.get("DOUBAO_RECV_TIMEOUT", "10")),
            end_smooth_window_ms=int(os.environ.get("DOUBAO_END_SMOOTH_WINDOW_MS", "1500")),
            stream_queue_max_chunks=int(os.environ.get("DOUBAO_STREAM_QUEUE_MAX_CHUNKS", "20")),
            dialog_model=os.environ.get("DOUBAO_DIALOG_MODEL", cls.dialog_model).strip() or cls.dialog_model,
            tts_audio_format=os.environ.get("DOUBAO_TTS_AUDIO_FORMAT", cls.tts_audio_format).strip() or cls.tts_audio_format,
            tts_minimal_session=os.environ.get("DOUBAO_TTS_MINIMAL_SESSION", "").strip().lower() in {"1", "true", "yes", "on"},
            tts_use_chat_tts_text=os.environ.get("DOUBAO_TTS_USE_CHAT_TTS_TEXT", "").strip().lower() in {"1", "true", "yes", "on"},
            enable_asr_twopass=os.environ.get("DOUBAO_ENABLE_ASR_TWOPASS", "1").strip().lower()
            not in {"0", "false", "no", "off"},
        )

    def headers(self, connect_id: str) -> dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-App-Key": self.app_key,
            "X-Api-Connect-Id": connect_id,
        }


def extract_final_asr_text(response: dict[str, Any]) -> str:
    if response.get("event") != 451:
        return ""
    payload = response.get("payload_msg", {})
    results = payload.get("results", []) if isinstance(payload, dict) else []
    for item in reversed(results):
        if item.get("is_interim"):
            continue
        text = str(item.get("text", "") or "").strip()
        if text:
            return text
    return ""


def extract_interim_asr_text(response: dict[str, Any]) -> str:
    if response.get("event") != 451:
        return ""
    payload = response.get("payload_msg", {})
    results = payload.get("results", []) if isinstance(payload, dict) else []
    for item in reversed(results):
        if not item.get("is_interim"):
            continue
        text = str(item.get("text", "") or "").strip()
        if text:
            return text
    return ""


def connect_websocket(url: str, *, headers: dict[str, str]):
    connect_params = inspect.signature(websockets.connect).parameters
    header_key = "additional_headers" if "additional_headers" in connect_params else "extra_headers"
    return websockets.connect(url, **{header_key: headers}, ping_interval=None)


def _encode_json_payload(data: Any, *, compress_threshold: int = 100) -> tuple[bytes, int]:
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if len(raw) < int(compress_threshold):
        return raw, protocol.NO_COMPRESSION
    return gzip.compress(raw), protocol.GZIP


@lru_cache(maxsize=1)
def _doubao_asr_context() -> dict[str, Any]:
    words: list[str] = []
    seen: set[str] = set()

    def add_word(value: str) -> None:
        word = str(value or "").strip()
        if not word or len(word) > 24:
            return
        if word in seen:
            return
        seen.add(word)
        words.append(word)

    for wake_word in configured_wake_words():
        add_word(wake_word)

    for index in range(1, 11):
        chinese = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")[index - 1]
        add_word(f"步骤{chinese}")
        add_word(f"第{chinese}步")
        add_word(f"步骤{index}")
        add_word(f"第{index}步")
    for label in ("A", "B", "C", "D", "home", "Home", "HOME"):
        add_word(f"位置{label}")
    for func_id in (104, 108, 109, 110, 120):
        add_word(f"Func{func_id}")

    for word in load_standard_words().values():
        add_word(word.standard)
        for alias in word.homophones[:6]:
            add_word(alias)
        for alias in word.sichuan_variants[:3]:
            add_word(alias)

    correct_words = {
        "速度二(?=，?等待|,?等待|，?延时|,?延时|，?移动|,?移动|，?输出|,?输出)": "步骤二",
        "速度三(?=，?等待|,?等待|，?延时|,?延时|，?移动|,?移动|，?输出|,?输出)": "步骤三",
        "速度四(?=，?等待|,?等待|，?延时|,?延时|，?移动|,?移动|，?输出|,?输出)": "步骤四",
        "速度五(?=，?等待|,?等待|，?延时|,?延时|，?移动|,?移动|，?输出|,?输出)": "步骤五",
    }
    return {
        "hotwords": [{"word": word} for word in words[:80]],
        "correct_words": correct_words,
    }


class DoubaoStreamingAsrSession:
    """Long-lived streaming ASR session for GUI continuous voice mode."""

    def __init__(
        self,
        config: DoubaoVoiceConfig | None = None,
        *,
        on_final_text,
        on_partial_text=None,
        on_speech_start=None,
        on_error=None,
    ) -> None:
        self.config = config or DoubaoVoiceConfig.from_env()
        self._on_final_text = on_final_text
        self._on_partial_text = on_partial_text
        self._on_speech_start = on_speech_start
        self._on_error = on_error
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=max(1, int(self.config.stream_queue_max_chunks)))
        self._ready_event = threading.Event()
        self._closed_event = threading.Event()
        self._final_text_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_thread, name="doubao-streaming-asr", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=max(1, int(self.config.recv_timeout))):
            self.close()
            raise DoubaoVoiceError("豆包流式语音会话启动超时。")
        if self._error is not None:
            raise DoubaoVoiceError(str(self._error)) from self._error

    def send_audio(self, pcm_data: bytes) -> None:
        if self._stop_event.is_set() or not pcm_data:
            return
        self._put_latest_audio(bytes(pcm_data))

    def _put_latest_audio(self, pcm_data: bytes) -> None:
        try:
            self._audio_queue.put_nowait(pcm_data)
            return
        except queue.Full:
            pass
        try:
            self._audio_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._audio_queue.put_nowait(pcm_data)
        except queue.Full:
            pass

    def close(self) -> None:
        self._stop_event.set()
        self._audio_queue.put(None)
        loop = self._loop
        ws = self._ws
        close = getattr(ws, "close", None)
        if loop is not None and callable(close):
            try:
                loop.call_soon_threadsafe(lambda: asyncio.create_task(close()))
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1, int(self.config.recv_timeout)))

    def wait_for_final_text(self, *, timeout: float | None = None) -> bool:
        return self._final_text_event.wait(timeout=timeout)

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._closed_event.is_set() and not self._stop_event.is_set()

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run_async())
        except BaseException as exc:
            if self._stop_event.is_set():
                return
            self._error = exc
            self._ready_event.set()
            self._emit_error(exc)
        finally:
            self._closed_event.set()

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        client = DoubaoVoiceClient(self.config)
        session_id = str(uuid.uuid4())
        connect_id = str(uuid.uuid4())
        async with connect_websocket(self.config.ws_url, headers=self.config.headers(connect_id)) as ws:
            self._ws = ws
            await client._send_start_connection(ws)
            await client._recv_checked(ws)
            await client._send_start_session(ws, session_id, input_mod="audio", include_tts=False)
            await client._recv_checked(ws)
            self._ready_event.set()
            send_task = asyncio.create_task(self._send_loop(client, ws, session_id))
            recv_task = asyncio.create_task(self._recv_loop(client, ws))
            try:
                await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                self._stop_event.set()
                for task in (send_task, recv_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(send_task, recv_task, return_exceptions=True)
                await client._send_finish_session(ws, session_id)
                await client._send_finish_connection(ws)

    async def _send_loop(self, client: "DoubaoVoiceClient", ws, session_id: str) -> None:
        while not self._stop_event.is_set():
            pcm = await asyncio.to_thread(self._audio_queue.get)
            if pcm is None:
                break
            await client._send_audio(ws, session_id, pcm)

    async def _recv_loop(self, client: "DoubaoVoiceClient", ws) -> None:
        while not self._stop_event.is_set():
            response = await client._recv_checked(ws, timeout=None)
            event = response.get("event")
            if event == 450:
                self._emit(self._on_speech_start)
            partial_text = extract_interim_asr_text(response)
            if partial_text:
                self._emit(self._on_partial_text, partial_text)
            text = extract_final_asr_text(response)
            if text:
                self._final_text_event.set()
                self._emit(self._on_final_text, text)
            if event in {152, 153}:
                break

    def _emit(self, callback, *args) -> None:
        if not callable(callback):
            return
        try:
            callback(*args)
        except Exception:
            pass

    def _emit_error(self, exc: BaseException) -> None:
        if callable(self._on_error):
            try:
                self._on_error(exc)
            except Exception:
                pass


class DoubaoVoiceClient:
    def __init__(self, config: DoubaoVoiceConfig | None = None) -> None:
        self.config = config or DoubaoVoiceConfig.from_env()

    def _run_async(self, coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        thread_result: dict[str, object] = {}
        thread_error: list[BaseException] = []

        def run_in_thread() -> None:
            try:
                thread_result["value"] = asyncio.run(coroutine)
            except BaseException as exc:
                thread_error.append(exc)

        import threading

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join()
        if thread_error:
            raise thread_error[0]
        return thread_result.get("value")

    def transcribe_pcm(self, pcm_data: bytes, *, partial_callback=None) -> dict[str, object]:
        return self._run_async(self._transcribe_pcm_async(pcm_data, partial_callback=partial_callback))

    def synthesize_text(self, text: str) -> bytes:
        return self._run_async(self._synthesize_text_async(text))

    def stream_synthesize_text(self, text: str, chunk_callback) -> None:
        self._run_async(self._stream_synthesize_text_async(text, chunk_callback))

    def check_connection(self) -> None:
        """Perform a minimal websocket handshake for login preflight."""
        self._run_async(self._check_connection_async())

    async def _transcribe_pcm_async(self, pcm_data: bytes, *, partial_callback=None) -> dict[str, object]:
        started = time.perf_counter()
        if not pcm_data:
            return {"text": "", "timing": {"voice_mode": "doubao_asr", "voice_total_ms": 0}}
        session_id = str(uuid.uuid4())
        connect_id = str(uuid.uuid4())
        final_text = ""
        async with connect_websocket(self.config.ws_url, headers=self.config.headers(connect_id)) as ws:
            await self._send_start_connection(ws)
            await self._recv_checked(ws)
            await self._send_start_session(ws, session_id, input_mod="audio_file", include_tts=False)
            await self._recv_checked(ws)
            await self._send_audio(ws, session_id, pcm_data)
            while True:
                response = await self._recv_checked(ws)
                text = extract_final_asr_text(response)
                if text:
                    final_text = text
                    if partial_callback is not None:
                        partial_callback(text)
                    break
                event = response.get("event")
                if event in {459, 359, 152, 153}:
                    break
            await self._send_finish_session(ws, session_id)
            await self._send_finish_connection(ws)
        total_ms = int((time.perf_counter() - started) * 1000)
        return {"text": final_text.strip(), "timing": {"voice_mode": "doubao_asr", "voice_total_ms": total_ms}}

    async def _send_start_connection(self, ws) -> None:
        payload, compression = _encode_json_payload({})
        request = bytearray(protocol.generate_header(compression_type=compression))
        request.extend(protocol.START_CONNECTION.to_bytes(4, "big"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_start_session(
        self,
        ws,
        session_id: str,
        *,
        input_mod: str,
        include_tts: bool = True,
        minimal_dialog: bool = False,
    ) -> None:
        dialog: dict[str, Any] = {
            "bot_name": "豆包",
            "extra": {
                "strict_audit": False,
                "recv_timeout": self.config.recv_timeout,
                "input_mod": input_mod,
                "model": self.config.dialog_model,
            },
        }
        if not minimal_dialog:
            dialog.update(
                {
                    "system_role": "你是机械手系统的语音接口。只做简短确认，不直接生成机械手控制动作。",
                    "speaking_style": "回答简洁。",
                }
            )
        request_params: dict[str, Any] = {
            "asr": {
                "extra": {
                    "end_smooth_window_ms": self.config.end_smooth_window_ms,
                    "enable_asr_twopass": bool(self.config.enable_asr_twopass),
                    "context": _doubao_asr_context(),
                }
            },
            "dialog": dialog,
        }
        if include_tts:
            request_params["tts"] = {
                "speaker": self.config.speaker,
                "audio_config": {
                    "channel": 1,
                    "format": self.config.tts_audio_format,
                    "sample_rate": self.config.tts_sample_rate,
                },
            }
        payload, compression = _encode_json_payload(request_params)
        request = bytearray(protocol.generate_header(compression_type=compression))
        request.extend(protocol.START_SESSION.to_bytes(4, "big"))
        request.extend(len(session_id).to_bytes(4, "big"))
        request.extend(session_id.encode("utf-8"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_audio(self, ws, session_id: str, pcm_data: bytes) -> None:
        payload = gzip.compress(pcm_data)
        request = bytearray(
            protocol.generate_header(
                message_type=protocol.CLIENT_AUDIO_ONLY_REQUEST,
                serial_method=protocol.NO_SERIALIZATION,
            )
        )
        request.extend(protocol.TASK_REQUEST.to_bytes(4, "big"))
        request.extend(len(session_id).to_bytes(4, "big"))
        request.extend(session_id.encode("utf-8"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_finish_session(self, ws, session_id: str) -> None:
        payload, compression = _encode_json_payload({})
        request = bytearray(protocol.generate_header(compression_type=compression))
        request.extend(protocol.FINISH_SESSION.to_bytes(4, "big"))
        request.extend(len(session_id).to_bytes(4, "big"))
        request.extend(session_id.encode("utf-8"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_finish_connection(self, ws) -> None:
        payload, compression = _encode_json_payload({})
        request = bytearray(protocol.generate_header(compression_type=compression))
        request.extend(protocol.FINISH_CONNECTION.to_bytes(4, "big"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _check_connection_async(self) -> None:
        connect_id = str(uuid.uuid4())
        async with connect_websocket(self.config.ws_url, headers=self.config.headers(connect_id)) as ws:
            await self._send_start_connection(ws)
            await self._recv_checked(ws, timeout=min(float(self.config.recv_timeout), 8.0))
            await self._send_finish_connection(ws)

    async def _recv_checked(self, ws, *, timeout: float | None = "default") -> dict[str, Any]:
        try:
            recv_timeout = self.config.recv_timeout if timeout == "default" else timeout
            if recv_timeout is None:
                raw = await ws.recv()
            else:
                raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
        except asyncio.TimeoutError as exc:
            raise DoubaoVoiceError("豆包语音服务响应超时。") from exc
        response = protocol.parse_response(raw)
        if response.get("message_type") == "SERVER_ERROR_RESPONSE":
            raise DoubaoVoiceError(f"豆包语音服务错误: {response}")
        return response

    async def _synthesize_text_async(self, text: str) -> bytes:
        audio = bytearray()

        def collect(chunk: bytes) -> None:
            audio.extend(chunk)

        await self._stream_synthesize_text_async(text, collect)
        return bytes(audio)

    async def _stream_synthesize_text_async(self, text: str, chunk_callback) -> None:
        clean = str(text or "").strip()
        if not clean:
            return None
        session_id = str(uuid.uuid4())
        connect_id = str(uuid.uuid4())
        async with connect_websocket(self.config.ws_url, headers=self.config.headers(connect_id)) as ws:
            await self._send_start_connection(ws)
            await self._recv_checked(ws)
            await self._send_start_session(
                ws,
                session_id,
                input_mod="text",
                minimal_dialog=self.config.tts_minimal_session,
            )
            await self._recv_checked(ws)
            if self.config.tts_use_chat_tts_text:
                await self._send_chat_tts_text(ws, session_id, clean)
            else:
                await self._send_say_hello_text(ws, session_id, clean)
            while True:
                response = await self._recv_checked(ws)
                if response.get("message_type") == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
                    chunk_callback(response["payload_msg"])
                if response.get("event") in {359, 152, 153}:
                    break
            await self._send_finish_session(ws, session_id)
            await self._send_finish_connection(ws)
        return None

    async def _send_say_hello_text(self, ws, session_id: str, text: str) -> None:
        payload, compression = _encode_json_payload({"content": text})
        request = bytearray(protocol.generate_header(compression_type=compression))
        request.extend(protocol.SAY_HELLO.to_bytes(4, "big"))
        request.extend(len(session_id).to_bytes(4, "big"))
        request.extend(session_id.encode("utf-8"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_chat_tts_text(self, ws, session_id: str, text: str) -> None:
        for start, end, content in ((True, False, text), (False, True, "")):
            payload, compression = _encode_json_payload({"start": start, "end": end, "content": content})
            request = bytearray(protocol.generate_header(compression_type=compression))
            request.extend(protocol.CHAT_TTS_TEXT.to_bytes(4, "big"))
            request.extend(len(session_id).to_bytes(4, "big"))
            request.extend(session_id.encode("utf-8"))
            request.extend(len(payload).to_bytes(4, "big"))
            request.extend(payload)
            await ws.send(bytes(request))
