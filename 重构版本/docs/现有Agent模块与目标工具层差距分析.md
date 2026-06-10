# 现有 Agent 模块与目标工具层差距分析

本文专门分析 `robot_modbus_lite/agent/` 现有模块如何迁移到目标 `agent_tools/` 工具层。目标不是“再写一套 Agent”，而是把已经存在的解析、草案、安全、确认、查询能力包装成稳定、可测试、可被 LangChain/LangGraph 调用的本地工具。

## 1. 总体结论

现有代码已经有不少可复用能力：

- `CommandUnderstandingAgent` 可以做控制类意图识别。
- `ParameterCompletionAgent` 可以补全运动、点动、延时、IO 参数。
- `SafetyReviewAgent` 已经接入 L1/L2/姿态夹角安全预检。
- `ConfirmationAgent` 已经有 draft session、超时、签名校验和一次性确认机制。
- `PositionQueryAgent`、`DashboardQueryAgent`、`ChatExplanationAgent` 已经能处理部分查询和解释。
- `FlowDraftAgent`、`RegisteredFlowAgent` 已经能复用旧解析器识别流程草案和已登记流程。

但这些模块目前更像“内部 Agent 组件”，还不是目标工具层。主要差距是：

- 返回结构不统一，有的是 dataclass，有的是 dict，有的是 `QueryRecord`，有的是自然语言 `text`。
- 状态归属不统一，状态散落在 `ConfirmationAgent._sessions`、UI pending draft、旧 NLP plan、执行服务里。
- 失败收口不统一，异常或 fallback 可能导致日志停在 `pending`。
- chat/fallback 仍可能产生听起来像动作完成的回复，但没有绑定真实 tool result。
- `AgentOrchestrator.handle()` 仍是硬编码路由链，未来只能作为兼容 fallback，不能再作为主链路。

目标工具层必须统一返回：

```json
{
  "ok": true,
  "state": "flow_draft_updated",
  "message": "已添加第1步，等待补充坐标。",
  "data": {},
  "errors": []
}
```

其中 `ok/state/data/errors` 是程序事实，`message` 只是给用户看的表达。Agent/LLM 只能基于工具结果生成回复，不能自己声明“已创建”“已保存”“已执行”。

## 2. 当前链路概览

当前主链路大致是：

```text
用户文本
  -> AgentOrchestrator.handle()
    -> CommandUnderstandingAgent.understand()
    -> CompoundCommandCoordinator.plan()
    -> memory / position_memory / atomic_template
    -> dashboard_query / flow_draft / registered_flow
    -> LLM fallback
    -> RestrictedAgentService.parse()
      -> CommandUnderstandingAgent.understand()
      -> ParameterCompletionAgent.complete()
      -> SafetyReviewAgent.review()
      -> ConfirmationAgent.begin()
    -> fallback_legacy
      -> VoiceNlpAdapter
```

这条链路的问题不是没有能力，而是能力入口太多、状态不集中、返回格式不一致。

目标链路应收敛为：

```text
用户文本
  -> SessionState
  -> LangChain/LangGraph 或短期规则 router
  -> agent_tools/*
  -> ToolResult
  -> ExecutionGate
  -> UI 回复 + 日志归档
```

## 3. 目标工具层分组

建议目标目录：

```text
robot_modbus_lite/agent_tools/
  status_tools.py
  flow_tools.py
  command_tools.py
  safety_tools.py
  chat_tools.py
  memory_tools.py
  tool_result.py
```

建议统一结果对象：

```python
@dataclass(frozen=True)
class ToolResult:
    ok: bool
    state: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
```

建议最低字段约定：

- `ok`: 工具是否成功产生目标事实。
- `state`: 机器可读状态，例如 `missing_params`、`waiting_confirmation`、`precheck_failed`。
- `message`: 给用户看的简短解释。
- `data`: 结构化数据，例如 `draft_id`、`params`、`missing_fields`、`precheck_result`。
- `errors`: 结构化错误，至少包含 `code/message/field/source`。

