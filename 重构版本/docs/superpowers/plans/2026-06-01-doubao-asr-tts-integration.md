# Doubao ASR/TTS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Doubao as both the ASR and TTS provider while keeping the existing project NLP, safety precheck, confirmation, and robot execution chain in control.

**Architecture:** Add a focused Doubao realtime protocol/client layer under `robot_modbus_lite`, then select ASR and TTS providers through environment/config switches. The first implementation uses short per-utterance Doubao sessions for ASR and per-speech Doubao sessions for TTS, preserving the current `{"text": str, "timing": dict}` ASR contract and `SpeechSink` TTS contract.

**Scope note:** This plan replaces the operator GUI continuous voice session path. `WebVoiceService` and `iflytek_worker.py` still use the existing short-recording iFlytek worker unless a later task explicitly routes that Web API path through `VOICE_ASR_PROVIDER=doubao`.

**Tech Stack:** Python, asyncio, websockets, gzip/json binary protocol, sounddevice/PyAudio-compatible PCM, PySide6 existing background task pattern, pytest.

---

## File Structure

- Create `robot_modbus_lite/doubao_realtime_protocol.py`
  - Owns binary protocol constants, header generation, response parsing, gzip/json handling.
- Create `robot_modbus_lite/doubao_voice_client.py`
  - Owns Doubao config loading, WebSocket lifecycle, ASR segment transcription, TTS PCM synthesis.
  - Uses a small `connect_websocket()` compatibility helper because `websockets` 14+ renamed `extra_headers` to `additional_headers`.
- Create `robot_modbus_lite/env_loader.py`
  - Owns shared `.env` loading so iFlytek, DeepSeek, and Doubao do not each carry their own copy.
- Modify `robot_modbus_lite/voice_mixin.py`
  - Adds provider selection and routes ASR to Doubao or the existing iFlytek path.
- Modify `robot_modbus_lite/speech_broadcast.py`
  - Adds `DoubaoSpeechSink` implementing `SpeechSink`.
- Modify `robot_modbus_lite/operator_ui_mixin.py`
  - Configures TTS sink from `VOICE_TTS_PROVIDER`, preserving local fallback.
- Modify `requirements.txt`
  - Adds `websockets>=11,<16`; `sounddevice` and `numpy` are already present and are reused for Doubao TTS playback.
- Create `tests/test_doubao_realtime_protocol.py`
  - Unit tests for protocol round-trip and parsing behavior.
- Create `tests/test_doubao_voice_client.py`
  - Unit tests for config loading and event extraction without network.
- Modify `tests/test_voice_session_mixin.py`
  - Tests provider routing from voice session ASR.
- Modify or create `tests/test_speech_broadcast.py`
  - Tests `DoubaoSpeechSink` conforms to `SpeechSink` behavior using a fake synthesizer/player.

---

### Task 1: Add Doubao Realtime Protocol Module

**Files:**
- Create: `robot_modbus_lite/doubao_realtime_protocol.py`
- Test: `tests/test_doubao_realtime_protocol.py`

- [ ] **Step 1: Write the failing protocol tests**

Create `tests/test_doubao_realtime_protocol.py`:

```python
import gzip
import json

from robot_modbus_lite.doubao_realtime_protocol import (
    CLIENT_AUDIO_ONLY_REQUEST,
    CLIENT_FULL_REQUEST,
    GZIP,
    JSON,
    MSG_WITH_EVENT,
    NO_SERIALIZATION,
    SERVER_FULL_RESPONSE,
    generate_header,
    parse_response,
)


def test_generate_header_defaults_match_doubao_protocol():
    header = generate_header()

    assert header == bytes([0x11, (CLIENT_FULL_REQUEST << 4) | MSG_WITH_EVENT, (JSON << 4) | GZIP, 0x00])


def test_generate_header_audio_request_uses_no_serialization():
    header = generate_header(message_type=CLIENT_AUDIO_ONLY_REQUEST, serial_method=NO_SERIALIZATION)

    assert header[1] >> 4 == CLIENT_AUDIO_ONLY_REQUEST
    assert header[2] >> 4 == NO_SERIALIZATION
    assert header[2] & 0x0F == GZIP


def test_parse_server_full_response_with_json_payload():
    session_id = b"session-1"
    payload = gzip.compress(json.dumps({"results": [{"text": "你好"}]}, ensure_ascii=False).encode("utf-8"))
    packet = bytearray()
    packet.extend(generate_header(message_type=SERVER_FULL_RESPONSE))
    packet.extend((451).to_bytes(4, "big"))
    packet.extend(len(session_id).to_bytes(4, "big"))
    packet.extend(session_id)
    packet.extend(len(payload).to_bytes(4, "big"))
    packet.extend(payload)

    parsed = parse_response(bytes(packet))

    assert parsed["message_type"] == "SERVER_FULL_RESPONSE"
    assert parsed["event"] == 451
    assert parsed["session_id"] == "session-1"
    assert parsed["payload_msg"]["results"][0]["text"] == "你好"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest tests/test_doubao_realtime_protocol.py -v
```

