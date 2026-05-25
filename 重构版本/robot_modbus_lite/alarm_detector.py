from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alarm_advice import AlarmAdvice, AlarmAdviceBook
from .models import SixAxisAlarmDetail


OK_ALARM_CODES = {"", "0", "ERR_000", "-"}
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class AlarmDetection:
    active: bool
    codes: tuple[str, ...]
    details: tuple[str, ...]
    advices: tuple[AlarmAdvice, ...]
    severity: str = "info"
    auto_clear_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "codes": list(self.codes),
            "details": list(self.details),
            "severity": self.severity,
            "auto_clear_allowed": self.auto_clear_allowed,
            "advices": [
                {
                    "code": advice.code,
                    "title": advice.title,
                    "severity": advice.severity,
                    "operator_hint": advice.operator_hint,
                    "engineer_hint": advice.engineer_hint,
                    "auto_clear": advice.auto_clear,
                }
                for advice in self.advices
            ],
        }


class AlarmDetector:
    def __init__(self, advice_book: AlarmAdviceBook | None = None):
        self.advice_book = advice_book or AlarmAdviceBook.default()

    def detect(
        self,
        *,
        alarm_code: str = "",
        alarm_text: str = "",
        long38_raw: int | float | None = None,
        estop: bool = False,
        paused: bool = False,
        realtime_feedback: str = "online",
        controller: str = "online",
    ) -> AlarmDetection:
        codes: list[str] = []
        details: list[str] = []
        if estop:
            codes.append("E_STOP")
            details.append("急停输入有效")
        if paused:
            codes.append("PAUSED")
            details.append("暂停输入有效")
        raw_code = str(alarm_code or "").strip()
        raw_text = str(alarm_text or "").strip()
        if raw_code not in OK_ALARM_CODES:
            details.append(raw_text or raw_code)
        if long38_raw not in (None, "", "-"):
            try:
                detail = SixAxisAlarmDetail.from_value(long38_raw)
                codes.extend(self._codes_from_long38(detail))
                text = str(detail)
                if text and text != "无报警详情":
                    details.append(text)
            except Exception:
                details.append(f"LONG(38)={long38_raw}")
        if str(realtime_feedback or "").lower() in {"stale", "offline", "unknown"}:
            codes.append("COMM_STALE")
            details.append("实时反馈不可用或过期")
        if str(controller or "").lower() in {"stale", "offline", "unknown"}:
            codes.append("CONTROLLER_NOT_READY")
            details.append("控制器未就绪")
        if raw_code not in OK_ALARM_CODES and not codes:
            codes.append(raw_code)
        normalized_codes = tuple(dict.fromkeys(code for code in codes if code))
        advices = tuple(self.advice_book.get(code) for code in normalized_codes)
        active = bool(normalized_codes or raw_code not in OK_ALARM_CODES)
        severity = self._highest_severity(advices) if active else "info"
        return AlarmDetection(
            active=active,
            codes=normalized_codes,
            details=tuple(dict.fromkeys(detail for detail in details if detail)),
            advices=advices,
            severity=severity,
            auto_clear_allowed=bool(active and advices and all(advice.auto_clear for advice in advices)),
        )

    @staticmethod
    def _codes_from_long38(detail: SixAxisAlarmDetail) -> list[str]:
        codes: list[str] = []
        if detail.radius or detail.height:
            codes.append("CART_LIMIT")
        if detail.speed:
            codes.append("OVER_SPEED")
        if detail.accel:
            codes.append("OVER_ACCEL")
        if detail.decel:
            codes.append("OVER_DECEL")
        if detail.ecat_exceeded:
            codes.append("COMM_STALE")
        if detail.cmd_busy or detail.drive_alarm:
            codes.append("CONTROLLER_NOT_READY")
        return codes

    @staticmethod
    def _highest_severity(advices: tuple[AlarmAdvice, ...]) -> str:
        if not advices:
            return "warning"
        return max((advice.severity for advice in advices), key=lambda item: SEVERITY_ORDER.get(item, 0))
