# 自然语言 Agent 工具化与自学习需求整理

本文整理当前自然语言对话系统暴露的问题、用户提出的新架构想法、LangChain/tool-calling 的使用边界、自学习机制，以及后续实现和测试要求。

## 0. 范围约定

本阶段只面向 Qt/EXE 端的用户自然语言对话链路，不考虑 Web 端。

范围内：

- Qt 用户页自然语言输入
- 语音识别后的文本输入
- 本地工具调用
- 本地会话状态
- 本地执行门禁
- 本地日志归档
- EXE 打包后的记忆/经验存储

当前实现完成度、四份需求文档逐项映射和剩余缺口，见 `docs/四份自然语言需求当前完成度审计.md`。该审计文档以当前代码、自动对话模拟器报告和回归测试结果为证据，不以设计意图作为完成依据。

范围外：

- Web API 的 NLP 行为统一
- Web session state
- Web 端工具调用服务化
- 多端共享会话状态

后续如果需要恢复 Web 端，需要把本方案中的 Tool Layer、SessionState、ExecutionGate 抽成独立服务层，再由 Qt 和 Web 共用。当前阶段先收敛 Qt/EXE 主链路，避免范围扩大。

## 1. 背景问题

当前自然语言系统已经具备多种能力：状态查询、流程执行、流程草案、受限 Agent、安全预检、聊天解释、旧规则解析等。但真实对话日志显示，体验上存在“乱”和“没逻辑”的问题。

主要表现：

- 同一轮对话中，新 `AgentOrchestrator`、旧 `VoiceNlpAdapter`、`jieba_rule`、DeepSeek chat 等路径交替处理。
- 多轮任务上下文不稳定，例如“创建流程 -> 说流程名 -> 添加步骤 -> 补坐标 -> 查看流程”容易断链。
- 日志中出现 `engine=pending`、`execution.result=pending`、`response.final=""`，说明某些路径没有完整收尾。
- chat/LLM 回复可能出现“已创建”“已登记成功”等动作承诺，但实际没有绑定到明确状态变更。
- `X 一百`、`小镇`、`点投`、`速度二` 等真实语音/ASR 结果没有稳定归一化。
- `确认执行`、流程草案、待确认计划、执行门禁没有形成一个清晰统一的状态机。

核心问题不是“大模型能不能理解”，而是系统缺少一套统一的：

- 工具边界
- 会话状态
- 事实来源
- 执行门禁
- 失败收尾
- 学习审核机制

## 2. 用户的目标想法

用户希望采用类似 LangChain tool-calling 的方式：

- 把系统能力拆成一组明确的本地工具。
- Agent 只负责和用户对话、理解意图、选择合适工具、补齐上下文、生成结构化结果。
- 大模型/ LangChain 不负责写命令、不负责控制器执行、不负责绕过安全规则。
- 最终执行必须由本地确定性代码完成，并经过唤醒词、参数边界、安全预检、确认状态机等硬规则。
- Agent 需要能管理上下文，并逐步学习用户习惯、ASR 纠错、常用流程表达等经验。

一句话原则：

> LLM 可以决定下一步应该调用哪个本地能力，但不能决定系统事实已经发生。系统事实只能由本地工具返回值产生。

补充原则：

- LLM 不能直接拼装“已创建”“已添加”“已保存”“已执行”等事实陈述。用户可见回复中的事实必须能在 `ToolResult.state/message/data/errors` 或本地执行结果中找到来源；LLM 只能做措辞组织，不能推断工具未返回的状态。
- 输入归一化必须保持双轨：`raw_text` 原文和 `normalized_text` 归一化文本同时进入归档和状态。记忆纠错、ASR 别名或中文数字归一化失败时，系统必须能回退到 `raw_text` 重新判断，不能让错误 active memory 污染唯一事实来源。
- 日志归档是横切关注点，不是成功执行后的终点。聊天、追问、拒绝、失败、超时、控制器写失败等路径都必须通过统一归档出口收尾。
- 不可学习区是硬约束：寄存器地址、安全边界值、权限矩阵、跳过确认规则、控制器协议常量、控制器参数默认值只能来自代码或配置文件，不能来自 LLM 推断、用户投票、离线学习或 active memory。

## 3. 架构边界

### 3.1 LangChain / LLM 负责什么

LangChain 或类似 Agent 框架可以负责：

- 对话上下文管理
- 意图识别
- 工具选择
- 缺失信息判断
- 澄清问题生成
- 将用户自然语言映射到结构化 tool call
- 从工具结果生成自然语言回复
- 基于审核后的记忆改进理解

示例：

```json
{
  "intent": "flow_append_step",
  "tool": "append_flow_step",
  "missing": ["position_A_pose"],
  "next_question": "位置A的坐标是多少？"
}
```

### 3.2 本地工具负责什么

本地工具负责产生事实和状态变化：

- 查询设备状态
- 查询报警
- 查询当前位置
- 查询流程列表
- 创建流程草案
- 设置流程名
- 添加流程步骤
- 修改步骤参数
- 查询当前草案
- 查询知识库命令 schema
- 参数解析和归一化
- 参数边界检查
- 安全预检
- 创建待确认计划
- 消费确认状态

工具必须返回结构化结果，而不是只返回自由文本。

示例：

```json
{
  "ok": true,
  "state": "flow_draft_updated",
  "draft_id": "draft-001",
  "flow_name": "测试",
  "message": "已添加第1步，等待补充坐标"
}
```

### 3.3 入口门禁和执行门禁负责什么

入口门禁和执行门禁必须完全留在本地确定性代码中。

入口门禁负责在工具选择或业务状态变更前判断：

- 是否存在唤醒词
- 当前用户权限是否允许
- 当前系统模式是否允许该类工具，例如新手/专家/确认模式
- 当前工具是否允许由 LLM 触发

执行门禁负责在写控制器前判断：

- 参数是否完整
- 参数是否通过边界检查
- 是否通过安全预检
- 是否存在有效 pending confirm
- 用户是否确认
- 确认是否超时或签名变化
- 是否允许生成执行记录并写控制器

建议固定检查顺序：

1. 入口分类：判断是否为控制类、流程编辑类、确认类、查询/解释类。
2. 入口门禁：控制类动作先检查唤醒词、用户权限、系统模式权限、工具触发权限；非执行查询和解释可跳过唤醒词。
3. 参数完整性：缺参数只追问缺失字段。
4. 参数边界：边界失败直接拒绝。
5. 安全预检：预检失败直接拒绝。
6. pending confirm：创建或校验待确认状态。
7. 用户确认：确认通过后才允许生成执行记录。
8. 写控制器：只有本地执行层可以写 Modbus。

任一步不通过都必须短路返回明确拒绝或追问，不继续后续检查，不产生控制器写入。缺参数时只追问缺失字段，不进入边界检查和安全预检；边界或安全预检失败时不能创建可执行确认计划。所有失败路径都必须写入最终归档结果，不能停留在 `pending`。

LangChain 和 LLM 不能绕过这些规则。

