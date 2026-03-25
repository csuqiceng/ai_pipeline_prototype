from __future__ import annotations

from .models import VisionInput, VoiceInput


class VoiceInputAdapter:
    """第一阶段先用规则法做意图抽取，后续可替换为 ASR/LLM。"""

    def parse_text(self, text: str) -> VoiceInput:
        normalized = text.strip()
        if "停止" in normalized:
            return VoiceInput(text=normalized, intent="stop_task", confidence=0.98)
        if "回零" in normalized or "复位" in normalized:
            return VoiceInput(text=normalized, intent="go_home", confidence=0.95)
        if "左边托盘" in normalized and "右边工位" in normalized:
            return VoiceInput(
                text=normalized,
                intent="pick_and_place",
                target_area="left_tray",
                destination_area="right_station",
                confidence=0.93,
            )
        if "抓取" in normalized:
            return VoiceInput(text=normalized, intent="pick", confidence=0.80)
        return VoiceInput(text=normalized, intent="unknown", confidence=0.20)


class VisionInputAdapter:
    """第一阶段直接接收结构化视觉结果，后续替换为 OpenCV/检测模型输出。"""

    def from_detection(
        self,
        *,
        camera_id: str,
        target_found: bool,
        target_id: str | None,
        position: list[float] | None,
        angle: float | None,
        confidence: float,
        safe_region_ok: bool,
        target_type: str = "metal_part",
    ) -> VisionInput:
        return VisionInput(
            camera_id=camera_id,
            target_found=target_found,
            target_id=target_id,
            target_type=target_type,
            position=position,
            angle=angle,
            confidence=confidence,
            safe_region_ok=safe_region_ok,
        )
