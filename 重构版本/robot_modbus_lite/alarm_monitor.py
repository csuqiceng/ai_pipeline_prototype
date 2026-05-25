from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alarm_detector import AlarmDetection, AlarmDetector


@dataclass
class AlarmMonitorSample:
    detection: AlarmDetection
    interval_ms: int

    def to_dict(self) -> dict[str, Any]:
        payload = self.detection.to_dict()
        payload["interval_ms"] = self.interval_ms
        return payload


class AlarmMonitor:
    """Small 50ms alarm detector loop state, independent from dashboard rendering."""

    def __init__(self, *, interval_ms: int = 50, detector: AlarmDetector | None = None):
        self.interval_ms = max(1, int(interval_ms))
        self.detector = detector or AlarmDetector()
        self.last_sample: AlarmMonitorSample | None = None

    def sample_from_source(self, source: Any) -> AlarmMonitorSample:
        detection = self.detector.detect(
            alarm_code=str(getattr(source, "alarm_code", "") or ""),
            alarm_text=str(getattr(source, "alarm_text", "") or ""),
            long38_raw=(
                getattr(source, "six_long38", None)
                if getattr(source, "six_long38", None) is not None
                else getattr(source, "long38_raw", None)
            ),
            estop=bool(getattr(source, "estop_active", False)),
            paused=bool(getattr(source, "pause_active", False)),
            realtime_feedback=self._realtime_feedback(source),
            controller=self._controller_state(source),
        )
        self.last_sample = AlarmMonitorSample(detection=detection, interval_ms=self.interval_ms)
        return self.last_sample

    @staticmethod
    def _realtime_feedback(source: Any) -> str:
        value = getattr(source, "realtime_feedback", None)
        if value:
            return str(value)
        monitor_label = getattr(source, "monitor_label", None)
        monitor_text = monitor_label.text() if monitor_label is not None and hasattr(monitor_label, "text") else ""
        return "online" if monitor_text == "实时监控运行中" else "offline"

    @staticmethod
    def _controller_state(source: Any) -> str:
        value = getattr(source, "controller_state", None)
        if value:
            return str(value)
        return "online" if AlarmMonitor._realtime_feedback(source) == "online" else "unknown"
