from __future__ import annotations

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