## 4. 模块差距明细

| 现有模块 | 当前职责 | 目标工具 | 可复用部分 | 主要差距 |
|---|---|---|---|---|
| `agent/orchestrator.py` | 硬编码调用多个 Agent，决定走新链路还是旧链路 | 短期规则 router；长期兼容 fallback | 现有路由顺序、拒绝 LLM 候选的逻辑 | 不能继续作为主路由；结果不是 `ToolResult`；fallback 会引入两条 NLP 管线交替 |
| `agent/service.py` | 受限 Agent 编排：理解、补全、安全预检、确认 | `command_tools.build_command_draft()` + `safety_tools.run_safety_precheck()` + `safety_tools.create_pending_confirm()` | 后端不直接执行控制器写入的边界是正确的 | 一个方法做了多步，难以让 LangChain 分步追问；缺少统一 SessionState |
| `agent/command_understanding.py` | 把控制文本解析成 `CommandUnderstandingResult` | `command_tools.parse_command_intent()`、`command_tools.parse_command_params()` | 意图、func_id、部分参数正则、系统动作别名、基础中文数字参数归一化、ASR 标准词/坐标轴误听纠错、`raw_text/normalized_text` 双轨结果和 interaction archive 双轨落盘 | 缺参数返回不够结构化；参数 schema 没暴露为工具；现场 ASR 词表仍需按真实日志迭代 |
| `agent/parameter_completion.py` | 根据控制器快照补全参数，生成 `CommandDraft` | `command_tools.complete_params_from_state()`、`command_tools.validate_required_params()` | 参数来源标记 `specified/inherited/controller/default` 很有价值 | 继承快照和用户输入容易混淆；缺少“是否允许自动继承”的显式策略 |
| `agent/drafts.py` | 定义 `CommandDraft` 和转 `QueryRecord` | `command_tools.build_command_draft()`、`command_tools.draft_to_query_record()` | `REQUIRED_PARAM_KEYS`、`CommandDraft`、缺参检查 | `QueryRecord` 是执行侧格式，不能太早暴露给 LLM；缺少幂等 key/版本号 |
| `agent/safety_review.py` | 把草案转 L1/L2/姿态检查 | `safety_tools.run_safety_precheck()` | L1/L2/pose 结果归一化已经比较完整 | 工具结果应固定 `pass/warning/fail/blocking_level/items`；失败不能进入确认 |
| `agent/confirmation.py` | 管理待确认草案、超时、签名、确认/拒绝 | `safety_tools.create_pending_confirm()`、`confirm_pending_plan()`、`cancel_pending_plan()` | 一次性确认、状态签名、安全签名、超时机制 | 需要统一到 `SessionState.pending_confirm`；取消后要清理脏状态；确认结果不能直接等于执行 |
| `agent/flow_draft.py` | 复用旧 `parse_func` 识别流程草案 | `flow_tools.start_flow_draft()`、`set_flow_name()`、`append_flow_step()` | 能识别 `source=flow_draft` 的旧计划 | 返回旧 plan 对象；没有明确多轮状态机；“流程名/步骤/参数”未拆为工具 |
| `agent/registered_flow.py` | 识别已登记流程计划 | `flow_tools.query_registered_flow()`、`prepare_registered_flow_execution()` | 能复用旧流程解析结果 | `generates_command=True` 容易和执行混淆；需要 ExecutionGate 二次确认 |
| `agent/compound.py` | 拆分复合指令并逐步确认 | `flow_tools.build_compound_draft()` 或 `command_tools.split_compound_command()` | 已有 step machine 和逐步确认概念 | 状态独立于统一 SessionState；复合步骤幂等性和失败回滚要补 |
| `agent/position_query.py` | 查询保存位置坐标 | `status_tools.get_saved_position()` | 查询不触发动作的边界清楚 | 返回自然语言文本为主；不存在时应返回结构化 `not_found` |
| `agent/dashboard_query.py` | 匹配 dashboard 查询 spec | `status_tools.get_dashboard_section()`、`status_tools.get_status()` | `match_dashboard_query_spec()` 可复用 | 目前只返回命中范围，不一定返回真实状态数据 |
| `agent/axis_status.py` | 分解轴状态位 | `status_tools.get_axis_status()` | 位解析逻辑可复用 | 需要和 dashboard/status 快照统一，形成事实查询结果 |
| `agent/alarm_explanation.py` | 解释报警和安全状态 | `status_tools.get_alarm()` + `chat_tools.explain_alarm()` | 报警解释和建议可复用 | 事实查询和解释混在一起；解释不能修改状态 |
| `agent/chat_explanation.py` | 回答能力、确认、L2、使用方式等说明 | `chat_tools.explain_capability()`、`explain_confirmation()`、`suggest_flow_steps()` | 已经避免部分控制关键词 | 需要强约束不能承诺动作完成；建议类回复不能伪装成草案成功 |
| `agent/llm_fallback.py` | LLM 兜底识别结构化意图或候选文本 | 废弃，或限制为 `chat_tools.fallback_explain()` | JSON payload 校验和 forbidden key 检查可参考 | 不能作为业务 fallback 直接产生流程/命令事实；不能绕过 tool schema |
| `agent/position_memory.py` | 位置保存/删除意图 | `memory_tools.save_position_alias()`、`delete_position_alias()` | 保存/删除位置的意图识别可复用 | 属于状态修改工具，必须有权限、幂等和审计 |
| `agent/memory_setting.py` | 记忆设置类自然语言处理 | `memory_tools.create_memory_candidate()`、`apply_memory_candidate()` | 可复用记忆入口意图 | 生效前要进入候选/审核机制，不能让 LLM 直接改 active memory |
| `agent/atomic_template.py` | 原子模板识别和内存快照恢复 | `command_tools.apply_atomic_template()` | 模板解析、失败恢复思路可复用 | 工具要显式返回模板命中、参数来源和是否需确认 |
| `agent/address_resolver.py` | 解析 func 地址/别名 | `command_tools.resolve_command_address()` | 地址解析必须本地确定 | 不能暴露给 LLM 修改，只能作为只读工具 |
| `agent/plan_adapter.py` | 把 Agent 结果转 `VoiceNlpPlan` | 迁移期兼容 adapter | 可短期用于 UI 兼容 | 长期应从 `ToolResult` 适配 UI，而不是从 Agent 内部对象适配 |

