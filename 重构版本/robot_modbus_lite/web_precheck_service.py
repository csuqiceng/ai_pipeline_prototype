"""Safety precheck service used by the Web API."""

from __future__ import annotations

from typing import Any

from .runtime_paths import resolve_runtime_data_file
from .system_config import load_system_config


class WebPrecheckService:
    """Runs controller-independent safety checks before a plan can execute."""

    def run_l1(self, snapshot: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan_id = str(plan.get("plan_id")) if plan else "adhoc"
        config = load_system_config(resolve_runtime_data_file("system_config.json"))
        safety = snapshot.get("safety", {})
        connection = snapshot.get("connection", {})
        motion = snapshot.get("motion", {})
        position = snapshot.get("position", {}).get("cartesian", {})

        active_plan_id = motion.get("active_plan_id")
        running_state = motion.get("running_state")
        channel_ok = running_state in {"idle", "waiting_confirm"} and (not active_plan_id or active_plan_id == plan_id)

        items = [
            self._item("estop", "L1", "无紧急停止", not bool(safety.get("estop")), "急停回路正常。", "急停已触发。"),
            self._item("alarm", "L1", "无活动报警", not bool(safety.get("alarm_active")), "当前没有活动报警。", "当前存在活动报警。"),
            self._item("paused", "L1", "未处于暂停状态", not bool(safety.get("paused")), "系统未暂停。", "系统处于暂停状态。"),
            self._item(
                "controller",
                "L1",
                "控制器在线",
                connection.get("controller") == "online",
                "控制器连接正常。",
                "控制器未在线。",
            ),
            self._item(
                "realtime_feedback",
                "L1",
                "实时反馈在线",
                connection.get("realtime_feedback") == "online",
                "实时反馈正常。",
                "实时反馈未在线。",
            ),
            self._item("channel_idle", "L1", "执行通道可用", channel_ok, "当前通道可接收计划。", "当前已有其他任务占用执行通道。"),
        ]

        r_value = float(position.get("r", 0.0))
        z_value = float(position.get("z", 0.0))
        if config.safe_r_max > 0:
            items.append(
                self._item(
                    "current_r_range",
                    "L1",
                    "当前 R 在安全范围内",
                    config.safe_r_min <= r_value <= config.safe_r_max,
                    "当前 R 未越界。",
                    f"当前 R={r_value:.1f}mm 超出安全范围。",
                )
            )
        if config.safe_z_max > 0:
            items.append(
                self._item(
                    "current_z_range",
                    "L1",
                    "当前 Z 在安全范围内",
                    config.safe_z_min <= z_value <= config.safe_z_max,
                    "当前 Z 未越界。",
                    f"当前 Z={z_value:.1f}mm 超出安全范围。",
                )
            )

        status = "pass" if all(item["status"] == "pass" for item in items) else "fail"
        return {
            "plan_id": plan_id,
            "status": status,
            "items": items,
            "suggestion": None if status == "pass" else "请处理失败项后再执行计划。",
        }

    def _item(
        self,
        item_id: str,
        level: str,
        label: str,
        passed: bool,
        pass_message: str,
        fail_message: str,
    ) -> dict[str, str]:
        return {
            "id": item_id,
            "level": level,
            "label": label,
            "status": "pass" if passed else "fail",
            "message": pass_message if passed else fail_message,
        }