Expected: fails with `ModuleNotFoundError: No module named 'robot_modbus_lite.doubao_realtime_protocol'`.

- [ ] **Step 3: Add the protocol implementation**

Create `robot_modbus_lite/doubao_realtime_protocol.py`:

```python
"""Doubao realtime dialogue binary protocol helpers."""

from __future__ import annotations

import gzip
import json
from typing import Any

PROTOCOL_VERSION = 0b0001

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010

SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

START_CONNECTION = 1
FINISH_CONNECTION = 2
START_SESSION = 100
FINISH_SESSION = 102
TASK_REQUEST = 200
SAY_HELLO = 300
CHAT_TTS_TEXT = 500

NO_SEQUENCE = 0b0000
NEG_SEQUENCE = 0b0010
MSG_WITH_EVENT = 0b0100

NO_SERIALIZATION = 0b0000
JSON = 0b0001

NO_COMPRESSION = 0b0000
GZIP = 0b0001


def generate_header(
    *,
    version: int = PROTOCOL_VERSION,
    message_type: int = CLIENT_FULL_REQUEST,
    message_type_specific_flags: int = MSG_WITH_EVENT,
    serial_method: int = JSON,
    compression_type: int = GZIP,
    reserved_data: int = 0x00,
    extension_header: bytes = b"",
) -> bytes:
    header_size = int(len(extension_header) / 4) + 1
    header = bytearray()
    header.append((version << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    header.extend(extension_header)
    return bytes(header)


def parse_response(packet: bytes | str) -> dict[str, Any]:
    if isinstance(packet, str) or not packet:
        return {}
    header_size = packet[0] & 0x0F
    message_type = packet[1] >> 4
    message_type_specific_flags = packet[1] & 0x0F
    serialization_method = packet[2] >> 4
    message_compression = packet[2] & 0x0F
    payload = packet[header_size * 4:]
    result: dict[str, Any] = {}

    if message_type in {SERVER_FULL_RESPONSE, SERVER_ACK}:
        result["message_type"] = "SERVER_ACK" if message_type == SERVER_ACK else "SERVER_FULL_RESPONSE"
        start = 0
        if message_type_specific_flags & NEG_SEQUENCE > 0:
            result["seq"] = int.from_bytes(payload[:4], "big", signed=False)
            start += 4
        if message_type_specific_flags & MSG_WITH_EVENT > 0:
            result["event"] = int.from_bytes(payload[start:start + 4], "big", signed=False)
            start += 4
        payload = payload[start:]
        session_id_size = int.from_bytes(payload[:4], "big", signed=True)
        session_id = payload[4:session_id_size + 4].decode("utf-8", errors="replace")
        result["session_id"] = session_id
        payload = payload[4 + session_id_size:]
        payload_size = int.from_bytes(payload[:4], "big", signed=False)
        payload_msg = payload[4:4 + payload_size]
    elif message_type == SERVER_ERROR_RESPONSE:
        result["message_type"] = "SERVER_ERROR_RESPONSE"
        result["code"] = int.from_bytes(payload[:4], "big", signed=False)
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:8 + payload_size]
    else:
        return result

    if message_compression == GZIP and payload_msg:
        payload_msg = gzip.decompress(payload_msg)
    if serialization_method == JSON and payload_msg:
        result["payload_msg"] = json.loads(payload_msg.decode("utf-8"))
    elif serialization_method == NO_SERIALIZATION:
        result["payload_msg"] = payload_msg
    else:
        result["payload_msg"] = payload_msg.decode("utf-8", errors="replace") if payload_msg else ""
    result["payload_size"] = payload_size
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
pytest tests/test_doubao_realtime_protocol.py -v
```