## 5. 每组工具的改造要求

### 5.1 `status_tools.py`

复用模块：

- `agent/position_query.py`
- `agent/dashboard_query.py`
- `agent/axis_status.py`
- `agent/alarm_explanation.py`

建议工具：

- `get_current_status()`
- `get_axis_status(axis=None)`
- `get_saved_position(name)`
- `get_alarm_detail(code=None)`
- `get_dashboard_section(section)`

改造要求：

- 查询类工具必须无副作用、可重复调用。
- 返回真实数据和解释文本要分开：`data` 放事实，`message` 放表达。
- 找不到位置、找不到状态项时返回 `ok=false/state=not_found`，不能走空回复。
- 状态数据来源要标明，例如 `dashboard_cache`、`controller_snapshot`、`position_memory`。

### 5.2 `command_tools.py`

复用模块：

- `agent/command_understanding.py`
- `agent/parameter_completion.py`
- `agent/drafts.py`
- `agent/address_resolver.py`
- `agent/atomic_template.py`

建议工具：

- `lookup_command_schema(command_name)`
- `parse_command_intent(text)`
- `parse_command_params(text, schema=None)`
- `normalize_asr_text(text)`
- `normalize_chinese_numbers(text)`
- `validate_required_params(func_id, params)`
- `complete_params_from_state(intent, params, policy)`
- `build_command_draft(intent, params)`
- `draft_to_query_record(draft_id)`

