import os
import asyncio
import gzip
import json
import threading

import pytest

from robot_modbus_lite import doubao_realtime_protocol as protocol
from robot_modbus_lite.doubao_voice_client import (
    DoubaoStreamingAsrSession,
    DoubaoVoiceClient,
    DoubaoVoiceConfig,
    extract_final_asr_text,
    extract_interim_asr_text,
)
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


def test_doubao_config_loads_endpoint_and_queue_settings_from_env(monkeypatch):
    monkeypatch.setenv("DOUBAO_API_KEY", "key-1")
    monkeypatch.setenv("DOUBAO_END_SMOOTH_WINDOW_MS", "800")
    monkeypatch.setenv("DOUBAO_STREAM_QUEUE_MAX_CHUNKS", "12")

    config = DoubaoVoiceConfig.from_env()

    assert config.end_smooth_window_ms == 800
    assert config.stream_queue_max_chunks == 12


def test_doubao_config_defaults_to_reference_end_smooth_window(monkeypatch):
    monkeypatch.setenv("DOUBAO_API_KEY", "key-1")
    monkeypatch.delenv("DOUBAO_END_SMOOTH_WINDOW_MS", raising=False)

    config = DoubaoVoiceConfig.from_env()

    assert config.end_smooth_window_ms == 1500


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


def test_extract_interim_asr_text_returns_latest_interim_result():
    event = {
        "event": 451,
        "payload_msg": {
            "results": [
                {"text": "你", "is_interim": True},
                {"text": "你好小正", "is_interim": True},
            ]
        },
    }

    assert extract_interim_asr_text(event) == "你好小正"


def test_extract_interim_asr_text_ignores_final_result():
    event = {"event": 451, "payload_msg": {"results": [{"text": "移动到位置A", "is_interim": False}]}}

    assert extract_interim_asr_text(event) == ""


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


def test_doubao_voice_client_stream_synthesize_invokes_chunk_callback(monkeypatch):
    config = DoubaoVoiceConfig(api_key="key")
    client = DoubaoVoiceClient(config)

    async def fake_stream(text, chunk_callback):
        assert text == "执行完成"
        chunk_callback(b"chunk-1")
        chunk_callback(b"chunk-2")

    monkeypatch.setattr(client, "_stream_synthesize_text_async", fake_stream)

    chunks = []
    client.stream_synthesize_text("执行完成", chunks.append)

    assert chunks == [b"chunk-1", b"chunk-2"]


def test_transcribe_returns_after_final_asr_text_without_waiting_for_terminal_event(monkeypatch):
    client = DoubaoVoiceClient(DoubaoVoiceConfig(api_key="key", recv_timeout=1))
    events = iter(
        [
            {"message_type": "SERVER_FULL_RESPONSE", "event": 50, "payload_msg": {}},
            {"message_type": "SERVER_FULL_RESPONSE", "event": 150, "payload_msg": {}},
            {
                "message_type": "SERVER_FULL_RESPONSE",
                "event": 451,
                "payload_msg": {"results": [{"text": "移动到位置A", "is_interim": False}]},
            },
        ]
    )
    sent = []

    class FakeWebSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, data):
            sent.append(data)

    async def fake_recv(_ws):
        try:
            return next(events)
        except StopIteration:
            pytest.fail("client waited for another event after final ASR text")

    monkeypatch.setattr("robot_modbus_lite.doubao_voice_client.connect_websocket", lambda *_args, **_kwargs: FakeWebSocket())
    monkeypatch.setattr(client, "_recv_checked", fake_recv)

    result = client.transcribe_pcm(b"pcm")

    assert result["text"] == "移动到位置A"
    assert sent


def _server_full_packet(event: int, payload_obj: dict, *, session_id: str = "session-1") -> bytes:
    session_bytes = session_id.encode("utf-8")
    payload = gzip.compress(json.dumps(payload_obj, ensure_ascii=False).encode("utf-8"))
    packet = bytearray(protocol.generate_header(message_type=protocol.SERVER_FULL_RESPONSE))
    packet.extend(event.to_bytes(4, "big"))
    packet.extend(len(session_bytes).to_bytes(4, "big"))
    packet.extend(session_bytes)
    packet.extend(len(payload).to_bytes(4, "big"))
    packet.extend(payload)
    return bytes(packet)


