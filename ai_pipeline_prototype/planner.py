from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from .models import Task, VisionInput, VoiceInput


class PlanningError(Exception):
    pass


class TaskPlanner:
    """把语音意图和视觉结果整合成统一任务对象。"""

    def build_task(self, voice: VoiceInput, vision: VisionInput | None) -> Task:
        now = datetime.now()
        task_id = f"task_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:6]}"

        if voice.intent == "unknown":
            raise PlanningError("无法识别语音意图，不能生成任务。")

        if voice.intent in {"stop_task", "go_home"}:
            return Task(
                task_id=task_id,
                task_type=voice.intent,
                target_area=voice.target_area,
                destination_area=voice.destination_area,
                confidence=voice.confidence,
                source="voice+planner",
            )

        if vision is None:
            raise PlanningError("当前任务需要视觉输入，但未提供视觉结果。")

        if not vision.target_found:
            raise PlanningError("视觉未发现目标，不能下发抓取任务。")

        if not vision.safe_region_ok:
            raise PlanningError("视觉安全区检查失败，任务被拦截。")

        if not vision.position or len(vision.position) != 2:
            raise PlanningError("视觉坐标不完整，无法生成抓取点。")

        pick_point = [vision.position[0], vision.position[1], 50.0]
        place_point = self._resolve_place_point(voice.destination_area)
        confidence = round((voice.confidence + vision.confidence) / 2, 2)
        pose = [0.0, vision.angle or 0.0, 0.0]

        return Task(
            task_id=task_id,
            task_type=voice.intent,
            target_id=vision.target_id,
            target_area=voice.target_area,
            destination_area=voice.destination_area,
            pick_point=pick_point,
            place_point=place_point,
            pose=pose,
            confidence=confidence,
        )

    def _resolve_place_point(self, destination_area: str | None) -> list[float]:
        area_map = {
            "right_station": [410.0, 150.0, 60.0],
            "left_tray": [120.0, 200.0, 60.0],
        }
        return area_map.get(destination_area, [300.0, 120.0, 60.0])