### 3.4 禁止事项

LLM / LangChain 不允许：

- 直接写 Modbus
- 直接生成控制器寄存器写入
- 决定是否可以执行
- 绕过唤醒词
- 绕过确认
- 绕过安全预检
- 修改安全边界
- 修改命令函数号或寄存器地址
- 在没有工具成功返回时说“已创建”“已保存”“已执行”

当前代码还存在一条需要显式收口的绕过路径：UI 层的 streaming chat 可以在 DeepSeek 打开时直接生成流式回复。后续任何 streaming chat 或 LLM 直接回复路径，都必须先经过结构化意图分类，例如 `CommandUnderstandingAgent.understand()` 或目标 `command_tools.parse_command_intent()`。如果判断为控制类、流程编辑类、确认类或可能产生业务状态变化的输入，不能走 streaming chat，必须进入完整 Tool Layer + ExecutionGate 链路。

UI 层不能只用关键词 if-else 判断“是否聊天”。关键词规则可以作为快速过滤，但最终是否允许 LLM 直接回复，必须由结构化意图分类和本地门禁决定。

## 4. 工具分组需求

### 4.0 现有 agent 模块与目标工具层差距

阶段 2 不应重新写一套业务能力，而应优先把现有 `robot_modbus_lite/agent/` 中已经存在的能力包装成稳定 tool schema。核心差距是：现有模块多返回 `VoiceNlpPlan`、自然语言回复或 UI 可直接消费的计划对象；目标工具层必须返回统一的 `ok/state/message/data/errors`，并由 `SessionState` 和 `ExecutionGate` 统一收口。

建议对照：

| 现有模块 | 目标工具层 | 需要补齐的差距 |
|---|---|---|
| `agent/flow_draft.py` | `flow_tools.start_flow_draft()`、`flow_tools.set_flow_name()`、`flow_tools.append_flow_step()`、`flow_tools.query_current_draft()` | 把 UI 草案状态转换为显式 `SessionState.flow_draft`；工具成功后才允许回复“已创建/已添加”。 |
| `agent/drafts.py` | `command_tools.build_command_draft()`、`flow_tools.prepare_flow_execution()` | 返回结构化草案数据、缺失字段、可确认状态，而不是直接生成执行计划文案。 |
| `agent/confirmation.py` | `safety_tools.create_pending_confirm()`、`safety_tools.confirm_pending_plan()`、`safety_tools.cancel_pending_plan()` | 确认必须消费本地 pending 状态；取消和超时必须清理状态并归档。 |
| `agent/safety_review.py` | `safety_tools.run_safety_precheck()` | 安全结论必须是结构化结果，失败不能进入确认。 |
| `agent/command_understanding.py` | `command_tools.lookup_command_schema()`、`command_tools.parse_command_params()` | 基础中文数字参数归一化已落地；ASR 标准词/方言/同音纠错和坐标轴误听归一化已接入；`CommandUnderstandingResult`、`VoiceNlpPlan` 和 interaction archive 的 `input/nlp_result` 已保留 `raw_text/normalized_text` 双轨；仍需补缺参 `missing_fields`。 |
| `agent/parameter_completion.py` | `command_tools.validate_required_params()`、`command_tools.complete_params_from_state()` | 自动补全必须标明来源，不能把快照值伪装成用户输入。 |
| `agent/compound.py` | `command_tools.split_compound_command()`、`flow_tools.build_compound_draft()` 或 `compound_tools.plan_compound()` | 复合指令要拆成多步草案并进入统一 `SessionState`；每一步确认、失败、回滚和幂等都要可追踪。 |
| `agent/position_query.py` | `status_tools.get_position()` | 查询结果作为事实工具返回，供 Agent 解释。 |
| `agent/dashboard_query.py`、`agent/axis_status.py`、`agent/alarm_explanation.py` | `status_tools.get_status()`、`status_tools.get_alarm()`、`chat_tools.explain_alarm()` | 区分事实查询和解释文案，解释不能修改业务状态。 |
| `agent/chat_explanation.py` | `chat_tools.explain_*()`、`chat_tools.suggest_flow_steps()` | 只能解释、建议、追问；禁止动作完成承诺。 |
| `agent/llm_fallback.py` | 废弃，或降级为 `chat_tools.fallback_explain()` | 不能作为业务 fallback 绕过工具层；不能产生“已执行/已保存”等事实。 |
| `agent/orchestrator.py` | 短期规则 router；长期 LangChain 不可用时的兼容 fallback | 主链路迁移到 `SessionState + ToolResult + ExecutionGate` 后，硬编码 if-else 不再作为优先路由。 |

### 4.1 基础状态和系统工具

这一类工具应确定性实现，不依赖 LLM 自由发挥。

建议工具：

- `get_status()`
- `get_position()`
- `get_alarm()`
- `get_execution_progress()`
- `pause()`
- `resume()`
- `estop()`
- `alarm_reset()`

要求：

- 状态查询类不需要唤醒词。
- 系统控制类是否需要唤醒词应作为明确产品策略固定下来。
- 急停、暂停、继续、报警复位必须有固定 semantic level 和安全策略。

### 4.2 命令知识库和参数工具

这一类工具负责把用户口语映射到本地可校验参数。

建议工具：

- `lookup_command_schema(command_name)`
- `lookup_command_alias(text)`
- `parse_command_params(text, schema)`
- `normalize_asr_text(text)`
- `normalize_chinese_numbers(text)`
- `validate_required_params(params, schema)`
- `check_param_bounds(params, schema)`
- `build_command_draft(intent, params)`

要求：

- 参数解析必须可追踪原文和归一化文本。
- 缺参数时返回 `missing_fields`，由 Agent 追问。
- 边界检查不能由 LLM 决定。
- ASR 纠错要保留证据和可回滚记录。

### 4.3 流程草案工具

这一类工具负责多轮流程创建和编辑。

建议工具：

- `start_flow_draft(flow_name=None)`
- `set_flow_name(flow_name)`
- `append_flow_step(step)`
- `update_step_params(step_id, params)`
- `delete_flow_step(step_id)`
- `query_current_draft()`
- `save_flow_draft()`
- `prepare_flow_execution()`

要求：

- “创建流程”“流程名字叫测试”“添加第一步”“看一下刚刚的流程”必须进入同一个 session state。
- 任何回复“已创建流程草案”“已添加步骤”必须由工具成功返回支撑。
- 流程草案状态以本地状态为准，例如 `_operator_pending_flow_draft` 或后续统一的 `SessionState.flow_draft`。

### 4.4 安全和确认工具

建议工具：

- `run_safety_precheck(draft)`
- `create_pending_confirm(draft)`
- `query_pending_confirm()`
- `confirm_pending_plan()`
- `cancel_pending_plan()`
- `expire_pending_plan()`

要求：

- Agent 只能创建草案或请求确认，不能直接执行。
- 确认必须消费本地 pending confirm 状态。
- 没有待确认计划时说“确认执行”，必须明确提示没有待确认。
- 确认超时必须清理状态并归档。