改造要求：

- `parse_command_params()` 必须保留 `raw_text`、`normalized_text`、`param_sources`。
- 中文数字必须先归一化，例如 `X 一百` 应进入 `target_x=100`；当前基础参数归一化已落地，`CommandUnderstandingResult` 已显式返回 `normalized_text`。
- 参数缺失时返回 `missing_fields`，由 Agent 追问，不要直接静默继承。
- 自动继承当前位姿或控制器安全参数必须标记来源，并能在确认文本里显示。
- `draft_to_query_record()` 只能在 ExecutionGate 和确认通过后调用。

### 5.3 `flow_tools.py`

复用模块：

- `agent/flow_draft.py`
- `agent/registered_flow.py`
- `agent/compound.py`

建议工具：

- `start_flow_draft(flow_name=None)`
- `set_flow_name(flow_name)`
- `append_flow_step(step_text=None, command_draft=None)`
- `update_flow_step(step_id, params)`
- `delete_flow_step(step_id)`
- `query_current_flow_draft()`
- `save_flow_draft()`
- `query_registered_flow(name)`
- `prepare_flow_execution(flow_id)`

改造要求：

- 流程草案必须进入 `SessionState.flow_draft`，不能只存在 UI 临时变量或旧 plan 中。
- “流程名字叫测试”必须能接住上一轮 `waiting_flow_name` 状态。
- 添加步骤时先创建步骤草案；缺坐标时返回 `missing_fields`。
- 保存流程和准备执行是两个动作，不能混在一个 chat 回复里。
- 已登记流程准备执行也要经过 ExecutionGate，不能因为 `generates_command=True` 就直接写控制器。

### 5.4 `safety_tools.py`

复用模块：

- `agent/safety_review.py`
- `agent/confirmation.py`
- `agent/pose_angle.py`
- `safety_precheck.py`
- `motion_plan.py`

建议工具：

- `run_safety_precheck(draft_id)`
- `create_pending_confirm(draft_id)`
- `query_pending_confirm()`
- `confirm_pending_plan(confirm_text)`
- `cancel_pending_plan(reason=None)`
- `expire_pending_plan()`

改造要求：

- `run_safety_precheck()` 只返回安全结论，不创建执行事实。
- 预检失败时返回 `ok=false/state=precheck_failed`，不能创建可执行确认。
- `create_pending_confirm()` 必须写入 `SessionState.pending_confirm`。
- `confirm_pending_plan()` 必须消费 pending 状态，一次确认只能成功一次。
- `cancel_pending_plan()` 必须清理 pending confirm、pending execution 和临时执行草案。
- 控制器状态签名或安全签名变化时，确认必须过期。

### 5.5 `chat_tools.py`

复用模块：

- `agent/chat_explanation.py`
- `agent/alarm_explanation.py`
- 受限使用 `agent/llm_fallback.py`

建议工具：

- `explain_capability()`
- `explain_confirmation()`
- `explain_safety_result(precheck_result)`
- `explain_alarm(alarm_data)`
- `suggest_flow_steps(context)`
- `fallback_explain(text, context)`

改造要求：

- chat 工具只能解释、建议、追问。
- 禁止返回“已创建”“已添加”“已保存”“已执行”等业务事实。
- 如果用户表达的是业务动作，chat 工具必须返回 `state=requires_business_tool`，交给对应工具。
- `llm_fallback.py` 若保留，只能输出候选意图或解释，不允许直接生成执行事实。

### 5.6 `memory_tools.py`

复用模块：

- `agent/position_memory.py`
- `agent/memory_setting.py`
- 现有 `data/*.json` 记忆文件
- 后续 SQLite `agent_memory.sqlite3`

建议工具：