Expected: 3 passed.

---

### Task 2: Add Doubao Config and Event Extraction

**Files:**
- Create: `robot_modbus_lite/env_loader.py`
- Modify: `robot_modbus_lite/doubao_voice_client.py`
- Modify: `robot_modbus_lite/iflytek_iat.py`
- Modify: `robot_modbus_lite/deepseek_client.py`
- Test: `tests/test_doubao_voice_client.py`

- [ ] **Step 1: Write failing tests for config and ASR event extraction**

Create `tests/test_doubao_voice_client.py`:

```python
import os

from robot_modbus_lite.doubao_voice_client import DoubaoVoiceConfig, extract_final_asr_text
from robot_modbus_lite.env_loader import load_local_env_file


def test_doubao_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("DOUBAO_API_KEY", "key-1")
    monkeypatch.setenv("DOUBAO_RESOURCE_ID", "volc.speech.dialog")
    monkeypatch.setenv("DOUBAO_APP_KEY", "app-key-1")
    monkeypatch.setenv("DOUBAO_SPEAKER", "speaker-1")

    config = DoubaoVoiceConfig.from_env()

    assert config.api_key == "key-1"
    assert config.resource_id == "volc.speech.dialog"
    assert config.app_key == "app-key-1"
    assert config.speaker == "speaker-1"


def test_shared_env_loader_reads_project_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DOUBAO_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)

    load_local_env_file(extra_paths=[env_file])

    assert os.environ["DOUBAO_API_KEY"] == "from-file"


def test_extract_final_asr_text_prefers_non_interim_result():
    event = {
        "event": 451,
        "payload_msg": {
            "results": [
                {"text": "临时", "is_interim": True},
                {"text": "移动到位置A", "is_interim": False},
            ]
        },
    }

    assert extract_final_asr_text(event) == "移动到位置A"


def test_extract_final_asr_text_returns_empty_for_interim_only():
    event = {"event": 451, "payload_msg": {"results": [{"text": "临时", "is_interim": True}]}}

    assert extract_final_asr_text(event) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_doubao_voice_client.py -v
```

Expected: fails with missing `doubao_voice_client`.

- [ ] **Step 3: Add shared environment loader**

Create `robot_modbus_lite/env_loader.py`:

```python
"""Shared local .env loading helpers."""

from __future__ import annotations

import os
from pathlib import Path


def expected_env_locations() -> list[Path]:
    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent
    return [package_root / ".env", project_root / ".env"]


def load_local_env_file(*, extra_paths: list[Path] | None = None) -> None:
    paths = list(extra_paths or []) + expected_env_locations()
    for env_path in paths:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            normalized = line.strip()
            if not normalized or normalized.startswith("#") or "=" not in normalized:
                continue
            key, value = normalized.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value
```

- [ ] **Step 4: Point existing env loaders at the shared helper**

In `robot_modbus_lite/iflytek_iat.py`, keep the public function names for compatibility but delegate:

```python
def expected_env_locations() -> list[Path]:
    from .env_loader import expected_env_locations as shared_expected_env_locations

    return shared_expected_env_locations()


def _load_local_env_file() -> None:
    from .env_loader import load_local_env_file

    load_local_env_file()
```

In `robot_modbus_lite/deepseek_client.py`, replace its duplicated env loading body with:

```python
def _load_local_env_file() -> None:
    from .env_loader import load_local_env_file

    load_local_env_file()
```

- [ ] **Step 5: Implement config and event extraction**

Create `robot_modbus_lite/doubao_voice_client.py`:

```python
"""Doubao realtime ASR/TTS client adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


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
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_doubao_voice_client.py -v
```