### 4.5 聊天解释工具

建议工具：

- `explain_capability()`
- `explain_confirmation()`
- `explain_alarm(code)`
- `explain_out_of_scope(question)`
- `suggest_flow_steps(context)`

要求：

- chat 工具只能解释、建议、追问。
- chat 回复禁止包含动作完成承诺。
- 如果确实需要创建/修改/保存，必须走对应业务工具。

## 5. 会话状态需求

系统需要统一的 `SessionState`，避免状态散落在 UI、Agent、旧 adapter、ExecutionPlanService 中。

当前代码不是单纯“缺少一个 SessionState 对象”，而是已经存在多套并行状态机：

| 状态来源 | 当前位置 | 当前问题 |
|---|---|---|
| `ExecutionPlanStatus` / `pending_plan` | `execution_plan.py`、`execution_plan_service.py` | 管流程草案和执行计划，但和 Agent 确认状态不同步。 |
| `DraftStatus` / `ConfirmationAgent._sessions` | `agent/confirmation.py` | 管单条命令确认，但生命周期依赖 `ConfirmationAgent` 实例。 |
| `CompoundStepMachine` | `agent/compound.py` | 管复合指令逐步确认，但没有进入统一会话状态。 |
| UI mixin 手动 pending 字段 | `operator_ui_mixin.py` 中 `_operator_pending_confirm_plan`、`_operator_pending_flow_draft` 等 | UI 同时承担业务状态管理，Agent 层无法可靠读取。 |

统一 `SessionState` 的含义不是再新增第五套状态，而是把上述四套状态收口到一个会话级对象中。UI mixin 只保留显示状态和事件转发，不再直接持有业务 truth source。

建议状态字段：

```json
{
  "thread_id": "session-id",
  "mode": "idle | clarifying | creating_flow | editing_flow | waiting_params | waiting_confirm | confirm_expired | executing | blocked",
  "current_intent": "flow_create",
  "current_flow_draft": {},
  "pending_missing_fields": [],
  "pending_confirm": {},
  "last_tool_call": {},
  "last_user_text": "",
  "last_normalized_text": "",
  "applied_memories": [],
  "safety_context": {}
}
```

状态规则：

- 每轮输入必须读取当前 state。
- 每个 tool 成功后必须更新 state。
- 每轮结束必须落到明确状态。
- 不允许长期停在 `pending`。
- UI 日志中的 `pending` 只允许作为短暂初始状态，不能作为最终状态。
- `clarifying` 表示 Agent 正在追问缺失意图或缺失参数，下一轮用户输入必须优先回填当前 `pending_missing_fields`。
- `confirm_expired` 表示待确认计划已超时或控制器/安全签名变化，必须清理可执行 pending 状态；下一句“确认执行”不能继续消费旧计划。
- `AgentOrchestrator`、`RestrictedAgentService`、`ConfirmationAgent`、复合指令协调器等带状态组件必须会话级复用，不能每轮输入重新创建后丢失内部状态。

## 6. 自我学习需求

用户希望系统能逐步学习用户操作习惯和真实语音误识别模式。这里的“学习”不是训练模型权重，而是沉淀可审核的经验记忆。

### 6.1 在线投票学习

在关键场景后收集轻量反馈。

示例：

```text
我把“小镇执行点头流程”理解为“小正执行点头流程”，以后也这样处理吗？
[记住] [仅本次] [不对]
```

适合触发投票的场景：

- 系统自动纠错
- 系统创建候选经验
- 用户取消或否定结果
- 流程草案创建完成
- 同类表达重复出现多次

不适合每轮都投票，避免打扰用户。

### 6.2 离线定期学习

每隔一段时间分析历史日志，生成候选经验。

输入：

- `interaction_session_*.jsonl`
- 用户反馈记录
- 成功/失败执行记录
- pending/空回复案例

输出候选经验：

- ASR 别名
- 用户常用流程名
- 用户默认参数偏好
- 高频失败句式
- 成功纠错样本
- 常见澄清模板

### 6.3 候选经验审核

经验不能直接生效，建议三段式：

```text
candidate_memory -> reviewed_memory -> active_memory
```

候选经验示例：

```json
{
  "type": "asr_alias",
  "source": "小镇",
  "target": "小正",
  "confidence": 0.92,
  "evidence_count": 18,
  "status": "candidate"
}
```

审核通过后才允许进入 active memory。

### 6.4 允许学习的内容

允许学习：

- ASR 纠错：`小镇 -> 小正`
- 口语别名：`点投流程 -> 点头流程`
- 用户偏好：默认速度、默认加速度策略
- 常用流程命名
- 常见流程创建顺序
- 某类失败后的推荐澄清方式
- 非安全相关的对话策略

禁止学习：

- 安全边界
- 唤醒词绕过规则
- 执行权限
- 急停、暂停、继续的硬规则
- Modbus 寄存器地址
- 函数号映射
- 参数最大/最小安全范围
- 跳过确认策略

### 6.5 不可学习区硬约束

以下内容属于执行事实或安全事实，永远不能进入经验库，也不能被 active memory 覆盖：

- Modbus 寄存器地址
- 函数号映射
- 安全边界值
- 权限矩阵
- 唤醒词绕过规则
- 跳过确认策略
- 控制器协议常量
- 控制器参数默认值

这些内容只能来自代码、受控配置文件或人工审核后的正式配置变更。用户投票、离线日志学习、LLM 推断、LangChain memory、SQLite active memory 都不能改变这些值。若记忆候选涉及不可学习区，必须直接拒绝或标记为 `forbidden_memory_candidate`，不能进入 `candidate_memory`。

## 7. 框架选型整理

### 7.1 LangGraph / Pydantic / DeepSeekToolDecider

定位：

- `LangGraph` 作为多轮状态机框架。
- `Pydantic` 作为本地工具输入/输出 schema 和校验层。
- `DeepSeekToolDecider` 作为窄口径 LLM 工具选择器，只返回 `tool_name + args + tool_call_id`。
- `LocalToolRegistry` 作为唯一工具执行入口。
- 完整 LangChain Agent 体系不作为当前目标。

适合当前目标的是 LangGraph 和 Pydantic，不是完整 LangChain Agent。

使用边界：

- 不使用 `AgentExecutor` / ReAct / 自由规划式 Agent 作为在线主控。
- 不让 LangChain memory 保存不可审核的安全事实。
- LLM 只选择工具和填参数，不能直接组织执行事实。
- Pydantic schema 是工具边界的主要约束；LangChain `StructuredTool` 可作为可选适配层，但不是核心依赖。

推荐组合：

```text
LangGraph: 只管理有分支/循环的状态机部分，例如 flow draft 多轮追问、pending confirm 等待/超时、compound 拆分循环
Pydantic: 本地工具输入/输出 schema、必填项、类型和边界前置校验
DeepSeekToolDecider: 窄 JSON 工具决策协议，保留并加强校验
SQLite/JSON Memory: 可审核长期经验记忆
ExecutionGate: 本地安全执行门禁
```

