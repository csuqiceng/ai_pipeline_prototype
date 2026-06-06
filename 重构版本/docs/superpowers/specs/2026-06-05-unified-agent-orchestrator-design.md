# Unified Agent Orchestrator Design

## 目标

把当前“白名单命中才进入 restricted agent”的入口，演进为“所有用户输入先进入 Agent Orchestrator，再由 Router 分流”的统一入口架构。

统一入口不等于所有逻辑都交给大模型。安全判断、参数补全、确认、执行请求生成仍必须由确定性代码完成；大模型只允许用于模糊理解、闲聊解释和自然语言润色，不能决定安全和执行。

## 当前问题

当前 GUI 入口大致是：

```text
用户输入
  -> _operator_should_try_restricted_agent()
     -> 命中白名单：RestrictedAgentService.parse()
     -> 未命中：旧 VoiceNlpAdapter / AtomicParser / 普通问答
```

这导致：

- 控制类能力散在 Agent 与旧 NLP 两条入口。
- 闲聊/解释类问题没有统一 Agent 子模块。
- 复合指令只能保护性拒绝，无法生成可审计多步计划。
- `needs_model=True` 的输入需要明确边界：LLM 兜底可选启用，但只能输出候选文本或反问，候选文本必须重新经过本地规则、补全、预检和确认。

## 推荐架构

```text
用户输入
  -> AgentOrchestrator
     -> IntentRouter
        -> ChatExplanationAgent
        -> AlarmExplanationAgent / StatusQueryAgent
        -> EmergencyCommandAgent
        -> MotionCommandAgent
        -> UtilityCommandAgent
        -> CompoundCommandCoordinator
        -> MemorySettingAgent
        -> PositionMemoryAgent
        -> AtomicTemplateAgent
        -> DashboardQueryAgent
        -> FlowDraftAgent
        -> RegisteredFlowAgent
        -> ClarificationAgent

共享确定性模块：
  - ParameterCompletionAgent
  - SafetyReviewAgent
  - ConfirmationAgent
  - CommandDraft / QueryRecord 转换
```

## 路由原则

1. 规则优先。
   明确的急停、暂停、坐标、延时、IO、报警查询、状态查询不调用大模型。

2. 大模型兜底。
   只在规则无法判断但文本可能有用时调用，例如“帮我解释 L2 是什么”“刚才为什么不能动”“往安全一点的位置挪一下”。

3. 安全确定性。
   大模型不得输出最终可执行参数。运动指令必须经过参数补全、安全预检、复述确认。

4. 执行走旧链路。
   Agent 确认后只生成 `QueryRecord` 或多步执行计划，后续仍走现有执行链路和六轴三道门。

5. 复合指令单独处理。
   复合指令不能一次确认后绕过所有子步骤。必须拆分、逐步补全、逐步预检，并生成可审计计划。

6. Func106/107 只作为辅助点动能力。
   自然语言参数类运动主路径仍以 Func108/Func112 为准；关节轴/虚拟轴点动、示教和微调类指令可路由到 Func106/Func107，但必须经过同样的补全、预检、确认和现有执行链路。

## 子模块职责

### AgentOrchestrator

统一入口。接收所有用户输入，调用 `IntentRouter`，再分发给具体子模块。

输出统一结果类型：

```text
chat_answer
query_answer
dashboard_query_action
flow_draft_plan
registered_flow_plan
memory_setting_answer
position_memory_action
atomic_template_action
clarification
waiting_confirmation
precheck_failed
compound_plan_draft
system_bypass
fallback_legacy
```

当 Orchestrator 返回 `fallback_legacy` 时，表示当前输入没有被任何已接入子 Agent 接管，GUI 可以继续交给旧 `VoiceNlpAdapter` / `AtomicParser` 路径处理。该结果必须携带审计 payload：

- `reason`: 交回旧路径的原因。
- `needs_model`: 规则理解阶段是否认为它可能需要模型兜底。
- `understanding`: `CommandUnderstandingAgent` 的稳定摘要，至少包含 `raw_text`、`intent`、`func_id`、`confidence`、`clarification`、`bypass_completion`。