Expected: 4 passed.

---

### Task 3: Implement Short-Session Doubao ASR

**Files:**
- Modify: `robot_modbus_lite/doubao_voice_client.py`
- Test: `tests/test_doubao_voice_client.py`

- [ ] **Step 1: Add tests for ASR request packet creation and timing shape**

Append to `tests/test_doubao_voice_client.py`:

```python
import asyncio

from robot_modbus_lite.doubao_voice_client import DoubaoVoiceClient


def test_doubao_voice_client_transcribe_returns_existing_final_text(monkeypatch):
    config = DoubaoVoiceConfig(api_key="key")
    client = DoubaoVoiceClient(config)

    async def fake_transcribe(_pcm, *, partial_callback=None):
        if partial_callback is not None:
            partial_callback("移动")
        return {"text": "移动到位置A", "timing": {"voice_mode": "doubao_asr"}}

    monkeypatch.setattr(client, "_transcribe_pcm_async", fake_transcribe)

    partials = []
    result = client.transcribe_pcm(b"pcm", partial_callback=partials.append)

    assert result["text"] == "移动到位置A"
    assert result["timing"]["voice_mode"] == "doubao_asr"
    assert partials == ["移动"]
```

- [ ] **Step 2: Run the new test to verify it fails**

Run:

```powershell
pytest tests/test_doubao_voice_client.py::test_doubao_voice_client_transcribe_returns_existing_final_text -v
```

Expected: fails with missing `DoubaoVoiceClient`.

- [ ] **Step 3: Add synchronous wrapper and async method skeleton**

Append to `robot_modbus_lite/doubao_voice_client.py`:

```python
import asyncio
import gzip
import inspect
import json
import time
import uuid

import websockets

from . import doubao_realtime_protocol as protocol


def connect_websocket(url: str, *, headers: dict[str, str]):
    connect_params = inspect.signature(websockets.connect).parameters
    header_key = "additional_headers" if "additional_headers" in connect_params else "extra_headers"
    return websockets.connect(url, **{header_key: headers}, ping_interval=None)


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
            await self._send_start_session(ws, session_id, input_mod="audio_file")
            await self._recv_checked(ws)
            await self._send_audio(ws, session_id, pcm_data)
            while True:
                response = await self._recv_checked(ws)
                text = extract_final_asr_text(response)
                if text:
                    final_text = text
                    if partial_callback is not None:
                        partial_callback(text)
                event = response.get("event")
                if event in {459, 359, 152, 153}:
                    break
            await self._send_finish_session(ws, session_id)
            await self._send_finish_connection(ws)
        total_ms = int((time.perf_counter() - started) * 1000)
        return {"text": final_text.strip(), "timing": {"voice_mode": "doubao_asr", "voice_total_ms": total_ms}}
```

- [ ] **Step 4: Add packet helper methods**

Append below `DoubaoVoiceClient._transcribe_pcm_async` in `robot_modbus_lite/doubao_voice_client.py`:

```python
    async def _send_start_connection(self, ws) -> None:
        payload = gzip.compress(b"{}")
        request = bytearray(protocol.generate_header())
        request.extend(protocol.START_CONNECTION.to_bytes(4, "big"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_start_session(self, ws, session_id: str, *, input_mod: str) -> None:
        # Keep one StartSession payload shape for V1 short sessions. In ASR-only
        # mode, Doubao ignores the TTS block; later long-connection reuse can
        # split this by input_mod if the extra payload becomes measurable.
        request_params = {
            "asr": {"extra": {"end_smooth_window_ms": 1500}},
            "tts": {
                "speaker": self.config.speaker,
                "audio_config": {"channel": 1, "format": "pcm", "sample_rate": self.config.tts_sample_rate},
            },
            "dialog": {
                "bot_name": "豆包",
                "system_role": "你是机械手系统的语音接口。只做简短确认，不直接生成机械手控制动作。",
                "speaking_style": "回答简洁。",
                "extra": {"strict_audit": False, "recv_timeout": self.config.recv_timeout, "input_mod": input_mod},
            },
        }
        payload = gzip.compress(json.dumps(request_params, ensure_ascii=False).encode("utf-8"))
        request = bytearray(protocol.generate_header())
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
        payload = gzip.compress(b"{}")
        request = bytearray(protocol.generate_header())
        request.extend(protocol.FINISH_SESSION.to_bytes(4, "big"))
        request.extend(len(session_id).to_bytes(4, "big"))
        request.extend(session_id.encode("utf-8"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_finish_connection(self, ws) -> None:
        payload = gzip.compress(b"{}")
        request = bytearray(protocol.generate_header())
        request.extend(protocol.FINISH_CONNECTION.to_bytes(4, "big"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _recv_checked(self, ws) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.config.recv_timeout)
        except asyncio.TimeoutError as exc:
            raise DoubaoVoiceError("豆包语音服务响应超时。") from exc
        response = protocol.parse_response(raw)
        if response.get("message_type") == "SERVER_ERROR_RESPONSE":
            raise DoubaoVoiceError(f"豆包语音服务错误: {response}")
        return response
```

