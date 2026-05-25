"""Append V2.1 type-B interaction records to JSONL archives."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .dialog_logger import DialogLogger
from .json_schema import InteractionRecord


class InteractionArchiveWriter:
    """Writes standard interaction records without replacing the GUI log."""

    def __init__(
        self,
        *,
        path: Path,
        session_id: str,
        clock: Callable[[], str] | None = None,
        dialog_logger: DialogLogger | None = None,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.clock = clock or self._now_iso
        self.dialog_logger = dialog_logger

    def append_input_record(
        self,
        *,
        source: str,
        raw_text: str,
        device_snapshot: dict[str, Any] | None = None,
        asr_confidence: float | None = None,
        scene_state: dict[str, Any] | None = None,
    ) -> InteractionRecord:
        timestamp = self.clock()
        input_payload = {"source": source, "raw_text": raw_text, "asr_confidence": asr_confidence}
        if scene_state is not None:
            input_payload["scene_state"] = dict(scene_state)
        record = InteractionRecord(
            msg_id=f"interaction-{uuid.uuid4().hex}",
            session_id=self.session_id,
            timestamp_start=timestamp,
            timestamp_end=timestamp,
            duration_ms=0,
            input=input_payload,
            nlp_result={
                "semantic_level": 0,
                "intent": "pending",
                "func_id": None,
                "params": {},
                "confidence": 0.0,
                "engine": "pending",
            },
            safety_check={
                "pc_precheck": "pending",
                "pc_precheck_detail": {},
                "controller_check": "pending",
                "controller_check_func": None,
                "warnings": [],
            },
            execution={
                "modbus_write": {},
                "state_before": {},
                "state_after": {},
                "result": "pending",
                "exec_duration_ms": 0,
            },
            response={"ack": "", "ack_delay_ms": 0, "final": "", "final_delay_ms": 0},
            device_snapshot=dict(device_snapshot or {}),
        )
        self._append(record.to_dict())
        if self.dialog_logger is not None:
            self.dialog_logger.append(
                role="user",
                text=raw_text,
                result="received",
                extra={"source": source, "session_id": self.session_id, "msg_id": record.msg_id},
            )
        return record

    def update_nlp_result(self, msg_id: str, nlp_result: dict[str, Any]) -> bool:
        return self.update_record(msg_id, {"nlp_result": dict(nlp_result)})

    def update_record(self, msg_id: str, updates: dict[str, Any]) -> bool:
        if not self.path.exists():
            return False
        changed = False
        lines: list[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(payload, dict) and payload.get("msg_id") == msg_id:
                payload.update(updates)
                changed = True
            lines.append(json.dumps(payload, ensure_ascii=False, default=str) if isinstance(payload, dict) else line)
        if changed:
            self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return changed

    def _append(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="milliseconds")