- `create_memory_candidate(kind, source, value)`
- `vote_interaction(session_id, record_id, vote)`
- `query_memory_candidates(status)`
- `approve_memory_candidate(candidate_id)`
- `disable_memory(memory_id)`
- `lookup_active_memory(kind, text)`

改造要求：

- 用户投票和离线学习先进入 candidate memory，默认不生效。
- active memory 只能影响理解和推荐，不能覆盖安全边界。
- 每次应用记忆必须记录 `memory_applied`。
- SQLite 数据库放运行时可写目录，不写入 EXE 内部资源。

## 6. 建议迁移顺序

### 阶段 A：先定义公共结果和日志收口

输出：

- `agent_tools/tool_result.py`
- `ToolResult`
- `tool_call_id`
- `tool_name`
- `input_summary`
- `output_state`
- `errors`

验收：

- 任意工具失败都能生成最终回复和最终日志。
- 不允许停在 `engine=pending` 或 `execution.result=pending`。

### 阶段 B：包装查询和 chat 工具

优先原因：

- 查询和解释无控制器写入风险。
- 容易验证幂等性。
- 可以先消除 chat 动作承诺问题。

输出：

- `status_tools.py`
- `chat_tools.py`

验收：

- 状态查询、位置查询、报警解释都有结构化 `data`。
- chat 工具不再承诺业务动作完成。

### 阶段 C：包装命令草案工具

输出：

- `command_tools.py`
- 中文数字归一化
- 参数 schema 查询
- 参数缺失追问结果

验收：

- `X 一百，Y 0，Z 100，速度 50` 能解析出 `target_x=100`。
- 缺少坐标时返回 `missing_fields`，而不是静默继承错误值。
- 参数来源在确认文本和日志中可追踪。

### 阶段 D：包装流程草案工具

输出：

- `flow_tools.py`
- `SessionState.flow_draft`
- 流程名等待状态
- 步骤草案状态

验收：

- “创建流程 -> 流程名字叫测试 -> 添加第一步 -> 补坐标 -> 查看流程” 必须保持同一个 session。
- 任何“已添加步骤”的回复都必须来自 `append_flow_step()` 成功结果。

### 阶段 E：包装安全和确认工具

输出：

- `safety_tools.py`
- `SessionState.pending_confirm`
- `cancel_pending_plan()`
- `expire_pending_plan()`

验收：

- 无 pending 时说“确认执行”必须拒绝。
- 取消后不能再被下一句“确认执行”消费。
- 预检失败不能生成可执行确认。

### 阶段 F：替换主路由

输出：

- 短期规则 router 调用 `agent_tools/*`
- 后续 LangGraph/LangChain tool-calling 主入口
- `AgentOrchestrator.handle()` 降级为兼容 fallback

验收：

- Qt 自然语言输入只走一条主链路。
- `VoiceNlpAdapter` 不能绕过工具层和 ExecutionGate。
- 同一轮输入只能有一个最终结果。

## 7. 最小 ToolResult 示例

### 7.1 参数缺失

```json
{
  "ok": false,
  "state": "missing_params",
  "message": "还缺少 X、Y、Z 坐标。",
  "data": {
    "intent": "move_linear",
    "missing_fields": ["target_x", "target_y", "target_z"],
    "parsed_params": {
      "spd_pct": 50
    }
  },
  "errors": [
    {
      "code": "MISSING_REQUIRED_PARAMS",
      "fields": ["target_x", "target_y", "target_z"]
    }
  ]
}
```

### 7.2 草案生成成功

```json
{
  "ok": true,
  "state": "command_draft_created",
  "message": "已生成运动草案，等待安全预检。",
  "data": {
    "draft_id": "a1b2c3d4",
    "func_id": 108,
    "intent": "move_linear",
    "params": {
      "target_x": 100,
      "target_y": 0,
      "target_z": 100,
      "spd_pct": 50
    },
    "param_sources": {
      "target_x": "specified",
      "target_y": "specified",
      "target_z": "specified",
      "spd_pct": "specified"
    }
  },
  "errors": []
}
```

