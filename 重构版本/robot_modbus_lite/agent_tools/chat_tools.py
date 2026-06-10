from __future__ import annotations

from typing import Any

from robot_modbus_lite.agent.chat_explanation import ChatExplanationAgent
from robot_modbus_lite.agent_tools.tool_result import ToolResult


def explain_text(text: str, *, agent: ChatExplanationAgent | None = None) -> ToolResult:
    answer = (agent or ChatExplanationAgent()).answer(text)
    if answer is None:
        return ToolResult.failure(
            state="requires_business_tool",
            message="该输入需要交给业务工具处理。",
            code="REQUIRES_BUSINESS_TOOL",
            data={"raw_text": str(text or "")},
        )
    return ToolResult.success(
        state="chat_explained",
        message=str(answer.get("text", "") or ""),
        data={
            "raw_text": str(text or ""),
            "kind": str(answer.get("kind", "chat_answer") or "chat_answer"),
            "generates_command": bool(answer.get("generates_command", False)),
        },
    )


def query_command_catalog(service: Any, *, text: str = "", limit: int = 8) -> ToolResult:
    table = getattr(service, "table", None)
    flow_names = _flow_names(service)
    templates = _template_commands(table, limit=limit)
    lines: list[str] = []
    if flow_names:
        lines.append(f"当前共有 {len(flow_names)} 个流程：")
        lines.extend(f"{index}. {name}" for index, name in enumerate(flow_names[:limit], start=1))
        if len(flow_names) > limit:
            lines.append(f"... 还有 {len(flow_names) - limit} 个流程未显示。")
        first = flow_names[0]
        lines.append(f"可以说“查看{first}流程”查看步骤，或说“小正，执行{first}流程”执行。")
    else:
        lines.append("当前没有已保存流程。")

    lines.append("")
    if templates:
        lines.append(f"当前共有 {len(templates)} 个本地命令模板，示例：")
        for item in templates[:limit]:
            lines.append(f"- {item['description']}（{item['query_key']}，Func{item['func_id']}）")
        if len(templates) > limit:
            lines.append(f"... 还有 {len(templates) - limit} 个模板未显示。")
    else:
        lines.append("当前没有本地命令模板。")

    lines.append("")
    lines.append("可用命令示例：")
    lines.append("- 查询流程：现在有哪些流程 / 查看点头流程")
    lines.append("- 创建流程：新建流程 / 创建测试流程")
    lines.append("- 添加步骤：移动到位置A，X100 Y0 Z800，速度50% / 等待2秒 / 输出1打开")
    if flow_names:
        lines.append(f"- 执行流程：小正，执行{flow_names[0]}流程")
    else:
        lines.append("- 执行动作：小正，移动到X100 Y0 Z800")

    return ToolResult.success(
        state="command_catalog_loaded",
        message="\n".join(lines),
        data={
            "raw_text": str(text or ""),
            "flow_names": flow_names,
            "templates": templates,
            "generates_command": False,
        },
    )


def _flow_names(service: Any) -> list[str]:
    lister = getattr(service, "list_flow_names", None)
    if not callable(lister):
        return []
    try:
        names = [str(name) for name in list(lister())]
    except Exception:
        return []
    return sorted(name for name in names if name)


def _template_commands(table: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(table, dict):
        return []
    items: list[dict[str, Any]] = []
    for key, record in sorted(table.items(), key=lambda item: str(item[0])):
        description = str(getattr(record, "description", "") or key).strip()
        try:
            func_id = int(getattr(record, "func_num", 0) or 0)
        except (TypeError, ValueError):
            func_id = 0
        if not description or func_id <= 0:
            continue
        items.append(
            {
                "query_key": str(getattr(record, "query_key", "") or key),
                "description": description,
                "func_id": func_id,
            }
        )
        if len(items) >= max(int(limit or 8), 1) + 1:
            break
    return items
