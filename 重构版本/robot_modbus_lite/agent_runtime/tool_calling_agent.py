from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any, Callable

from robot_modbus_lite.agent.orchestrator import AgentOrchestratorResult

from .local_tool_registry import LocalToolRegistry
from .session_state import SessionState
from .tool_schemas import tool_input_schema, tool_output_schema


@dataclass(frozen=True)
class LocalToolSpec:
    name: str
    group: str
    description: str
    side_effect: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.input_schema:
            object.__setattr__(self, "input_schema", tool_input_schema(self.name))
        if not self.output_schema:
            object.__setattr__(self, "output_schema", tool_output_schema(self.name))


ToolCallingRunner = Callable[[str, SessionState, tuple[LocalToolSpec, ...]], AgentOrchestratorResult | None]


def build_local_tool_specs() -> tuple[LocalToolSpec, ...]:
    return (
        LocalToolSpec(
            name="lookup_command_schema",
            group="command_tools",
            description="查询本地命令 schema 和必填参数，不执行。",
        ),
        LocalToolSpec(
            name="parse_command_intent",
            group="command_tools",
            description="结构化判断控制类意图，只返回意图、func_id 和置信度，不解析执行参数，不执行。",
        ),
        LocalToolSpec(
            name="parse_command_params",
            group="command_tools",
            description="解析控制类命令参数，只返回结构化参数，不执行。",
        ),
        LocalToolSpec(
            name="validate_required_params",
            group="command_tools",
            description="校验本地命令必填参数，缺参只返回 missing_fields，不执行。",
        ),
        LocalToolSpec(
            name="check_param_bounds",
            group="command_tools",
            description="按本地配置检查参数边界，结果只供门禁使用，不执行。",
        ),
        LocalToolSpec(
            name="resolve_command_address",
            group="command_tools",
            description="解析只读协议地址常量，不允许修改地址。",
        ),
        LocalToolSpec(
            name="build_system_action_draft",
            group="command_tools",
            description="生成急停、暂停、继续、报警复位等系统动作草案，不执行。",
        ),
        LocalToolSpec(
            name="build_command_draft",
            group="command_tools",
            description="生成命令草案，只返回结构化草案，不执行控制器写入。",
        ),
        LocalToolSpec(
            name="apply_atomic_template",
            group="command_tools",
            description="匹配原子模板并返回结构化执行草案，不执行控制器写入。",
        ),
        LocalToolSpec(
            name="draft_to_query_record",
            group="command_tools",
            description="仅在草案已确认后转换为执行记录；未确认草案必须拒绝。",
        ),
        LocalToolSpec(
            name="run_safety_precheck",
            group="safety_tools",
            description="对命令草案运行本地安全预检，只返回预检结果，不执行。",
        ),
        LocalToolSpec(
            name="create_pending_confirm",
            group="safety_tools",
            description="为命令草案创建待确认计划，不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="query_pending_confirm",
            group="safety_tools",
            description="查询待确认计划状态，不执行。",
        ),
        LocalToolSpec(
            name="confirm_pending_plan",
            group="safety_tools",
            description="确认待执行草案并生成执行记录，不直接写控制器。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="cancel_pending_plan",
            group="safety_tools",
            description="取消待确认计划并清理确认状态。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="expire_pending_plan",
            group="safety_tools",
            description="将待确认计划标记为过期并清理确认状态。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="split_compound_command",
            group="compound_tools",
            description="拆分顺序复合指令，只返回步骤列表，不执行。",
        ),
        LocalToolSpec(
            name="plan_compound_command",
            group="compound_tools",
            description="生成复合指令草案和步骤结果，不执行控制器写入。",
        ),
        LocalToolSpec(
            name="start_flow_draft",
            group="flow_tools",
            description="开始创建流程草案；缺少流程名时进入澄清状态，不保存不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="set_flow_name",
            group="flow_tools",
            description="为当前流程草案设置名称，不保存不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="append_flow_step",
            group="flow_tools",
            description="向当前流程草案追加步骤；缺参数时进入澄清状态，不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="answer_flow_clarification",
            group="flow_tools",
            description="回答流程草案追问并回填当前步骤参数，不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="edit_flow_draft_params",
            group="flow_tools",
            description="修改当前流程草案已有步骤的速度、加速度或减速度参数，不保存不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="save_flow_draft",
            group="flow_tools",
            description="保存当前流程草案到本地流程库，不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="query_registered_flow",
            group="flow_tools",
            description="查询已登记流程列表或单个流程详情，不执行。",
        ),
        LocalToolSpec(
            name="prepare_registered_flow_execution",
            group="flow_tools",
            description="为已登记流程生成执行草案；必须等待门禁和确认，不直接执行。",
        ),
        LocalToolSpec(
            name="set_flow_draft",
            group="flow_tools",
            description="写入或更新流程草案，可能进入澄清状态，不执行。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="query_current_flow_draft",
            group="flow_tools",
            description="读取当前流程草案，不执行。",
        ),
        LocalToolSpec(
            name="cancel_flow_draft",
            group="flow_tools",
            description="取消当前流程草案并清理 pending 计划。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="query_dashboard_section",
            group="status_tools",
            description="查询控制器、报警、队列和预检等状态看板。",
        ),
        LocalToolSpec(
            name="get_axis_status",
            group="status_tools",
            description="读取逐轴 AXISSTATUS 并返回结构化故障位、建议和异常标志。",
        ),
        LocalToolSpec(
            name="get_alarm",
            group="status_tools",
            description="读取报警、急停、暂停和硬件状态并返回结构化报警解释。",
        ),
        LocalToolSpec(
            name="get_execution_progress",
            group="status_tools",
            description="读取当前执行进度，不执行控制器写入。",
        ),
        LocalToolSpec(
            name="query_saved_position",
            group="status_tools",
            description="查询已保存位置点参数。",
        ),
        LocalToolSpec(
            name="query_command_catalog",
            group="chat_tools",
            description="读取本地流程、命令模板和可用命令示例，不生成执行命令。",
        ),
        LocalToolSpec(
            name="explain_text",
            group="chat_tools",
            description="解释非控制类问题，不生成执行命令。",
        ),
        LocalToolSpec(
            name="create_memory_candidate",
            group="memory_tools",
            description="创建候选经验，默认不生效。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="query_memory_candidates",
            group="memory_tools",
            description="查询待审核候选经验，不修改记忆状态。",
        ),
        LocalToolSpec(
            name="query_memory_review",
            group="memory_tools",
            description="按状态或类型查询经验记录，并返回可审核的审计线索，不修改记忆状态。",
        ),
        LocalToolSpec(
            name="approve_memory_candidate",
            group="memory_tools",
            description="审核通过候选经验，使其进入 active memory。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="disable_memory",
            group="memory_tools",
            description="禁用已生效或候选经验，保留审计记录。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="rollback_memory",
            group="memory_tools",
            description="回滚错误生效的 active memory，清出生效查询并保留审计原因。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="lookup_active_memory",
            group="memory_tools",
            description="查询已审核生效的经验，只影响理解和推荐。",
        ),
        LocalToolSpec(
            name="record_memory_applied",
            group="memory_tools",
            description="记录本轮应用了哪条经验，用于审计和回滚。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="record_feedback_vote",
            group="memory_tools",
            description="记录用户对回答、候选经验或解析结果的投票反馈。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="save_position_alias",
            group="memory_tools",
            description="保存当前位置别名到本地位置库；修改本地状态但不执行机器人动作。",
            side_effect=True,
        ),
        LocalToolSpec(
            name="delete_position_alias",
            group="memory_tools",
            description="删除本地位置库中的位置别名；修改本地状态但不执行机器人动作。",
            side_effect=True,
        ),
    )


