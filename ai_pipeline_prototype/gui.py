from __future__ import annotations

import argparse
import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ai_pipeline_prototype.app_service import PipelineAppService


DEFAULT_VOICE = "抓取左边托盘里的工件放到右边工位"


class PipelineAppUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AI Mechanical Arm Prototype")
        self.root.geometry("1200x760")

        self.service = PipelineAppService()

        self.voice_var = tk.StringVar(value=DEFAULT_VOICE)
        self.target_found_var = tk.BooleanVar(value=True)
        self.target_id_var = tk.StringVar(value="part_01")
        self.pos_x_var = tk.StringVar(value="120.5")
        self.pos_y_var = tk.StringVar(value="230.0")
        self.angle_var = tk.StringVar(value="35.2")
        self.confidence_var = tk.StringVar(value="0.94")
        self.safe_region_var = tk.BooleanVar(value=True)
        self.connection_var = tk.StringVar(value="")
        self.mic_seconds_var = tk.StringVar(value="4")
        self.mic_device_var = tk.StringVar(value="1")
        self.mic_backend_var = tk.StringVar(value="sounddevice")
        self.iflytek_text_var = tk.StringVar(value="")
        self.iat_status_var = tk.StringVar(value="空闲")
        self._iat_busy = False

        self._build_layout()
        self._render_snapshot(self.service.get_snapshot())

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)

        title = ttk.Label(
            self.root,
            text="语音 / 视觉 -> 任务 JSON -> 调度器 -> 控制器服务",
            font=("Helvetica", 18, "bold"),
        )
        title.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(16, 8))

        left = ttk.LabelFrame(self.root, text="输入与控制", padding=16)
        right = ttk.LabelFrame(self.root, text="结果与状态", padding=16)
        left.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(0, 16))
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(0, 16))

        left.columnconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        top_bar = ttk.Frame(right)
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top_bar.columnconfigure(0, weight=1)
        top_bar.columnconfigure(1, weight=0)
        top_bar.columnconfigure(2, weight=0)

        self.status_lights = ttk.Label(top_bar, text="", font=("Helvetica", 12, "bold"))
        self.status_lights.grid(row=0, column=0, sticky="w")

        ttk.Button(top_bar, text="连接控制器", command=self.on_connect).grid(row=0, column=1, padx=(8, 6))
        ttk.Button(top_bar, text="断开控制器", command=self.on_disconnect).grid(row=0, column=2)

        ttk.Label(left, text="语音文本").grid(row=0, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.voice_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Checkbutton(left, text="视觉发现目标", variable=self.target_found_var).grid(
            row=1, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(left, text="安全区域通过", variable=self.safe_region_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(left, text="目标 ID").grid(row=3, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.target_id_var).grid(row=3, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(left, text="X").grid(row=4, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.pos_x_var).grid(row=4, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(left, text="Y").grid(row=5, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.pos_y_var).grid(row=5, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(left, text="角度").grid(row=6, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.angle_var).grid(row=6, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(left, text="置信度").grid(row=7, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.confidence_var).grid(row=7, column=1, sticky="ew", pady=(0, 12))

        speech_frame = ttk.LabelFrame(left, text="讯飞 IAT", padding=12)
        speech_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        speech_frame.columnconfigure(1, weight=1)
        speech_frame.columnconfigure(3, weight=1)

        ttk.Label(speech_frame, text="录音秒数").grid(row=0, column=0, sticky="w")
        ttk.Entry(speech_frame, textvariable=self.mic_seconds_var, width=8).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(speech_frame, text="设备号").grid(row=0, column=2, sticky="w")
        ttk.Entry(speech_frame, textvariable=self.mic_device_var, width=8).grid(row=0, column=3, sticky="ew")

        ttk.Label(speech_frame, text="后端").grid(row=1, column=0, sticky="w", pady=(8, 0))
        backend_box = ttk.Combobox(
            speech_frame,
            textvariable=self.mic_backend_var,
            values=["sounddevice", "pyaudio"],
            state="readonly",
        )
        backend_box.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))

        self.iflytek_mic_button = ttk.Button(speech_frame, text="麦克风识别", command=self.on_iflytek_mic)
        self.iflytek_mic_button.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(8, 0))
        self.iflytek_audio_button = ttk.Button(speech_frame, text="导入音频识别", command=self.on_iflytek_audio_file)
        self.iflytek_audio_button.grid(row=1, column=3, sticky="ew", pady=(8, 0))
        self.iflytek_list_button = ttk.Button(speech_frame, text="列设备", command=self.on_list_mics)
        self.iflytek_list_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.iflytek_use_text_button = ttk.Button(speech_frame, text="采用识别文本", command=self.on_use_iflytek_text)
        self.iflytek_use_text_button.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Label(speech_frame, text="当前状态").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(speech_frame, textvariable=self.iat_status_var).grid(
            row=3, column=1, columnspan=3, sticky="w", pady=(8, 0)
        )

        ttk.Label(speech_frame, text="识别结果").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(speech_frame, textvariable=self.iflytek_text_var).grid(
            row=4, column=1, columnspan=3, sticky="ew", pady=(8, 0)
        )

        button_bar = ttk.Frame(left)
        button_bar.grid(row=9, column=0, columnspan=2, sticky="ew")
        button_bar.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(button_bar, text="提交任务", command=self.on_submit).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(button_bar, text="注入报警", command=self.on_inject_alarm).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(button_bar, text="清除报警", command=self.on_clear_alarm).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        quick_bar = ttk.Frame(left)
        quick_bar.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        quick_bar.columnconfigure((0, 1, 2), weight=1)
        ttk.Button(quick_bar, text="抓取放置示例", command=self.use_pick_example).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(quick_bar, text="回零示例", command=self.use_home_example).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(quick_bar, text="停止示例", command=self.use_stop_example).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        status_frame = ttk.LabelFrame(right, text="控制器状态", padding=12)
        status_frame.grid(row=1, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)

        self.status_text = tk.Text(status_frame, height=10, wrap="word")
        self.status_text.grid(row=0, column=0, sticky="nsew")

        notebook = ttk.Notebook(right)
        notebook.grid(row=2, column=0, sticky="nsew", pady=(12, 0))

        self.result_text = self._build_text_tab(notebook, "结果")
        self.alarm_text = self._build_text_tab(notebook, "报警")
        self.command_text = self._build_text_tab(notebook, "命令历史")
        self.task_text = self._build_text_tab(notebook, "任务历史")

    def _build_text_tab(self, notebook: ttk.Notebook, title: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, wrap="word")
        text.grid(row=0, column=0, sticky="nsew")
        notebook.add(frame, text=title)
        return text

    def on_submit(self) -> None:
        payload = self.service.submit(
            voice_text=self.voice_var.get().strip(),
            target_found=self.target_found_var.get(),
            target_id=self.target_id_var.get().strip() or None,
            position=self._build_position(),
            angle=self._parse_float(self.angle_var.get()),
            confidence=self._parse_float(self.confidence_var.get(), default=0.0),
            safe_region_ok=self.safe_region_var.get(),
        )
        self._render_result(payload)

    def on_inject_alarm(self) -> None:
        alarm = self.service.inject_alarm("UI001", "界面手动注入报警", level="warning")
        snapshot = self.service.get_snapshot()
        self._render_snapshot(snapshot)
        self._set_text(self.result_text, json.dumps({"alarm": alarm}, ensure_ascii=False, indent=2))

    def on_clear_alarm(self) -> None:
        payload = self.service.clear_alarms()
        self._render_snapshot(payload)
        self._set_text(self.result_text, json.dumps(payload, ensure_ascii=False, indent=2))

    def on_connect(self) -> None:
        snapshot = self.service.connect_controller()
        self._render_snapshot(snapshot)
        self._set_text(self.result_text, json.dumps({"action": "connect_controller"}, ensure_ascii=False, indent=2))

    def on_disconnect(self) -> None:
        snapshot = self.service.disconnect_controller()
        self._render_snapshot(snapshot)
        self._set_text(self.result_text, json.dumps({"action": "disconnect_controller"}, ensure_ascii=False, indent=2))

    def on_iflytek_audio_file(self) -> None:
        if self._iat_busy:
            return
        file_path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("PCM Audio", "*.pcm"), ("All Files", "*.*")],
        )
        if not file_path:
            return
        self._run_iat_task(
            status_text="识别音频文件中...",
            worker=lambda: self.service.transcribe_iflytek_audio(file_path),
        )

    def on_iflytek_mic(self) -> None:
        if self._iat_busy:
            return
        duration = self._parse_float(self.mic_seconds_var.get(), default=4.0) or 4.0
        device = self._parse_int(self.mic_device_var.get())
        backend = self.mic_backend_var.get().strip() or None
        self._run_iat_task(
            status_text="录音并识别中...",
            worker=lambda: self.service.transcribe_iflytek_microphone(
                duration_sec=duration,
                device=device,
                backend=backend,
            ),
        )

    def on_list_mics(self) -> None:
        if self._iat_busy:
            return
        backend = self.mic_backend_var.get().strip() or None
        self._run_iat_task(
            status_text="读取麦克风设备中...",
            worker=lambda: self.service.list_iflytek_microphones(backend=backend),
            success_handler=self._handle_device_payload,
        )

    def on_use_iflytek_text(self) -> None:
        recognized = self.iflytek_text_var.get().strip()
        if not recognized:
            messagebox.showwarning("没有识别结果", "请先进行一次音频或麦克风识别。")
            return
        self.voice_var.set(recognized)

    def use_pick_example(self) -> None:
        self.voice_var.set(DEFAULT_VOICE)
        self.target_found_var.set(True)
        self.safe_region_var.set(True)
        self.iflytek_text_var.set("")

    def use_home_example(self) -> None:
        self.voice_var.set("机械手回零")
        self.iflytek_text_var.set("")

    def use_stop_example(self) -> None:
        self.voice_var.set("停止作业")
        self.iflytek_text_var.set("")

    def _build_position(self) -> list[float] | None:
        x = self._parse_float(self.pos_x_var.get())
        y = self._parse_float(self.pos_y_var.get())
        if x is None or y is None:
            return None
        return [x, y]

    def _parse_float(self, value: str, default: float | None = None) -> float | None:
        try:
            return float(value)
        except ValueError:
            return default

    def _parse_int(self, value: str) -> int | None:
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return int(normalized)
        except ValueError:
            return None

    def _handle_iflytek_payload(self, payload: dict) -> None:
        if payload.get("ok"):
            recognized = str(payload.get("text", "")).strip()
            self.iflytek_text_var.set(recognized)
            if recognized:
                self.voice_var.set(recognized)
        else:
            self.iflytek_text_var.set("")
            messagebox.showerror("讯飞识别失败", payload.get("error", "未知错误"))
        self._render_snapshot(self.service.get_snapshot())
        self._set_text(self.result_text, json.dumps(payload, ensure_ascii=False, indent=2))

    def _handle_device_payload(self, payload: dict) -> None:
        if not payload.get("ok"):
            messagebox.showerror("列出设备失败", payload.get("error", "未知错误"))
        self._render_snapshot(self.service.get_snapshot())
        self._set_text(self.result_text, json.dumps(payload, ensure_ascii=False, indent=2))

    def _run_iat_task(self, *, status_text: str, worker, success_handler=None) -> None:
        self._set_iat_busy(True, status_text)

        def job() -> None:
            payload = worker()
            self.root.after(0, lambda: self._finish_iat_task(payload, success_handler))

        threading.Thread(target=job, daemon=True).start()

    def _finish_iat_task(self, payload: dict, success_handler=None) -> None:
        self._set_iat_busy(False, "空闲")
        if success_handler is not None:
            success_handler(payload)
            return
        self._handle_iflytek_payload(payload)

    def _set_iat_busy(self, busy: bool, status_text: str) -> None:
        self._iat_busy = busy
        self.iat_status_var.set(status_text)
        state = "disabled" if busy else "normal"
        for widget in (
            self.iflytek_mic_button,
            self.iflytek_audio_button,
            self.iflytek_list_button,
            self.iflytek_use_text_button,
        ):
            widget.configure(state=state)

    def _render_result(self, payload: dict) -> None:
        status_payload = {
            "status": payload.get("status"),
            "alarms": payload.get("alarms") or ([payload["alarm"]] if payload.get("alarm") else []),
            "command_history": payload.get("command_history", self.service.get_snapshot()["command_history"]),
            "task_history": self.service.get_snapshot()["task_history"],
        }
        self._render_snapshot(status_payload)
        self._set_text(self.result_text, json.dumps(payload, ensure_ascii=False, indent=2))

    def _render_snapshot(self, snapshot: dict) -> None:
        status = snapshot.get("status", {})
        self._set_status_lights(status)
        self._set_text(self.status_text, json.dumps(status, ensure_ascii=False, indent=2))
        self._set_text(self.alarm_text, json.dumps(snapshot.get("alarms", []), ensure_ascii=False, indent=2))
        self._set_text(self.command_text, json.dumps(snapshot.get("command_history", []), ensure_ascii=False, indent=2))
        self._set_text(self.task_text, json.dumps(snapshot.get("task_history", []), ensure_ascii=False, indent=2))

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)

    def _set_status_lights(self, status: dict) -> None:
        connected = "CONNECTED" if status.get("connected") else "DISCONNECTED"
        servo = "SERVO ON" if status.get("servo_enabled") else "SERVO OFF"
        alarm = "ALARM" if status.get("alarm_active") else "NORMAL"
        estop = "E-STOP" if status.get("emergency_stopped") else "READY"
        self.status_lights.configure(text=f"{connected} | {servo} | {alarm} | {estop}")


def smoke_test() -> None:
    service = PipelineAppService()
    payload = service.submit(
        voice_text=DEFAULT_VOICE,
        target_found=True,
        target_id="part_01",
        position=[120.5, 230.0],
        angle=35.2,
        confidence=0.94,
        safe_region_ok=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="GUI for AI pipeline prototype")
    parser.add_argument("--smoke-test", action="store_true", help="run a headless service check instead of opening the GUI")
    args = parser.parse_args()

    if args.smoke_test:
        smoke_test()
        return

    root = tk.Tk()
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    PipelineAppUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