### 7.1.1 当前项目是否需要 LangChain

结论：当前项目需要的是 `LangGraph` 这部分能力，不需要把完整 LangChain Agent 体系作为终点。

更准确地说：

- 需要：`LangGraph` 的状态机能力，用于 flow draft 多轮追问、pending confirm 等待/超时、compound 分步确认这类有分支、有循环的对话流程。
- 需要：`Pydantic` 的工具 schema 能力，用于在本地工具执行前做参数名、类型、必填项、枚举、边界和跨字段约束校验。
- 保留：`DeepSeekToolDecider` 的窄 JSON 工具决策协议。它只产出 `tool_name + args + tool_call_id`，不直接执行、不自由规划、不绕过门禁。
- 不需要作为主链路：`AgentExecutor`、ReAct、自由 tool-calling Agent、LangChain memory 主控。
- 可选但非核心：`StructuredTool.from_function()` 只作为兼容 LangChain 生态的包装层，不能替代本地 `LocalToolRegistry + Pydantic schema + ExecutionGate`。

因此后续不是“继续接入更多 LangChain”，而是：

1. 把已接入的 LangGraph 用深，覆盖真正的多轮状态机和确认等待循环。
2. 继续补 Pydantic schema 的字段级和跨字段业务边界。
3. 保持 DeepSeekToolDecider 的窄协议，不切换成 AgentExecutor 或 ReAct。
4. 所有事实、状态变化和执行结果都以 `ToolResult`、`SessionState`、本地执行层为准。

当前落地状态需要区分：

- 已落地：`LangGraph` 协议层、默认多节点 graph（`check_pending_timeout -> expire_pending_state/sync_compound_step_result/decide_tool -> call_tool -> sync_flow_state/sync_confirm_state/sync_compound_state/local_rules`）、flow draft 专用状态同步节点、pending confirm 专用状态同步节点、pending confirm 入口超时短路、compound 专用状态同步节点、compound step result 事件短路节点、`local_rules` 兜底路径的 flow 参数回填状态回写、flow 追加步骤内联 delay/io 参数回填、明确步骤序号的 flow 多步追加、Qt 复合步骤执行结果到 `compound_step_result` 的 bridge 转发，且 graph 同步异常时会回退本地 `SessionState.advance_compound_step()` 并在 payload 中保留 `graph_error` 审计字段、compound plan 写入 `active_step_index/active_step/active_step_result`、`SessionState.advance_compound_step()` 纯状态推进/完成/失败收口、`DeepSeekToolDecider` JSON 工具决策、本地 `LocalToolRegistry` 执行、Pydantic 工具输入/输出 schema、registry 调用前参数校验和调用后输出校验、命令草案类工具字段级与 confirmed 生命周期校验、控制类文本工具非空边界、命令 schema lookup key 非空边界、`validate_required_params.func_id > 0`、命令地址解析名称非空边界、移动/连续路径类草案 `func_id=8/102/108/112` 位姿/速度/模式边界、关节/虚拟轴草案 `axis_no/pos_val/spd_pct/acc_pct/dec_pct` 边界、位置别名名称/pose/spd/move_type schema 边界、`check_param_bounds` 的 bounds schema 版本要求和速度类百分比基础边界、确认类 `draft_id` 非空边界、已登记流程执行的流程名非空边界、Memory 工具 `memory_id/kind` 非空边界、feedback vote 的 `interaction_id/target_id` 非空和 `target_type/vote` 枚举边界、`create_memory_candidate` 的非空 `kind/key` 与 `confidence` `[0,1]` 边界、Memory 不可学习区初版硬拦截、`rollback_memory` 回滚工具链和审计事件、`query_memory_review` 审核查询工具、Qt 用户页轻量经验审核/批准/回滚命令入口、按 `kind` 筛选经验列表、批量批准候选经验、批量停用/回滚 active 经验命令、`MemoryReviewView/MemoryReviewRow` 列表与详情 presenter、`operator_ui_mixin.py` 右侧栏真实 memory 审核表格/详情/筛选控件、筛选触发查询、表格行选择联动详情、选中经验的批准/停用/回滚按钮、confirmed 后执行失败回滚入口（普通确认计划同步执行异常、Agent 草稿同步异常与自然语言异步终止日志），异步终止日志中 runtime 失败记录自身异常也会进入归档文本、复合步骤失败/完成/取消的 runtime 状态收口、确认门禁异常 fail-safe 拒绝、部分 `StructuredTool.from_function()` 包装函数。
- 未落地且不作为主目标：LangChain 原生 `AgentExecutor`、ReAct、自由 tool-calling Agent、LangChain memory 主控。
- 未完全落地但需要推进：LangGraph 内部 compound 逐步确认循环，flow draft 更深的步骤回填循环，更多 Pydantic 字段级业务边界和跨字段约束，复合步骤更细的控制器级异常演练。
- 准确表述为：`LangGraph runtime + DeepSeek JSON tool-decider + Pydantic schema + 本地工具层`。这不是“没接 LangChain”，而是只保留最适合工业控制的 LangGraph 部分。

### 7.2 Mem0

定位：

- 独立长期记忆层
- 适合快速接入用户偏好、ASR 纠错、历史经验检索

适用：

- 如果暂时不想强绑定 LangChain 生态，可以用 Mem0 做 memory layer。

### 7.2.1 SQLite / 现有 JSON 记忆层

短期优先采用现有 JSON 文件和 SQLite 作为记忆层，而不是一开始引入 Mem0/LangMem。

当前已有可复用的数据文件：

- `data/memory_params.json`
- `data/nlp_standard_words.json`
- `data/flow_phrase_aliases.json`
- `data/dashboard_query_aliases.json`
- `data/voice_wake_words.json`
- `data/flow_registry.json`
- `data/flows.json`

这些可以视为 active memory / normalization memory 的雏形。后续可以逐步迁移到 SQLite，或保留 JSON 作为种子数据。

SQLite 用途：

- 存储 active memory
- 存储 candidate memory
- 存储用户投票反馈
- 存储 ASR 纠错证据
- 存储经验生效/回滚记录
- 存储 tool-call trace 摘要

SQLite 适合 EXE：

- Python 标准库自带 `sqlite3`，常规 PyInstaller/Nuitka 打包时不需要额外数据库服务。
- SQLite 数据库是普通文件，例如 `agent_memory.sqlite3`。
- EXE 内置资源目录通常不适合直接写入数据库，因此运行时数据库应放在可写目录。

建议路径：

```text
<runtime_root>/data/agent_memory.sqlite3
```

或 Windows 用户数据目录：

```text
%APPDATA%/<app_name>/agent_memory.sqlite3
```

打包原则：

- 可以把空库 schema 或种子 JSON 打进 EXE。
- 首次启动时复制/初始化到可写 data 目录。
- 运行时只读写外部可写目录中的 SQLite 文件。
- 不要直接写 PyInstaller onefile 解压临时目录里的数据库。
- 数据库 schema 需要版本号和迁移逻辑。

短期结论：