class ToolCallingAgentRuntime:
    def __init__(
        self,
        *,
        langchain_available: bool | None = None,
        runner: ToolCallingRunner | None = None,
        tool_specs: tuple[LocalToolSpec, ...] | None = None,
        tool_registry: LocalToolRegistry | None = None,
    ) -> None:
        self._langchain_available = langchain_available
        self.runner = runner
        self.tool_specs = tool_specs or build_local_tool_specs()
        self.tool_registry = tool_registry or LocalToolRegistry()

    @property
    def available(self) -> bool:
        if self.runner is not None:
            return True
        if self._langchain_available is not None:
            return bool(self._langchain_available)
        return _langchain_runtime_available()

    def handle(self, text: str, *, session_state: SessionState, apply_memory: bool = True) -> AgentOrchestratorResult:
        if not self.available:
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="LangChain/LangGraph 当前不可用，回退兼容 AgentOrchestrator。",
                payload={"fallback_required": True},
            )
        if self.runner is None:
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="LangChain/LangGraph 已安装但尚未配置 runner，回退兼容 AgentOrchestrator。",
                payload={"fallback_required": True},
            )
        if apply_memory:
            result = self.runner(str(text or ""), session_state, self.tool_specs)
        else:
            original_memory_store = self.tool_registry.memory_store
            self.tool_registry.memory_store = None
            try:
                result = self.runner(str(text or ""), session_state, self.tool_specs)
            finally:
                self.tool_registry.memory_store = original_memory_store
        if result is None:
            return AgentOrchestratorResult(
                kind="tool_calling_unavailable",
                message="Tool-calling runner 未返回结果，回退兼容 AgentOrchestrator。",
                payload={"fallback_required": True},
            )
        return result


def _langchain_runtime_available() -> bool:
    return bool(importlib.util.find_spec("langchain_core")) and bool(importlib.util.find_spec("langgraph"))
