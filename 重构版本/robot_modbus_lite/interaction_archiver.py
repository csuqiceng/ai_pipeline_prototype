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
        dialogue_path: Path | None = None,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.clock = clock or self._now_iso
        self.dialog_logger = dialog_logger
        self.dialogue_path = dialogue_path

    def append_input_record(
        self,
        *,
        source: str,
        raw_text: str,
        normalized_text: str | None = None,
        device_snapshot: dict[str, Any] | None = None,
        asr_confidence: float | None = None,
        scene_state: dict[str, Any] | None = None,
        input_event: dict[str, Any] | None = None,
    ) -> InteractionRecord:
        timestamp = self.clock()
        input_payload = {
            "source": source,
            "raw_text": raw_text,
            "normalized_text": str(normalized_text if normalized_text is not None else raw_text),
            "asr_confidence": asr_confidence,
        }
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
        self._upsert_dialogue_record(record.to_dict(), input_event=input_event)
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
        updated_payload: dict[str, Any] | None = None
        response_event = updates.get("_dialogue_response_event")
        public_updates = {key: value for key, value in updates.items() if not str(key).startswith("_dialogue_")}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(payload, dict) and payload.get("msg_id") == msg_id:
                payload.update(public_updates)
                changed = True
                updated_payload = dict(payload)
            lines.append(json.dumps(payload, ensure_ascii=False, default=str) if isinstance(payload, dict) else line)
        if changed:
            self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            if updated_payload is not None:
                self._upsert_dialogue_record(
                    updated_payload,
                    response_event=response_event if isinstance(response_event, dict) else None,
                )
        return changed

    def _append(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _upsert_dialogue_record(
        self,
        interaction_payload: dict[str, Any],
        *,
        input_event: dict[str, Any] | None = None,
        response_event: dict[str, Any] | None = None,
    ) -> None:
        if self.dialogue_path is None:
            return
        existing: dict[str, Any] | None = None
        lines: list[str] = []
        if self.dialogue_path.exists():
            for line in self.dialogue_path.read_text(encoding="utf-8").splitlines():
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    lines.append(line)
                    continue
                if isinstance(payload, dict) and payload.get("msg_id") == interaction_payload.get("msg_id"):
                    existing = payload
                    continue
                lines.append(json.dumps(payload, ensure_ascii=False, default=str) if isinstance(payload, dict) else line)
        payload = self._dialogue_payload_from_interaction(
            interaction_payload,
            seq=int((existing or {}).get("seq", len(lines) + 1) or len(lines) + 1),
            input_event=input_event or (existing or {}).get("input_event"),
            response_event=response_event or (existing or {}).get("response_event"),
        )
        lines.append(json.dumps(payload, ensure_ascii=False, default=str))
        self.dialogue_path.parent.mkdir(parents=True, exist_ok=True)
        self.dialogue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _dialogue_payload_from_interaction(
        self,
        interaction_payload: dict[str, Any],
        *,
        seq: int,
        input_event: dict[str, Any] | None,
        response_event: dict[str, Any] | None,
    ) -> dict[str, Any]:
        input_payload = dict(interaction_payload.get("input") or {})
        response = dict(interaction_payload.get("response") or {})
        event = dict(response_event or input_event or {})
        final_text = str(response.get("final", "") or "")
        detail = str(event.get("detail", "") or final_text or input_payload.get("raw_text", "") or "")
        return {
            "msg_type": "dialogue_record",
            "msg_id": interaction_payload.get("msg_id"),
            "session_id": interaction_payload.get("session_id", self.session_id),
            "seq": int(seq),
            "time": str(event.get("time", "") or ""),
            "ts": str(event.get("ts", "") or interaction_payload.get("timestamp_end", "") or interaction_payload.get("timestamp_start", "")),
            "monotonic_ms": event.get("monotonic_ms", 0),
            "host": str(event.get("host", "") or ""),
            "controller_mode": str(event.get("controller_mode", "") or ""),
            "thread": str(event.get("thread", "") or ""),
            "category": str(event.get("category", "自然语言") or "自然语言"),
            "action": str(event.get("action", "") or ""),
            "result": str(event.get("result", "") or ""),
            "detail": detail,
            "timestamp_start": interaction_payload.get("timestamp_start"),
            "timestamp_end": interaction_payload.get("timestamp_end"),
            "duration_ms": int(interaction_payload.get("duration_ms", 0) or 0),
            "user": {
                "source": str(input_payload.get("source", "") or ""),
                "raw_text": str(input_payload.get("raw_text", "") or ""),
                "normalized_text": str(input_payload.get("normalized_text", "") or ""),
                "asr_confidence": input_payload.get("asr_confidence"),
                "scene_state": input_payload.get("scene_state", {}),
            },
            "assistant": {
                "ack": str(response.get("ack", "") or ""),
                "final_text": final_text,
                "event": dict(response_event or {}),
            },
            "input_event": dict(input_event or {}),
            "response_event": dict(response_event or {}),
            "input": input_payload,
            "nlp_result": dict(interaction_payload.get("nlp_result") or {}),
            "safety_check": dict(interaction_payload.get("safety_check") or {}),
            "execution": dict(interaction_payload.get("execution") or {}),
            "response": response,
            "device_snapshot": dict(interaction_payload.get("device_snapshot") or {}),
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="milliseconds")