`fallback_legacy` 不是执行结果，也不能直接生成 MODBUS 写入；它只用于保留旧能力和定位剩余未迁移边界。

GUI 入口收到 `fallback_legacy` 后，应在运行日志中记录一条摘要，包括 `reason`、`intent`、`func_id`、`confidence`、`needs_model` 和 `clarification`，然后再返回旧 NLP fallback。这样用户仍看到旧行为，开发和评审可以从日志判断哪些输入还没被 Orchestrator 接管。

### IntentRouter

负责分类，不负责执行。

路由规则只允许有一个维护源。Orchestrator 不再复制 GUI 白名单正则；首版应复用 `CommandUnderstandingAgent.understand()` 的结果判断是否进入 restricted agent，避免 `command_understanding.py`、`operator_ui_mixin.py` 和 Orchestrator 三处规则漂移。

优先级：

1. 应急系统动作
2. 报警/状态查询
3. 复合指令
4. 本地记忆参数设置
5. 位置库本地保存/删除
6. 旧原子模板动作
7. 看板运行信息查询
8. 流程草案创建/澄清
9. 已登记流程运行
10. 单条运动/IO/延时
11. 闲聊/解释
12. 模糊指令反问

其中单条运动包含两类：笛卡尔参数类运动走 Func108/Func112；辅助点动和微调类指令走 Func106/Func107。Func106/107 不作为笛卡尔参数类运动主路径的替代函数。

### ChatExplanationAgent

处理解释类问题，例如：

- “L2 是什么？”
- “为什么要确认？”
- “这个报警是什么意思？”
- “现在这个提示是不是失败？”

禁止生成 `CommandDraft`。

ChatExplanationAgent 不能抢占控制指令。实现时必须先确认 `CommandUnderstandingAgent.understand(text)` 返回 `unknown`，或者在 Chat 内部显式排除坐标、运动、IO、延时、急停、暂停、复位等控制关键词。比如“确认执行走到X1000”不得被“确认”关键词匹配为闲聊解释。

### ClarificationAgent

处理“像控制指令但参数不明确”的输入，例如：

```text
往安全一点的位置挪一下
```

这类输入由 `CommandUnderstandingAgent` 标记为 `needs_model=True`。当 LLM 兜底未启用或模型只返回反问时，Orchestrator 直接输出 `clarification`，复用现有澄清 UI 分支提示操作者补充明确坐标、方向或参数；当模型返回 `candidate_text` 时，候选文本必须重新通过本地规则。候选文本可以进入单条受限 Agent 链路，也可以进入本地 `CompoundCommandCoordinator`，但不能绕过补全、预检、确认和复合步骤审计。

ClarificationAgent 不生成 `CommandDraft`、不生成 `QueryRecord`、不进入安全预检和确认状态机。

### CompoundCommandCoordinator

处理顺序复合指令，例如：

```text
走到X1000，等待2秒，再打开IO1
```

输出多步草案：

```text
step1: Func108 move_linear
step2: Func109 delay
step3: Func120 io
```

每步都必须独立补全、预检、确认或显示风险。首版只支持顺序执行，不支持循环、条件、并行运动。

复合拆分必须先验证每个子句都是可执行或可确认的动作，不能只凭“然后/再/接着”拆分。比如“走到X1000然后告诉我结果”后半句是解释/查询，不应生成复合执行计划，应进入解释、反问或旧路径 fallback。

复合计划需要携带审计字段：

- `plan_id`
- `created_at`
- `raw_text`
- `steps`
- `step_results`

### MemorySettingAgent

处理本地原子函数记忆参数，例如：

- “小正，速度60%”
- “小正，步长10毫米”
- “小正，专家模式”

该模块只允许更新 `AtomicMemory` 中的本地偏好参数，并通过现有保存钩子持久化。输出 `memory_setting_answer`，不生成 `CommandDraft`、不生成 `QueryRecord`、不进入安全预检和确认状态机。

MemorySettingAgent 必须复用 `AtomicParser` 对 `family == "memory"` 的确定性识别，不能用模糊文本规则抢占运动指令。例如“让机械手走到X1000速度60%”仍应作为运动指令处理，“小正，上升3毫米”仍应作为 Func107 虚拟轴点动处理。

