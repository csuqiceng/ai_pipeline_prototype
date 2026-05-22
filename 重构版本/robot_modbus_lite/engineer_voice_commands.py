"""Voice-equivalent command table for the Qt engineer pages."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


EngineerVoiceAction = Literal[
    "show_run_page",
    "show_manage_page",
    "show_log_page",
    "alarm_reset",
    "pause",
    "resume",
    "estop",
    "show_license",
    "read_feedback",
    "start_flow",
    "step_flow",
    "stop_flow",
    "reset_flow",
    "refresh_microphones",
    "parse_text",
    "execute_text",
    "record",
    "clear_text",
    "new_template",
    "save_template",
    "clone_template",
    "delete_template",
    "export_template",
    "import_template",
    "add_interp_point",
    "delete_interp_point",
    "move_interp_point_up",
    "move_interp_point_down",
    "show_json_preview",
    "show_system_params",
    "show_safe_points",
    "show_flow_manage",
    "save_system_config",
    "reload_system_config",
    "read_controller_limits",
    "new_safe_point",
    "save_safe_point",
    "delete_safe_point",
    "save_avoidance_config",
    "add_flow_step",
    "remove_flow_step",
    "move_flow_step_up",
    "move_flow_step_down",
    "new_flow",
    "save_flow",
    "delete_flow",
    "refresh_logs",
    "export_logs",
    "clear_logs",
]

DangerLevel = Literal["normal", "confirm", "danger", "emergency"]
ExecutionPolicy = Literal["direct", "confirm", "rejected", "listed_only"]


DIRECT_EXECUTE_ACTIONS = frozenset(
    {
        "show_run_page",
        "show_manage_page",
        "show_log_page",
        "show_json_preview",
        "show_system_params",
        "show_safe_points",
        "show_flow_manage",
        "read_feedback",
        "refresh_microphones",
        "refresh_logs",
    }
)

EXECUTION_POLICY_LABELS: dict[ExecutionPolicy, str] = {
    "direct": "可直接语音执行",
    "confirm": "需二次确认",
    "rejected": "拒绝语音执行",
    "listed_only": "仅清单保留",
}


@dataclass(frozen=True)
class EngineerVoiceCommandSpec:
    section: str
    button_label: str
    action: EngineerVoiceAction
    aliases: tuple[str, ...]
    danger_level: DangerLevel = "normal"


ENGINEER_BUTTON_VOICE_COMMANDS: tuple[EngineerVoiceCommandSpec, ...] = (
    EngineerVoiceCommandSpec("主导航", "运行", "show_run_page", ("切到运行", "打开运行页", "运行页面")),
    EngineerVoiceCommandSpec("主导航", "后台", "show_manage_page", ("切到后台", "打开后台", "后台页面")),
    EngineerVoiceCommandSpec("主导航", "日志", "show_log_page", ("切到日志", "打开日志", "日志页面")),
    EngineerVoiceCommandSpec("系统面板", "报警复位", "alarm_reset", ("报警复位", "复位报警"), "confirm"),
    EngineerVoiceCommandSpec("系统面板", "暂停", "pause", ("暂停系统", "暂停运行"), "confirm"),
    EngineerVoiceCommandSpec("系统面板", "继续", "resume", ("继续系统", "恢复运行"), "confirm"),
    EngineerVoiceCommandSpec("系统面板", "急停", "estop", ("急停 授权码 急停",), "emergency"),
    EngineerVoiceCommandSpec("系统面板", "授权", "show_license", ("打开授权", "授权管理")),
    EngineerVoiceCommandSpec("运行页", "读取反馈", "read_feedback", ("读取反馈", "刷新反馈", "读取控制器反馈")),
    EngineerVoiceCommandSpec("运行页", "开始流程", "start_flow", ("开始流程", "启动流程"), "confirm"),
    EngineerVoiceCommandSpec("运行页", "单步执行", "step_flow", ("单步执行", "执行一步"), "confirm"),
    EngineerVoiceCommandSpec("运行页", "停止流程", "stop_flow", ("停止流程", "停止当前流程"), "confirm"),
    EngineerVoiceCommandSpec("运行页", "重置流程", "reset_flow", ("重置流程", "流程归零"), "confirm"),
    EngineerVoiceCommandSpec("运行页", "刷新设备", "refresh_microphones", ("刷新设备", "刷新麦克风")),
    EngineerVoiceCommandSpec("运行页", "解析文本", "parse_text", ("解析文本", "解析当前文本")),
    EngineerVoiceCommandSpec("运行页", "执行", "execute_text", ("执行文本", "执行当前文本"), "confirm"),
    EngineerVoiceCommandSpec("运行页", "开始录音", "record", ("开始录音", "打开录音")),
    EngineerVoiceCommandSpec("运行页", "清空", "clear_text", ("清空文本", "清空输入")),
    EngineerVoiceCommandSpec("后台操作", "新增", "new_template", ("新增模板", "新建指令模板")),
    EngineerVoiceCommandSpec("后台操作", "保存", "save_template", ("保存模板", "保存当前模板"), "confirm"),
    EngineerVoiceCommandSpec("后台操作", "另存为", "clone_template", ("另存为模板", "复制当前模板")),
    EngineerVoiceCommandSpec("后台操作", "删除", "delete_template", ("删除模板", "删除当前模板"), "danger"),
    EngineerVoiceCommandSpec("后台操作", "导出指令", "export_template", ("导出指令", "导出模板")),
    EngineerVoiceCommandSpec("后台操作", "导入指令", "import_template", ("导入指令", "导入模板"), "confirm"),
    EngineerVoiceCommandSpec("模板点位", "新增点", "add_interp_point", ("新增插补点", "添加插补点")),
    EngineerVoiceCommandSpec("模板点位", "删除点", "delete_interp_point", ("删除插补点", "移除插补点"), "confirm"),
    EngineerVoiceCommandSpec("模板点位", "上移", "move_interp_point_up", ("上移插补点", "插补点上移")),
    EngineerVoiceCommandSpec("模板点位", "下移", "move_interp_point_down", ("下移插补点", "插补点下移")),
    EngineerVoiceCommandSpec("后台页签", "JSON预览", "show_json_preview", ("打开JSON预览", "查看JSON预览")),
    EngineerVoiceCommandSpec("后台页签", "系统参数", "show_system_params", ("切到系统参数", "打开系统参数")),
    EngineerVoiceCommandSpec("后台页签", "安全中间点", "show_safe_points", ("切到安全中间点", "打开安全中间点")),
    EngineerVoiceCommandSpec("后台页签", "流程管理", "show_flow_manage", ("切到流程管理", "打开流程管理")),
    EngineerVoiceCommandSpec("系统参数", "保存配置", "save_system_config", ("保存系统参数", "保存配置"), "confirm"),
    EngineerVoiceCommandSpec("系统参数", "重载配置", "reload_system_config", ("重载系统参数", "重载配置"), "confirm"),
    EngineerVoiceCommandSpec("系统参数", "读取控制器限位", "read_controller_limits", ("读取控制器限位", "读取安全限位")),
    EngineerVoiceCommandSpec("安全中间点", "新增中间点", "new_safe_point", ("新增安全中间点", "新建中间点")),
    EngineerVoiceCommandSpec("安全中间点", "保存中间点", "save_safe_point", ("保存安全中间点", "保存中间点"), "confirm"),
    EngineerVoiceCommandSpec("安全中间点", "删除中间点", "delete_safe_point", ("删除安全中间点", "删除中间点"), "danger"),
    EngineerVoiceCommandSpec("安全中间点", "保存规避配置", "save_avoidance_config", ("保存规避配置", "保存中间点规则"), "confirm"),
    EngineerVoiceCommandSpec("流程管理", "添加步骤", "add_flow_step", ("添加流程步骤", "添加步骤")),
    EngineerVoiceCommandSpec("流程管理", "移除步骤", "remove_flow_step", ("移除流程步骤", "移除步骤"), "confirm"),
    EngineerVoiceCommandSpec("流程管理", "上移", "move_flow_step_up", ("上移流程步骤", "流程步骤上移")),
    EngineerVoiceCommandSpec("流程管理", "下移", "move_flow_step_down", ("下移流程步骤", "流程步骤下移")),
    EngineerVoiceCommandSpec("流程管理", "新增流程", "new_flow", ("新增流程", "新建流程")),
    EngineerVoiceCommandSpec("流程管理", "保存流程", "save_flow", ("保存流程", "保存当前流程"), "confirm"),
    EngineerVoiceCommandSpec("流程管理", "删除流程", "delete_flow", ("删除流程", "删除当前流程"), "danger"),
    EngineerVoiceCommandSpec("日志页", "刷新日志", "refresh_logs", ("刷新日志", "重新读取日志")),
    EngineerVoiceCommandSpec("日志页", "导出日志", "export_logs", ("导出日志", "导出操作日志")),
    EngineerVoiceCommandSpec("日志页", "清空日志", "clear_logs", ("清空日志", "删除全部日志"), "danger"),
)


ENGINEER_REQUIRED_BUTTON_LABELS = frozenset(
    {
        "运行",
        "后台",
        "日志",
        "报警复位",
        "暂停",
        "继续",
        "急停",
        "授权",
        "读取反馈",
        "开始流程",
        "单步执行",
        "停止流程",
        "重置流程",
        "刷新设备",
        "解析文本",
        "执行",
        "开始录音",
        "清空",
        "新增",
        "保存",
        "另存为",
        "删除",
        "导出指令",
        "导入指令",
        "新增点",
        "删除点",
        "上移",
        "下移",
        "JSON预览",
        "系统参数",
        "安全中间点",
        "流程管理",
        "保存配置",
        "重载配置",
        "读取控制器限位",
        "新增中间点",
        "保存中间点",
        "删除中间点",
        "保存规避配置",
        "添加步骤",
        "移除步骤",
        "新增流程",
        "保存流程",
        "删除流程",
        "刷新日志",
        "导出日志",
        "清空日志",
    }
)


def engineer_button_labels_with_voice_aliases() -> set[str]:
    return {spec.button_label for spec in ENGINEER_BUTTON_VOICE_COMMANDS}


def aliases_for_engineer_button(button_label: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for spec in ENGINEER_BUTTON_VOICE_COMMANDS:
        if spec.button_label == button_label:
            aliases.extend(spec.aliases)
    return tuple(aliases)


def missing_required_engineer_button_labels() -> list[str]:
    covered = engineer_button_labels_with_voice_aliases()
    return sorted(label for label in ENGINEER_REQUIRED_BUTTON_LABELS if label not in covered)


def duplicate_engineer_voice_aliases() -> dict[str, tuple[str, ...]]:
    owners: dict[str, list[str]] = {}
    for spec in ENGINEER_BUTTON_VOICE_COMMANDS:
        for alias in spec.aliases:
            owners.setdefault(alias, []).append(spec.action)
    return {alias: tuple(actions) for alias, actions in owners.items() if len(set(actions)) > 1}


def engineer_voice_execution_policy(spec: EngineerVoiceCommandSpec | None) -> ExecutionPolicy:
    if spec is None:
        return "listed_only"
    if spec.action in DIRECT_EXECUTE_ACTIONS:
        return "direct"
    if spec.danger_level == "confirm":
        return "confirm"
    if spec.danger_level in {"danger", "emergency"}:
        return "rejected"
    return "listed_only"


def engineer_voice_capability_summary(*, limit_per_group: int = 5) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {
        policy: {"count": 0, "examples": []}
        for policy in EXECUTION_POLICY_LABELS
    }
    preferred_examples: dict[ExecutionPolicy, tuple[str, ...]] = {
        "direct": ("后台", "系统参数", "流程管理", "读取反馈"),
        "confirm": ("保存配置", "保存模板", "保存流程", "开始流程"),
        "rejected": ("急停", "删除流程", "清空日志", "删除中间点"),
        "listed_only": ("授权", "新增", "导出日志", "新增流程"),
    }
    labels_by_policy: dict[ExecutionPolicy, list[str]] = {policy: [] for policy in EXECUTION_POLICY_LABELS}
    for spec in ENGINEER_BUTTON_VOICE_COMMANDS:
        policy = engineer_voice_execution_policy(spec)
        group = summary[policy]
        group["count"] = int(group["count"]) + 1
        labels = labels_by_policy[policy]
        if spec.button_label not in labels:
            labels.append(spec.button_label)
    for policy, labels in labels_by_policy.items():
        examples = summary[policy]["examples"]
        if not isinstance(examples, list):
            continue
        for label in preferred_examples[policy]:
            if label in labels and label not in examples and len(examples) < limit_per_group:
                examples.append(label)
        for label in labels:
            if label not in examples and len(examples) < limit_per_group:
                examples.append(label)
    return summary


def match_engineer_voice_command(text: str) -> EngineerVoiceCommandSpec | None:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return None
    for spec in ENGINEER_BUTTON_VOICE_COMMANDS:
        aliases = {re.sub(r"\s+", "", alias) for alias in spec.aliases}
        if compact in aliases:
            return spec
    for spec in ENGINEER_BUTTON_VOICE_COMMANDS:
        for alias in spec.aliases:
            compact_alias = re.sub(r"\s+", "", alias)
            if len(compact_alias) >= 3 and compact_alias in compact:
                return spec
    return None


def export_engineer_voice_command_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for spec in ENGINEER_BUTTON_VOICE_COMMANDS:
        policy = engineer_voice_execution_policy(spec)
        rows.append(
            {
                "section": spec.section,
                "button_label": spec.button_label,
                "action": spec.action,
                "aliases": list(spec.aliases),
                "danger_level": spec.danger_level,
                "execution_policy": policy,
                "execution_policy_label": EXECUTION_POLICY_LABELS[policy],
            }
        )
    return rows


def export_engineer_voice_command_markdown() -> str:
    lines = [
        "# 工程师页语音等价指令清单",
        "",
        "| 区域 | 按钮/入口 | 动作 | 语音说法 | 风险级别 | 执行策略 |",
        "|---|---|---|---|---|---|",
    ]
    for row in export_engineer_voice_command_rows():
        aliases = "、".join(str(alias) for alias in row["aliases"])
        lines.append(
            f"| {row['section']} | {row['button_label']} | `{row['action']}` | {aliases} | {row['danger_level']} | {row['execution_policy_label']} |"
        )
    lines.append("")
    return "\n".join(lines)