- [ ] **Step 5: Run unit tests**

Run:

```powershell
pytest tests/test_doubao_voice_client.py tests/test_doubao_realtime_protocol.py -v
```

Expected: all tests pass.

---

### Task 4: Route Voice Session ASR by Provider

**Files:**
- Modify: `robot_modbus_lite/voice_mixin.py`
- Test: `tests/test_voice_session_mixin.py`

- [ ] **Step 1: Add failing provider routing test**

Append to `tests/test_voice_session_mixin.py`:

```python
def test_voice_session_routes_to_doubao_asr_when_provider_is_doubao(monkeypatch):
    dummy = DummyVoiceSession()
    dummy._VOICE_FRAME_MS = 20
    dummy._VOICE_SILENCE_THRESHOLD = 1
    dummy._VOICE_PADDING_MS = 0
    monkeypatch.setenv("VOICE_ASR_PROVIDER", "doubao")
    calls = []

    class FakeClient:
        def transcribe_pcm(self, pcm, *, partial_callback=None):
            calls.append(pcm)
            if partial_callback is not None:
                partial_callback("移动")
            return {"text": "移动到位置A", "timing": {"voice_mode": "doubao_asr"}}

    dummy._get_doubao_voice_client = lambda: FakeClient()

    result = dummy._transcribe_pcm_for_voice_session(b"pcm")

    assert result["text"] == "移动到位置A"
    assert result["timing"]["voice_mode"] == "doubao_asr"
    assert calls == [b"pcm"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest tests/test_voice_session_mixin.py::test_voice_session_routes_to_doubao_asr_when_provider_is_doubao -v
```

Expected: fails with missing `_transcribe_pcm_for_voice_session`.

- [ ] **Step 3: Add provider helper methods to `VoiceMixin`**

In `robot_modbus_lite/voice_mixin.py`, add after `_transcribe_pcm_via_local_client`:

```python
    def _voice_asr_provider_name(self) -> str:
        import os

        provider = str(os.environ.get("VOICE_ASR_PROVIDER", "iflytek")).strip().lower()
        return provider or "iflytek"

    def _get_doubao_voice_client(self):
        client = getattr(self, "_doubao_voice_client", None)
        if client is None:
            from .doubao_voice_client import DoubaoVoiceClient

            client = DoubaoVoiceClient()
            self._doubao_voice_client = client
        return client

    def _transcribe_pcm_for_voice_session(self, pcm_data: bytes, *, partial_callback=None) -> dict[str, object]:
        if self._voice_asr_provider_name() == "doubao":
            return self._get_doubao_voice_client().transcribe_pcm(pcm_data, partial_callback=partial_callback)
        return self._transcribe_pcm_via_local_client(pcm_data, partial_callback=partial_callback)
```

- [ ] **Step 4: Replace voice session ASR call**

In `_voice_session_process_next_segment`, replace:

```python
            return self._transcribe_pcm_via_local_client(
                segment,
                partial_callback=self._voice_session_schedule_partial_text,
            )
```

with:

```python
            return self._transcribe_pcm_for_voice_session(
                segment,
                partial_callback=self._voice_session_schedule_partial_text,
            )
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_voice_session_mixin.py -v
```