### PositionMemoryAgent

处理位置库本地保存/删除意图，例如：

- “小正，保存当前位置为位置A”
- “小正，删除位置A”

该模块只生成 `position_memory_action`，并转换为现有 `VoiceNlpAction("memory", ...)`。解析阶段不得写入或删除位置库，真正落库必须发生在现有 `_execute_nlp_plan()` 的 memory action 执行阶段。

PositionMemoryAgent 不能抢占位置查询和位置移动：

- “位置A坐标是多少”应继续走 `PositionQueryAgent` 只读回答。
- “小正，移动到位置A”应继续作为运动类动作处理，不能被本地记忆动作截获。

### AtomicTemplateAgent

处理可复用旧原子模板的动作，例如：

- “小正，移动到位置A”
- “小正，去休息”
- “小正，再走一次”
- “小正，继续”
- “小正，返回上一步”

该模块输出 `atomic_template_action`，由 `AgentPlanAdapter` 转为现有 `VoiceNlpAction("atomic_template", ...)` 和 `atomic_records`。后续仍走现有 `_execute_nlp_plan()`、执行前检查、确认、`QueryRecord` 执行链，不开新的 MODBUS 写入路径。

已迁移的历史类命令包括“再走一次”“继续”“返回上一步”。解析时需要恢复 `AtomicMemory` 的 last record / last direction / position stack 快照，避免只解析就改历史记忆或弹出历史位置。

### DashboardQueryAgent

处理七个运行看板的只读查询，例如：

- “通讯正常吗”
- “当前位置安全吗”
- “流程预演到哪了”
- “速度有没有超限”

该模块只复用 `dashboard_query_specs.match_dashboard_query_spec()` 识别查询目标，并输出 `dashboard_query_action`。实际回答文本仍由现有 `DashboardQueryService` 基于 `_operator_dashboard_snapshot_dict()` 生成，避免 Agent 复制状态解释逻辑。

DashboardQueryAgent 不能抢占笛卡尔运动指令，例如“让机械手走到X1000 Y200 Z800”不得被“到/位置”等词误识别为查询。

### FlowDraftAgent

处理流程草案创建和流程草案澄清，例如：

- “小正，创建一个打招呼流程，先到home，再点头3次”
- 旧流程草案缺少动作映射后的补充回答

该模块不重新实现流程编排。它只委托现有 `VoiceNlpAdapter.parse()`，并只透传 `source == "flow_draft"` 的旧 `VoiceNlpPlan`。流程草案保存、预检、执行和多轮编辑仍由现有 GUI 流程草案链路处理。

FlowDraftAgent 不启用新的执行通道，不直接写流程库，不直接下发 MODBUS。

### RegisteredFlowAgent

处理已登记流程运行，例如：

- “执行打招呼”
- “开始A到B流程”

该模块不重新实现流程匹配。它只委托现有 `VoiceNlpAdapter.parse()`，并只透传 actions 全部为 `flow` 的旧 `VoiceNlpPlan`。流程运行仍由现有 `_execute_nlp_plan()` 和 `_start_flow()` 链路处理。

RegisteredFlowAgent 不直接启动流程、不直接修改流程库、不直接下发 MODBUS。

### 共享模块

`ParameterCompletionAgent`、`SafetyReviewAgent`、`ConfirmationAgent` 不属于某个子 Agent 专属模块，而是 Motion、Utility、Compound 都会复用的横切能力。

## 分阶段策略

### Phase A：统一入口但保持旧行为

新增 `AgentOrchestrator`，但默认路由结果与当前白名单策略一致。目标是替换入口形态，不改变用户可见行为。

### Phase B：ChatExplanationAgent

让解释类问题也进入 Agent 总入口，但只返回文本，不进入执行链。

### Phase C：LLM 兜底开关

接入可关闭的模型兜底，仅用于模糊文本改写和反问。默认规则优先。LLM 输出白名单只有 `candidate_text` 和 `clarification`；`candidate_text` 必须重新通过本地规则解析，可被本地路由为单条指令或复合计划，不能携带 `func_id`、寄存器、QueryRecord 或执行参数。

