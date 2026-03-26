from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ai_pipeline_prototype.controller_service import ControllerService
from ai_pipeline_prototype.dispatcher import TaskDispatcher
from ai_pipeline_prototype.executor import MotionSDKRobotExecutor
from ai_pipeline_prototype.inputs import VisionInputAdapter, VoiceInputAdapter, VoiceInputError
from ai_pipeline_prototype.planner import PlanningError, TaskPlanner
from ai_pipeline_prototype.sdk_adapter import MotionSDKClient, MotionSDKConfig
from ai_pipeline_prototype.voice_iflytek import IFlytekIATClient, IFlytekIATConfig, IFlytekMicrophoneConfig, IFlytekRTASRError


class PipelineAppService:
    """应用服务层，负责把输入、规划、调度和控制器状态统一起来。"""

    def __init__(self, config: MotionSDKConfig | None = None) -> None:
        self.voice_adapter = VoiceInputAdapter()
        self.vision_adapter = VisionInputAdapter()
        self.planner = TaskPlanner()
        self.client = MotionSDKClient(config or MotionSDKConfig())
        self.controller_service = ControllerService(self.client)
        self.executor = MotionSDKRobotExecutor(self.client, self.controller_service)
        self.dispatcher = TaskDispatcher(self.executor)
        self.task_history: list[dict[str, Any]] = []

    def submit(
        self,
        *,
        voice_text: str | None = None,
        voice_payload: dict[str, Any] | None = None,
        target_found: bool,
        target_id: str | None,
        position: list[float] | None,
        angle: float | None,
        confidence: float,
        safe_region_ok: bool,
        camera_id: str = "cam_01",
    ) -> dict[str, Any]:
        try:
            voice = self.voice_adapter.parse(text=voice_text, payload=voice_payload)
        except VoiceInputError as exc:
            alarm = self.controller_service.report_alarm("V001", str(exc), level="warning")
            return {
                "ok": False,
                "error": str(exc),
                "voice": voice_payload if voice_payload is not None else {"text": voice_text},
                "vision": None,
                "alarm": asdict(alarm),
                "status": asdict(self.controller_service.get_status()),
            }

        vision = None
        if voice.intent not in {"go_home", "stop_task"}:
            vision = self.vision_adapter.from_detection(
                camera_id=camera_id,
                target_found=target_found,
                target_id=target_id,
                position=position,
                angle=angle,
                confidence=confidence,
                safe_region_ok=safe_region_ok,
            )

        try:
            task = self.planner.build_task(voice, vision)
            result = self.dispatcher.dispatch(task)
        except PlanningError as exc:
            alarm = self.controller_service.report_alarm("P001", str(exc), level="warning")
            payload = {
                "ok": False,
                "error": str(exc),
                "voice": asdict(voice),
                "vision": asdict(vision) if vision else None,
                "alarm": asdict(alarm),
                "status": asdict(self.controller_service.get_status()),
            }
            self.task_history.append(
                {
                    "ok": False,
                    "task_id": None,
                    "intent": voice.intent,
                    "message": str(exc),
                }
            )
            return payload

        payload = {
            "ok": result.success,
            "voice": asdict(voice),
            "vision": asdict(vision) if vision else None,
            "task": task.to_dict(),
            "dispatch_result": asdict(result),
            "status": asdict(self.controller_service.get_status()),
            "alarms": [asdict(item) for item in self.controller_service.get_alarm_history()],
            "command_history": list(self.controller_service.command_history),
        }
        self.task_history.append(
            {
                "ok": result.success,
                "task_id": task.task_id,
                "task_type": task.task_type,
                "message": result.message,
            }
        )
        return payload

    def connect_controller(self) -> dict[str, Any]:
        if not self.controller_service.get_status().connected:
            self.controller_service.connect()
        return self.get_snapshot()

    def disconnect_controller(self) -> dict[str, Any]:
        if self.controller_service.get_status().connected:
            self.controller_service.disconnect()
        return self.get_snapshot()

    def inject_alarm(self, code: str, message: str, level: str = "warning") -> dict[str, Any]:
        alarm = self.controller_service.report_alarm(code, message, level)
        return asdict(alarm)

    def clear_alarms(self) -> dict[str, Any]:
        self.controller_service.clear_alarms()
        return {
            "status": asdict(self.controller_service.get_status()),
            "alarms": [],
        }

    def transcribe_iflytek_audio(self, file_path: str) -> dict[str, Any]:
        try:
            result = IFlytekIATClient(IFlytekIATConfig.from_env()).transcribe_file(file_path)
        except IFlytekRTASRError as exc:
            alarm = self.controller_service.report_alarm("ASR001", str(exc), level="warning")
            return {
                "ok": False,
                "error": str(exc),
                "alarm": asdict(alarm),
                "status": asdict(self.controller_service.get_status()),
            }
        return {"ok": True, "text": result.text, "chunks": result.chunks}

    def transcribe_iflytek_microphone(
        self,
        *,
        duration_sec: float = 4.0,
        device: int | None = None,
        backend: str | None = None,
        debug_save_path: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = IFlytekIATClient(IFlytekIATConfig.from_env()).transcribe_microphone(
                IFlytekMicrophoneConfig(
                    duration_sec=duration_sec,
                    device=device,
                    preferred_backend=backend,
                    debug_save_path=debug_save_path,
                )
            )
        except IFlytekRTASRError as exc:
            alarm = self.controller_service.report_alarm("ASR002", str(exc), level="warning")
            return {
                "ok": False,
                "error": str(exc),
                "alarm": asdict(alarm),
                "status": asdict(self.controller_service.get_status()),
            }
        return {"ok": True, "text": result.text, "chunks": result.chunks}

    def list_iflytek_microphones(self, backend: str | None = None) -> dict[str, Any]:
        try:
            devices = IFlytekIATClient(IFlytekIATConfig.from_env()).list_microphone_devices(backend=backend)
        except IFlytekRTASRError as exc:
            alarm = self.controller_service.report_alarm("ASR003", str(exc), level="warning")
            return {
                "ok": False,
                "error": str(exc),
                "alarm": asdict(alarm),
                "status": asdict(self.controller_service.get_status()),
            }
        return {"ok": True, "devices": devices}

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "status": asdict(self.controller_service.get_status()),
            "alarms": [asdict(item) for item in self.controller_service.get_alarm_history()],
            "command_history": list(self.controller_service.command_history),
            "task_history": list(self.task_history),
        }
