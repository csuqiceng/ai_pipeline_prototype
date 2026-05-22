"""Emergency command recognition with explicit authorization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EmergencyDecision:
    """Result of evaluating a possible emergency utterance."""

    matched: bool
    authorized: bool
    action_key: str | None
    message: str
    reason: str


class EmergencyChannel:
    """Validates three-part emergency phrases before returning sys_estop."""

    _CODED_PATTERN = re.compile(r"^\s*(?:急停|紧急停止)\s+([A-Za-z0-9_-]{3,16})\s+(?:急停|紧急停止)\s*$")
    _KEYWORDS = ("急停", "紧急停止")

    def __init__(self, authorized_codes: Iterable[str] | None = None) -> None:
        self.authorized_codes = {str(code).strip() for code in (authorized_codes or {"A1B2"}) if str(code).strip()}

    def evaluate(self, text: str) -> EmergencyDecision:
        normalized = " ".join(str(text or "").strip().split())
        if not normalized:
            return self._none()

        coded_match = self._CODED_PATTERN.match(normalized)
        if coded_match:
            code = coded_match.group(1)
            if code in self.authorized_codes:
                return EmergencyDecision(
                    matched=True,
                    authorized=True,
                    action_key="sys_estop",
                    message="急停授权码有效，正在执行急停。",
                    reason="authorized",
                )
            return EmergencyDecision(
                matched=True,
                authorized=False,
                action_key=None,
                message="急停授权码无效，未执行急停。",
                reason="invalid_code",
            )

        compact = normalized.replace(" ", "")
        if any(keyword in compact for keyword in self._KEYWORDS):
            return EmergencyDecision(
                matched=True,
                authorized=False,
                action_key=None,
                message="已识别到急停意图。请按“急停 授权码 急停”格式确认。",
                reason="missing_code",
            )

        return self._none()

    @staticmethod
    def _none() -> EmergencyDecision:
        return EmergencyDecision(
            matched=False,
            authorized=False,
            action_key=None,
            message="",
            reason="none",
        )
