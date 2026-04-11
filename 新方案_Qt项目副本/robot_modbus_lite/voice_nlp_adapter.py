from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .command_parser import CommandParseError, parse_command
from .deepseek_client import DeepSeekClient
from .models import QueryRecord


SYSTEM_ACTION_ALIASES = {
    "上电": "power_on",
    "开机": "power_on",
    "启动": "auto_start",
    "开始": "auto_start",
    "停机": "auto_stop",
    "停止": "auto_stop",
    "暂停": "sys_pause",
    "继续": "sys_resume",
    "恢复": "sys_resume",
    "急停": "sys_estop",
}


@dataclass(frozen=True)
class VoiceNlpAction:
    action_type: str
    target: str | None
    source: str
    raw_text: str
    reason: str

    def to_preview_dict(self) -> dict[str, str]:
        return {
            "actionType": self.action_type,
            "target": self.target or "",
            "source": self.source,
            "rawText": self.raw_text,
            "reason": self.reason,
        }


class VoiceNlpAdapter:
    def __init__(self, table: dict[str, QueryRecord], flow_names: Iterable[str]) -> None:
        self.table = table
        self.flow_names = tuple(sorted(str(name) for name in flow_names))

    def parse(self, text: str, *, use_deepseek: bool = False) -> VoiceNlpAction:
        normalized = text.strip()
        if not normalized:
            return VoiceNlpAction("unknown", None, "rule", text, "输入为空")

        if use_deepseek:
            deepseek_result = self._parse_with_deepseek(normalized)
            if deepseek_result is not None:
                return deepseek_result

        return self._parse_with_rules(normalized)

    def _parse_with_rules(self, text: str) -> VoiceNlpAction:
        system_action = self._match_system_action(text)
        if system_action:
            return VoiceNlpAction("system", system_action, "rule", text, "命中系统动作规则")

        flow_name = self._match_flow_name(text)
        if flow_name:
            return VoiceNlpAction("flow", flow_name, "rule", text, "命中流程规则")

        try:
            parsed = parse_command(text, self.table)
            return VoiceNlpAction("template", parsed.query_key, "rule", text, "命中模板规则")
        except CommandParseError:
            return VoiceNlpAction("unknown", None, "rule", text, "未命中模板、流程或系统动作")

    def _parse_with_deepseek(self, text: str) -> VoiceNlpAction | None:
        try:
            client = DeepSeekClient()
            prompt = self._build_deepseek_prompt(text)
            payload = client.parse_json(prompt, model="deepseek-chat")
            if not payload:
                return None
            action_type = str(payload.get("actionType", "")).strip().lower()
            target = str(payload.get("target", "")).strip() or None
            reason = str(payload.get("reason", "")).strip() or "DeepSeek解析"
            if action_type == "system" and target in SYSTEM_ACTION_ALIASES.values():
                return VoiceNlpAction("system", target, "deepseek", text, reason)
            if action_type == "flow" and target in self.flow_names:
                return VoiceNlpAction("flow", target, "deepseek", text, reason)
            if action_type == "template" and target in self.table:
                return VoiceNlpAction("template", target, "deepseek", text, reason)
            return None
        except Exception:
            return None

    def _build_deepseek_prompt(self, text: str) -> str:
        template_names = "、".join(sorted(self.table))
        flow_names = "、".join(self.flow_names) or "无"
        system_names = "power_on、auto_start、auto_stop、sys_pause、sys_resume、sys_estop"
        return (
            "你负责把自然语言归类到现有 Qt 控制系统动作。\n"
            "只允许返回以下 JSON：\n"
            '{'
            '"actionType":"template|flow|system|unknown",'
            '"target":"目标名称",'
            '"reason":"一句原因"'
            '}\n'
            f"可用模板: {template_names}\n"
            f"可用流程: {flow_names}\n"
            f"系统动作目标: {system_names}\n"
            "如果无法确定，返回 actionType=unknown。\n"
            f"用户输入: {text}"
        )

    def _match_system_action(self, text: str) -> str | None:
        compact = text.replace(" ", "")
        for keyword, action_key in SYSTEM_ACTION_ALIASES.items():
            if keyword in compact:
                return action_key
        return None

    def _match_flow_name(self, text: str) -> str | None:
        compact = text.strip()
        match = re.search(r"(执行|开始|运行)?流程[:：]?\s*(.+)", compact)
        if match:
            candidate = match.group(2).strip()
            if candidate in self.flow_names:
                return candidate
        for flow_name in self.flow_names:
            if flow_name and flow_name in compact:
                return flow_name
        return None
