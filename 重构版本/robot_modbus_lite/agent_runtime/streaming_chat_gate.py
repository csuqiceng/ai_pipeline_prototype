from __future__ import annotations

import re
from typing import Any


BUSINESS_ROUTE_KEYWORDS = (
    "创建流程",
    "新建流程",
    "添加流程",
    "新流程",
    "流程名字",
    "流程名称",
    "添加步骤",
    "加步骤",
    "流程草案",
    "确认执行",
    "确认保存",
    "取消确认",
    "取消指令",
    "取消流程",
    "执行流程",
    "保存流程",
)


CONTEXTUAL_EDIT_KEYWORDS = (
    "改成",
    "改为",
    "修改为",
    "调成",
    "设为",
    "设置为",
    "还是",
)

FLOW_PARAM_KEYWORDS = (
    "速度",
    "加速度",
    "减速度",
    "加速",
    "减速",
)


def text_requires_agent_route(text: str, *, understanding_agent: Any | None = None) -> bool:
    agent = understanding_agent
    if agent is None:
        from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent

        agent = CommandUnderstandingAgent()
    try:
        understanding = agent.understand(text)
    except Exception:
        return False
    intent = str(getattr(understanding, "intent", "") or "")
    if intent == "unknown":
        compact = re.sub(r"\s+", "", str(text or ""))
        return any(word in compact for word in BUSINESS_ROUTE_KEYWORDS) or _looks_like_contextual_flow_edit(compact)
    if intent in {"alarm_query", "status_query"}:
        return False
    return True


def _looks_like_contextual_flow_edit(compact: str) -> bool:
    if not compact:
        return False
    has_number = bool(re.search(r"-?\d+(?:\.\d+)?%?", compact))
    if not has_number:
        return False
    has_edit_word = any(word in compact for word in CONTEXTUAL_EDIT_KEYWORDS)
    has_param_word = any(word in compact for word in FLOW_PARAM_KEYWORDS)
    return has_edit_word or has_param_word