### Phase D：CompoundCommandCoordinator

实现顺序复合指令拆分和多步草案。每个子步骤独立走受限 Agent 补全、预检和确认草案生成。若所有步骤均可确认，PlanAdapter 会生成现有 flow 草案；GUI 等待操作者说“确认执行”后复用旧流程执行链逐步执行。

### Phase E：GUI 统一入口切换

GUI 不再用 `_operator_should_try_restricted_agent()` 决定是否进入 Agent，而是所有输入进入 Orchestrator，由 Orchestrator 返回是否交给旧路径。

GUI 创建 `RestrictedAgentService` 只受 `axis_ranges.restricted_agent_enabled` 功能开关控制，不再受旧白名单 `_operator_should_try_restricted_agent()` 控制。旧白名单可以保留给兼容 helper，但不能决定 Orchestrator 是否拥有受限 Agent 子服务。

### Phase F：MemorySettingAgent

把旧原子层中的速度、步长、确认模式设置迁移到 Orchestrator。该阶段只改本地 `AtomicMemory`，不触发机械手动作。

### Phase G：PositionMemoryAgent

把旧原子层中的位置保存/删除意图迁移到 Orchestrator。该阶段只生成本地 memory 动作计划；保存/删除必须在执行阶段落库，避免“只解析就改状态”。

### Phase H：AtomicTemplateAgent

把旧原子层中的位置移动、默认休息位、再走一次、继续、返回上一步迁移到 Orchestrator。该阶段只生成现有 `atomic_template` 计划，执行链和安全检查仍复用旧路径。

### Phase I：DashboardQueryAgent

把七个用户页运行看板查询迁移到 Orchestrator。该阶段只生成现有 `query` 计划，回答仍由 `DashboardQueryService` 完成。

### Phase J：FlowDraftAgent

把流程草案创建和澄清入口迁移到 Orchestrator。该阶段只透传现有 `VoiceNlpAdapter` 的 `flow_draft` 计划，后续保存/执行仍复用旧 GUI 流程草案链路。

### Phase K：RegisteredFlowAgent

把已登记流程运行入口迁移到 Orchestrator。该阶段只透传现有 `VoiceNlpAdapter` 的 `flow` 计划，后续流程启动仍复用旧 GUI 流程执行链路。

### Phase L：Fallback Audit

给 `fallback_legacy` 增加可审计 payload。目标是看清楚哪些输入仍交回旧路径、规则理解结果是什么、是否属于未来 LLM 兜底候选，不改变现有执行链路。

GUI 同步记录 fallback 摘要日志，但不把它转换成用户可见回答，不阻断旧 NLP 路径。

### Phase M：Deterministic Clarification

把 `needs_model=True` 的模糊控制文本先路由到确定性 `clarification`。该阶段不启用 LLM，只阻止模糊控制文本落回旧 NLP 误猜执行。

### Phase N：Increment Protocol Alias

对齐《自然语言参数类指令解析说明书》中的 `para(10) 位置增量` 语义。理解层和草案层增加 `position_increment` 字段：

- 绝对定位：`position_increment=0`
- 增量表达，如“向左移动200”“升高100”：`position_increment=1`

该字段用于协议语义呈现和执行副本生成。增量运动仍先换算为绝对目标点用于安全预检；操作者确认后，`CommandDraft` 转 `QueryRecord` 时把 `position_increment` 映射到执行副本的 `fuzzy_pos/para(10)`，原草案不被修改。

### Phase O：Func112 Executable Through Existing Chain

对齐《自然语言参数类指令解析说明书》中的“规划路径/规避/绕行到 → Func112 连续路径”语义。当前阶段只支持：

- 识别 `规划路径`、`规避`、`绕行` 等关键词。
- 按 Func108 相同参数格式补全 Func112 草案。
- 运行 L1/L2 安全预检。
- 进入复述确认队列。
- 确认后生成 `QueryRecord(func_num=112)`，并由 `SixAxisCommand` 按 Func108 相同参数地址写入，IEEE(0) 写 112。

