from __future__ import annotations

import argparse
import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ai_pipeline_prototype.app_service import PipelineAppService
from ai_pipeline_prototype.natural_language_processor import NaturalLanguageProcessor
from ai_pipeline_prototype.sdk_adapter import MotionSDKConfig


DEFAULT_VOICE = "抓取左边托盘里的工件放到右边工位"


class PipelineAppUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AI Mechanical Arm Prototype")
        self.root.geometry("1400x900")
        self.root.minsize(1200, 700)

        # 首先初始化所有变量
        self.voice_var = tk.StringVar(value=DEFAULT_VOICE)
        self.target_found_var = tk.BooleanVar(value=True)
        self.target_id_var = tk.StringVar(value="part_01")
        self.pos_x_var = tk.StringVar(value="120.5")
        self.pos_y_var = tk.StringVar(value="230.0")
        self.angle_var = tk.StringVar(value="35.2")
        self.confidence_var = tk.StringVar(value="0.94")
        self.safe_region_var = tk.BooleanVar(value=True)
        self.mic_seconds_var = tk.StringVar(value="4")
        self.mic_device_var = tk.StringVar(value="1")
        self.mic_backend_var = tk.StringVar(value="sounddevice")
        self.iflytek_text_var = tk.StringVar(value="")
        self.iat_status_var = tk.StringVar(value="空闲")
        self.auto_fill_voice_var = tk.BooleanVar(value=True)
        self.connected_var = tk.StringVar(value="未连接")
        self.servo_var = tk.StringVar(value="伺服关闭")
        self.alarm_var = tk.StringVar(value="无报警")
        self.estop_var = tk.StringVar(value="可运行")
        self.pose_var = tk.StringVar(value="0.0, 0.0, 0.0")
        self.last_command_var = tk.StringVar(value="无")
        self.result_summary_var = tk.StringVar(value="等待输入任务或语音识别。")
        self._iat_busy = False
        
        # 自然语言处理器
        self.use_deepseek_var = tk.BooleanVar(value=True)
        self.voice_auto_execute_var = tk.BooleanVar(value=True)
        self.nlp_input_var = tk.StringVar(value="移动到第一个位置")
        self.json_input_var = tk.StringVar(value='{"command": "MOVE", "parameters": {"target": "POSITION_1", "speed": 50}, "timestamp": "2026-04-02T12:00:00Z"}')
        
        # 控制器配置
        self.force_mock_var = tk.BooleanVar(value=False)
        self.controller_host_var = tk.StringVar(value="127.0.0.1")
        self.controller_port_var = tk.StringVar(value="5000")
        self.connection_type_var = tk.StringVar(value="eth")
        self.com_port_var = tk.StringVar(value="0")
        self.pci_card_var = tk.StringVar(value="0")
        self.axes_var = tk.StringVar(value="0,1,2")
        self.homing_mode_var = tk.StringVar(value="0")
        self.stop_mode_var = tk.StringVar(value="3")
        
        # 初始化服务，处理控制器连接失败的情况
        try:
            # 首先尝试使用用户配置（默认 force_mock=False）
            self._reinitialize_service()
        except Exception as e:
            # 如果失败，使用模拟模式
            from ai_pipeline_prototype.sdk_adapter import MotionSDKConfig
            config = MotionSDKConfig(force_mock=True)
            self.service = PipelineAppService(config)
            print(f"控制器初始化失败，使用模拟模式: {str(e)}")

        self._configure_styles()
        self._build_layout()
        self._render_snapshot(self.service.get_snapshot())

    def _configure_styles(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 9), foreground="#666666")
        style.configure("StatusConnected.TLabel", font=("Microsoft YaHei UI", 11, "bold"), foreground="#28a745")
        style.configure("StatusDisconnected.TLabel", font=("Microsoft YaHei UI", 11, "bold"), foreground="#dc3545")
        style.configure("StatusOK.TLabel", font=("Microsoft YaHei UI", 10), foreground="#28a745")
        style.configure("StatusWarn.TLabel", font=("Microsoft YaHei UI", 10), foreground="#ffc107")
        style.configure("StatusError.TLabel", font=("Microsoft YaHei UI", 10), foreground="#dc3545")
        style.configure("Card.TLabelframe", padding=10)
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Primary.TButton", padding=(16, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Secondary.TButton", padding=(12, 6))
        style.configure("Small.TButton", padding=(8, 4))
    
    def _reinitialize_service(self) -> None:
        """重新初始化服务，应用新的控制器配置"""
        try:
            axes_str = self.axes_var.get().strip()
            axes = tuple(int(x.strip()) for x in axes_str.split(","))
            
            config = MotionSDKConfig(
                host=self.controller_host_var.get().strip(),
                port=int(self.controller_port_var.get().strip()),
                connection_type=self.connection_type_var.get().strip(),
                com_port=int(self.com_port_var.get().strip()),
                pci_card=int(self.pci_card_var.get().strip()),
                axes=axes,
                homing_mode=int(self.homing_mode_var.get().strip()),
                stop_mode=int(self.stop_mode_var.get().strip()),
                force_mock=self.force_mock_var.get(),
            )
            
            # 测试连接，如果失败且不是模拟模式，会抛出异常
            temp_service = PipelineAppService(config)
            # 尝试连接以验证配置
            if not config.force_mock:
                temp_service.connect_controller()
            
            # 配置验证成功，使用新服务
            self.service = temp_service
            self._render_snapshot(self.service.get_snapshot())
            messagebox.showinfo("配置成功", "控制器配置已应用，请重新连接控制器。")
        except Exception as e:
            # 显示错误信息，但不阻止 GUI 运行
            error_msg = f"配置应用失败: {str(e)}"
            print(error_msg)
            # 使用简单的错误提示，避免可能的 GUI 崩溃
            try:
                messagebox.showerror("配置错误", error_msg)
            except Exception:
                pass
            # 保持原有的服务不变

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(
            header,
            text="AI Mechanical Arm Control Panel",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")

        status_frame = ttk.Frame(header)
        status_frame.grid(row=0, column=1, sticky="e", padx=(20, 0))
        self.status_lights = ttk.Label(status_frame, text="", font=("Microsoft YaHei UI", 10, "bold"))
        self.status_lights.pack(side="right")

        ttk.Label(
            header,
            text="语音/视觉输入 → 任务规划 → 调度执行 → 控制器",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        main = ttk.Frame(self.root, padding=16)
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=5)
        main.rowconfigure(0, weight=1)

        left_panel = ttk.Frame(main)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_panel.rowconfigure(0, weight=1)
        left_panel.columnconfigure(0, weight=1)

        right_panel = ttk.Frame(main)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_panel.rowconfigure(1, weight=1)
        right_panel.columnconfigure(0, weight=1)

        self._build_left_panel(left_panel)
        self._build_right_panel(right_panel)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew")

        task_tab = ttk.Frame(notebook, padding=8)
        task_tab.columnconfigure(0, weight=1)
        notebook.add(task_tab, text="  任务输入  ")

        voice_tab = ttk.Frame(notebook, padding=8)
        voice_tab.columnconfigure(0, weight=1)
        voice_tab.rowconfigure(2, weight=1)
        notebook.add(voice_tab, text="  语音识别  ")

        nlp_tab = ttk.Frame(notebook, padding=8)
        nlp_tab.columnconfigure(0, weight=1)
        nlp_tab.rowconfigure(2, weight=1)
        notebook.add(nlp_tab, text="  自然语言  ")

        json_tab = ttk.Frame(notebook, padding=8)
        json_tab.columnconfigure(0, weight=1)
        json_tab.rowconfigure(2, weight=1)
        notebook.add(json_tab, text="  JSON指令  ")

        self._build_task_tab(task_tab)
        self._build_voice_tab(voice_tab)
        self._build_nlp_tab(nlp_tab)
        self._build_json_tab(json_tab)

    def _build_task_tab(self, parent: ttk.Frame) -> None:
        input_frame = ttk.LabelFrame(parent, text="任务输入", style="Card.TLabelframe", padding=12)
        input_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="语音文本:", font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(input_frame, textvariable=self.voice_var, font=("Microsoft YaHei UI", 10)).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 4)
        )
        ttk.Label(
            input_frame,
            text="示例：机械手回零 / 停止作业 / 抓取左边托盘里的工件放到右边工位",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        vision_frame = ttk.LabelFrame(parent, text="视觉输入", style="Card.TLabelframe", padding=12)
        vision_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        vision_frame.columnconfigure(1, weight=1)
        vision_frame.columnconfigure(3, weight=1)

        row = 0
        ttk.Checkbutton(vision_frame, text="发现目标", variable=self.target_found_var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=2
        )
        ttk.Checkbutton(vision_frame, text="安全区域通过", variable=self.safe_region_var).grid(
            row=row, column=2, columnspan=2, sticky="w", pady=2
        )

        row += 1
        ttk.Label(vision_frame, text="目标 ID:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(vision_frame, textvariable=self.target_id_var, width=12).grid(row=row, column=1, sticky="ew", padx=(4, 16), pady=(8, 0))
        ttk.Label(vision_frame, text="X 坐标:").grid(row=row, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(vision_frame, textvariable=self.pos_x_var, width=10).grid(row=row, column=3, sticky="ew", pady=(8, 0))

        row += 1
        ttk.Label(vision_frame, text="Y 坐标:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(vision_frame, textvariable=self.pos_y_var, width=12).grid(row=row, column=1, sticky="ew", padx=(4, 16), pady=(8, 0))
        ttk.Label(vision_frame, text="角度:").grid(row=row, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(vision_frame, textvariable=self.angle_var, width=10).grid(row=row, column=3, sticky="ew", pady=(8, 0))

        row += 1
        ttk.Label(vision_frame, text="置信度:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(vision_frame, textvariable=self.confidence_var, width=12).grid(row=row, column=1, sticky="ew", padx=(4, 16), pady=(8, 0))

        action_frame = ttk.LabelFrame(parent, text="操作", style="Card.TLabelframe", padding=12)
        action_frame.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        action_frame.columnconfigure(0, weight=1)

        ttk.Button(action_frame, text="执行任务", style="Primary.TButton", command=self.on_submit).grid(
            row=0, column=0, sticky="ew"
        )

        example_frame = ttk.LabelFrame(parent, text="快速示例", style="Card.TLabelframe", padding=12)
        example_frame.grid(row=3, column=0, sticky="ew")
        example_frame.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(example_frame, text="抓取放置", style="Small.TButton", command=self.use_pick_example).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(example_frame, text="机械手回零", style="Small.TButton", command=self.use_home_example).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(example_frame, text="停止作业", style="Small.TButton", command=self.use_stop_example).grid(
            row=0, column=2, sticky="ew", padx=(4, 0)
        )

    def _build_voice_tab(self, parent: ttk.Frame) -> None:
        config_frame = ttk.LabelFrame(parent, text="识别参数", style="Card.TLabelframe", padding=12)
        config_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        config_frame.columnconfigure(1, weight=1)
        config_frame.columnconfigure(3, weight=1)

        ttk.Label(config_frame, text="录音秒数:").grid(row=0, column=0, sticky="w")
        ttk.Entry(config_frame, textvariable=self.mic_seconds_var, width=8).grid(row=0, column=1, sticky="w", padx=(4, 16))
        ttk.Label(config_frame, text="设备号:").grid(row=0, column=2, sticky="w")
        ttk.Entry(config_frame, textvariable=self.mic_device_var, width=8).grid(row=0, column=3, sticky="w", padx=(4, 0))

        ttk.Label(config_frame, text="音频后端:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            config_frame,
            textvariable=self.mic_backend_var,
            values=["sounddevice", "pyaudio"],
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=(4, 16), pady=(8, 0))
        ttk.Checkbutton(config_frame, text="自动回填到任务输入", variable=self.auto_fill_voice_var).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(config_frame, text="语音识别后自动执行(DeepSeek)", variable=self.voice_auto_execute_var).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )

        action_frame = ttk.LabelFrame(parent, text="识别操作", style="Card.TLabelframe", padding=12)
        action_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        action_frame.columnconfigure((0, 1, 2), weight=1)

        self.iflytek_list_button = ttk.Button(
            action_frame, text="列出麦克风设备", style="Secondary.TButton", command=self.on_list_mics
        )
        self.iflytek_list_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.iflytek_mic_button = ttk.Button(
            action_frame, text="开始麦克风识别", style="Primary.TButton", command=self.on_iflytek_mic
        )
        self.iflytek_mic_button.grid(row=0, column=1, sticky="ew", padx=4)

        self.iflytek_audio_button = ttk.Button(
            action_frame, text="导入音频文件", style="Secondary.TButton", command=self.on_iflytek_audio_file
        )
        self.iflytek_audio_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        self.iflytek_use_text_button = ttk.Button(
            action_frame, text="采用识别结果 → 任务输入", style="Secondary.TButton", command=self.on_use_iflytek_text
        )
        self.iflytek_use_text_button.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        result_frame = ttk.LabelFrame(parent, text="识别结果", style="Card.TLabelframe", padding=12)
        result_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(2, weight=1)
        result_frame.rowconfigure(4, weight=1)

        status_row = ttk.Frame(result_frame)
        status_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(status_row, text="状态:", font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        ttk.Label(status_row, textvariable=self.iat_status_var, style="StatusOK.TLabel").pack(side="left", padx=(8, 0))

        ttk.Label(result_frame, text="识别文本:", font=("Microsoft YaHei UI", 9, "bold")).grid(row=1, column=0, sticky="w", pady=(8, 4))
        self.iflytek_result_text = scrolledtext.ScrolledText(result_frame, height=3, wrap="word", font=("Microsoft YaHei UI", 10))
        self.iflytek_result_text.grid(row=2, column=0, sticky="nsew")

        ttk.Separator(result_frame, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=(12, 12))

        ttk.Label(result_frame, text="DeepSeek解析结果:", font=("Microsoft YaHei UI", 9, "bold")).grid(row=4, column=0, sticky="w", pady=(0, 4))
        self.voice_nlp_result_text = scrolledtext.ScrolledText(result_frame, height=10, wrap="word", font=('Consolas', 9))
        self.voice_nlp_result_text.grid(row=5, column=0, sticky="nsew")
        self.voice_nlp_result_text.insert("1.0", "语音指令解析结果将显示在这里...")
        
        tips_frame = ttk.Frame(parent)
        tips_frame.grid(row=3, column=0, sticky="ew")
        ttk.Label(
            tips_frame,
            text="提示：先列出设备确认设备号，录音 3-5 秒，命令尽量简短明确",
            style="Subtitle.TLabel",
        ).pack(side="left")

        ttk.Button(
            parent, text="执行任务", style="Primary.TButton", command=self.on_execute_voice_result
        ).grid(row=4, column=0, sticky="ew")

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        # 控制器配置
        config_frame = ttk.LabelFrame(parent, text="控制器配置", style="Card.TLabelframe", padding=12)
        config_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        config_frame.columnconfigure(1, weight=1)
        config_frame.columnconfigure(3, weight=1)
        
        row = 0
        ttk.Checkbutton(config_frame, text="使用模拟控制器 (开发测试用)", variable=self.force_mock_var).grid(
            row=row, column=0, columnspan=4, sticky="w", pady=2
        )
        
        row += 1
        ttk.Label(config_frame, text="连接方式:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        connection_combo = ttk.Combobox(
            config_frame,
            textvariable=self.connection_type_var,
            values=["eth", "com", "pci", "fast"],
            state="readonly",
            width=8,
        )
        connection_combo.grid(row=row, column=1, sticky="w", padx=(4, 16), pady=(8, 0))
        
        ttk.Label(config_frame, text="主机IP:").grid(row=row, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(config_frame, textvariable=self.controller_host_var, width=12).grid(row=row, column=3, sticky="ew", pady=(8, 0))
        
        row += 1
        ttk.Label(config_frame, text="端口:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(config_frame, textvariable=self.controller_port_var, width=8).grid(row=row, column=1, sticky="w", padx=(4, 16), pady=(8, 0))
        
        ttk.Label(config_frame, text="串口/PCI号:").grid(row=row, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(config_frame, textvariable=self.com_port_var, width=8).grid(row=row, column=3, sticky="w", pady=(8, 0))
        
        row += 1
        ttk.Label(config_frame, text="运动轴:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(config_frame, textvariable=self.axes_var, width=12).grid(row=row, column=1, sticky="w", padx=(4, 16), pady=(8, 0))
        
        ttk.Label(config_frame, text="回零模式:").grid(row=row, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(config_frame, textvariable=self.homing_mode_var, width=8).grid(row=row, column=3, sticky="w", pady=(8, 0))
        
        row += 1
        ttk.Label(config_frame, text="停止模式:").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(config_frame, textvariable=self.stop_mode_var, width=8).grid(row=row, column=1, sticky="w", padx=(4, 16), pady=(8, 0))
        
        ttk.Button(config_frame, text="应用配置", style="Secondary.TButton", command=self._reinitialize_service).grid(
            row=row, column=2, columnspan=2, sticky="ew", pady=(8, 0)
        )
        
        # 控制按钮
        control_frame = ttk.Frame(parent)
        control_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        control_frame.columnconfigure(0, weight=1)

        ttk.Label(control_frame, text="控制器连接:", font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        ttk.Button(control_frame, text="连接", style="Primary.TButton", command=self.on_connect).pack(side="left", padx=(12, 4))
        ttk.Button(control_frame, text="断开", style="Secondary.TButton", command=self.on_disconnect).pack(side="left", padx=4)
        
        ttk.Button(control_frame, text="注入报警", style="Secondary.TButton", command=self.on_inject_alarm).pack(side="left", padx=4)
        ttk.Button(control_frame, text="清除报警", style="Secondary.TButton", command=self.on_clear_alarm).pack(side="left", padx=4)

        status_card = ttk.LabelFrame(parent, text="状态概览", style="Card.TLabelframe", padding=12)
        status_card.grid(row=2, column=0, sticky="nsew")
        status_card.columnconfigure(0, weight=1)
        status_card.rowconfigure(2, weight=1)

        summary_frame = ttk.Frame(status_card)
        summary_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for i in range(6):
            summary_frame.columnconfigure(i, weight=1)

        self._build_status_cell(summary_frame, 0, "连接", self.connected_var)
        self._build_status_cell(summary_frame, 1, "伺服", self.servo_var)
        self._build_status_cell(summary_frame, 2, "报警", self.alarm_var)
        self._build_status_cell(summary_frame, 3, "急停", self.estop_var)
        self._build_status_cell(summary_frame, 4, "位姿", self.pose_var)
        self._build_status_cell(summary_frame, 5, "最后命令", self.last_command_var)

        feedback_frame = ttk.Frame(status_card)
        feedback_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(feedback_frame, text="执行反馈:", font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        ttk.Label(feedback_frame, textvariable=self.result_summary_var, wraplength=500).pack(side="left", padx=(8, 0))

        notebook = ttk.Notebook(status_card)
        notebook.grid(row=2, column=0, sticky="nsew")

        self.result_text = self._build_text_tab(notebook, "  执行结果  ")
        self.status_text = self._build_text_tab(notebook, "  控制器状态  ")
        self.alarm_text = self._build_text_tab(notebook, "  报警记录  ")
        self.command_text = self._build_text_tab(notebook, "  命令历史  ")
        self.task_text = self._build_text_tab(notebook, "  任务历史  ")

    def _build_status_cell(self, parent: ttk.Frame, col: int, label: str, variable: tk.StringVar) -> None:
        cell = ttk.Frame(parent, padding=4)
        cell.grid(row=0, column=col, sticky="nsew", padx=2)
        ttk.Label(cell, text=label, font=("Microsoft YaHei UI", 8), foreground="#888888").pack(anchor="w")
        ttk.Label(cell, textvariable=variable, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(2, 0))

    def _build_text_tab(self, notebook: ttk.Notebook, title: str) -> scrolledtext.ScrolledText:
        frame = ttk.Frame(notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 9))
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
        # 显示完整的连接结果，包括后端类型
        result = {
            "action": "connect_controller",
            "status": snapshot.get("status", {}),
            "backend": self.service.client.backend_name
        }
        self._set_text(self.result_text, json.dumps(result, ensure_ascii=False, indent=2))

    def on_disconnect(self) -> None:
        snapshot = self.service.disconnect_controller()
        self._render_snapshot(snapshot)
        # 显示完整的断开结果
        result = {
            "action": "disconnect_controller",
            "status": snapshot.get("status", {})
        }
        self._set_text(self.result_text, json.dumps(result, ensure_ascii=False, indent=2))

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
        recognized = self._get_iflytek_text()
        if not recognized:
            messagebox.showwarning("没有识别结果", "请先进行一次音频或麦克风识别。")
            return
        self.voice_var.set(recognized)

    def on_execute_voice_result(self) -> None:
        """手动执行识别结果"""
        recognized = self._get_iflytek_text()
        if not recognized:
            messagebox.showwarning("没有识别结果", "请先进行一次音频或麦克风识别。")
            return
        
        try:
            self._execute_voice_command(recognized)
        except Exception as e:
            messagebox.showerror("执行失败", f"语音指令执行失败: {str(e)}")

    def use_pick_example(self) -> None:
        self.voice_var.set(DEFAULT_VOICE)
        self.target_found_var.set(True)
        self.safe_region_var.set(True)
        self._set_iflytek_text("")

    def use_home_example(self) -> None:
        self.voice_var.set("机械手回零")
        self._set_iflytek_text("")

    def use_stop_example(self) -> None:
        self.voice_var.set("停止作业")
        self._set_iflytek_text("")

    def _build_nlp_tab(self, parent: ttk.Frame) -> None:
        input_frame = ttk.LabelFrame(parent, text="自然语言输入", style="Card.TLabelframe", padding=12)
        input_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="指令文本:", font=('Microsoft YaHei UI', 9, 'bold')).grid(row=0, column=0, sticky="w")
        ttk.Entry(input_frame, textvariable=self.nlp_input_var, font=('Microsoft YaHei UI', 10)).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 4)
        )

        config_frame = ttk.LabelFrame(parent, text="配置", style="Card.TLabelframe", padding=12)
        config_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(config_frame, text="使用DeepSeek API", variable=self.use_deepseek_var).grid(
            row=0, column=0, sticky="w", pady=2
        )

        result_frame = ttk.LabelFrame(parent, text="解析结果", style="Card.TLabelframe", padding=12)
        result_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=1)

        ttk.Label(result_frame, text="DeepSeek解析结果:", font=('Microsoft YaHei UI', 9, 'bold')).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.nlp_result_text = scrolledtext.ScrolledText(result_frame, height=5, wrap="word", font=('Consolas', 9))
        self.nlp_result_text.grid(row=1, column=0, sticky="nsew")
        self.nlp_result_text.insert("1.0", "解析结果将显示在这里...")
        
        example_frame = ttk.LabelFrame(parent, text="快速示例", style="Card.TLabelframe", padding=12)
        example_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        example_frame.columnconfigure((0, 1), weight=1)

        ttk.Button(example_frame, text="移动到第一个位置", style="Small.TButton", command=lambda: self.nlp_input_var.set("移动到第一个位置")).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(example_frame, text="抓取物体A", style="Small.TButton", command=lambda: self.nlp_input_var.set("抓取物体A")).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        ttk.Button(example_frame, text="向上移动10毫米", style="Small.TButton", command=lambda: self.nlp_input_var.set("向上移动10毫米")).grid(
            row=1, column=0, sticky="ew", padx=(0, 4), pady=(4, 0)
        )
        ttk.Button(example_frame, text="回零", style="Small.TButton", command=lambda: self.nlp_input_var.set("回零")).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(4, 0)
        )

        action_frame = ttk.LabelFrame(parent, text="操作", style="Card.TLabelframe", padding=12)
        action_frame.grid(row=4, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        ttk.Button(action_frame, text="执行任务", style="Primary.TButton", command=self.on_nlp_submit).grid(
            row=0, column=0, sticky="ew"
        )

    def _build_json_tab(self, parent: ttk.Frame) -> None:
        input_frame = ttk.LabelFrame(parent, text="JSON指令输入", style="Card.TLabelframe", padding=12)
        input_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        input_frame.columnconfigure(0, weight=1)
        input_frame.rowconfigure(0, weight=1)

        json_text = scrolledtext.ScrolledText(input_frame, height=16, wrap="word", font=('Consolas', 9))
        json_text.grid(row=0, column=0, sticky="nsew")
        json_text.insert("1.0", self.json_input_var.get())
        self.json_text_widget = json_text

        action_frame = ttk.LabelFrame(parent, text="操作", style="Card.TLabelframe", padding=12)
        action_frame.grid(row=1, column=0, sticky="ew")
        action_frame.columnconfigure((0, 1), weight=1)

        ttk.Button(action_frame, text="执行任务", style="Primary.TButton", command=self.on_json_submit).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(action_frame, text="加载文件", style="Secondary.TButton", command=self.on_load_json_file).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        example_frame = ttk.LabelFrame(parent, text="快速示例", style="Card.TLabelframe", padding=12)
        example_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        example_frame.columnconfigure((0, 1), weight=1)

        ttk.Button(example_frame, text="MOVE指令", style="Small.TButton", command=lambda: self._load_json_example('move')).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(example_frame, text="HOME指令", style="Small.TButton", command=lambda: self._load_json_example('home')).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        ttk.Button(example_frame, text="GRASP指令", style="Small.TButton", command=lambda: self._load_json_example('grasp')).grid(
            row=1, column=0, sticky="ew", padx=(0, 4), pady=(4, 0)
        )
        ttk.Button(example_frame, text="RELEASE指令", style="Small.TButton", command=lambda: self._load_json_example('release')).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(4, 0)
        )

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
            self._set_iflytek_text(recognized)
            if recognized and self.auto_fill_voice_var.get():
                self.voice_var.set(recognized)
            self.result_summary_var.set(f"语音识别完成：{recognized[:50]}{'...' if len(recognized) > 50 else ''}")
            
            # 如果开启了自动执行，则自动处理
            if recognized and self.voice_auto_execute_var.get():
                try:
                    self._execute_voice_command(recognized)
                except Exception as e:
                    messagebox.showerror("执行失败", f"语音指令执行失败: {str(e)}")
        else:
            error_msg = payload.get("error", "未知错误")
            self._set_iflytek_text(f"识别失败：{error_msg}")
            self.result_summary_var.set(f"语音识别失败：{error_msg}")
        self._render_snapshot(self.service.get_snapshot())
        self._set_text(self.result_text, json.dumps(payload, ensure_ascii=False, indent=2))
    
    def _execute_voice_command(self, text: str) -> None:
        """执行语音识别到的命令"""
        try:
            processor = NaturalLanguageProcessor(use_deepseek=self.use_deepseek_var.get())
            command_json = processor.process_to_json(text)
            
            # 显示解析结果
            self.voice_nlp_result_text.delete("1.0", tk.END)
            self.voice_nlp_result_text.insert("1.0", json.dumps(command_json, ensure_ascii=False, indent=2))
            
            result = self.service.execute_json_command(command_json)
            self._render_result(result)
            self.result_summary_var.set(f"语音指令执行完成: {text}")
        except Exception as e:
            messagebox.showerror("执行失败", f"指令执行失败: {str(e)}")

    def _handle_device_payload(self, payload: dict) -> None:
        if payload.get("ok"):
            devices = payload.get("devices", [])
            self.result_summary_var.set(f"已读取 {len(devices)} 个可用输入设备。")
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
            self.iflytek_list_button,
            self.iflytek_mic_button,
            self.iflytek_audio_button,
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
        self.connected_var.set("已连接" if status.get("connected") else "未连接")
        self.servo_var.set("开启" if status.get("servo_enabled") else "关闭")
        self.alarm_var.set("无" if not status.get("alarm_active") else "有报警")
        self.estop_var.set("正常" if not status.get("emergency_stopped") else "已急停")
        pose = status.get("current_pose") or [0.0, 0.0, 0.0]
        self.pose_var.set(", ".join(f"{float(item):.1f}" for item in pose))
        self.last_command_var.set(str(status.get("last_command") or "无")[:20])
        self._set_text(self.status_text, json.dumps(status, ensure_ascii=False, indent=2))
        self._set_text(self.alarm_text, json.dumps(snapshot.get("alarms", []), ensure_ascii=False, indent=2))
        self._set_text(self.command_text, json.dumps(snapshot.get("command_history", []), ensure_ascii=False, indent=2))
        self._set_text(self.task_text, json.dumps(snapshot.get("task_history", []), ensure_ascii=False, indent=2))

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)

    def _set_iflytek_text(self, content: str) -> None:
        self.iflytek_text_var.set(content)
        self._set_text(self.iflytek_result_text, content)

    def _get_iflytek_text(self) -> str:
        return self.iflytek_result_text.get("1.0", tk.END).strip()

    def _set_status_lights(self, status: dict) -> None:
        connected = "CONNECTED" if status.get("connected") else "DISCONNECTED"
        servo = "SERVO ON" if status.get("servo_enabled") else "SERVO OFF"
        alarm = "ALARM" if status.get("alarm_active") else "NORMAL"
        estop = "E-STOP" if status.get("emergency_stopped") else "READY"
        self.status_lights.configure(text=f"{connected} | {servo} | {alarm} | {estop}")

    def on_nlp_submit(self) -> None:
        """处理自然语言指令提交"""
        text = self.nlp_input_var.get().strip()
        if not text:
            messagebox.showwarning("输入为空", "请输入自然语言指令")
            return

        try:
            processor = NaturalLanguageProcessor(use_deepseek=self.use_deepseek_var.get())
            command_json = processor.process_to_json(text)
            
            # 显示解析结果
            self.nlp_result_text.delete("1.0", tk.END)
            self.nlp_result_text.insert("1.0", json.dumps(command_json, ensure_ascii=False, indent=2))
            
            result = self.service.execute_json_command(command_json)
            self._render_result(result)
            self.result_summary_var.set(f"自然语言指令执行完成: {text}")
        except Exception as e:
            messagebox.showerror("执行失败", f"指令执行失败: {str(e)}")

    def on_json_submit(self) -> None:
        """处理JSON指令提交"""
        json_text = self.json_text_widget.get("1.0", tk.END).strip()
        if not json_text:
            messagebox.showwarning("输入为空", "请输入JSON指令")
            return

        try:
            command_data = json.loads(json_text)
            result = self.service.execute_json_command(command_data)
            self._render_result(result)
            self.result_summary_var.set("JSON指令执行完成")
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON格式错误", f"JSON格式解析失败: {str(e)}")
        except Exception as e:
            messagebox.showerror("执行失败", f"指令执行失败: {str(e)}")

    def on_load_json_file(self) -> None:
        """加载JSON文件"""
        file_path = filedialog.askopenfilename(
            title="选择JSON指令文件",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if not file_path:
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.json_text_widget.delete("1.0", tk.END)
            self.json_text_widget.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("加载失败", f"文件加载失败: {str(e)}")

    def _load_json_example(self, example_type: str) -> None:
        """加载JSON示例"""
        examples = {
            'move': '''{
  "command": "MOVE",
  "parameters": {
    "target": "POSITION_1",
    "offset": {
      "x": 100.0,
      "y": 100.0,
      "z": 50.0,
      "rotation": 0.0
    },
    "speed": 50,
    "relative": false
  },
  "timestamp": "2026-04-02T12:00:00Z"
}''',
            'home': '''{
  "command": "HOME",
  "parameters": {},
  "timestamp": "2026-04-02T12:00:00Z"
}''',
            'grasp': '''{
  "command": "GRASP",
  "parameters": {
    "target": "OBJECT_1",
    "force": 5.0
  },
  "timestamp": "2026-04-02T12:00:00Z"
}''',
            'release': '''{
  "command": "RELEASE",
  "parameters": {},
  "timestamp": "2026-04-02T12:00:00Z"
}'''
        }

        if example_type in examples:
            self.json_text_widget.delete("1.0", tk.END)
            self.json_text_widget.insert("1.0", examples[example_type])


def smoke_test() -> None:
    from ai_pipeline_prototype.sdk_adapter import MotionSDKConfig
    # 使用 force_mock=True 确保 smoke test 能通过
    config = MotionSDKConfig(force_mock=True)
    service = PipelineAppService(config)
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
    PipelineAppUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
