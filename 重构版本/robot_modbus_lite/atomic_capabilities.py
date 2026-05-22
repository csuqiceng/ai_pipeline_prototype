"""Reviewable capability table for the V2.0 secondary atomic command layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AtomicCapability:
    key: str
    name: str
    examples: tuple[str, ...]
    implementation: str
    status: str
    notes: str


ATOMIC_CAPABILITIES: tuple[AtomicCapability, ...] = (
    AtomicCapability(
        "wake_word",
        "唤醒词隔离",
        ("小正，上升3毫米", "小郑，速度60%"),
        "VoiceNlpAdapter + AtomicParser",
        "implemented",
        "生产控制类原子命令必须带唤醒词；单独急停词只提示标准编码。",
    ),
    AtomicCapability(
        "memory_sp",
        "SP 参数命令",
        ("速度60%", "步长10毫米", "专家模式"),
        "AtomicMemory + AtomicResolver._resolve_memory",
        "implemented",
        "速度、步长和确认模式会持久化到 data/atomic_state.json。",
    ),
    AtomicCapability(
        "joint_j",
        "J 类关节命令",
        ("J1转到45度", "J2正转3度"),
        "AtomicParser._parse_joint -> Func106",
        "basic",
        "绝对关节目标保持高风险确认。",
    ),
    AtomicCapability(
        "virtual_v",
        "V 类虚拟轴命令",
        ("上升3毫米", "左移5毫米", "RY正转2度"),
        "AtomicParser._parse_virtual -> Func107",
        "basic",
        "按速度、步长、旋转轴做 low/medium/high 基础分级。",
    ),
    AtomicCapability(
        "cartesian_c",
        "C 类笛卡尔命令",
        ("X100Y200Z300", "X100Y200Z300RX0RY90RZ0"),
        "AtomicParser._parse_cartesian -> Func108",
        "basic",
        "只支持显式坐标，不做自然语言复杂位姿推理。",
    ),
    AtomicCapability(
        "delay_d",
        "D 类延时命令",
        ("等待2秒", "延时500毫秒"),
        "AtomicParser._parse_delay -> Func110",
        "implemented",
        "延时动作风险等级为 low。",
    ),
    AtomicCapability(
        "io",
        "IO 类命令",
        ("IO1开", "输出2关闭"),
        "AtomicParser._parse_io -> Func120",
        "implemented",
        "IO 会改变外部设备状态，风险等级为 medium。",
    ),
    AtomicCapability(
        "position_library",
        "位置库",
        ("保存当前位置为位置A", "移动到位置A", "位置A坐标是多少"),
        "AtomicMemory position store",
        "basic",
        "位置来源依赖 Qt 当前反馈；移动到命名位置生成 Func108。",
    ),
    AtomicCapability(
        "history",
        "动作历史",
        ("再走一次", "返回上一步", "继续上升3毫米"),
        "AtomicMemory last_record / position_stack",
        "basic",
        "返回依赖位置栈，位置栈填充仍需结合真实执行结果细化。",
    ),
    AtomicCapability(
        "dashboard_q",
        "Q 类看板查询",
        ("当前位置", "速度多少", "能不能到350 200 500"),
        "DashboardQueryService",
        "basic",
        "Q08 目标点查询做离线 XYZ 软限位判断，不代表真机 L2 逆解。",
    ),
    AtomicCapability(
        "sequence",
        "顺序组合命令",
        ("上升3毫米然后IO1开", "J1转到45度然后等待2秒然后IO1关"),
        "VoiceNlpAdapter._split_atomic_parts",
        "basic",
        "只支持 then-style 顺序组合，整体进入预检/确认链。",
    ),
    AtomicCapability(
        "complex_guard",
        "复杂组合保护",
        ("重复3次", "同时上升并IO1开", "如果没有报警就上升"),
        "VoiceNlpAdapter._unsupported_complex_atomic_reason",
        "guarded",
        "循环、并行、条件命令保护性拒绝，不进入执行链。",
    ),
    AtomicCapability(
        "func11_guard",
        "Func11 连续插补保护",
        ("连续路径经过位置A和位置B", "插补到X100Y200Z300", "执行轨迹A"),
        "VoiceNlpAdapter._unsupported_complex_atomic_reason",
        "guarded",
        "连续插补/轨迹类命令保护性拒绝，避免误拆成普通动作。",
    ),
    AtomicCapability(
        "func11_execution",
        "Func11 连续插补执行",
        ("连续路径", "轨迹执行"),
        "未接入",
        "deferred",
        "真实连续插补执行需要真机协议和路径安全验证，本轮不实现。",
    ),
    AtomicCapability(
        "machine_validation",
        "真机参数边界验证",
        ("真实控制器回显", "FRAME_TRANS2", "运动完成验证"),
        "未接入",
        "deferred",
        "当前为 Qt 离线解析和 QueryRecord 生成，未做真机联调。",
    ),
)


def atomic_capability_rows() -> list[dict[str, object]]:
    return [
        {
            "key": item.key,
            "name": item.name,
            "examples": list(item.examples),
            "implementation": item.implementation,
            "status": item.status,
            "notes": item.notes,
        }
        for item in ATOMIC_CAPABILITIES
    ]


def atomic_capability_summary() -> dict[str, int]:
    rows = atomic_capability_rows()
    summary = {"total": len(rows), "implemented": 0, "basic": 0, "guarded": 0, "deferred": 0}
    for row in rows:
        status = str(row["status"])
        if status in summary:
            summary[status] += 1
    return summary


def export_atomic_capability_markdown() -> str:
    lines = [
        "# 二次原子函数能力审计清单",
        "",
        "| 能力 | key | 状态 | 示例 | 实现位置 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for row in atomic_capability_rows():
        examples = "、".join(str(item) for item in row["examples"])
        lines.append(
            f"| {row['name']} | `{row['key']}` | {row['status']} | {examples} | {row['implementation']} | {row['notes']} |"
        )
    lines.append("")
    summary = atomic_capability_summary()
    lines.append(
        f"汇总：共 {summary['total']} 项，已实现 {summary['implemented']} 项，基础实现 {summary['basic']} 项，"
        f"保护性拒绝 {summary['guarded']} 项，延期 {summary['deferred']} 项。"
    )
    lines.append("")
    return "\n".join(lines)