大模型不得生成 Func112 点列或直接写 MODBUS。若固件后续要求 Func11/111 兼容入口或多点点列格式，应通过 `AddressResolver` 和服务构建层配置调整。

### Phase P：Compound Step State Machine

给复合指令草案增加纯状态机元数据：

- `waiting_step_confirmation`: 等待确认当前步骤。
- `step_confirmed`: 当前步骤已确认，等待外部执行结果。
- `completed`: 所有步骤完成。
- `blocked` / `failed`: 任一步骤不可确认或执行失败，整条复合计划停止。

该状态机作为 `flow_draft.step_machine` 审计和 UI 展示元数据；可执行复合计划会额外转换为现有 flow 草案，等待操作者确认后复用旧流程逐步执行链。

### Phase Q：Compound Step UI Summary

GUI 在展示复合指令草案时读取 `flow_draft.step_machine`，显示当前等待确认的步骤，例如：

```text
当前等待确认第 1/2 步：走到X1000
```

如果状态机为 `blocked` / `failed`，展示停止原因。若所有步骤均可确认，GUI 保存为待执行流程草案；操作者说“确认执行”后，复用现有流程保存和 `_start_flow()` 逐步执行。

## 明确不做

- 不让大模型决定是否安全。
- 不让大模型直接写 MODBUS。
- 不让大模型生成 Func112 点列或直接写 MODBUS。
- 不让增量运动绕过绝对目标安全预检；只在确认后的执行副本映射 `position_increment -> fuzzy_pos/para(10)`。
- 不把旧 AtomicParser 立即删除。
- LLM 兜底只允许输出 `candidate_text` 或 `clarification`，候选文本必须重新通过本地规则；单条指令走补全、预检和确认，复合候选走 `CompoundCommandCoordinator` 的拆分、逐步预检和审计。

## 验收标准

- 所有用户输入都能进入 Orchestrator。
- Orchestrator 的受限 Agent 子服务注入不依赖旧白名单，只依赖 `restricted_agent_enabled` 功能开关。
- 闲聊/解释类问题不会生成执行草案。
- 运动指令仍必须复述确认。
- 复合指令不会绕过单步预检；可执行复合计划在用户确认后转为现有 flow 草案逐步执行。
- 记忆参数设置会返回解释层 chat 结果，并明确不生成执行草案。
- 位置保存/删除会返回本地 memory 动作计划，不生成机械手动作；解析阶段不会改位置库。
- 位置移动/休息位/再走一次/继续/返回上一步会返回现有 atomic template 计划，并继续走现有确认和执行前检查。
- 看板查询会返回现有 query 计划，并继续复用 DashboardQueryService 生成回答。
- 流程草案会返回现有 flow_draft 计划，并继续复用旧流程草案保存/编辑/执行逻辑。
- 已登记流程运行会返回现有 flow 计划，并继续复用旧流程执行逻辑。
- 未被 Orchestrator 接管的输入会返回 `fallback_legacy`，并带有 reason、needs_model 和规则理解摘要，方便审计和后续迁移。
- GUI 对 `fallback_legacy` 记录运行日志摘要，然后继续交给旧路径处理。
- 模糊控制文本会返回 `clarification`，提示补充明确坐标、方向或参数，不生成动作草案。
- 增量运动草案带有 `position_increment` 协议别名，确认后执行副本会映射到 `fuzzy_pos/para(10)`。
- Func112 连续路径按 108 同参数格式进入确认，确认后通过现有六轴三道门写控制器。
- 复合指令草案带有逐步确认状态机元数据；可执行计划会转为待执行 flow 草案。
- GUI 会显示复合指令当前等待确认的步骤或阻断原因；可执行计划在用户确认后触发现有 flow 执行链。
- 旧白名单指令行为不回退；Task 7 完成后必须运行 `tests/test_agent_command_understanding.py`、`tests/test_restricted_agent_service.py` 和 `tests/test_operator_precheck_helpers.py`，确认替换入口后行为一致。
- 旧原子命令路径可保留，直到新 Orchestrator 覆盖并验证。
