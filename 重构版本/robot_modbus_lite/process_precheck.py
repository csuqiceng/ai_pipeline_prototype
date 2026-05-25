"""L3 flow-level precheck service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import FlowDefinition, QueryRecord


L1Runner = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
L2Runner = Callable[[QueryRecord], dict[str, Any]]
ProgressCallback = Callable[[dict[str, Any]], None]


class ProcessPrecheckService:
    """Runs a conservative flow-level precheck over configured flow steps."""

    def __init__(
        self,
        *,
        l1_runner: L1Runner | None = None,
        l2_runner: L2Runner | None = None,
        progress_callback: ProgressCallback | None = None,
        min_step_delay_ms: int = 0,
        cumulative_error_limit_mm: float | None = None,
    ) -> None:
        self.l1_runner = l1_runner or self._default_l1_runner
        self.l2_runner = l2_runner or self._default_l2_runner
        self.progress_callback = progress_callback
        self.min_step_delay_ms = max(0, int(min_step_delay_ms))
        self.cumulative_error_limit_mm = cumulative_error_limit_mm

    def run_l3(
        self,
        *,
        flow: FlowDefinition,
        table: dict[str, QueryRecord],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        self._publish_stage_progress(flow.name, "start", 0, "准备 L3 流程级预演。")
        self._publish_stage_progress(flow.name, "template_check", 5, "正在检查流程模板完整性。")
        records, step_keys, missing = self._flow_records(flow, table)
        if missing:
            self._publish_stage_progress(flow.name, "complete", 100, "L3 流程级预演未通过，流程模板不完整。")
            return {
                "status": "fail",
                "flow_name": flow.name,
                "progress_percent": 0,
                "items": [
                    self._item(
                        "missing_template",
                        "L3",
                        "流程模板完整性",
                        "fail",
                        f"流程包含不存在的模板: {', '.join(missing)}。",
                    )
                ],
                "suggestion": "请先补齐流程模板后再执行。",
            }

        items: list[dict[str, str]] = []
        failed = False
        total = max(1, len(flow.steps))
        midpoint_suggestions: list[dict[str, Any]] = []
        for index, (step, record) in enumerate(zip(step_keys, records), start=1):
            plan = self._plan_dict(record)
            l1 = self.l1_runner(snapshot, plan)
            l1_status = "fail" if l1.get("status") == "fail" else "pass"
            failed = failed or l1_status == "fail"
            items.append(
                self._item(
                    "step_l1",
                    "L3",
                    f"第{index}步 L1 预检",
                    l1_status,
                    f"{step}: {'未通过' if l1_status == 'fail' else '通过'}。",
                )
            )

            l2 = self.l2_runner(record)
            l2_raw_status = str(l2.get("status", "unavailable"))
            if l2_raw_status == "fail":
                failed = True
                l2_status = "fail"
                message = self._first_l2_message(l2) or f"{step}: L2 运动规划预演未通过。"
            elif l2_raw_status == "unavailable":
                l2_status = "warn"
                message = str(l2.get("suggestion") or f"{step}: L2 运动规划预演不可用。")
            else:
                l2_status = "pass"
                message = f"{step}: 通过。"
            items.append(self._item("step_l2", "L3", f"第{index}步 L2 预演", l2_status, message))
            if l2.get("need_midpoint") and l2.get("midpoint_pose") is not None:
                suggestion = {
                    "step_index": index,
                    "step_key": step,
                    "midpoint_pose": l2.get("midpoint_pose"),
                    "midpoint_fstatus": l2.get("midpoint_fstatus"),
                    "suggestion": str(l2.get("suggestion") or "建议经中点绕行。"),
                }
                midpoint_suggestions.append(suggestion)
                items.append(
                    self._item(
                        "step_midpoint_suggestion",
                        "L3",
                        f"第{index}步流程级中点建议",
                        "warn",
                        f"{step}: 建议中点绕行，midpoint={suggestion['midpoint_pose']}。",
                    )
                )
            self._publish_progress(flow.name, index, total, step)

        self._publish_stage_progress(flow.name, "timing_check", 80, "正在检查流程步间隔。")
        timing = self._timing_summary(flow)
        self._publish_stage_progress(flow.name, "error_budget", 88, "正在评估流程累计误差。")
        error_budget = self._error_budget(records)
        self._publish_stage_progress(flow.name, "interference_check", 95, "正在检查简化禁入包围盒干涉。")
        aggregate_items = [
            *self._timing_items(flow, timing),
            *self._cumulative_error_items(error_budget),
            *self._interference_items(records, snapshot),
        ]
        failed = failed or any(item["status"] == "fail" for item in aggregate_items)
        items.extend(aggregate_items)
        self._publish_stage_progress(
            flow.name,
            "complete",
            100,
            "L3 流程级预演未通过，请修复失败步骤后再执行。"
            if failed
            else "L3 流程级预演通过。",
        )

        final_suggestion = "请修复失败步骤后再执行流程。" if failed else None
        if midpoint_suggestions:
            joined = "；".join(
                f"第{item['step_index']}步 {item['step_key']} 建议中点绕行"
                for item in midpoint_suggestions[:5]
            )
            final_suggestion = f"{joined}。"

        return {
            "status": "fail" if failed else "pass",
            "flow_name": flow.name,
            "progress_percent": 100,
            "items": items,
            "timing": timing,
            "error_budget": error_budget,
            "midpoint_suggestions": midpoint_suggestions,
            "suggestion": final_suggestion,
        }

    @staticmethod
    def _step_key(step: Any) -> str:
        if isinstance(step, str):
            return step
        params = getattr(step, "params", None)
        if isinstance(params, dict) and params.get("query_key"):
            return str(params["query_key"])
        for attr in ("query_key", "description", "action"):
            value = getattr(step, attr, None)
            if value:
                return str(value)
        return str(step)

    @classmethod
    def _flow_records(
        cls,
        flow: Any,
        table: dict[str, QueryRecord],
    ) -> tuple[list[QueryRecord], list[str], list[str]]:
        records: list[QueryRecord] = []
        step_keys: list[str] = []
        missing: list[str] = []
        flow_name = str(getattr(flow, "name", "flow") or "flow")
        for index, step in enumerate(getattr(flow, "steps", []) or [], start=1):
            record = cls._record_from_step(step, table, flow_name=flow_name, index=index)
            step_key = record.query_key if record is not None else cls._step_key(step)
            step_keys.append(step_key)
            if record is None:
                missing.append(step_key)
            else:
                records.append(record)
        return records, step_keys, missing

    @classmethod
    def _record_from_step(
        cls,
        step: Any,
        table: dict[str, QueryRecord],
        *,
        flow_name: str,
        index: int,
    ) -> QueryRecord | None:
        if isinstance(step, str):
            return table.get(step)
        params = getattr(step, "params", None)
        if isinstance(params, dict):
            query_key = str(params.get("query_key") or "").strip()
            if query_key and query_key in table:
                return table[query_key]
            func_id = int(getattr(step, "func_id", 0) or 0)
            if func_id > 0:
                generated_key = query_key or f"flow:{flow_name}:{int(getattr(step, 'step_id', index) or index)}"
                record_params = dict(params)
                record_params.pop("query_key", None)
                return QueryRecord(
                    query_key=generated_key,
                    func_num=func_id,
                    params=record_params,
                    keywords=str(getattr(step, "action", "") or ""),
                    description=str(getattr(step, "description", "") or getattr(step, "action", "") or generated_key),
                    safety_level=5,
                )
        step_key = cls._step_key(step)
        return table.get(step_key)

    @staticmethod
    def _plan_dict(record: QueryRecord) -> dict[str, Any]:
        plan: dict[str, Any] = {"plan_id": record.query_key}
        pose = record.pose_tuple()
        if pose is not None:
            plan["target"] = {"x": pose[0], "y": pose[1], "z": pose[2]}
        plan["speed"] = {
            "spd_pct": record.spd_pct_value(),
            "acc_pct": record.acc_pct_value(),
            "dec_pct": record.dec_pct_value(),
        }
        return plan

    @staticmethod
    def _first_l2_message(result: dict[str, Any]) -> str:
        for item in result.get("items", []) or []:
            if isinstance(item, dict) and item.get("message"):
                return str(item["message"])
        return ""

    @staticmethod
    def _timing_summary(flow: FlowDefinition) -> dict[str, int]:
        transition_count = max(0, len(flow.steps) - 1)
        step_delay_ms = max(0, int(flow.step_delay_ms))
        return {
            "step_count": len(flow.steps),
            "transition_count": transition_count,
            "step_delay_ms": step_delay_ms,
            "estimated_total_delay_ms": transition_count * step_delay_ms,
        }

    def _timing_items(self, flow: FlowDefinition, timing: dict[str, int]) -> list[dict[str, str]]:
        if self.min_step_delay_ms <= 0 or timing["transition_count"] <= 0:
            return []
        passed = timing["step_delay_ms"] >= self.min_step_delay_ms
        return [
            self._item(
                "timing_state",
                "L3",
                "流程步间隔满足状态切换",
                "pass" if passed else "fail",
                f"流程步间隔 {timing['step_delay_ms']}ms 满足最小 {self.min_step_delay_ms}ms。",
            )
            if passed
            else self._item(
                "timing_state",
                "L3",
                "流程步间隔满足状态切换",
                "fail",
                f"流程步间隔 {timing['step_delay_ms']}ms 小于最小 {self.min_step_delay_ms}ms，可能造成状态未稳定即进入下一步。",
            )
        ]

    def _error_budget(self, records: list[QueryRecord]) -> dict[str, float | None]:
        total = 0.0
        for record in records:
            total += self._float_param(record, "expected_error_mm", 0.0)
        return {
            "estimated_cumulative_error_mm": round(total, 3),
            "limit_mm": None if self.cumulative_error_limit_mm is None else float(self.cumulative_error_limit_mm),
        }

    def _cumulative_error_items(self, error_budget: dict[str, float | None]) -> list[dict[str, str]]:
        limit = error_budget["limit_mm"]
        if limit is None:
            return []
        total = float(error_budget["estimated_cumulative_error_mm"] or 0.0)
        passed = total <= float(limit)
        if passed:
            return [
                self._item(
                    "cumulative_error",
                    "L3",
                    "累计误差在预算内",
                    "pass",
                    f"预计累计误差 {total:.3f}mm 未超过 {float(limit):.3f}mm。",
                )
            ]
        return [
            self._item(
                "cumulative_error",
                "L3",
                "累计误差在预算内",
                "fail",
                f"预计累计误差 {total:.3f}mm 超过 {float(limit):.3f}mm。",
            )
        ]

    def _interference_items(self, records: list[QueryRecord], snapshot: dict[str, Any]) -> list[dict[str, str]]:
        boxes = self._forbidden_boxes(snapshot)
        if not boxes:
            return []
        items: list[dict[str, str]] = []
        for record in records:
            pose = record.pose_tuple()
            if pose is None:
                continue
            x, y, z, *_ = pose
            for box in boxes:
                if self._point_in_box((x, y, z), box):
                    box_id = str(box.get("id") or box.get("name") or "forbidden_box")
                    items.append(
                        self._item(
                            "interference_box",
                            "L3",
                            "目标点避开禁入包围盒",
                            "fail",
                            f"{record.query_key}: 目标点进入禁入包围盒 {box_id}。",
                        )
                    )
        return items

    @staticmethod
    def _forbidden_boxes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        workspace = snapshot.get("workspace", {}) if isinstance(snapshot, dict) else {}
        boxes = workspace.get("forbidden_boxes", []) if isinstance(workspace, dict) else []
        return [box for box in boxes if isinstance(box, dict)]

    @classmethod
    def _point_in_box(cls, point: tuple[float, float, float], box: dict[str, Any]) -> bool:
        return all(cls._value_in_range(value, box.get(axis)) for value, axis in zip(point, ("x", "y", "z")))

    @staticmethod
    def _value_in_range(value: float, range_value: Any) -> bool:
        if isinstance(range_value, dict):
            low = range_value.get("min")
            high = range_value.get("max")
        elif isinstance(range_value, (list, tuple)) and len(range_value) >= 2:
            low = range_value[0]
            high = range_value[1]
        else:
            return False
        try:
            low_float = float(low)
            high_float = float(high)
        except (TypeError, ValueError):
            return False
        return min(low_float, high_float) <= float(value) <= max(low_float, high_float)

    @staticmethod
    def _float_param(record: QueryRecord, key: str, default: float = 0.0) -> float:
        try:
            return float(record.params.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _default_l1_runner(_snapshot: dict[str, Any], _plan: dict[str, Any]) -> dict[str, Any]:
        return {"status": "unavailable", "items": [], "suggestion": "未配置 L1 预检服务。"}

    @staticmethod
    def _default_l2_runner(_record: QueryRecord) -> dict[str, Any]:
        return {"status": "unavailable", "items": [], "suggestion": "未配置 L2 运动规划服务。"}

    def _publish_progress(self, flow_name: str, current_step: int, total_steps: int, step_key: str) -> None:
        if self.progress_callback is None:
            return
        percent = 5 + int(current_step / max(1, total_steps) * 65 + 0.5)
        try:
            self.progress_callback(
                {
                    "stage": "step_complete",
                    "flow_name": flow_name,
                    "current_step": int(current_step),
                    "total_steps": int(total_steps),
                    "step_key": step_key,
                    "percent": percent,
                }
            )
        except Exception:
            return

    def _publish_stage_progress(self, flow_name: str, stage: str, percent: int, message: str) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(
                {
                    "stage": stage,
                    "flow_name": flow_name,
                    "current_step": 0,
                    "total_steps": 0,
                    "step_key": "-",
                    "percent": int(percent),
                    "message": message,
                }
            )
        except Exception:
            return

    @staticmethod
    def _item(item_id: str, level: str, label: str, status: str, message: str) -> dict[str, str]:
        return {"id": item_id, "level": level, "label": label, "status": status, "message": message}