Expected: all tests pass.

---

### Task 5: Add Doubao TTS SpeechSink

**Files:**
- Modify: `robot_modbus_lite/doubao_voice_client.py`
- Modify: `robot_modbus_lite/speech_broadcast.py`
- Test: `tests/test_speech_broadcast.py`

- [ ] **Step 1: Add failing TTS sink test**

Create or append to `tests/test_speech_broadcast.py`:

```python
from robot_modbus_lite.speech_broadcast import DoubaoSpeechSink


def test_doubao_speech_sink_synthesizes_and_plays_text():
    played = []

    class FakeClient:
        def synthesize_text(self, text):
            assert text == "执行完成"
            return b"pcm-data"

    sink = DoubaoSpeechSink(client=FakeClient(), player=lambda pcm, sample_rate: played.append((pcm, sample_rate)), sample_rate=24000)

    sink.speak("执行完成")

    assert played == [(b"pcm-data", 24000)]
    assert sink.is_speaking is False


def test_doubao_speech_sink_stop_marks_cancel_requested():
    sink = DoubaoSpeechSink(client=object(), player=lambda _pcm, _sample_rate: None)

    sink.stop()

    assert sink.is_speaking is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest tests/test_speech_broadcast.py -v
```

Expected: fails with missing `DoubaoSpeechSink`.

- [ ] **Step 3: Add TTS method to DoubaoVoiceClient**

Append to `robot_modbus_lite/doubao_voice_client.py`:

```python
    def synthesize_text(self, text: str) -> bytes:
        return self._run_async(self._synthesize_text_async(text))

    async def _synthesize_text_async(self, text: str) -> bytes:
        clean = str(text or "").strip()
        if not clean:
            return b""
        session_id = str(uuid.uuid4())
        connect_id = str(uuid.uuid4())
        audio = bytearray()
        async with connect_websocket(self.config.ws_url, headers=self.config.headers(connect_id)) as ws:
            await self._send_start_connection(ws)
            await self._recv_checked(ws)
            await self._send_start_session(ws, session_id, input_mod="text")
            await self._recv_checked(ws)
            await self._send_say_hello_text(ws, session_id, clean)
            while True:
                response = await self._recv_checked(ws)
                if response.get("message_type") == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
                    audio.extend(response["payload_msg"])
                if response.get("event") in {359, 152, 153}:
                    break
            await self._send_finish_session(ws, session_id)
            await self._send_finish_connection(ws)
        return bytes(audio)

    async def _send_say_hello_text(self, ws, session_id: str, text: str) -> None:
        payload = gzip.compress(json.dumps({"content": text}, ensure_ascii=False).encode("utf-8"))
        request = bytearray(protocol.generate_header())
        request.extend(protocol.SAY_HELLO.to_bytes(4, "big"))
        request.extend(len(session_id).to_bytes(4, "big"))
        request.extend(session_id.encode("utf-8"))
        request.extend(len(payload).to_bytes(4, "big"))
        request.extend(payload)
        await ws.send(bytes(request))

    async def _send_chat_tts_text(self, ws, session_id: str, text: str) -> None:
        # Kept only for future duplex-dialog customization. Standalone TTS uses
        # SAY_HELLO/content because ChatTTSText is tied to an active dialog reply.
        for start, end, content in ((True, False, text), (False, True, "")):
            payload = gzip.compress(json.dumps({"start": start, "end": end, "content": content}, ensure_ascii=False).encode("utf-8"))
            request = bytearray(protocol.generate_header())
            request.extend(protocol.CHAT_TTS_TEXT.to_bytes(4, "big"))
            request.extend(len(session_id).to_bytes(4, "big"))
            request.extend(session_id.encode("utf-8"))
            request.extend(len(payload).to_bytes(4, "big"))
            request.extend(payload)
            await ws.send(bytes(request))
```

- [ ] **Step 4: Add DoubaoSpeechSink**

Modify `robot_modbus_lite/speech_broadcast.py` and add after `WindowsSapiSpeechSink`:

