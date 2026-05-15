"""语音识别配置、录音、子进程通信和语音指令执行逻辑。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Signal

from .voice_ipc import cleanup_stop_flag, make_voice_worker_files, reset_stop_flag, write_stop_flag


class VoiceMixin:
    """为主窗口增加语音识别和语音指令执行能力。"""

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
            self._create_iflytek_client()

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

    def _ensure_mic_stream(self) -> None:
        """确保麦克风。"""
        if self._mic_recorder_thread is not None:
            return
        try:
            import sounddevice as sd
        except ImportError:
            return

        from PySide6.QtCore import QThread as _QThread

        selected_device = self._selected_microphone_device()
        sample_rate = 16000

        class MicStreamThread(_QThread):
            """麦克风流线程，负责持续采集并在停止时回传音频。"""
            audio_captured = Signal(bytes)  # 原始音频数据

            def __init__(self, parent_win, sample_rate, device):
                """初始化对象。"""
                super().__init__(parent_win)
                self._sample_rate = sample_rate
                self._device = device
                self._capturing = False
                self._shutdown = False
                self._frames = []
                self._stop_requested = False

            def start_capturing(self):
                """启动相关数据。"""
                self._frames = []
                self._capturing = True

            def stop_capturing(self):
                """停止相关数据。"""
                self._capturing = False
                self._stop_requested = True

            def shutdown(self):
                """关闭相关数据。"""
                self._shutdown = True
                self._capturing = False

            def run(self):
                """运行相关数据。"""
                try:
                    def callback(indata, frames_count, time_info, status):
                        """处理相关数据。"""
                        if self._capturing:
                            self._frames.append(indata.copy())
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
                                    self.audio_captured.emit(audio.tobytes())
                                else:
                                    self.audio_captured.emit(b'')
                except sd.CallbackStop:
                    pass
                except Exception:
                    if not self._shutdown:
                        self.audio_captured.emit(b'')

        self._mic_recorder_thread = MicStreamThread(self, sample_rate, selected_device)
        self._mic_recorder_thread.audio_captured.connect(self._on_mic_audio_captured)
        self._mic_recorder_thread.start()
        self._append_log("语音", "预热麦克风", "成功", "麦克风流已后台启动")

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
            audio_b64 = base64.b64encode(pcm_data).decode()
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
            resp = _requests.post(proxy_url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 401:
                raise RuntimeError("授权已过期")
            elif resp.status_code == 429:
                raise RuntimeError("今日语音配额已用尽")
            resp.raise_for_status()
            return resp.json().get("data", {}).get("text", "").strip()

        def on_result(result):
            """处理结果。"""
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            if isinstance(result, Exception):
                self._show_critical("麦克风识别失败", str(result))
                self._append_log("语音", "麦克风识别", "失败", str(result))
            else:
                self.nlp_input_edit.setPlainText(result)
                self.status_label.setText("麦克风识别完成")
                self._append_log("语音", "麦克风识别", "成功", result or "-")

        self._run_in_background(work, on_result)

    def _recognize_via_local(self, pcm_data: bytes) -> None:
        """识别本地。"""
        import tempfile

        self.status_label.setText("正在识别语音...")
        self.mic_toggle_btn.setEnabled(False)
        self.mic_toggle_btn.setText("识别中...")

        def work():
            """处理相关数据。"""
            tmp = tempfile.NamedTemporaryFile(suffix='.pcm', delete=False)
            tmp.write(pcm_data)
            tmp_name = tmp.name
            tmp.close()
            try:
                return self._run_iflytek_worker(["--mode", "audio", "--input", tmp_name])
            finally:
                Path(tmp_name).unlink(missing_ok=True)

        def on_result(result):
            """处理结果。"""
            self.mic_toggle_btn.setEnabled(True)
            self.mic_toggle_btn.setText("开始录音")
            if isinstance(result, Exception):
                self._show_critical("麦克风识别失败", str(result))
                self._append_log("语音", "麦克风识别", "失败", str(result))
            else:
                self.nlp_input_edit.setPlainText(result)
                self.status_label.setText("麦克风识别完成")
                self._append_log("语音", "麦克风识别", "成功", result or "-")

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