> 第一版自学习不需要先上 Mem0/LangMem。先用 SQLite + 现有 JSON，把候选经验、投票反馈、ASR 别名和生效经验管起来；后续再考虑接 LangMem/Mem0。

### 7.3 Graphiti / Cognee

定位：

- 图谱化长期记忆和知识库
- 适合处理随时间变化的事实、文档和历史关系

适用：

- 命令手册、报警手册、流程演化、用户历史操作关系图。

### 7.4 Hermes Agent

定位：

- 长期运行的个人/开发者自治 Agent
- 有 memory、skill creation、自我改进能力

建议：

- 不作为机械手在线主控对话框架。
- 可作为离线日志分析、经验总结、候选规则生成工具。
- 自动 skill 生成必须经过审核，不能直接影响在线安全执行。

## 8. 推荐目标架构

推荐目标架构：

```text
User Input
  ↓
Input Normalizer
  - ASR纠错
  - 中文数字参数归一化已覆盖坐标、速度、延时和增量距离的基础场景；ASR 纠错已接入标准词、方言/同音词和坐标轴误听别名；`CommandUnderstandingResult`、`VoiceNlpPlan`、interaction archive 的 `input/nlp_result` 已保留原文和归一化文本。
  - 保留 raw_text / normalized_text
  - 归一化失败可回退 raw_text
  ↓
SessionState / LangGraph State Machine
  ↓
DeepSeekToolDecider / Local Rule Router
  - 读状态
  - 选择工具
  - 填充 Pydantic tool args
  - 生成澄清
  - 不执行
  ↓
Local Tool Layer
  - status tools
  - command knowledge tools
  - flow draft tools
  - safety tools
  - chat tools
  ↓
Structured Tool Result
  ├─ needs_clarification / missing_fields
  │    ↓
  │  Agent 生成追问
  │    ↓
  │  用户补充输入
  │    ↺ 回到 SessionState
  ↓
ExecutionGate
  - 入口门禁：唤醒词、用户权限、系统模式权限、工具触发权限
  - 执行门禁：参数完整性、边界、安全预检、pending confirm、用户确认
  ↓
Final Response / Pending Confirm / Execute
  - 参数不完整则追问，不执行
  - 拒绝则返回原因，不执行
  - confirmed 后控制器写失败必须回滚执行状态，已覆盖同步异常和自然语言异步终止日志路径；异步日志路径中 runtime 失败记录自身异常也必须进入归档 `final_text`
  ↓
Interaction Archive（横切每个节点）
  - 每轮、每分支、每失败路径都必须收尾
  - 记录 raw_text / normalized_text / tool call / state / final
  ↓
Learning Pipeline
  - 投票反馈
  - 离线日志分析
  - 候选经验
  - 审核后生效
  - SQLite/JSON 记忆落地
```

说明：

- 当前阶段该架构只服务 Qt/EXE。
- Web 端不参与本轮重构和验收。
- SQLite 位于运行时可写目录，随 EXE 使用但不直接写入 EXE 内部资源。
- 澄清/追问不是异常路径，而是正常状态机回环；`missing_fields`、`needs_clarification`、`flow_draft_needs_name` 等状态必须回到同一 `SessionState` 等待用户补充。
- 归档不是链路末端的附加动作，而是每个状态节点的 finalize 钩子。
- 完整 LangChain Agent/AgentExecutor 不进入在线主控路径；需要做深的是 LangGraph 多节点状态机和 Pydantic schema。

## 9. 关键验收要求

### 9.1 对话链路验收

- 每轮非空输入必须有最终回复。
- 不允许最终停在 `engine=pending`。
- 不允许最终停在 `execution.result=pending`。
- Agent 回复必须能追溯到 tool result 或 chat explain。
- 同一个流程创建任务必须持续使用同一个 session state。
- 所有非执行路径也必须归档收尾，包括 chat answer、clarification、flow draft、candidate rejected、LLM suggestion、streaming chat fallback。
- 非执行路径必须写入 `execution.result="skipped"` 或更具体的非执行结果，并写入 `response.final=<实际回复文本>`。
- 交互归档需要统一出口，例如 `_archive_non_execution_result(result, final_text)` 或 pipeline finalize 装饰器；不能依赖每个分支手动记得调用 `update_record()`。
- “收尾”必须同时满足三件事：响应收尾、日志收尾、状态机收尾。响应收尾要求返回非空、非 pending 的用户回复；日志收尾要求结构化归档从 pending 移到明确结果；状态机收尾要求 `SessionState.current_intent/pending_confirm/current_flow_draft/pending_missing_fields` 落到稳定状态。
- `raw_text`、`normalized_text` 和已应用记忆必须同时归档；归一化或记忆应用导致解析失败时，必须保留从 `raw_text` 重新判断的能力。

### 9.2 工具调用验收

- 每个工具有固定输入输出 schema。
- 工具返回 `ok/state/message/data/errors`。
- 工具失败必须返回明确错误，不能抛到 UI 空回复。
- 工具成功后才能更新系统事实。
- chat 工具不能更新业务事实。
- 所有用户可见事实陈述必须来自 `ToolResult` 或本地执行结果。LLM 不能在组织回复时新增工具未返回的事实。
- 每个工具必须有 Pydantic 输入/输出 schema，至少覆盖参数名、类型、必填项、默认值和基础边界。`StructuredTool` 只能作为可选适配层；占位描述和单一 `payload_json` 不能算完成工具 schema。
- 查询类工具必须幂等，同一参数重复调用不能产生副作用。
- 修改类工具必须具备 `idempotency_key`、草案版本号或重复调用拒绝机制；同一 tool 连续两次相同参数调用，第二次必须返回明确幂等结果或明确拒绝，不能产生未追踪的重复步骤、重复确认计划或重复执行计划。
- 幂等检查范围必须明确：同一 `tool_call_id` 在当前会话内永久幂等；同一 `draft_id` 或 `confirm_id` 在 pending 生命周期内幂等；已 `confirmed/rejected/expired/cancelled` 的确认计划不可再次确认。
- 对没有 `draft_id` 的修改类工具，必须使用草案版本号或短时间窗口去重，建议默认窗口为 60 秒；窗口外重复调用也必须能通过日志追踪为新操作。

### 9.3 执行门禁验收

- 无唤醒词不执行控制类动作，除非产品策略明确例外。
- 参数不完整不执行。
- 边界检查不通过不执行。
- 未确认不执行。
- 确认超时不执行。
- LLM 不能直接创建执行事实。
- ExecutionGate 或安全预检内部异常必须 fail-safe，按“拒绝执行”处理并归档，不能默认放行。
- `cancel_pending_plan()` 后必须清理 pending confirm、pending execution、临时执行草案等状态，并把 `SessionState` 回到明确的 `idle` 或 `editing_flow`。
- 取消、超时、拒绝后不能残留可被下一句“确认执行”误消费的脏数据。
- pending confirm 转 confirmed 后，如果控制器写失败，必须进入执行失败回滚路径：清理可重复消费的 confirmed/pending 状态，记录执行失败原因，保留失败执行记录和用户可见响应，禁止下一句“确认执行”重复消费旧计划。
- `AgentOrchestrator` 及其内部 `RestrictedAgentService`、`ConfirmationAgent`、复合指令状态机必须在会话级别单例化或由 `SessionState` 持久化；不能每轮输入重建导致 `DraftSession`、复合步骤或待确认状态丢失。
- `cancel_pending_plan()` 必须清理统一 `SessionState` 中的 pending 状态；如果迁移期仍存在 UI pending、`ExecutionPlanService.pending_plan`、`ConfirmationAgent._sessions` 等兼容状态，也必须同步清理。