```python
class DoubaoSpeechSink:
    """Doubao TTS sink backed by realtime dialogue TTS audio."""

    def __init__(self, client: object | None = None, player: Callable[[bytes, int], None] | None = None, sample_rate: int = 24000) -> None:
        self._client = client
        self._player = player or self._default_player
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
            pcm = client.synthesize_text(clean)
            if pcm and not self._cancel_requested:
                self._player(pcm, self._sample_rate)
        finally:
            self._is_speaking = False

    def stop(self) -> None:
        self._cancel_requested = True
        self._is_speaking = False

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
```

- [ ] **Step 5: Run TTS sink tests**

Run:

```powershell
pytest tests/test_speech_broadcast.py -v
```

Expected: all tests pass.

---

### Task 6: Configure TTS Provider Selection

**Files:**
- Modify: `robot_modbus_lite/operator_ui_mixin.py`
- Test: `tests/test_speech_broadcast.py`

- [ ] **Step 1: Add provider selection unit test**

Append to `tests/test_speech_broadcast.py`:

```python
from types import SimpleNamespace

from robot_modbus_lite.operator_ui_mixin import OperatorUiMixin
from robot_modbus_lite.speech_broadcast import DoubaoSpeechSink


class DummyOperator(OperatorUiMixin):
    pass


def test_operator_configures_doubao_tts_from_env(monkeypatch):
    dummy = DummyOperator.__new__(DummyOperator)
    dummy.axis_ranges = SimpleNamespace(operator_tts_enabled=True)
    dummy.operator_speech_sink = None
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "doubao")

    sink = dummy._operator_configure_tts_from_settings()

    assert isinstance(sink, DoubaoSpeechSink)


def test_operator_delivers_doubao_tts_async():
    sink = DoubaoSpeechSink(client=object(), player=lambda _pcm, _sample_rate: None)

    assert OperatorUiMixin._operator_should_deliver_speech_async(sink) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest tests/test_speech_broadcast.py::test_operator_configures_doubao_tts_from_env -v
```

Expected: fails because operator config still returns local TTS.

- [ ] **Step 3: Modify TTS configuration**

In `robot_modbus_lite/operator_ui_mixin.py`, extend the existing speech import:

```python
from .speech_broadcast import (
    DoubaoSpeechSink,
    Pyttsx3SpeechSink,
    SpeechBroadcastDeliveryService,
    SpeechDeliveryResult,
    WindowsSapiSpeechSink,
)
```

In `robot_modbus_lite/operator_ui_mixin.py`, update `_operator_configure_tts_from_settings`:

```python
    def _operator_configure_tts_from_settings(self):
        if not bool(getattr(getattr(self, "axis_ranges", None), "operator_tts_enabled", False)):
            self.operator_speech_sink = None
            return None
        import os

        provider = os.environ.get("VOICE_TTS_PROVIDER", "local").strip().lower()
        if provider == "doubao":
            self.operator_speech_sink = DoubaoSpeechSink()
            return self.operator_speech_sink
        return self._operator_enable_local_tts()
```

- [ ] **Step 4: Route Doubao TTS through the existing async speech delivery path**

In `robot_modbus_lite/operator_ui_mixin.py`, update `_operator_should_deliver_speech_async`:

```python
    @staticmethod
    def _operator_should_deliver_speech_async(sink: object) -> bool:
        return isinstance(sink, (Pyttsx3SpeechSink, WindowsSapiSpeechSink, DoubaoSpeechSink))
```

This is required because `DoubaoSpeechSink.speak()` performs network I/O and blocking audio playback.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_speech_broadcast.py -v
```

Expected: all tests pass.

---

### Task 7: Update Dependencies and Documentation

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `docs/superpowers/specs/2026-06-01-doubao-voice-interface-evaluation.md`

- [ ] **Step 1: Update dependencies**

Modify `requirements.txt` voice section by adding `websockets>=11,<16`; keep the existing `sounddevice` and `numpy` entries:

```text
# Voice features
sounddevice
numpy
websockets>=11,<16
```

- [ ] **Step 2: Update `.env.example`**

Append:

```env
# Doubao voice interface
VOICE_ASR_PROVIDER=iflytek
VOICE_TTS_PROVIDER=local
DOUBAO_API_KEY=
DOUBAO_RESOURCE_ID=volc.speech.dialog
DOUBAO_APP_KEY=PlgvMymc7f3tQnJ6
DOUBAO_SPEAKER=zh_male_yunzhou_jupiter_bigtts
DOUBAO_WS_URL=wss://openspeech.bytedance.com/api/v3/realtime/dialogue
DOUBAO_INPUT_SAMPLE_RATE=16000
DOUBAO_TTS_SAMPLE_RATE=24000
DOUBAO_RECV_TIMEOUT=10
```

- [ ] **Step 3: Update evaluation document**

Add a section:

```markdown
## 已确认实施范围

