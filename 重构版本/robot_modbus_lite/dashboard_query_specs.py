"""Reviewable query specifications for the seven operator dashboard boards."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class DashboardQuerySpec:
    board_key: str
    board_name: str
    aliases: tuple[str, ...]
    answer_scope: str
    priority_hint: str = "normal"


DASHBOARD_QUERY_SPECS: tuple[DashboardQuerySpec, ...] = (
    DashboardQuerySpec(
        "device_status",
        "看板1 设备基础状态",
        ("设备状态", "现在状态", "当前状态", "系统状态", "现在设备状态", "报警后", "报警处理", "报警怎么恢复", "报警怎么处理"),
        "系统状态、急停、暂停、报警、当前位置和报警恢复建议。",
    ),
    DashboardQuerySpec(
        "action_feasibility",
        "看板2 动作执行可行性",
        ("能不能执行", "可以执行", "可执行", "能发指令", "能动", "能不能动", "现在能不能执行", "为什么不能执行", "为什么不能动", "为什么现在不能动"),
        "通道空闲、L1安全预检、L2运动规划和阻断原因。",
    ),
    DashboardQuerySpec(
        "safety_boundary",
        "看板3 全域安全边界",
        ("安全吗", "安全不安全", "当前位置安全", "半径", "高度", "边界", "软限位", "关节限位", "当前位置安全吗"),
        "当前R/Z、安全R/Z范围和关节软限位。",
    ),
    DashboardQuerySpec(
        "motion_limits",
        "看板4 运动极限参数",
        ("速度", "加速度", "减速度", "超限", "速度有没有超限"),
        "当前速度、执行进度、速度/加速度/减速度上限。",
    ),
    DashboardQuerySpec(
        "process_preview",
        "看板5 工艺流程预演进度",
        ("预演到哪", "预演进度", "流程预演", "流程到哪", "执行到哪", "流程预演到哪了", "为什么有风险"),
        "L3流程状态、当前流程、当前步骤、进度和风险摘要。",
    ),
    DashboardQuerySpec(
        "process_adaptation",
        "看板6 工艺适配评估",
        ("到不到", "能到", "运动规划", "奇异", "姿态", "这个位置能到吗"),
        "L2状态、FSTATUS、奇异点风险和建议。",
    ),
    DashboardQuerySpec(
        "communication_faults",
        "看板7 通讯+设备故障诊断",
        ("通讯", "通信", "连接", "ethercat", "ecat", "通讯正常吗"),
        "ECAT/控制器/实时反馈/IO状态，反馈过期时高优先级提示。",
        "high_when_fault",
    ),
)


def _load_alias_config() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).resolve().parent.parent / "data" / "dashboard_query_aliases.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for key, values in aliases.items():
        if isinstance(values, list):
            cleaned = tuple(str(value).strip() for value in values if str(value).strip())
            if cleaned:
                result[str(key)] = cleaned
    return result


def dashboard_query_specs() -> tuple[DashboardQuerySpec, ...]:
    alias_config = _load_alias_config()
    if not alias_config:
        return DASHBOARD_QUERY_SPECS
    specs: list[DashboardQuerySpec] = []
    for spec in DASHBOARD_QUERY_SPECS:
        aliases = alias_config.get(spec.board_key, spec.aliases)
        specs.append(
            DashboardQuerySpec(
                spec.board_key,
                spec.board_name,
                aliases,
                spec.answer_scope,
                spec.priority_hint,
            )
        )
    return tuple(specs)


REQUIRED_DASHBOARD_QUERY_KEYS = frozenset(
    {
        "device_status",
        "action_feasibility",
        "safety_boundary",
        "motion_limits",
        "process_preview",
        "process_adaptation",
        "communication_faults",
    }
)


def dashboard_query_keys() -> set[str]:
    return {spec.board_key for spec in dashboard_query_specs()}


def missing_dashboard_query_keys() -> list[str]:
    keys = dashboard_query_keys()
    return sorted(key for key in REQUIRED_DASHBOARD_QUERY_KEYS if key not in keys)


def match_dashboard_query_spec(text: str) -> DashboardQuerySpec | None:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return None
    lowered = compact.lower()
    for spec in dashboard_query_specs():
        if any(alias.lower() in lowered for alias in spec.aliases):
            return spec
    return None


def export_dashboard_query_rows() -> list[dict[str, object]]:
    return [
        {
            "board_key": spec.board_key,
            "board_name": spec.board_name,
            "aliases": list(spec.aliases),
            "answer_scope": spec.answer_scope,
            "priority_hint": spec.priority_hint,
        }
        for spec in dashboard_query_specs()
    ]


def export_dashboard_query_markdown() -> str:
    lines = [
        "# 用户页完整状态查询清单",
        "",
        "| 看板 | board_key | 可问法 | 回答内容 |",
        "|---|---|---|---|",
    ]
    for row in export_dashboard_query_rows():
        aliases = "、".join(str(alias) for alias in row["aliases"])
        lines.append(f"| {row['board_name']} | `{row['board_key']}` | {aliases} | {row['answer_scope']} |")
    lines.append("")
    return "\n".join(lines)