### 9.4 学习机制验收

- 学习内容必须可追踪来源。
- 候选经验默认不生效。
- 安全相关规则不可学习覆盖。
- 生效经验可以回滚。
- 每次应用经验要记录 `memory_applied`。
- SQLite 数据库能在 EXE 运行目录或用户数据目录中创建、读写和备份。
- 初始 JSON 经验能导入 SQLite 或作为只读种子参与初始化。
- 不可学习区必须有代码层硬约束，不能只靠文档约束。候选经验命中寄存器地址、安全边界、权限矩阵、确认/专家/新手模式、自动执行/直接执行策略、跳过确认规则、控制器协议常量、控制器参数默认值时必须拒绝。当前 `AgentMemoryStore.create_candidate()` 已对明确 kind 和关键词做硬拦截，工具层返回 `forbidden_memory_candidate`；`query_memory_review` 已能按 `status/kind` 查询 candidate/active/disabled/rolled_back 经验并附带每条记忆的审计事件；Qt 用户页已支持“查看待审核经验/查看生效经验/查看已回滚经验/查看停用经验”“查看生效经验 asr_alias”“批准经验 <memory_id>”“回滚经验 <memory_id>”“批准全部待审核经验”“停用全部生效经验”“回滚全部生效经验”这类轻量命令入口。后续需要继续扩充安全事实样本，并补完整 active memory 审核/回滚 UI 的可视化列表、详情面板和显式筛选控件。

### 9.5 Qt/EXE 范围验收

- 本阶段不要求 Web 端行为一致。
- Qt 用户页的自然语言输入必须走统一 Tool Layer / SessionState / ExecutionGate。
- EXE 打包后仍能读写 `agent_memory.sqlite3`。
- EXE 打包后仍能读取现有 data JSON 种子文件。
- EXE 升级不应覆盖用户运行时记忆库。

### 9.6 故障模型验收

| 故障 | 必须行为 | 验收要求 |
|---|---|---|
| LLM / DeepSeek 不可用 | 降级到本地规则 runner 或兼容 fallback | 用户得到明确回复，日志不 pending；控制类动作仍不能绕过门禁。 |
| LangChain / LangGraph 依赖不可用 | 降级到 `LocalToolCallingRunner` 或兼容 `AgentOrchestrator` | 不影响基础查询、流程草案、系统动作和确认门禁的确定性路径。 |
| 工具调用抛异常 | `LocalToolRegistry.call()` 捕获并返回 `ToolResult.failure` | UI 不空回复，归档包含 `TOOL_CALL_FAILED`。 |
| LangGraph 事件同步异常 | bridge 捕获异常并回退本地 `SessionState` 推进 | 复合步骤结果不能丢；返回 payload 包含 `graph_error`，并写入 agent 日志用于审计。 |
| 安全预检或 ExecutionGate 异常 | fail-safe 拒绝执行 | 不能写控制器；归档为 blocked/rejected。当前工具层预检异常会被 `LocalToolRegistry` 收口为失败 `ToolResult`，UI 确认门禁异常会直接 blocked 归档。 |
| confirmed 后控制器写失败 | 执行失败回滚 | 清理可消费确认状态，保留失败执行记录，下一句“确认执行”不能重复执行旧计划。当前已覆盖普通确认计划同步执行异常、Agent 草稿同步执行异常、自然语言异步终止日志路径、异步日志路径中 runtime 失败记录自身异常的归档线索，以及复合步骤 confirmed 后控制器写失败时将 `current_compound_plan` 标记为 `failed/blocked`。 |
| SessionState 跨线程访问 | thread_id 隔离，必要时加锁或单线程约束 | Qt 多线程/异步路径不能串用其他会话 pending 状态。 |
| active memory 错纠 | 保留 raw_text 并可回退 | 归档 `applied_memories`，错误记忆可 disable/rollback。 |

## 10. 建议实施阶段

### 阶段 1：稳定现有链路

目标：

- 修复 pending 兜底收尾。
- 禁止 chat 动作承诺。
- 增加固定回放测试。
- 统一非执行路径归档收尾，覆盖 chat、clarification、flow draft、candidate rejected、LLM suggestion、streaming chat fallback。
- 控制类输入不得进入 streaming chat 直出路径。

输出：

- `tools/replay_interaction_loops.py`
- 基础断言：`NO_PENDING`、`NO_EMPTY_FINAL`、`NO_CHAT_ACTION_PROMISE`
- 非执行归档出口：`_archive_non_execution_result()` 或等价 pipeline finalize 层
- streaming chat 控制类意图拦截断言

### 阶段 2 前置：拆分 UI mixin 业务职责

目标：

- 将 `operator_ui_mixin.py` 中的业务状态管理抽出，至少包括 pending confirm、pending flow draft、scene override、pending interruption。
- 将 Agent 编排抽出，至少包括 orchestrator 构建、result 分流、fallback 处理、归档收尾。
- 将流程编辑逻辑抽出，至少包括流程草案创建、步骤编辑、坐标补全、保存、准备执行。
- UI mixin 只保留 UI 构建、事件绑定、显示刷新和事件转发。

输出：

- 会话级 `SessionState` 管理模块。
- 会话级 Agent runtime / orchestrator provider，保证带状态组件不按输入轮次重建。
- 流程草案服务或 `flow_tools` 前置服务。
- 归档 finalize 服务或统一出口。
- 当前进展：`operator_memory_review.py` 已抽出 active memory 审核/批准/回滚命令解析、按 kind 筛选、批量批准候选经验、批量停用/回滚 active 经验逻辑，并提供 `MemoryReviewView/MemoryReviewRow` 作为后续 Qt 表格和详情面板的数据 presenter；`operator_ui_mixin.py` 右侧栏已放置 `operator_memory_review_table/operator_memory_review_detail/operator_memory_status_filter/operator_memory_kind_filter`，并能在 memory tool result 返回时填充表格、详情和筛选项。

验收：

- UI mixin 不再作为业务状态 truth source。
- `AgentOrchestrator`、`RestrictedAgentService`、`ConfirmationAgent` 的状态能跨轮次存活。
- 取消、超时、确认、流程编辑都通过同一 `SessionState` 反映。

### 阶段 2：抽象本地工具层

目标：

- 把现有能力包装成工具。
- 每个工具固定 schema。
- 先不用 LangChain，也可以通过规则 router 调用。

输出：