用户确认 ASR 和 TTS 都要接入豆包。实施仍保持安全边界：豆包负责听写和播报，项目负责 NLP、安全预检、确认和执行。

默认配置仍使用现有 provider：

```env
VOICE_ASR_PROVIDER=iflytek
VOICE_TTS_PROVIDER=local
```

启用豆包：

```env
VOICE_ASR_PROVIDER=doubao
VOICE_TTS_PROVIDER=doubao
```
```

- [ ] **Step 4: Run documentation-independent tests**

Run:

```powershell
pytest tests/test_doubao_realtime_protocol.py tests/test_doubao_voice_client.py tests/test_voice_session_mixin.py tests/test_speech_broadcast.py -v
```

Expected: all tests pass.

---

### Task 8: Manual Verification

**Files:**
- No source changes.

- [ ] **Step 1: Configure local `.env` without committing secret**

Set:

```env
VOICE_ASR_PROVIDER=doubao
VOICE_TTS_PROVIDER=doubao
DOUBAO_API_KEY=<local key>
```

- [ ] **Step 2: Verify imports compile**

Run:

```powershell
python -m compileall -q robot_modbus_lite tests
```

Expected: exit code 0.

- [ ] **Step 3: Verify unit tests**

Run:

```powershell
pytest tests/test_doubao_realtime_protocol.py tests/test_doubao_voice_client.py tests/test_voice_session_mixin.py tests/test_speech_broadcast.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Verify existing Doubao demo still runs**

Run:

```powershell
cd tests\realtime_dialog\python3.7
python main.py --audio=whoareyou.wav
```

Expected: WebSocket connects, StartConnection event 50, StartSession event 150, TTS end event 359, FinishConnection event 52.

- [ ] **Step 5: Verify GUI manually**

Run:

```powershell
python gui_main.py
```

Expected:

- Voice session starts.
- Spoken input appears in the user chat as recognized text.
- Existing NLP and safety behavior remains unchanged.
- Assistant response is spoken with Doubao TTS when `VOICE_TTS_PROVIDER=doubao`.
- Switching `VOICE_ASR_PROVIDER=iflytek` and `VOICE_TTS_PROVIDER=local` returns to previous behavior.

---

## Self-Review

- Spec coverage: ASR and TTS are both covered. Existing NLP/safety/execute chain remains unchanged. Fallback provider switches are included.
- Review fixes incorporated: Doubao TTS playback converts float32 PCM bytes with `np.frombuffer`, Doubao TTS is routed through async speech delivery, WebSocket receives use `recv_timeout`, and ASR/TTS sync wrappers both use the shared `_run_async` helper.
- Second review incorporated: V1 short-session choices are documented for `StartSession`, standalone TTS uses `SAY_HELLO` after real API verification, and the TTS provider test now bypasses `OperatorUiMixin.__init__` while explicitly testing async delivery routing.
- Self-review incorporated: `websockets` version/API compatibility is handled with `connect_websocket()`, dependency guidance is pinned to `websockets>=11,<16`, and the GUI-only scope is explicit so the Web short-recording iFlytek worker is not mistaken as replaced.
- Placeholder scan: No TBD/TODO placeholders remain in implementation steps.
- Type consistency: `DoubaoVoiceConfig`, `DoubaoVoiceClient`, `DoubaoSpeechSink`, and `_transcribe_pcm_for_voice_session` names are consistent across tasks.
- Scope control: This plan does not implement direct Doubao model control of robot actions.
