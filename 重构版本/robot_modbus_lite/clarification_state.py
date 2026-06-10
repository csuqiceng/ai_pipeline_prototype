"""Clarification state for dialog-driven execution plans."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from .execution_plan import ExecutionPlan, ExecutionPlanStatus, ExecutionStep


@dataclass(frozen=True)
class PendingClarification:
    clarification_id: str
    plan_id: str
    step_id: int | None
    missing_field: str
    question: str
    accepted_answer_types: tuple[str, ...]
    attempts: int
    expires_at: float

    @classmethod
    def new(
        cls,
        plan_id: str,
        step_id: int | None,
        missing_field: str,
        question: str,
        accepted_answer_types: tuple[str, ...],
        *,
        now: float,
        timeout_sec: float = 60.0,
    ) -> "PendingClarification":
        return cls(
            clarification_id=uuid4().hex,
            plan_id=plan_id,
            step_id=step_id,
            missing_field=missing_field,
            question=question,
            accepted_answer_types=tuple(accepted_answer_types),
            attempts=0,
            expires_at=now + timeout_sec,
        )


@dataclass(frozen=True)
class ClarificationResult:
    applied: bool
    plan: ExecutionPlan
    message: str
    clarification: PendingClarification | None = None


class ClarificationManager:
    """Owns pending clarification questions outside NLP adapters."""

    def __init__(self, *, default_timeout_sec: float = 60.0) -> None:
        self.default_timeout_sec = float(default_timeout_sec)
        self._pending: dict[str, list[PendingClarification]] = {}

    def add_missing_fields(self, plan: ExecutionPlan, fields: list[PendingClarification]) -> None:
        if not fields:
            return
        queue = self._pending.setdefault(plan.plan_id, [])
        existing = {(item.step_id, item.missing_field) for item in queue}
        for field in fields:
            key = (field.step_id, field.missing_field)
            if key in existing:
                continue
            queue.append(field)
            existing.add(key)

    def current_question(self, plan_id: str) -> PendingClarification | None:
        queue = self._pending.get(plan_id) or []
        return queue[0] if queue else None

    def apply_answer(self, plan: ExecutionPlan, text: str) -> ClarificationResult:
        clarification = self.current_question(plan.plan_id)
        if clarification is None:
            return ClarificationResult(False, plan, "当前没有待回答的追问。", None)

        if clarification.missing_field in {"target_pose", "target", "pose"}:
            parsed = self._parse_pose_answer(text)
            if parsed is None:
                return ClarificationResult(False, plan, "无法识别目标坐标，请按 X,Y,Z,RX,RY,RZ 回答。", clarification)
            updated = self._update_step_params(
                plan,
                clarification.step_id,
                {
                    "target_x": parsed[0],
                    "target_y": parsed[1],
                    "target_z": parsed[2],
                    "target_rx": parsed[3],
                    "target_ry": parsed[4],
                    "target_rz": parsed[5],
                    "fuzzy_pos": 0,
                },
            )
            self._pop_current(plan.plan_id)
            final_plan = self._status_after_success(updated)
            step_label = f"第{clarification.step_id}步" if clarification.step_id is not None else "当前步骤"
            return ClarificationResult(True, final_plan, f"已补齐{step_label}目标坐标。", clarification)

        if clarification.missing_field in {"speed", "spd_pct"}:
            speed = self._parse_speed_answer(text)
            if speed is None:
                return ClarificationResult(False, plan, "无法识别速度，请按 30% 这类格式回答。", clarification)
            updated = self._update_step_params(
                plan,
                clarification.step_id,
                {"spd_pct": speed, "acc_pct": speed, "dec_pct": speed},
            )
            self._pop_current(plan.plan_id)
            final_plan = self._status_after_success(updated)
            step_label = f"第{clarification.step_id}步" if clarification.step_id is not None else "当前步骤"
            return ClarificationResult(True, final_plan, f"已补齐{step_label}速度为 {speed:g}%。", clarification)

        if clarification.missing_field in {"delay_sec", "delay"}:
            delay_sec = self._parse_duration_answer(text)
            if delay_sec is None:
                return ClarificationResult(False, plan, "无法识别延时时间，请按 2秒 或 500毫秒 这类格式回答。", clarification)
            updated = self._update_step_params(plan, clarification.step_id, {"delay_sec": delay_sec})
            self._pop_current(plan.plan_id)
            final_plan = self._status_after_success(updated)
            step_label = f"第{clarification.step_id}步" if clarification.step_id is not None else "当前步骤"
            return ClarificationResult(True, final_plan, f"已补齐{step_label}延时为 {delay_sec:g} 秒。", clarification)

        if clarification.missing_field == "io_no":
            io_no = self._parse_io_no_answer(text)
            if io_no is None:
                return ClarificationResult(False, plan, "无法识别 IO 编号，请按 IO3 或 3 这类格式回答。", clarification)
            updated = self._update_step_params(plan, clarification.step_id, {"io_no": io_no})
            self._pop_current(plan.plan_id)
            final_plan = self._status_after_success(updated)
            step_label = f"第{clarification.step_id}步" if clarification.step_id is not None else "当前步骤"
            return ClarificationResult(True, final_plan, f"已补齐{step_label}IO编号为 {io_no}。", clarification)

        if clarification.missing_field == "io_action":
            action = self._parse_io_action_answer(text)
            if action is None:
                return ClarificationResult(False, plan, "无法识别 IO 动作，请回答打开或关闭。", clarification)
            updated = self._update_step_params(plan, clarification.step_id, {"io_action": action})
            self._pop_current(plan.plan_id)
            final_plan = self._status_after_success(updated)
            step_label = f"第{clarification.step_id}步" if clarification.step_id is not None else "当前步骤"
            label = "打开" if action == 1 else "关闭"
            return ClarificationResult(True, final_plan, f"已补齐{step_label}IO动作为{label}。", clarification)

        return ClarificationResult(False, plan, f"暂不支持回填参数：{clarification.missing_field}。", clarification)

    def expire(self, *, now: float) -> list[str]:
        expired_plan_ids: list[str] = []
        for plan_id, queue in list(self._pending.items()):
            remaining = [item for item in queue if item.expires_at > now]
            if len(remaining) != len(queue):
                expired_plan_ids.append(plan_id)
            if remaining:
                self._pending[plan_id] = remaining
            else:
                self._pending.pop(plan_id, None)
        return expired_plan_ids

    def clear(self, plan_id: str) -> None:
        self._pending.pop(plan_id, None)

    def _pop_current(self, plan_id: str) -> None:
        queue = self._pending.get(plan_id) or []
        if not queue:
            return
        remaining = queue[1:]
        if remaining:
            self._pending[plan_id] = remaining
        else:
            self._pending.pop(plan_id, None)

    def _status_after_success(self, plan: ExecutionPlan) -> ExecutionPlan:
        if self.current_question(plan.plan_id) is not None:
            if plan.status == ExecutionPlanStatus.NEED_CLARIFICATION:
                return plan
            return replace(plan, status=ExecutionPlanStatus.NEED_CLARIFICATION)
        if plan.status == ExecutionPlanStatus.NEED_CLARIFICATION:
            return plan.transition_to(ExecutionPlanStatus.MODIFIED)
        return replace(plan, status=ExecutionPlanStatus.MODIFIED)

    @staticmethod
    def _update_step_params(plan: ExecutionPlan, step_id: int | None, params: dict[str, Any]) -> ExecutionPlan:
        steps: list[ExecutionStep] = []
        target_step_id = step_id if step_id is not None else (plan.steps[0].step_id if plan.steps else None)
        for step in plan.steps:
            if step.step_id == target_step_id:
                steps.append(replace(step, params={**step.params, **params}))
            else:
                steps.append(step)
        return plan.with_steps(steps)

    @staticmethod
    def _parse_pose_answer(text: str) -> tuple[float, float, float, float, float, float] | None:
        labelled: dict[str, float] = {}
        for match in re.finditer(
            r"(RX|RY|RZ|X|Y|Z)\s*=?\s*(-?\d+(?:\.\d+)?)",
            text or "",
            flags=re.IGNORECASE,
        ):
            labelled[match.group(1).lower()] = float(match.group(2))
        if {"x", "y", "z"}.issubset(labelled):
            return (
                labelled["x"],
                labelled["y"],
                labelled["z"],
                labelled.get("rx", 0.0),
                labelled.get("ry", 0.0),
                labelled.get("rz", 0.0),
            )
        values = re.findall(r"-?\d+(?:\.\d+)?", text or "")
        if len(values) < 6:
            return None
        try:
            return tuple(float(value) for value in values[:6])  # type: ignore[return-value]
        except ValueError:
            return None

    @staticmethod
    def _parse_speed_answer(text: str) -> float | None:
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text or "")
        if not match:
            return None
        value = float(match.group(1))
        if value < 5.0 or value > 150.0:
            return None
        return value

    @staticmethod
    def _parse_duration_answer(text: str) -> float | None:
        normalized = _normalize_chinese_duration_numbers(text or "")
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(毫秒|ms|秒|s)", normalized, flags=re.IGNORECASE)
        if not match:
            return None
        value = float(match.group(1))
        if value <= 0.0:
            return None
        unit = match.group(2).lower()
        if unit in {"毫秒", "ms"}:
            value /= 1000.0
        return value

    @staticmethod
    def _parse_io_no_answer(text: str) -> int | None:
        match = re.search(r"(?:io|IO|输出|Y|y)?\s*(\d+)", text or "")
        if not match:
            return None
        value = int(match.group(1))
        if value < 0 or value > 11:
            return None
        return value

    @staticmethod
    def _parse_io_action_answer(text: str) -> int | None:
        compact = re.sub(r"\s+", "", text or "").lower()
        if compact in {"开", "打开", "开启", "on", "1"}:
            return 1
        if compact in {"关", "关闭", "off", "0"}:
            return 0
        return None


def _normalize_chinese_duration_numbers(text: str) -> str:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }

    def parse_cn_number(value: str) -> float | None:
        raw = str(value or "")
        if not raw:
            return None
        if "点" in raw:
            left, right = raw.split("点", 1)
            left_value = parse_cn_number(left) if left else 0
            if left_value is None:
                return None
            decimals: list[str] = []
            for char in right:
                if char not in digits:
                    return None
                decimals.append(str(digits[char]))
            return float(f"{int(left_value)}.{''.join(decimals) or '0'}")
        if raw == "十":
            return 10.0
        if "十" in raw:
            left, right = raw.split("十", 1)
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return float(tens * 10 + ones)
        if "百" in raw:
            left, right = raw.split("百", 1)
            hundreds = digits.get(left, 1) if left else 1
            tail = parse_cn_number(right) if right else 0
            if tail is None:
                return None
            return float(hundreds * 100 + tail)
        if raw in digits:
            return float(digits[raw])
        return None

    def replace_match(match: re.Match[str]) -> str:
        value = parse_cn_number(match.group("number"))
        if value is None:
            return match.group(0)
        formatted = f"{value:g}"
        return f"{formatted}{match.group('unit')}"

    return re.sub(
        r"(?P<number>[零〇一二两三四五六七八九十百点]+)\s*(?P<unit>毫秒|秒)",
        replace_match,
        str(text or ""),
    )