### 7.3 安全预检失败

```json
{
  "ok": false,
  "state": "precheck_failed",
  "message": "安全预检未通过，不能创建确认计划。",
  "data": {
    "draft_id": "a1b2c3d4",
    "blocking_level": "L1",
    "items": []
  },
  "errors": [
    {
      "code": "SAFETY_PRECHECK_FAILED",
      "message": "目标点超出安全边界。"
    }
  ]
}
```

### 7.4 待确认创建成功

```json
{
  "ok": true,
  "state": "waiting_confirmation",
  "message": "安全预检通过，请确认是否执行。",
  "data": {
    "confirm_id": "confirm-a1b2c3d4",
    "draft_id": "a1b2c3d4",
    "expires_at": 1780830000,
    "confirmation_text": "【复述确认】Func108 直线插补..."
  },
  "errors": []
}
```

## 8. 风险点

### 8.1 不要把旧 plan 直接当工具结果

`FlowDraftAgent` 和 `RegisteredFlowAgent` 现在返回旧 plan 对象。迁移时不能只把 plan 塞进 `data` 就结束，必须拆出：

- 当前 session 状态变化
- 流程名
- 步骤列表
- 缺失字段
- 是否需要确认
- 是否允许执行

### 8.2 不要让 LLM fallback 产生业务事实

`LlmFallbackAgent` 可以帮助识别候选意图，但不能成为“创建流程成功”“添加步骤成功”的事实来源。事实必须来自 `flow_tools` 或 `command_tools` 的 `ok=true`。

### 8.3 不要过早生成 `QueryRecord`

`draft_to_query_record()` 会把草案转换成执行侧记录。这个动作应该放在确认和 ExecutionGate 之后。LangChain/LLM 不应该直接接触可写控制器的最终记录。

### 8.4 自动补全必须显式

`ParameterCompletionAgent` 当前会从当前位置和安全参数中继承值。这个能力可以保留，但每个继承值必须进入 `param_sources`，并在确认文本和日志中显示。否则用户会以为系统使用的是自己刚才说的参数。

### 8.5 幂等性要前置设计

修改类工具必须有幂等策略：

- 同一个 `tool_call_id` 重放不能重复添加步骤。
- 同一个 `draft_id` 不能重复创建多个有效 pending confirm。
- 已确认或已取消的 pending plan 不能再次确认。
- 保存流程时要处理同名流程覆盖、版本号或明确拒绝。

## 9. 进入阶段 2 前的检查清单

- [ ] 定义 `ToolResult` 和错误码规范。
- [ ] 定义 `SessionState` 中 `flow_draft`、`command_draft`、`pending_confirm` 的字段。
- [ ] 明确哪些查询工具无须唤醒词，哪些控制工具必须唤醒词。
- [ ] 明确 `draft_to_query_record()` 只能在确认后调用。
- [ ] 增加中文数字和 ASR 归一化入口。（中文数字基础入口、ASR 标准词/坐标轴误听纠错、底层 `raw_text/normalized_text` 双轨结果、interaction archive 双轨落盘已完成；现场 ASR 词表仍需持续迭代。）
- [ ] 把 chat 工具的动作承诺加入自动测试黑名单。
- [ ] 给修改类工具增加幂等 key 或版本号。
- [ ] 给 `cancel_pending_plan()` 增加回滚/清理验收。
- [ ] 明确 `AgentOrchestrator.handle()` 仅为迁移期 fallback。

## 10. 最终建议

阶段 2 的正确做法是“包装现有模块”，不是“重写 Agent”。

建议先从低风险工具开始：

1. `status_tools.py`
2. `chat_tools.py`
3. `command_tools.py`
4. `flow_tools.py`
5. `safety_tools.py`
6. LangChain/LangGraph 主入口

这样可以逐步把当前分散的 Agent 能力收敛到统一工具层，同时避免一次性替换导致 Qt 主链路不可用。
