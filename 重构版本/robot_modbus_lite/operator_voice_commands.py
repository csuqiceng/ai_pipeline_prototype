"""Voice-equivalent command table for the Qt operator page."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


OperatorVoiceAction = Literal[
    "execute_text",
    "record",
    "clear_text",
    "estop",
    "pause",
    "resume",
    "stop_current",
    "show_full_status",
    "go_home",
    "enter_fullscreen",
    "exit_fullscreen",
    "compact_window",
    "restore_window",
    "tts_on",
    "tts_off",
    "confirm_execute",
    "accept_suggestion",
    "cancel_confirm",
    "ack_alarm",
    "alarm_reset",
]


@dataclass(frozen=True)
class OperatorVoiceCommandSpec:
    button_label: str
    action: OperatorVoiceAction
    aliases: tuple[str, ...]
    requires_emergency_code: bool = False


OPERATOR_BUTTON_VOICE_COMMANDS: tuple[OperatorVoiceCommandSpec, ...] = (
    OperatorVoiceCommandSpec(
        "发送",
        "execute_text",
        ("发送当前指令", "发送指令", "发送输入", "执行当前指令", "执行指令", "执行输入"),
    ),
    OperatorVoiceCommandSpec("录音", "record", ("开启会话", "打开会话", "结束会话", "关闭会话", "开始录音", "打开录音", "录音")),
    OperatorVoiceCommandSpec("清空", "clear_text", ("清空输入", "清空文本", "清空指令")),
    OperatorVoiceCommandSpec("急停", "estop", ("急停 授权码 急停",), requires_emergency_code=True),
    OperatorVoiceCommandSpec("暂停", "pause", ("暂停",)),
    OperatorVoiceCommandSpec("继续", "resume", ("继续", "恢复")),
    OperatorVoiceCommandSpec(
        "停止当前",
        "stop_current",
        ("停止流程", "停止当前流程", "停止当前任务", "停止当前动作", "取消当前任务", "取消当前动作", "结束当前动作"),
    ),
    OperatorVoiceCommandSpec("完整状态", "show_full_status", ("显示完整状态", "完整状态", "状态看板", "七类看板")),
    OperatorVoiceCommandSpec("主界面", "go_home", ("回到主界面", "返回主界面", "主界面", "待机画面")),
    OperatorVoiceCommandSpec("回到主界面", "go_home", ("回到主界面", "返回主界面", "主界面", "待机画面")),
    OperatorVoiceCommandSpec("全屏", "enter_fullscreen", ("全屏", "放大界面")),
    OperatorVoiceCommandSpec("全屏", "enter_fullscreen", ("进入全屏",)),
    OperatorVoiceCommandSpec("退出全屏", "exit_fullscreen", ("退出全屏", "恢复窗口", "普通窗口")),
    OperatorVoiceCommandSpec("小窗口", "compact_window", ("小窗口", "缩小界面")),
    OperatorVoiceCommandSpec("恢复窗口", "exit_fullscreen", ("恢复窗口", "普通窗口")),
    OperatorVoiceCommandSpec("语音播报", "tts_on", ("开启语音播报", "打开语音播报", "启用语音播报")),
    OperatorVoiceCommandSpec("语音播报", "tts_off", ("关闭语音播报", "停止语音播报", "禁用语音播报")),
    OperatorVoiceCommandSpec("确认执行", "confirm_execute", ("确认执行", "确认", "执行确认")),
    OperatorVoiceCommandSpec("采纳建议", "accept_suggestion", ("采纳建议", "采用建议", "接受建议")),
    OperatorVoiceCommandSpec("取消", "cancel_confirm", ("取消", "取消执行", "取消计划")),
    OperatorVoiceCommandSpec("确认报警", "ack_alarm", ("确认报警", "报警确认", "已查看报警")),
    OperatorVoiceCommandSpec("复位", "alarm_reset", ("复位", "报警复位")),
)


OPERATOR_REQUIRED_BUTTON_LABELS = frozenset(
    {
        "发送",
        "录音",
        "清空",
        "急停",
        "暂停",
        "继续",
        "停止当前",
        "完整状态",
        "主界面",
        "全屏",
        "退出全屏",
        "小窗口",
        "恢复窗口",
        "语音播报",
        "确认执行",
        "采纳建议",
        "取消",
        "确认报警",
        "复位",
        "回到主界面",
    }
)


def button_labels_with_voice_aliases() -> set[str]:
    return {spec.button_label for spec in OPERATOR_BUTTON_VOICE_COMMANDS}


def aliases_for_button(button_label: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for spec in OPERATOR_BUTTON_VOICE_COMMANDS:
        if spec.button_label == button_label:
            aliases.extend(spec.aliases)
    return tuple(aliases)


def missing_required_button_labels() -> list[str]:
    covered = button_labels_with_voice_aliases()
    return sorted(label for label in OPERATOR_REQUIRED_BUTTON_LABELS if label not in covered)


def duplicate_voice_aliases() -> dict[str, tuple[str, ...]]:
    owners: dict[str, list[str]] = {}
    for spec in OPERATOR_BUTTON_VOICE_COMMANDS:
        for alias in spec.aliases:
            owners.setdefault(alias, []).append(spec.action)
    return {alias: tuple(actions) for alias, actions in owners.items() if len(set(actions)) > 1}


def match_operator_voice_command(text: str) -> OperatorVoiceCommandSpec | None:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return None
    for spec in OPERATOR_BUTTON_VOICE_COMMANDS:
        if spec.requires_emergency_code:
            continue
        if compact in {re.sub(r"\s+", "", alias) for alias in spec.aliases}:
            return spec
    for spec in OPERATOR_BUTTON_VOICE_COMMANDS:
        if spec.requires_emergency_code:
            continue
        if any(re.sub(r"\s+", "", alias) in compact for alias in spec.aliases if len(re.sub(r"\s+", "", alias)) >= 3):
            return spec
    return None


def export_operator_voice_command_rows() -> list[dict[str, object]]:
    return [
        {
            "button_label": spec.button_label,
            "action": spec.action,
            "aliases": list(spec.aliases),
            "requires_emergency_code": bool(spec.requires_emergency_code),
        }
        for spec in OPERATOR_BUTTON_VOICE_COMMANDS
    ]


def export_operator_voice_command_markdown() -> str:
    lines = [
        "# 用户页语音等价指令清单",
        "",
        "| 按钮/入口 | 动作 | 语音说法 | 备注 |",
        "|---|---|---|---|",
    ]
    for row in export_operator_voice_command_rows():
        note = "需要三段式应急编码" if row["requires_emergency_code"] else "-"
        aliases = "、".join(str(alias) for alias in row["aliases"])
        lines.append(f"| {row['button_label']} | `{row['action']}` | {aliases} | {note} |")
    lines.append("")
    return "\n".join(lines)
