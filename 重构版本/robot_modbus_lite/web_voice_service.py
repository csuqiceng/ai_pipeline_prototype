"""Voice control service boundary for the Web API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import threading
import time
from typing import Any

from .doubao_voice_client import DoubaoVoiceClient
from .runtime_paths import runtime_dir


@dataclass
class VoiceServiceState:
    running: bool = False
    phase: str = "idle"
    mode: str = "backend_capture"
    last_text: str = ""
    last_error: str = ""
    result_path: str = ""
    worker_log_path: str = ""
    started_at: str = ""
    finished_at: str = ""


class WebVoiceService:
    """Web API voice facade backed by sounddevice capture and Doubao ASR."""

    def __init__(self) -> None:
        self._state = VoiceServiceState()
        self._recording_thread: threading.Thread | None = None
        self._stop_recording_event = threading.Event()
        self._recording_frames: list[bytes] = []
        self._recording_error: BaseException | None = None
        self._debug_pcm_path = None
        self._worker_log_path = None
        self._started_monotonic = 0.0
        self._pending_event: dict[str, Any] | None = None
        self._sample_rate = 16000

    def status(self) -> dict[str, Any]:
        return {
            "running": self._state.running,
            "phase": self._state.phase,
            "mode": self._state.mode,
            "last_text": self._state.last_text,
            "last_error": self._state.last_error,
            "result_path": self._state.result_path,
            "worker_log_path": self._state.worker_log_path,
            "started_at": self._state.started_at,
            "finished_at": self._state.finished_at,
        }

    def consume_event(self) -> dict[str, Any] | None:
        event = self._pending_event
        self._pending_event = None
        return event

    def devices(self) -> dict[str, Any]:
        devices: list[dict[str, Any]] = []
        try:
            import sounddevice as sd

            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                devices.append(
                    {
                        "id": str(index),
                        "name": str(device.get("name", f"Input {index}")),
                        "channels": int(device.get("max_input_channels", 0)),
                        "default_samplerate": float(device.get("default_samplerate", 0.0)),
                    }
                )
        except Exception as exc:
            self._state.last_error = f"{type(exc).__name__}: {exc}"
        return {"devices": devices, "selected_device_id": devices[0]["id"] if devices else None}

    def start(self) -> dict[str, Any]:
        if self._recording_thread is not None and self._recording_thread.is_alive():
            return self.status()

        log_dir = runtime_dir() / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._debug_pcm_path = log_dir / f"doubao_voice_web_{timestamp}.pcm"
        self._worker_log_path = log_dir / f"doubao_voice_web_{timestamp}.json"
        self._recording_frames = []
        self._recording_error = None
        self._stop_recording_event.clear()

        try:
            self._recording_thread = threading.Thread(
                target=self._capture_microphone_until_stop,
                name="web-doubao-voice-capture",
                daemon=True,
            )
            self._recording_thread.start()
            self._started_monotonic = time.monotonic()
            self._state.running = True
            self._state.phase = "recording"
            self._state.mode = "doubao_backend_capture"
            self._state.last_error = ""
            self._state.last_text = ""
            self._state.result_path = str(self._debug_pcm_path)
            self._state.worker_log_path = str(self._worker_log_path)
            self._state.started_at = datetime.now().isoformat(timespec="seconds")
            self._state.finished_at = ""
        except Exception as exc:
            self._state.running = False
            self._state.phase = "idle"
            self._state.last_error = f"{type(exc).__name__}: {exc}"
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self._recording_thread is not None and self._recording_thread.is_alive():
            elapsed = time.monotonic() - self._started_monotonic if self._started_monotonic else 0.0
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)
            self._state.phase = "recognizing"
            self._stop_recording_event.set()
            self._recording_thread.join(timeout=5.0)
            if self._recording_thread.is_alive():
                self._state.running = True
                self._state.phase = "recognizing"
                self._state.last_error = "已发送停止信号，仍在等待麦克风采集线程结束。"
                return self.status()
            self._finalize_recording()
        else:
            self._state.running = False
            self._state.phase = "idle"
        return self.status()

    def _capture_microphone_until_stop(self) -> None:
        try:
            import sounddevice as sd

            def callback(indata, frames_count, time_info, status):
                if status:
                    pass
                self._recording_frames.append(bytes(indata))

            with sd.RawInputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                blocksize=3200,
                callback=callback,
            ):
                while not self._stop_recording_event.is_set():
                    time.sleep(0.05)
        except BaseException as exc:
            self._recording_error = exc

    def _finalize_recording(self) -> None:
        if self._recording_error is not None:
            self._finish_failed(f"{type(self._recording_error).__name__}: {self._recording_error}")
            return

        pcm_data = b"".join(self._recording_frames)
        if not pcm_data:
            self._finish_failed("未录到音频，请确认麦克风可用并允许本程序访问麦克风。")
            return

        if self._debug_pcm_path:
            try:
                self._debug_pcm_path.write_bytes(pcm_data)
            except Exception:
                pass

        try:
            result = DoubaoVoiceClient().transcribe_pcm(pcm_data)
            text = str(result.get("text", "") if isinstance(result, dict) else result).strip()
            self._finish_success(text, result if isinstance(result, dict) else {})
        except Exception as exc:
            self._finish_failed(f"{type(exc).__name__}: {exc}")

    def _finish_success(self, text: str, result: dict[str, Any]) -> None:
        payload = {"ok": True, "text": text, "result": result}
        self._write_log_payload(payload)
        self._state.running = False
        self._state.phase = "idle"
        self._state.finished_at = datetime.now().isoformat(timespec="seconds")
        self._state.last_text = text
        self._state.last_error = ""
        if text:
            self._pending_event = {
                "type": "voice_input_complete",
                "text": text,
                "finished_at": self._state.finished_at,
                "result_path": self._state.result_path,
                "worker_log_path": self._state.worker_log_path,
            }

    def _finish_failed(self, message: str) -> None:
        payload = {"ok": False, "error": message}
        self._write_log_payload(payload)
        self._state.running = False
        self._state.phase = "idle"
        self._state.finished_at = datetime.now().isoformat(timespec="seconds")
        self._state.last_text = ""
        self._state.last_error = message

    def _write_log_payload(self, payload: dict[str, Any]) -> None:
        if self._worker_log_path:
            try:
                self._worker_log_path.write_text(
                    json.dumps({"payload": payload}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
