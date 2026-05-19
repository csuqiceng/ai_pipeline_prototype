"""Voice control service boundary for the Web API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import subprocess
import sys
import time
from typing import Any

from .runtime_paths import runtime_dir
from .voice_ipc import cleanup_stop_flag, make_voice_worker_files, reset_stop_flag, write_stop_flag


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
    """Short-term voice API facade.

    The existing iFlytek/VAD capture remains Python-side. This facade exposes
    the controls expected by the Web UI and can later delegate to the current
    voice worker without changing routes.
    """

    def __init__(self) -> None:
        self._state = VoiceServiceState()
        self._process: subprocess.Popen[str] | None = None
        self._stop_flag_path = None
        self._result_path = None
        self._worker_log_path = None
        self._started_monotonic = 0.0
        self._pending_event: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        self._poll_finished_process()
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
        self._poll_finished_process()
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
        self._poll_finished_process()
        if self._process and self._process.poll() is None:
            return self.status()

        log_dir = runtime_dir() / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        worker_files = make_voice_worker_files(log_dir, timestamp)
        reset_stop_flag(worker_files.stop_flag)

        cmd = self._build_worker_command(worker_files.debug_pcm, worker_files.result_json, worker_files.stop_flag)
        self._worker_log_path = log_dir / f"iflytek_worker_web_{timestamp}.log"
        self._stop_flag_path = worker_files.stop_flag
        self._result_path = worker_files.result_json

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(runtime_dir()),
            )
            self._started_monotonic = time.monotonic()
            self._state.running = True
            self._state.phase = "recording"
            self._state.last_error = ""
            self._state.last_text = ""
            self._state.result_path = str(worker_files.result_json)
            self._state.worker_log_path = str(self._worker_log_path)
            self._state.started_at = datetime.now().isoformat(timespec="seconds")
            self._state.finished_at = ""
        except Exception as exc:
            self._state.running = False
            self._state.phase = "idle"
            self._state.last_error = f"{type(exc).__name__}: {exc}"
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self._process and self._process.poll() is None and self._stop_flag_path:
            elapsed = time.monotonic() - self._started_monotonic if self._started_monotonic else 0.0
            if elapsed < 1.5:
                time.sleep(1.5 - elapsed)
            write_stop_flag(self._stop_flag_path)
            self._state.phase = "recognizing"
            self._wait_for_process(timeout_sec=75.0)
        else:
            self._poll_finished_process()
        return self.status()

    def _build_worker_command(self, debug_pcm, result_path, stop_flag_path) -> list[str]:
        args = [
            "--iflytek-worker",
            "--mode",
            "mic",
            "--duration",
            "3600",
            "--stop-flag-path",
            str(stop_flag_path),
            "--debug-save-path",
            str(debug_pcm),
            "--result-path",
            str(result_path),
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, *args]
        return [sys.executable, str(runtime_dir() / "gui_main.py"), *args]

    def _poll_finished_process(self) -> None:
        if not self._process or self._process.poll() is None:
            return
        self._finalize_process()

    def _wait_for_process(self, *, timeout_sec: float) -> None:
        if not self._process:
            return
        deadline = time.monotonic() + timeout_sec
        while self._process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if self._process.poll() is None:
            self._state.running = True
            self._state.phase = "recognizing"
            self._state.last_error = "已发送停止信号，仍在等待语音识别结果。"
            return
        self._finalize_process()

    def _finalize_process(self) -> None:
        process = self._process
        if not process:
            return
        try:
            stdout, stderr = process.communicate(timeout=2)
        except Exception:
            stdout, stderr = "", ""
        returncode = process.returncode
        self._process = None

        if self._stop_flag_path:
            try:
                cleanup_stop_flag(self._stop_flag_path)
            except Exception:
                pass
        self._stop_flag_path = None

        payload: dict[str, Any] = {}
        if self._result_path and self._result_path.exists():
            try:
                payload = json.loads(self._result_path.read_text(encoding="utf-8"))
            except Exception as exc:
                payload = {"ok": False, "error": f"语音结果文件解析失败: {type(exc).__name__}: {exc}"}
        else:
            payload = {"ok": False, "error": (stderr or "").strip() or "语音 worker 未返回结果。"}

        if self._worker_log_path:
            try:
                self._worker_log_path.write_text(
                    json.dumps(
                        {
                            "returncode": returncode,
                            "stdout": stdout or "",
                            "stderr": stderr or "",
                            "result_path": str(self._result_path or ""),
                            "payload": payload,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

        self._state.running = False
        self._state.phase = "idle"
        self._state.finished_at = datetime.now().isoformat(timespec="seconds")
        if payload.get("ok"):
            self._state.last_text = str(payload.get("text", "")).strip()
            self._state.last_error = ""
            if self._state.last_text:
                self._pending_event = {
                    "type": "voice_input_complete",
                    "text": self._state.last_text,
                    "finished_at": self._state.finished_at,
                    "result_path": self._state.result_path,
                    "worker_log_path": self._state.worker_log_path,
                }
        else:
            self._state.last_text = ""
            error_text = str(payload.get("error", "麦克风识别失败。"))
            if "10165" in error_text or "invalid handle" in error_text.lower():
                error_text = "录音时间过短或讯飞连接句柄未就绪，请等待按钮显示采集中后再停止录音。"
            self._state.last_error = error_text
