"""语音识别配置、录音、子进程通信和语音指令执行逻辑。"""

from __future__ import annotations

import importlib.util
import json
import queue
import subprocess
import sys
import threading
import time
from array import array
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Signal

from .voice_ipc import cleanup_stop_flag, make_voice_worker_files, reset_stop_flag, write_stop_flag
from .voice_session import VoiceSessionSegmenter


class VoiceMixin:
    """为主窗口增加语音识别和语音指令执行能力。"""

    _VOICE_SAMPLE_RATE = 16000
    _VOICE_SAMPLE_WIDTH_BYTES = 2
    _VOICE_SILENCE_THRESHOLD = 350
    _VOICE_FRAME_MS = 20
    _VOICE_PADDING_MS = 180
    _VOICE_STREAM_MIN_RECORD_SEC = 1.0

    def _create_iflytek_client(self):
        """创建客户端。"""
        # 订阅模式：无需本地凭证
        if self._use_license_voice:
            return True

        from .iflytek_iat import IFlytekIATConfig, IFlytekRTASRError, expected_env_locations

        try:
            IFlytekIATConfig.from_env()
            if importlib.util.find_spec("xfyunsdkspeech") is None:
                raise RuntimeError("未安装 xfyunsdkspeech，请先安装讯飞官方 SDK。")
            return True
        except IFlytekRTASRError as exc:
            env_locations = " / ".join(str(path) for path in expected_env_locations())
            raise RuntimeError(
                f"{exc}\n请在以下任一文件配置讯飞凭证后重试：\n{env_locations}\n"
                "需要的键：IFLYTEK_APP_ID、IFLYTEK_API_KEY、IFLYTEK_API_SECRET"
            ) from exc

    def _get_local_iflytek_client(self, *, reset: bool = False):
        """获取本地讯飞客户端。

        讯飞 IAT SDK 的 stream 连接在一次识别后会关闭。连续会话分段识别
        必须为每段语音创建新客户端，避免复用已关闭的 websocket。
        """
        from .iflytek_iat import IFlytekIATClient, IFlytekIATConfig

        lock = getattr(self, "_iflytek_local_client_lock", None)
        if lock is None:
            config = IFlytekIATConfig.from_env()
            return IFlytekIATClient(config), 0

        start = time.perf_counter()
        with lock:
            if reset:
                self._iflytek_local_client = None
            if self._iflytek_local_client is None:
                config = IFlytekIATConfig.from_env()
                self._iflytek_local_client = IFlytekIATClient(config)
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                return self._iflytek_local_client, elapsed_ms
            return self._iflytek_local_client, 0

    def _trim_pcm_silence(self, pcm_data: bytes) -> tuple[bytes, dict[str, int]]:
        """裁掉首尾静音，减少上传和识别的无效音频。"""
        bytes_per_frame = int(self._VOICE_SAMPLE_RATE * self._VOICE_SAMPLE_WIDTH_BYTES * self._VOICE_FRAME_MS / 1000)
        bytes_per_frame = max(self._VOICE_SAMPLE_WIDTH_BYTES, bytes_per_frame)
        frame_count = len(pcm_data) // bytes_per_frame
        original_ms = int(len(pcm_data) / (self._VOICE_SAMPLE_RATE * self._VOICE_SAMPLE_WIDTH_BYTES) * 1000)
        stats = {
            "voice_audio_original_ms": original_ms,
            "voice_audio_trimmed_ms": original_ms,
            "voice_audio_trimmed_head_ms": 0,
            "voice_audio_trimmed_tail_ms": 0,
        }
        if frame_count <= 2:
            return pcm_data, stats

        active_frames: list[int] = []
        for index in range(frame_count):
            chunk = pcm_data[index * bytes_per_frame:(index + 1) * bytes_per_frame]
            samples = array("h")
            samples.frombytes(chunk)
            if samples and max(abs(sample) for sample in samples) >= self._VOICE_SILENCE_THRESHOLD:
                active_frames.append(index)

        if not active_frames:
            return pcm_data, stats

        padding_frames = max(1, self._VOICE_PADDING_MS // self._VOICE_FRAME_MS)
        start_frame = max(0, active_frames[0] - padding_frames)
        end_frame = min(frame_count, active_frames[-1] + padding_frames + 1)
        start = start_frame * bytes_per_frame
        end = end_frame * bytes_per_frame
        if start <= 0 and end >= len(pcm_data):
            return pcm_data, stats

        trimmed = pcm_data[start:end]
        trimmed_ms = int(len(trimmed) / (self._VOICE_SAMPLE_RATE * self._VOICE_SAMPLE_WIDTH_BYTES) * 1000)
        stats.update(
            {
                "voice_audio_trimmed_ms": trimmed_ms,
                "voice_audio_trimmed_head_ms": int(start / (self._VOICE_SAMPLE_RATE * self._VOICE_SAMPLE_WIDTH_BYTES) * 1000),
                "voice_audio_trimmed_tail_ms": max(0, original_ms - trimmed_ms - int(start / (self._VOICE_SAMPLE_RATE * self._VOICE_SAMPLE_WIDTH_BYTES) * 1000)),
            }
        )
        return trimmed, stats

    def _format_voice_timing_detail(self, timings: dict[str, int], *, text: str | None = None) -> str:
        """格式化语音识别耗时。"""
        parts = [
            f"音频 {timings.get('voice_audio_original_ms', 0)}ms",
            f"裁剪后 {timings.get('voice_audio_trimmed_ms', 0)}ms",
            f"裁剪 {timings.get('voice_trim_ms', 0)}ms",
            f"写文件 {timings.get('voice_temp_file_ms', 0)}ms",
            f"client初始化 {timings.get('voice_client_init_ms', 0)}ms",
            f"识别 {timings.get('voice_transcribe_ms', 0)}ms",
            f"总耗时 {timings.get('voice_total_ms', 0)}ms",
        ]
        if text is not None:
            parts.append(f"文本: {text or '-'}")
        return " | ".join(parts)

    def _transcribe_pcm_via_local_client(self, pcm_data: bytes, *, partial_callback=None) -> dict[str, object]:
        """在后台线程中复用本地讯飞客户端识别 PCM。"""
        import tempfile

        total_start = time.perf_counter()
        trim_start = time.perf_counter()
        trimmed_pcm, timing = self._trim_pcm_silence(pcm_data)
        timing["voice_trim_ms"] = int((time.perf_counter() - trim_start) * 1000)

        file_start = time.perf_counter()
        tmp = tempfile.NamedTemporaryFile(suffix=".pcm", delete=False)
        tmp.write(trimmed_pcm)
        tmp_name = tmp.name
        tmp.close()
        timing["voice_temp_file_ms"] = int((time.perf_counter() - file_start) * 1000)

        try:
            client, init_ms = self._get_local_iflytek_client(reset=True)
            timing["voice_client_init_ms"] = init_ms
            transcribe_start = time.perf_counter()
            try:
                result = client.transcribe_file(tmp_name, chunk_callback=partial_callback)
            except Exception:
                client, retry_init_ms = self._get_local_iflytek_client(reset=True)
                timing["voice_client_retry"] = 1
                timing["voice_client_retry_init_ms"] = retry_init_ms
                result = client.transcribe_file(tmp_name, chunk_callback=partial_callback)
            finally:
                if hasattr(self, "_iflytek_local_client"):
                    self._iflytek_local_client = None
            timing["voice_transcribe_ms"] = int((time.perf_counter() - transcribe_start) * 1000)
            timing["voice_total_ms"] = int((time.perf_counter() - total_start) * 1000)
            return {"text": result.text.strip(), "timing": timing}
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def _run_iflytek_worker(self, args: list[str]) -> str:
        """运行子进程。"""
        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        debug_pcm = log_dir / f"voice_debug_{timestamp}.pcm"
        worker_log = log_dir / f"iflytek_worker_{timestamp}.log"
        result_path = log_dir / f"iflytek_result_{timestamp}.json"

        cmd = self._build_iflytek_worker_command(args, debug_pcm, result_path)

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            cwd=str(self.runtime_root),
        )

        stderr_text = (completed.stderr or "").strip()
        worker_log.write_text(
            json.dumps(
                {
                    "cmd": cmd,
                    "returncode": completed.returncode,
                    "stdout": (completed.stdout or ""),
                    "stderr": (completed.stderr or ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if not result_path.exists():
            detail = stderr_text or "讯飞 worker 未返回结果。"
            raise RuntimeError(f"{detail}\n调试日志: {worker_log}")

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"讯飞 worker 返回结果文件不是合法 JSON。\n{stderr_text}\n调试日志: {worker_log}") from exc

        if not payload.get("ok"):
            raise RuntimeError(f"{payload.get('error', '讯飞识别失败。')}\n调试日志: {worker_log}")
        return str(payload.get("text", "")).strip()

    def _build_iflytek_worker_command(self, args: list[str], debug_pcm: Path, result_path: Path) -> list[str]:
        """构建子进程命令。"""
        license_args: list[str] = []
        if self._use_license_voice:
            license_args = ["--use-license", "--cache-dir", str(self.runtime_root / "data")]

        if getattr(sys, "frozen", False):
            return [sys.executable, "--iflytek-worker", *args, *license_args, "--debug-save-path", str(debug_pcm), "--result-path", str(result_path)]
        return [
            sys.executable,
            str(self.runtime_root / "gui_main.py"),
            "--iflytek-worker",
            *args,
            *license_args,
            "--debug-save-path",
            str(debug_pcm),
            "--result-path",
            str(result_path),
        ]

    def _refresh_microphone_devices(self) -> None:
        """刷新麦克风。"""
        if not hasattr(self, "mic_device_combo"):
            return
        previous = self.mic_device_combo.currentData() if self.mic_device_combo.count() else None
        self.mic_device_combo.clear()
        self.mic_device_combo.addItem("系统默认麦克风", None)
        try:
            import sounddevice as sd

            added = 0
            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                name = str(device.get("name", f"设备{index}")).strip() or f"设备{index}"
                self.mic_device_combo.addItem(f"{index}: {name}", index)
                added += 1
            if previous is not None:
                restore_index = self.mic_device_combo.findData(previous)
                if restore_index >= 0:
                    self.mic_device_combo.setCurrentIndex(restore_index)
            self._append_log("语音", "刷新麦克风设备", "成功", f"检测到 {added} 个输入设备")
        except Exception as exc:
            self._append_log("语音", "刷新麦克风设备", "失败", str(exc))

    def _selected_microphone_device(self) -> int | None:
        """处理选中麦克风。"""
        if not hasattr(self, "mic_device_combo"):
            return None
        data = self.mic_device_combo.currentData()
        return int(data) if data is not None else None

    def _toggle_microphone_recording(self) -> None:
        """切换麦克风。"""
        if getattr(self, "_local_voice_streaming", False):
            self._stop_local_streaming_recognition()
            return
        # 持久线程：正在采集 → 停止
        if self._proxy_mic_capturing and self._mic_recorder_thread is not None:
            self._mic_recorder_thread.stop_capturing()
            self._proxy_mic_capturing = False
            self.mic_toggle_btn.setEnabled(False)
            self.status_label.setText("正在停止录音并等待识别结果。")
            self._append_log("语音", "停止录音", "成功", "已发送停止信号")
            return
        # 子进程模式：停止
        if self._mic_process and self._mic_process.poll() is None:
            self._stop_microphone_recording()
            return
        self._start_microphone_recording()

    def _start_microphone_recording(self) -> None:
        """启动麦克风。"""
        if self._mic_process and self._mic_process.poll() is None:
            return
        if self._proxy_mic_capturing:
            return
        try:
            if hasattr(self, "nlp_input_edit"):
                self.nlp_input_edit.clear()
            self._create_iflytek_client()

            if not self._use_license_voice:
                self._start_local_streaming_recognition()
                return

            # 优先使用持久线程（零延迟）
            if self._mic_recorder_thread is not None:
                self._mic_recorder_thread.start_capturing()
                self._proxy_mic_capturing = True
                self.mic_toggle_btn.setText("停止录音")
                self.status_label.setText("麦克风录音中，请说话...")
                self._append_log("语音", "开始录音", "成功", "零延迟模式")
            else:
                # 降级到子进程模式
                self._start_subprocess_recording()
        except Exception as exc:
            self._show_critical("开始录音失败", str(exc))
            self._append_log("语音", "开始录音", "失败", str(exc))

    def _start_local_streaming_recognition(self) -> None:
        """本地讯飞实时流式识别：录音时同步上传音频。"""
        if getattr(self, "_local_voice_streaming", False):
            return

        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stop_flag = log_dir / f"voice_stream_stop_{timestamp}.flag"
        debug_pcm = log_dir / f"voice_stream_debug_{timestamp}.pcm"
        reset_stop_flag(stop_flag)

        selected_device = self._selected_microphone_device()
        self._local_voice_streaming = True
        self._local_voice_stream_started_perf = time.perf_counter()
        self._local_voice_stream_stop_pending = False
        self._local_voice_stream_stop_flag_path = stop_flag
        self._local_voice_stream_debug_path = debug_pcm
        self.mic_toggle_btn.setText("停止录音")
        self.mic_toggle_btn.setEnabled(True)
        self.status_label.setText("实时语音识别中，请说话...")
        detail = "实时流式模式（系统默认）" if selected_device is None else f"实时流式模式（设备 {selected_device}）"
        self._append_log("语音", "开始录音", "成功", detail)

        def work():
            """后台执行本地流式识别。"""
            from .iflytek_iat import IFlytekMicrophoneConfig

            total_start = time.perf_counter()
            client, init_ms = self._get_local_iflytek_client(reset=True)
            transcribe_start = time.perf_counter()
            try:
                result = client.transcribe_microphone(
                    IFlytekMicrophoneConfig(
                        duration_sec=3600.0,
                        sample_rate=self._VOICE_SAMPLE_RATE,
                        channels=1,
                        sample_width_bytes=self._VOICE_SAMPLE_WIDTH_BYTES,
                        device=selected_device,
                        warmup_sec=0.1,
                        debug_save_path=str(debug_pcm),
                        stop_flag_path=str(stop_flag),
                    )
                )
            except Exception:
                self._get_local_iflytek_client(reset=True)
                raise

            total_ms = int((time.perf_counter() - total_start) * 1000)
            audio_ms = 0
            if debug_pcm.exists():
                audio_ms = int(debug_pcm.stat().st_size / (self._VOICE_SAMPLE_RATE * self._VOICE_SAMPLE_WIDTH_BYTES) * 1000)
            timing = {
                "voice_mode": "local_streaming",
                "voice_audio_original_ms": audio_ms,
                "voice_audio_trimmed_ms": audio_ms,
                "voice_trim_ms": 0,
                "voice_temp_file_ms": 0,
                "voice_client_init_ms": init_ms,
                "voice_transcribe_ms": int((time.perf_counter() - transcribe_start) * 1000),
                "voice_total_ms": total_ms,
            }
            return {"text": result.text.strip(), "timing": timing}

        def on_result(result):
            """处理流式识别结果。"""
            if self._local_voice_stream_stop_flag_path and self._local_voice_stream_stop_flag_path.exists():
                try:
                    cleanup_stop_flag(self._local_voice_stream_stop_flag_path)
                except Exception:
                    pass
            self._local_voice_streaming = False
            self._local_voice_stream_stop_pending = False
            self._local_voice_stream_stop_flag_path = None
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")

            if isinstance(result, Exception):
                message = str(result)
                if "10165" in message or "invalid handle" in message.lower():
                    message = "录音时间过短或讯飞连接句柄未就绪，请稍等到按钮可用后再停止录音。"
                self.status_label.setText("麦克风识别失败")
                self._show_critical("麦克风识别失败", message)
                self._append_log("语音", "麦克风识别", "失败", message)
                return

            text = str(result.get("text", "") if isinstance(result, dict) else result).strip()
            timing = result.get("timing", {}) if isinstance(result, dict) else {}
            self.nlp_input_edit.setPlainText(text)
            self.status_label.setText("麦克风识别完成")
            self._append_log("语音", "识别耗时", "成功", self._format_voice_timing_detail(timing, text=text), extra=timing)
            self._append_log("语音", "麦克风识别", "成功", text or "-")

        self._run_in_background(work, on_result)

    def _stop_local_streaming_recognition(self, *, force: bool = False) -> None:
        """停止本地流式识别。"""
        if getattr(self, "_local_voice_stream_stop_pending", False) and not force:
            return

        elapsed = time.perf_counter() - float(getattr(self, "_local_voice_stream_started_perf", 0.0) or 0.0)
        if not force and elapsed < self._VOICE_STREAM_MIN_RECORD_SEC:
            remaining_ms = int((self._VOICE_STREAM_MIN_RECORD_SEC - elapsed) * 1000)
            self._local_voice_stream_stop_pending = True
            self.mic_toggle_btn.setEnabled(False)
            self.status_label.setText("录音时间过短，正在补足最短录音时长。")
            self._append_log("语音", "停止录音延迟", "警告", f"点击过快，延迟 {remaining_ms}ms 后停止")
            QTimer.singleShot(max(100, remaining_ms), lambda: self._stop_local_streaming_recognition(force=True))
            return

        stop_flag = getattr(self, "_local_voice_stream_stop_flag_path", None)
        if stop_flag:
            write_stop_flag(stop_flag)
        self.mic_toggle_btn.setEnabled(False)
        self.status_label.setText("正在停止实时识别并等待最终文本。")
        self._append_log("语音", "停止录音", "成功", "实时流式停止信号已写入")

    def _ensure_mic_stream(self) -> None:
        """确保麦克风。"""
        if self._mic_recorder_thread is not None:
            return
        try:
            import sounddevice as sd
        except ImportError:
            return

        selected_device = self._selected_microphone_device()
        sample_rate = 16000

        class MicStreamThread(threading.Thread):
            """麦克风流线程，负责持续采集并在停止时回传音频。"""

            def __init__(self, parent_win, sample_rate, device):
                """初始化对象。"""
                super().__init__(daemon=True)
                self._parent_win = parent_win
                self._sample_rate = sample_rate
                self._device = device
                self._capturing = False
                self._shutdown = False
                self._frames = []
                self._stop_requested = False
                self._session_enabled = False
                self._session_paused = False
                self._segment_queue = queue.Queue()
                self._voice_start_queue = queue.Queue()
                self._capture_queue = queue.Queue()
                self._segmenter = VoiceSessionSegmenter(
                    silence_threshold=parent_win._VOICE_SILENCE_THRESHOLD,
                    frame_ms=parent_win._VOICE_FRAME_MS,
                    start_voice_ms=120,
                    end_silence_ms=500,
                    min_segment_ms=300,
                    max_segment_ms=5000,
                )

            def start_capturing(self):
                """启动相关数据。"""
                self._frames = []
                self._capturing = True

            def stop_capturing(self):
                """停止相关数据。"""
                self._capturing = False
                self._stop_requested = True

            def enable_session_mode(self):
                """开启连续会话分段。"""
                self._segmenter.reset()
                self._session_enabled = True

            def disable_session_mode(self):
                """关闭连续会话分段。"""
                self._session_enabled = False
                self._segmenter.reset()

            def reset_session_segmenter(self):
                """重置当前会话分段，丢弃触发打断的播报残音。"""
                self._segmenter.reset()

            def set_session_paused(self, paused: bool):
                """设置会话监听暂停状态。"""
                self._session_paused = bool(paused)

            def pop_audio_segment(self) -> bytes | None:
                """取出会话模式下的一段语音。"""
                try:
                    return self._segment_queue.get_nowait()
                except queue.Empty:
                    return None

            def pop_voice_start(self) -> bool:
                """取出会话模式下的起声事件。"""
                try:
                    self._voice_start_queue.get_nowait()
                    return True
                except queue.Empty:
                    return False

            def pop_audio_capture(self) -> bytes | None:
                """取出手动录音模式下的一段语音。"""
                try:
                    return self._capture_queue.get_nowait()
                except queue.Empty:
                    return None

            def shutdown(self):
                """关闭相关数据。"""
                self._shutdown = True
                self._capturing = False

            def wait(self, timeout_ms: int | None = None):
                """兼容 QThread.wait。"""
                timeout = None if timeout_ms is None else max(0, float(timeout_ms) / 1000.0)
                self.join(timeout=timeout)

            def run(self):
                """运行相关数据。"""
                try:
                    def callback(indata, frames_count, time_info, status):
                        """处理相关数据。"""
                        if self._capturing:
                            self._frames.append(indata.copy())
                        if self._session_enabled:
                            was_active = bool(getattr(self._segmenter, "is_active", False))
                            segment = self._segmenter.feed(indata.tobytes(), paused=self._session_paused)
                            is_active = bool(getattr(self._segmenter, "is_active", False))
                            if is_active and not was_active:
                                self._voice_start_queue.put(True)
                            if segment:
                                self._segment_queue.put(segment)
                        if self._shutdown:
                            raise sd.CallbackStop()

                    with sd.InputStream(
                        samplerate=self._sample_rate,
                        channels=1,
                        dtype='int16',
                        device=self._device,
                        callback=callback,
                    ):
                        while not self._shutdown:
                            time.sleep(0.05)
                            if self._stop_requested:
                                self._stop_requested = False
                                import numpy as np
                                captured = self._frames
                                self._frames = []
                                if captured:
                                    audio = np.concatenate(captured)
                                    self._capture_queue.put(audio.tobytes())
                                else:
                                    self._capture_queue.put(b'')
                except sd.CallbackStop:
                    pass
                except Exception:
                    if not self._shutdown:
                        self._capture_queue.put(b'')

        self._mic_recorder_thread = MicStreamThread(self, sample_rate, selected_device)
        self._mic_recorder_thread.start()
        self._start_voice_session_poll_timer()
        self._append_log("语音", "预热麦克风", "成功", "麦克风流已后台启动")

    def _start_voice_session(self) -> None:
        """开启连续语音会话。"""
        if getattr(self, "_voice_session_active", False):
            return
        try:
            self._create_iflytek_client()
            if getattr(self, "_use_license_voice", False):
                raise RuntimeError("当前授权代理语音模式暂不支持连续会话，请继续使用手动录音。")
            self._ensure_mic_stream()
            thread = getattr(self, "_mic_recorder_thread", None)
            if thread is None:
                raise RuntimeError("无法启动麦克风流，请确认 sounddevice 和麦克风设备可用。")
            if not hasattr(self, "_voice_session_segment_queue"):
                self._voice_session_segment_queue = queue.Queue()
            else:
                self._voice_session_clear_queue()
            self._voice_session_asr_busy = False
            self._voice_session_active = True
            thread.enable_session_mode()
            self._start_voice_session_poll_timer()
            if hasattr(self, "mic_toggle_btn"):
                self.mic_toggle_btn.setText("结束会话")
                self.mic_toggle_btn.setEnabled(True)
            if hasattr(self, "status_label"):
                self.status_label.setText("语音会话已开启，等待说话。")
            self._append_log("语音会话", "开启会话", "成功", "静音分段模式")
        except Exception as exc:
            self._voice_session_active = False
            if hasattr(self, "status_label"):
                self.status_label.setText("语音会话启动失败")
            if hasattr(self, "_show_critical"):
                self._show_critical("语音会话启动失败", str(exc))
            self._append_log("语音会话", "开启会话", "失败", str(exc))

    def _stop_voice_session(self) -> None:
        """关闭连续语音会话。"""
        self._stop_voice_session_poll_timer()
        thread = getattr(self, "_mic_recorder_thread", None)
        if thread is not None and hasattr(thread, "disable_session_mode"):
            thread.disable_session_mode()
        self._voice_session_active = False
        self._voice_session_asr_busy = False
        self._voice_session_clear_queue()
        if hasattr(self, "mic_toggle_btn"):
            self.mic_toggle_btn.setText("开启会话")
            self.mic_toggle_btn.setEnabled(True)
        if hasattr(self, "status_label"):
            self.status_label.setText("语音会话已关闭。")
        self._append_log("语音会话", "关闭会话", "成功", "已停止连续监听")

    def _start_voice_session_poll_timer(self) -> None:
        """启动主线程轮询语音分段，避免音频线程直接触碰 Qt。"""
        timer = getattr(self, "_voice_session_poll_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(80)
            timer.timeout.connect(self._poll_voice_session_segments)
            self._voice_session_poll_timer = timer
        if not timer.isActive():
            timer.start()

    def _stop_voice_session_poll_timer(self) -> None:
        timer = getattr(self, "_voice_session_poll_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _poll_voice_session_segments(self) -> None:
        thread = getattr(self, "_mic_recorder_thread", None)
        if thread is None:
            return
        if hasattr(thread, "pop_audio_capture"):
            capture = thread.pop_audio_capture()
            if capture is not None:
                self._on_mic_audio_captured(capture)
        if getattr(self, "_voice_session_active", False) and hasattr(thread, "pop_audio_segment"):
            ignore_audio = self._voice_session_should_ignore_audio()
            if hasattr(thread, "set_session_paused"):
                thread.set_session_paused(ignore_audio)
            if ignore_audio:
                self._voice_session_clear_queue()
                for _ in range(10):
                    if not thread.pop_audio_segment():
                        break
                return
            if hasattr(thread, "pop_voice_start") and thread.pop_voice_start():
                interrupter = getattr(self, "_operator_interrupt_current_speech_for_user_input", None)
                if callable(interrupter):
                    interrupter()
                reset_segmenter = getattr(thread, "reset_session_segmenter", None)
                if callable(reset_segmenter):
                    reset_segmenter()
            for _ in range(3):
                segment = thread.pop_audio_segment()
                if not segment:
                    break
                self._on_mic_audio_segment(segment)

    def _voice_session_should_ignore_audio(self) -> bool:
        """AI 正在生成文本时忽略麦克风帧；TTS 播报不阻塞下一轮输入。"""
        if bool(getattr(self, "nlp_parse_running", False)):
            return True
        if bool(getattr(self, "nlp_sequence_running", False)):
            return True
        if bool(getattr(self, "_operator_streaming_chat_active", False)):
            return True
        return False

    def _voice_session_clear_queue(self) -> None:
        q = getattr(self, "_voice_session_segment_queue", None)
        if q is None:
            return
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            return

    def _on_mic_audio_segment(self, pcm_data: bytes) -> None:
        """会话模式下收到一句语音段。"""
        if not getattr(self, "_voice_session_active", False):
            return
        if self._voice_session_should_ignore_audio():
            self._voice_session_clear_queue()
            return
        if not pcm_data:
            return
        interrupter = getattr(self, "_operator_interrupt_current_speech_for_user_input", None)
        if callable(interrupter):
            interrupter()
        q = getattr(self, "_voice_session_segment_queue", None)
        if q is None:
            q = queue.Queue()
            self._voice_session_segment_queue = q
        q.put(bytes(pcm_data))
        self._voice_session_process_next_segment()

    def _voice_session_process_next_segment(self) -> None:
        if not getattr(self, "_voice_session_active", False):
            return
        if self._voice_session_should_ignore_audio():
            self._voice_session_clear_queue()
            return
        if getattr(self, "_voice_session_asr_busy", False):
            return
        q = getattr(self, "_voice_session_segment_queue", None)
        if q is None:
            return
        try:
            segment = q.get_nowait()
        except queue.Empty:
            return
        self._voice_session_asr_busy = True
        if hasattr(self, "status_label"):
            self.status_label.setText("语音会话正在识别...")
        begin_status = getattr(self, "_operator_begin_voice_recognition_status", None)
        if callable(begin_status):
            begin_status()

        def work():
            return self._transcribe_pcm_via_local_client(
                segment,
                partial_callback=self._voice_session_schedule_partial_text,
            )

        def on_result(result):
            self._voice_session_asr_busy = False
            if isinstance(result, Exception):
                message = str(result)
                if hasattr(self, "status_label"):
                    self.status_label.setText("语音会话识别失败")
                clear_status = getattr(self, "_operator_clear_voice_recognition_status", None)
                if callable(clear_status):
                    clear_status()
                self._append_log("语音会话", "分段识别", "失败", message)
                self._voice_session_process_next_segment()
                return
            text = str(result.get("text", "") if isinstance(result, dict) else result).strip()
            timing = result.get("timing", {}) if isinstance(result, dict) else {}
            if text:
                if hasattr(self, "status_label"):
                    self.status_label.setText("语音会话识别完成，正在处理。")
                self._append_log("语音会话", "分段识别", "成功", self._format_voice_timing_detail(timing, text=text), extra=timing)
                handler = getattr(self, "_operator_handle_voice_session_text", None)
                if callable(handler):
                    finish_status = getattr(self, "_operator_finish_voice_recognition_status", None)
                    if callable(finish_status):
                        finish_status(text)
                    handler(text)
                elif hasattr(self, "nlp_input_edit"):
                    self.nlp_input_edit.setPlainText(text)
            else:
                clear_status = getattr(self, "_operator_clear_voice_recognition_status", None)
                if callable(clear_status):
                    clear_status()
                self._append_log("语音会话", "分段识别", "提示", "空文本")
            if getattr(self, "_voice_session_active", False) and hasattr(self, "status_label"):
                self.status_label.setText("语音会话等待说话。")
            self._voice_session_process_next_segment()

        self._run_in_background(work, on_result)

    def _voice_session_schedule_partial_text(self, text: str) -> None:
        """从识别线程安全地调度分段识别文本到 Qt 主线程。"""
        partial = str(text or "").strip()
        if not partial:
            return
        runner = getattr(self, "_run_on_main_thread", None)
        if callable(runner):
            runner(lambda partial=partial: self._voice_session_update_partial_text(partial))
        else:
            self._voice_session_update_partial_text(partial)

    def _voice_session_update_partial_text(self, text: str) -> None:
        partial = str(text or "").strip()
        if not partial:
            return
        update_status = getattr(self, "_operator_update_voice_recognition_status", None)
        if callable(update_status):
            update_status(partial)
        if hasattr(self, "operator_command_edit"):
            has_focus = False
            try:
                has_focus = bool(self.operator_command_edit.hasFocus())
            except Exception:
                has_focus = False
            if not has_focus:
                self.operator_command_edit.setText(partial)
        elif hasattr(self, "nlp_input_edit"):
            self.nlp_input_edit.setPlainText(partial)
        if hasattr(self, "status_label"):
            self.status_label.setText("语音会话正在识别文本...")

    def _on_mic_audio_captured(self, pcm_data: bytes) -> None:
        """处理麦克风音频。"""
        self._proxy_mic_capturing = False

        if not pcm_data:
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            self._show_critical("麦克风识别失败", "未录到音频")
            self._append_log("语音", "麦克风识别", "失败", "未录到音频")
            return

        if self._use_license_voice:
            self._recognize_via_proxy(pcm_data)
        else:
            self._recognize_via_local(pcm_data)

    def _recognize_via_proxy(self, pcm_data: bytes) -> None:
        """识别代理。"""
        import base64
        import requests as _requests

        self.status_label.setText("正在上传语音识别...")
        self.mic_toggle_btn.setEnabled(False)
        self.mic_toggle_btn.setText("识别中...")

        def work():
            """处理相关数据。"""
            total_start = time.perf_counter()
            trim_start = time.perf_counter()
            trimmed_pcm, timing = self._trim_pcm_silence(pcm_data)
            timing["voice_trim_ms"] = int((time.perf_counter() - trim_start) * 1000)
            audio_b64 = base64.b64encode(trimmed_pcm).decode()
            token = self.license_manager.get_access_token()
            if not token:
                raise RuntimeError("授权已过期")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
            payload = {
                "audio_data": audio_b64,
                "audio_format": "pcm",
                "sample_rate": 16000,
            }
            proxy_url = f"{self.license_manager.SERVER_URL}/api/v1/proxy/voice/transcribe"
            request_start = time.perf_counter()
            resp = _requests.post(proxy_url, headers=headers, json=payload, timeout=60)
            timing["voice_transcribe_ms"] = int((time.perf_counter() - request_start) * 1000)
            if resp.status_code == 401:
                raise RuntimeError("授权已过期")
            elif resp.status_code == 429:
                raise RuntimeError("今日语音配额已用尽")
            resp.raise_for_status()
            timing["voice_total_ms"] = int((time.perf_counter() - total_start) * 1000)
            return {"text": resp.json().get("data", {}).get("text", "").strip(), "timing": timing}

        def on_result(result):
            """处理结果。"""
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            if isinstance(result, Exception):
                self._show_critical("麦克风识别失败", str(result))
                self._append_log("语音", "麦克风识别", "失败", str(result))
            else:
                text = str(result.get("text", "") if isinstance(result, dict) else result).strip()
                timing = result.get("timing", {}) if isinstance(result, dict) else {}
                self.nlp_input_edit.setPlainText(text)
                self.status_label.setText("麦克风识别完成")
                self._append_log("语音", "识别耗时", "成功", self._format_voice_timing_detail(timing, text=text), extra=timing)
                self._append_log("语音", "麦克风识别", "成功", text or "-")

        self._run_in_background(work, on_result)

    def _recognize_via_local(self, pcm_data: bytes) -> None:
        """识别本地。"""

        self.status_label.setText("正在识别语音...")
        self.mic_toggle_btn.setEnabled(False)
        self.mic_toggle_btn.setText("识别中...")

        def work():
            """处理相关数据。"""
            return self._transcribe_pcm_via_local_client(pcm_data)

        def on_result(result):
            """处理结果。"""
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            if isinstance(result, Exception):
                self._show_critical("麦克风识别失败", str(result))
                self._append_log("语音", "麦克风识别", "失败", str(result))
            else:
                text = str(result.get("text", "") if isinstance(result, dict) else result).strip()
                timing = result.get("timing", {}) if isinstance(result, dict) else {}
                self.nlp_input_edit.setPlainText(text)
                self.status_label.setText("麦克风识别完成")
                self._append_log("语音", "识别耗时", "成功", self._format_voice_timing_detail(timing, text=text), extra=timing)
                self._append_log("语音", "麦克风识别", "成功", text or "-")

        self._run_in_background(work, on_result)

    def _start_subprocess_recording(self) -> None:
        """启动相关数据。"""
        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        worker_files = make_voice_worker_files(log_dir, timestamp)
        # 界面进程与语音子进程之间不用管道传停止命令，而是共享一个停止标记文件。
        # 这个约定必须和语音子进程及识别客户端保持一致，打包后也依赖它。
        reset_stop_flag(worker_files.stop_flag)
        selected_device = self._selected_microphone_device()
        mic_args = ["--mode", "mic", "--duration", "3600", "--stop-flag-path", str(worker_files.stop_flag)]
        if selected_device is not None:
            mic_args.extend(["--device", str(selected_device)])
        cmd = self._build_iflytek_worker_command(
            mic_args,
            worker_files.debug_pcm,
            worker_files.result_json,
        )
        self._mic_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(self.runtime_root),
        )
        self._mic_stop_flag_path = worker_files.stop_flag
        self._mic_result_path = worker_files.result_json
        self.mic_toggle_btn.setText("停止录音")

        self.status_label.setText("麦克风初始化中，请稍候...")
        detail = "麦克风录音已启动（系统默认）" if selected_device is None else f"麦克风录音已启动（设备 {selected_device}）"
        self._append_log("语音", "开始录音", "成功", detail)

        # 延迟 1.5 秒后提示可以说话
        self._mic_ready_timer = QTimer(self)
        self._mic_ready_timer.setSingleShot(True)
        self._mic_ready_timer.timeout.connect(self._on_mic_ready)
        self._mic_ready_timer.start(1500)

        if self._mic_poll_timer is None:
            self._mic_poll_timer = QTimer(self)
            self._mic_poll_timer.setInterval(300)
            self._mic_poll_timer.timeout.connect(self._poll_microphone_recording)
        self._mic_poll_timer.start()

    def _on_mic_ready(self) -> None:
        """处理麦克风就绪。"""

        self.status_label.setText("麦克风录音中，请说话... 点击'停止录音'结束。")

    def _stop_microphone_recording(self) -> None:
        """停止麦克风。"""
        # 代理模式：停止采集并上传
        if self._proxy_mic_capturing and self._mic_recorder_thread is not None:
            self._mic_recorder_thread.stop_capturing()
            self._proxy_mic_capturing = False
            self.mic_toggle_btn.setEnabled(False)
            self.status_label.setText("正在停止录音并等待识别结果。")
            self._append_log("语音", "停止录音", "成功", "已发送停止信号")
            return
        # 直连模式：写停止标记给子进程
        if not self._mic_process or self._mic_process.poll() is not None:
            return
        if self._mic_stop_flag_path:
            write_stop_flag(self._mic_stop_flag_path)
        self.mic_toggle_btn.setEnabled(False)
        self.status_label.setText("正在停止录音并等待识别结果。")
        self._append_log("语音", "停止录音", "成功", "已发送停止信号")

    def _poll_microphone_recording(self) -> None:
        """轮询麦克风。"""
        if not self._mic_process:
            if self._mic_poll_timer:
                self._mic_poll_timer.stop()
            return
        exit_code = self._mic_process.poll()
        if exit_code is None:
            return
        if self._mic_poll_timer:
            self._mic_poll_timer.stop()
        stdout, stderr = self._mic_process.communicate()
        self._mic_process = None
        # 子进程输出和结果文件分开保存：标准输出和错误输出用于排查厂商库与麦克风，
        # 结果路径只承载界面需要展示和执行的识别文本。
        log_dir = self.runtime_root / "data" / "exported_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        worker_log = log_dir / f"iflytek_worker_mic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        worker_log.write_text(
            json.dumps(
                {
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "stop_flag": str(self._mic_stop_flag_path) if self._mic_stop_flag_path else "",
                    "returncode": exit_code,
                    "result_path": str(self._mic_result_path) if getattr(self, "_mic_result_path", None) else "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if self._mic_stop_flag_path and self._mic_stop_flag_path.exists():
            try:
                cleanup_stop_flag(self._mic_stop_flag_path)
            except Exception:
                pass
        self._mic_stop_flag_path = None
        self.mic_toggle_btn.setEnabled(True)
        self.mic_toggle_btn.setText("开始录音")
        result_path = getattr(self, "_mic_result_path", None)
        self._mic_result_path = None
        if not result_path or not Path(result_path).exists():
            error_text = (stderr or "").strip() or "麦克风识别未返回结果。"
            error_text = f"{error_text}\n调试日志: {worker_log}"

            self._show_critical("麦克风识别失败", error_text)
            self._append_log("语音", "麦克风识别", "失败", error_text)
            return
        try:
            payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            message = f"麦克风识别结果文件不是合法 JSON。\n调试日志: {worker_log}"

            self._show_critical("麦克风识别失败", message)
            self._append_log("语音", "麦克风识别", "失败", message)
            return
        if not payload.get("ok"):
            message = f"{payload.get('error', '麦克风识别失败。')}\n调试日志: {worker_log}"

            self._show_critical("麦克风识别失败", message)
            self._append_log("语音", "麦克风识别", "失败", message)
            return

        text = str(payload.get("text", "")).strip()
        self.nlp_input_edit.setPlainText(text)

        self.status_label.setText("麦克风识别完成")
        self._append_log("语音", "麦克风识别", "成功", text or "-")
