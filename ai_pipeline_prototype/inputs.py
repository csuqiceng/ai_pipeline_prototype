from __future__ import annotations

from typing import Any, Mapping

from .models import VisionInput, VoiceInput


class VoiceInputError(ValueError):
    pass


class VoiceInputAdapter:
    """第一阶段先用规则法做意图抽取，后续可替换为 ASR/LLM。"""

    def parse_text(self, text: str) -> VoiceInput:
        normalized = text.strip()
        compact = normalized.replace(" ", "")

        if self._contains_any(compact, ["停止", "停下", "停机", "急停", "停止作业", "停止工作", "停一下", "先停"]):
            return VoiceInput(text=normalized, intent="stop_task", confidence=0.98)

        if self._contains_any(compact, ["回零", "复位", "回原点", "回到原点", "回到零点", "归零", "回家"]):
            return VoiceInput(text=normalized, intent="go_home", confidence=0.95)

        if self._is_pick_and_place(compact):
            return VoiceInput(
                text=normalized,
                intent="pick_and_place",
                target_area="left_tray",
                destination_area="right_station",
                confidence=0.93,
            )

        if self._contains_any(compact, ["抓取", "抓一下", "抓目标", "拿一下", "取一下", "拿起"]):
            return VoiceInput(text=normalized, intent="pick", confidence=0.80)
        return VoiceInput(text=normalized, intent="unknown", confidence=0.20)

    def from_payload(self, payload: Mapping[str, Any]) -> VoiceInput:
        text = str(payload.get("text", "")).strip()
        intent = str(payload.get("intent", "")).strip()
        target_area = self._optional_str(payload.get("target_area"))
        destination_area = self._optional_str(payload.get("destination_area"))
        confidence = self._parse_confidence(payload.get("confidence", 0.0))
        timestamp = self._optional_str(payload.get("timestamp"))

        if not text:
            raise VoiceInputError("语音结果缺少 text，不能接入。")
        if not intent:
            raise VoiceInputError("语音结果缺少 intent，不能接入。")

        voice = VoiceInput(
            text=text,
            intent=intent,
            target_area=target_area,
            destination_area=destination_area,
            confidence=confidence,
        )
        if timestamp:
            voice.timestamp = timestamp
        return voice

    def parse(self, *, text: str | None = None, payload: Mapping[str, Any] | None = None) -> VoiceInput:
        if payload is not None:
            return self.from_payload(payload)
        if text is None:
            raise VoiceInputError("必须提供 voice_text 或 voice_payload。")
        return self.parse_text(text)

    def _optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _parse_confidence(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise VoiceInputError(f"语音结果 confidence 非法: {value!r}") from exc

    def _contains_any(self, text: str, patterns: list[str]) -> bool:
        return any(pattern in text for pattern in patterns)

    def _is_pick_and_place(self, text: str) -> bool:
        left_patterns = ["左边托盘", "左托盘", "左边料盘", "左料盘", "左边盘子"]
        right_patterns = ["右边工位", "右工位", "右侧工位", "右边位置", "右边台子"]
        pick_patterns = ["抓", "拿", "取"]
        place_patterns = ["放到", "放去", "放在", "搬到", "移到"]

        has_left = self._contains_any(text, left_patterns)
        has_right = self._contains_any(text, right_patterns)
        has_pick = self._contains_any(text, pick_patterns) or "把" in text
        has_place = self._contains_any(text, place_patterns)
        return has_left and has_right and has_pick and has_place


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
