from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AgentContextBuilder:
    max_total_chars: int = 2400
    dialogue_limit: int = 8
    message_max_chars: int = 180
    confirm_text_max_chars: int = 700

    def build_text(
        self,
        *,
        current_scene: str = "",
        pending_confirm_plan: Any = None,
        pending_flow_draft: dict[str, Any] | None = None,
        recent_messages: Iterable[tuple[str, str]] = (),
        current_flow_text: str = "",
        last_execution_result: str = "",
        last_execution_state: str = "",
        position_lines: Iterable[str] = (),
        device_status: str = "",
    ) -> str:
        parts: list[str] = []
        scene = self._clean(current_scene)
        if scene:
            parts.append(f"当前页面：{scene}")

        confirm_text = self._pending_confirm_context(pending_confirm_plan)
        if confirm_text:
            parts.append(confirm_text)

        flow_text = self._pending_flow_context(pending_flow_draft)
        if flow_text:
            parts.append(flow_text)

        dialogue_text = self._recent_dialogue_context(recent_messages)
        if dialogue_text:
            parts.append(dialogue_text)

        for title, value in (
            ("当前流程", current_flow_text),
            ("上次执行", last_execution_result),
            ("执行状态", last_execution_state),
            ("设备状态", device_status),
        ):
            clean = self._clean(value)
            if clean:
                parts.append(f"{title}：{clean}")

        positions = [self._clean(line) for line in position_lines if self._clean(line)]
        if positions:
            parts.append("位置库：" + "；".join(positions[:8]))

        return self._limit_total("\n".join(part for part in parts if part).strip())

    def _pending_confirm_context(self, plan: Any) -> str:
        if plan is None:
            return ""
        draft = getattr(plan, "flow_draft", None)
        if not isinstance(draft, dict):
            return ""
        func_id = draft.get("func_id") or draft.get("func_num")
        title = f"待确认指令：Func{func_id}" if func_id is not None else "待确认指令"
        text = self._clean(draft.get("confirmation_text") or draft.get("summary") or "")
        if text:
            return f"{title}\n{self._limit(text, self.confirm_text_max_chars)}"
        params = draft.get("params")
        if isinstance(params, dict) and params:
            param_text = "，".join(f"{key}={value}" for key, value in list(params.items())[:12])
            return f"{title}\n参数：{param_text}"
        return title

    def _pending_flow_context(self, draft: dict[str, Any] | None) -> str:
        if not isinstance(draft, dict) or not draft:
            return ""
        name = self._clean(draft.get("flow_name") or draft.get("flowName") or "未命名流程")
        steps = draft.get("expanded_steps")
        step_items = steps if isinstance(steps, list) else []
        status = "待重新预检" if draft.get("needs_precheck") else "等待确认"
        lines = [f"待确认流程草案：{name}，共 {len(step_items)} 步，状态：{status}。"]
        for index, step in enumerate(step_items[:6], start=1):
            if not isinstance(step, dict):
                continue
            desc = self._clean(step.get("description") or step.get("action") or "")
            func_id = step.get("func_id") or step.get("func_num")
            if desc or func_id:
                lines.append(f"{index}. {desc or '步骤'}" + (f" Func{func_id}" if func_id else ""))
        return "\n".join(lines)

    def _recent_dialogue_context(self, messages: Iterable[tuple[str, str]]) -> str:
        items = list(messages or ())[-max(1, self.dialogue_limit) :]
        lines: list[str] = []
        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            role = self._clean(item[0]).lower()
            text = self._clean(item[1])
            if not text:
                continue
            label = "用户" if role == "user" else "AI" if role == "assistant" else role or "消息"
            lines.append(f"{label}：{self._limit(text, self.message_max_chars)}")
        if not lines:
            return ""
        return "最近对话：\n" + "\n".join(lines)

    @staticmethod
    def _clean(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _limit(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 3)].rstrip() + "..."

    def _limit_total(self, text: str) -> str:
        return self._limit(text, self.max_total_chars)