- `agent_tools/status_tools.py`：复用 `agent/position_query.py`、`agent/dashboard_query.py`、`agent/axis_status.py`、`agent/alarm_explanation.py`
- `agent_tools/flow_tools.py`：复用 `agent/flow_draft.py`、`agent/registered_flow.py`
- `agent_tools/command_tools.py`：复用 `agent/command_understanding.py`、`agent/drafts.py`、`agent/parameter_completion.py`
- `agent_tools/safety_tools.py`：复用 `agent/confirmation.py`、`agent/safety_review.py`，以及现有安全预检能力
- `agent_tools/chat_tools.py`：复用 `agent/chat_explanation.py`，必要时把 `agent/llm_fallback.py` 限制为解释类 fallback

### 阶段 3：做深 LangGraph 状态机和 Pydantic Schema

目标：

- 用 LangGraph 管有分支/循环的状态机，不把所有简单字段都塞进复杂 graph。
- 用 Pydantic 定义本地工具输入/输出 schema，并在 tool-decider 结果进入 `LocalToolRegistry` 前校验。
- 保留 `DeepSeekToolDecider + JSON 协议`，不切换到 `AgentExecutor`、ReAct 或自由规划式 LangChain Agent。
- 保留本地 ExecutionGate。
- 主链路切换为 LangGraph 根据 `SessionState` 分流，由 `DeepSeekToolDecider` 或本地规则选择工具。
- `AgentOrchestrator.handle()` 不再作为优先路由，只保留为 LLM 不可用、模型失败或兼容模式下的兜底。
- 如果 tool-decider 结果和旧 `AgentOrchestrator` 判断冲突，以 `SessionState + ToolResult + ExecutionGate` 为准。
- 旧 `VoiceNlpAdapter` 如需保留，只能包装成受限 `legacy_parse_tool` 或 chat fallback，不能绕过工具层和执行门禁。

输出：

- 新 Agent 入口
- thread/session checkpointer
- tool-call trace
- 主路由/兼容路由优先级说明
- Pydantic 工具输入/输出 schema
- schema 校验失败的 `ToolResult.failure`
- LangGraph 多节点或带条件边的状态机，至少覆盖 flow draft 追问、pending confirm 等待/超时、compound 步骤循环

### 阶段 4：引入记忆和学习

目标：

- 在线投票反馈。
- 离线日志学习。
- 候选经验审核。
- active memory 应用。
- SQLite 记忆库落地。
- 现有 JSON 经验作为种子数据导入或兼容读取。

输出：

- memory schema
- candidate review UI 或命令
- memory audit log
- `agent_memory.sqlite3`
- SQLite schema migration

### 阶段 5：收敛旧管线

目标：

- 旧 `VoiceNlpAdapter` 作为本地工具或兼容 fallback。
- 在线主链路统一到 tool-calling Agent。
- 删除或限制无状态 chat fallback。
- 阶段 3 上线稳定后，`AgentOrchestrator.handle()` 降级为兼容兜底；正常 Qt 对话不再直接依赖其硬编码 if-else 链。
- 无状态 chat fallback 只能回答解释类问题，不能创建流程、添加步骤、保存计划或生成执行事实。

## 11. 优先级结论

最优先要做的不是接完整 LangChain，而是把本地工具边界、Pydantic schema 和 LangGraph 状态机做深。完整 LangChain AgentExecutor/ReAct 不是当前项目的终点；当前终点是 `LangGraph 状态机 + DeepSeekToolDecider 窄 JSON 协议 + Pydantic schema + 本地工具/门禁/执行层`。

推荐顺序：

1. 修复 pending 和空回复。
2. 拦截 streaming chat 绕过控制类指令。
3. 拆分 `operator_ui_mixin.py` 的业务状态、Agent 编排和流程编辑逻辑。
4. 统一 `SessionState`，收口 ExecutionPlanStatus、DraftStatus、CompoundStepMachine 和 UI pending 状态。
5. 保证 `AgentOrchestrator` / `RestrictedAgentService` / `ConfirmationAgent` 会话级复用或状态外置。
6. 定义本地工具 Pydantic schema。
7. 增加 ExecutionGate。
8. 做深 LangGraph 多轮状态机，保留 DeepSeekToolDecider 窄 JSON 工具决策，不切换为 AgentExecutor/ReAct。
9. 用 SQLite/现有 JSON 落地可审核记忆。
10. 做投票反馈和离线学习。
11. 后续按需要再评估 LangMem/Mem0。

最终目标：

> LangGraph 负责有分支和循环的对话状态机，Pydantic 负责工具 schema，DeepSeekToolDecider 只负责窄 JSON 工具决策，本地工具负责事实和状态，ExecutionGate 负责安全和执行，Qt 执行层是唯一写控制器的入口，Memory 先用 SQLite/JSON 落地且只影响理解，不影响安全边界。

## 12. 当前落地程度对齐

截至当前实现，不能把“LangChain 已完整接入”作为完成口径，也不应把完整 LangChain Agent 作为终点。更准确的状态如下：