def test_streaming_asr_session_sends_audio_and_emits_final_text(monkeypatch):
    sent_events = []
    final_texts = []
    audio_sent = threading.Event()
    close_requested = threading.Event()

    class FakeWebSocket:
        def __init__(self):
            self._recv_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, data):
            event = int.from_bytes(data[4:8], "big")
            sent_events.append(event)
            if event == protocol.TASK_REQUEST:
                audio_sent.set()

        async def recv(self):
            self._recv_count += 1
            if self._recv_count == 1:
                return _server_full_packet(50, {})
            if self._recv_count == 2:
                return _server_full_packet(150, {})
            if self._recv_count == 3:
                await asyncio.to_thread(audio_sent.wait, 2)
                return _server_full_packet(
                    451,
                    {"results": [{"text": "移动到位置A", "is_interim": False}]},
                )
            await asyncio.to_thread(close_requested.wait, 2)
            return _server_full_packet(153, {})

        async def close(self):
            close_requested.set()

    monkeypatch.setattr("robot_modbus_lite.doubao_voice_client.connect_websocket", lambda *_args, **_kwargs: FakeWebSocket())
    session = DoubaoStreamingAsrSession(
        DoubaoVoiceConfig(api_key="key", recv_timeout=2),
        on_final_text=final_texts.append,
    )

    session.start()
    session.send_audio(b"pcm")
    assert audio_sent.wait(2)
    assert session.wait_for_final_text(timeout=2)
    session.close()

    assert final_texts == ["移动到位置A"]
    assert protocol.START_CONNECTION in sent_events
    assert protocol.START_SESSION in sent_events
    assert protocol.TASK_REQUEST in sent_events
    assert protocol.FINISH_SESSION in sent_events


def test_streaming_asr_session_emits_partial_text(monkeypatch):
    final_texts = []
    partial_texts = []
    audio_sent = threading.Event()
    close_requested = threading.Event()

    class FakeWebSocket:
        def __init__(self):
            self._recv_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send(self, data):
            event = int.from_bytes(data[4:8], "big")
            if event == protocol.TASK_REQUEST:
                audio_sent.set()

        async def recv(self):
            self._recv_count += 1
            if self._recv_count == 1:
                return _server_full_packet(50, {})
            if self._recv_count == 2:
                return _server_full_packet(150, {})
            if self._recv_count == 3:
                await asyncio.to_thread(audio_sent.wait, 2)
                return _server_full_packet(
                    451,
                    {"results": [{"text": "你好小正", "is_interim": True}]},
                )
            if self._recv_count == 4:
                return _server_full_packet(
                    451,
                    {"results": [{"text": "你好小正，移动到位置A", "is_interim": False}]},
                )
            await asyncio.to_thread(close_requested.wait, 2)
            return _server_full_packet(153, {})

        async def close(self):
            close_requested.set()

    monkeypatch.setattr("robot_modbus_lite.doubao_voice_client.connect_websocket", lambda *_args, **_kwargs: FakeWebSocket())
    session = DoubaoStreamingAsrSession(
        DoubaoVoiceConfig(api_key="key", recv_timeout=2),
        on_final_text=final_texts.append,
        on_partial_text=partial_texts.append,
    )

    session.start()
    session.send_audio(b"pcm")
    assert session.wait_for_final_text(timeout=2)
    session.close()

    assert partial_texts == ["你好小正"]
    assert final_texts == ["你好小正，移动到位置A"]


def test_streaming_asr_session_receive_loop_waits_without_idle_timeout():
    timeouts = []

    class FakeClient:
        async def _recv_checked(self, _ws, *, timeout="unset"):
            timeouts.append(timeout)
            return {"message_type": "SERVER_FULL_RESPONSE", "event": 153, "payload_msg": {}}

    session = DoubaoStreamingAsrSession(
        DoubaoVoiceConfig(api_key="key", recv_timeout=15),
        on_final_text=lambda _text: None,
    )

    asyncio.run(session._recv_loop(FakeClient(), object()))

    assert timeouts == [None]


def test_streaming_asr_session_drops_old_audio_when_queue_is_full():
    session = DoubaoStreamingAsrSession(
        DoubaoVoiceConfig(api_key="key", stream_queue_max_chunks=2),
        on_final_text=lambda _text: None,
    )

    session.send_audio(b"old-1")
    session.send_audio(b"old-2")
    session.send_audio(b"new-3")

    queued = [session._audio_queue.get_nowait(), session._audio_queue.get_nowait()]
    assert queued == [b"old-2", b"new-3"]