| 分层 | 当前完成度 | 当前事实 | 未完成项 |
|---|---:|---|---|
| LangGraph 状态机 | 约 88% | 默认 graph 已拆成 `check_pending_timeout -> expire_pending_state/sync_compound_step_result/decide_tool -> call_tool -> sync_flow_state/sync_confirm_state/sync_compound_state/local_rules` 多节点链路；direct tool 成功会写回 `SessionState`；schema 失败、flow 追问失败、compound 失败和确认类失败不会被本地规则掩盖；flow 创建/命名/取消已进入 flow 状态收口节点；`local_rules` 兜底路径已能把 `answer_flow_clarification` 的流程参数回填同步到 `SessionState`；本地 runner 的 flow payload 已回传更新后的 `session_state`，LangGraph 外层和本地兜底都能跨轮接力；flow 追加步骤已支持内联 `delay_sec` 与 `io_no/io_action` 回填，明确“步骤一/步骤二/第1步/第2步”这类序号的多步文本会一次追加多个步骤；flow 多步草案已经覆盖 `target_pose -> delay_sec -> io_no -> io_action` 连续追问，支持“两秒”等中文数字时长回答；compound plan 会写入 `SessionState.current_compound_plan`，并派生 `active_step_index/active_step/active_step_result/status=waiting_step_confirm` 供后续逐步确认循环消费；`SessionState.advance_compound_step()` 已支持当前步骤成功推进、最后一步完成、失败阻断并清理 pending confirm/execution；LangGraph 已有 `compound_step_result` 事件节点，可在 decider 前短路推进/失败当前步骤；`OperatorAgentRuntimeBridge.record_compound_step_result()` 已打通 Qt 复合步骤成功/失败结果到 LangGraph 事件，LangGraph 不可用或 graph 调用抛异常时回退本地状态推进，异常文本通过 `graph_error` 返回并写入日志；Qt 复合步骤失败/完成/取消会同步清理 runtime compound/pending 状态；`create/cancel/confirm/expire/query_pending_confirm` 已进入确认状态收口节点；过期 pending confirm 会在 decider 前短路到 `confirm_expired` 并清理状态 | 复合步骤更多控制器异常演练仍需补样本 |
| Pydantic 工具 schema | 约 97% | 45 个本地工具已暴露 Pydantic 输入 schema 和通用 `ToolResult` 输出 schema；`LocalToolRegistry.call()` 已在分发前校验输入、执行后校验输出并返回 `tool_args_invalid` / `tool_output_invalid`；`DeepSeekToolDecider` prompt 已包含 `input_schema` 和 `output_schema`；命令执行草案类工具已要求 `draft_id/func_id/intent/params`，`draft_to_query_record` 已要求 `confirmed=True`；控制类文本工具已拒绝空白 `text`，`lookup_command_schema` 已要求命令名或正数 `func_id`，`validate_required_params` 已要求 `func_id > 0`，`resolve_command_address` 已拒绝空白地址名；`CommandUnderstandingAgent` 已接入 ASR 标准词/方言/同音纠错、坐标轴误听别名、中文数字参数归一化，并在 `CommandUnderstandingResult` 中保留 `raw_text/normalized_text`，`VoiceNlpPlan` 已能透传 `normalized_text`，interaction archive 的 `input/nlp_result` 已落盘双轨字段，覆盖 `X一百/Y零/Z一百/速度五十/夫位/艾克斯一百` 等说明书回归场景；移动/连续路径类执行草案已覆盖 `func_id=8/102/108/112`，要求完整 6 轴姿态并限制 `spd_pct/acc_pct/dec_pct` 在 `(0,100]`，同时约束 `position_increment` 只能为 `0/1`、`move_type` 只能为 `0/1/2`；关节/虚拟轴草案要求 `axis_no` 在 `1..10`、`pos_val` 为数值，并限制 `spd_pct/acc_pct/dec_pct` 在 `(0,100]`；延时草案要求 `delay_sec > 0`，IO 草案要求 `io_no` 在 `0..11` 且 `io_action` 只能为 `0/1`；`save_flow_draft` 已在工具分发前要求非空流程名、至少一个步骤，并按步骤 `func_id` 校验移动/连续路径、关节/虚拟轴、延时、IO 参数完整性和基础边界；`set_flow_draft` 仍保留宽松 schema，允许缺参进入追问；位置别名工具已校验非空名称、6 轴 pose、默认速度 `spd` 在 `(0,100]`、`move_type` 在 `0/1/2`；`check_param_bounds` 已要求非空 bounds 携带 `schema_version`，并校验 `spd_pct/acc_pct/dec_pct` 必须在 `(0,100]`；确认状态工具已拒绝空白 `draft_id`；`prepare_registered_flow_execution` 已拒绝空白流程名；`query_memory_review` 已限制 status 只能为 `candidate/active/disabled/rolled_back`；Memory 审核/应用工具已拒绝空白 `memory_id`，`lookup_active_memory` 已拒绝空白 `kind`；`record_feedback_vote` 已限制 `target_type` 为 `interaction/answer/memory`、`vote` 为 `up/down`，并拒绝空白 `interaction_id/target_id`；`create_memory_candidate` 已要求非空 `kind/key` 且 `confidence` 必须在 `[0,1]` | 仍需补更多工具的细粒度业务字段边界和现场 ASR 词表样本 |
| LLM 工具选择 | 约 85% | `DeepSeekToolDecider` 使用 JSON 协议选择本地工具，支持 side-effect tool idempotency，工具提示中已包含输入 schema | 仍需更完整的失败回退和低置信度澄清；不计划换成 AgentExecutor |
| LocalToolRegistry 工具执行 | 约 95% | 本地工具分组、异常捕获、结构化 `ToolResult`、调用前参数校验和调用后输出校验已落地 | 仍需补少量工具颗粒度和字段级边界 |
| ExecutionGate / 确认门禁 | 约 96% | 参数、预检、pending confirm、超时、取消已覆盖较多路径；LangGraph direct path 中确认创建、取消、失败拒绝会更新或清理 `SessionState.pending_confirm`；入口超时分支会在 LLM/decider 前清理过期确认，避免旧计划被重复消费；普通确认计划 confirmed 后同步执行异常会复位 busy、归档 failure 并保留用户可见失败原因；Agent 草稿 confirmed 后同步执行异常和自然语言异步终止日志都会进入 `execution_failed`，清理 pending/confirmed 可复用状态并归档 failure；异步终止日志中如果 runtime 失败记录自身抛异常，也会把 `runtime failure record failed` 写入归档 `final_text`；复合步骤 confirmed 后控制器写失败会同步将 `current_compound_plan` 标记为 `failed`、`mode=blocked` 并清理 pending 状态；Qt 复合步骤失败/完成/取消会同步清理 runtime compound/pending 状态；确认门禁 L2/L3/ExecutionGate 内部异常已 fail-safe blocked 收口 | 更多控制器级异常演练仍需专项验证 |
| Qt 执行层 | 约 91% | 历史执行层仍是唯一写控制器入口；active memory 审核命令解析和批量批准逻辑已从 `operator_ui_mixin.py` 抽到 `operator_memory_review.py`，UI mixin 只做转发和展示 | 需要继续削减 `operator_ui_mixin.py` 中的确认状态、流程编辑、scene override 等业务职责 |
| 可审核 Memory | 约 95% | SQLite、candidate/active、投票、应用审计已实现；不可学习区硬拦截已落地，命中寄存器地址、安全边界、权限、确认/专家/新手模式、自动执行/直接执行、跳过确认、协议常量、控制器默认值等 kind/关键词会拒绝进入 candidate，工具层返回 `forbidden_memory_candidate`；`rollback_memory` 已能把错误生效经验移出 active 查询并写入 `memory_rolled_back` 审计事件；`query_memory_review` 已能按状态/类型列出经验并附带 `candidate_created/memory_approved/memory_applied/memory_rolled_back` 等审计线索；Qt 用户页已有轻量命令入口按 candidate/active/rolled_back/disabled 状态查看经验、按 kind 筛选、单条批准、单条回滚、批量批准全部待审核经验、批量停用/回滚全部 active 经验；`MemoryReviewView/MemoryReviewRow` 已能把 ToolResult 转成可用于表格和详情面板的 rows/detail/status_options/kind_options；`operator_ui_mixin.py` 右侧栏已放置真实可见的 memory 审核表格、详情面板和 status/kind 筛选控件，并能在 memory 结果返回时刷新内容；筛选控件变更会触发 `query_memory_review`，表格行选择会联动详情文本；面板按钮已支持对选中经验执行批准、停用、回滚 | 仍需增加更多安全事实样本、补更多异常/空选中状态的用户提示细节 |

整体真实进度按研发完成度应按 96%~97% 左右口径管理；按“主链路可跑通”口径可以更高，但不能用后者替代研发验收。后续重点是剩余工具字段边界、更多控制器级异常演练、active memory 更多安全事实样本和空选中/失败提示细节，不是接入更多 LangChain Agent 能力。
