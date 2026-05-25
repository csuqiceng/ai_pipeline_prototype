# 非 Web V1.1 最终验收结论

日期：2026-05-24；2026-05-25 更新

## 验收口径

- 不考虑 Web。
- 不按目录结构或伪代码验收，只按编程手册 V1.1 的需求内容验收。
- 协议主链路以当前 V5.0 为验收基线，不回退 V4.3 七步握手。
- 应急通道采用三段式授权码 + 可审计 Func104 快速通道，不实现完全绕过网关的 MODBUS 直写。

## 已实现主体能力

- 六轴 V5.0 执行链路：状态门、回显比对、触发前复查、接受确认、完成/错误轮询。
- Func104/106/107/108/109/110/11/120 建模、写入和执行。
- L1/L2/L3 预检，含目标球面半径、动作类型速度钳位、逆解/FSTATUS、流程级预演。
- 5 级语义分层、唤醒词、看板查询、生产动作确认、应急语义。
- 7 类看板、自然语言看板问答、L2 查询路径。
- 结构化位置库 `PositionRegistry`，含 `locked/is_system/spd/move_type/created_by`，并支持旧 `AtomicMemory.positions` 自动迁移。
- 结构化流程库 `FlowRegistry`，含 `FlowStep/confirmed/version/draft/rehearsal_spd/created_by`，并已接入 service 双轨存储、L3 预检和流程执行侧。
- 后端权限服务 `PermissionService`。
- 记忆参数 `MemoryManager`，含动作分类速度偏好、指令统计、主执行链路更新。
- 历史方向记忆：支持“小正，继续”和显式步长覆盖，状态持久化到 `atomic_state.json`。
- 每日对话日志 `dialog_YYYY-MM-DD.jsonl`。
- 报警建议、报警检测、LONG(38) 位映射、auto_clear 策略字段、独立 50ms 报警 monitor。
- V1.1 命名兼容报文 `CommandIntent/SystemReply/DashboardPush`。
- NLP 标准词配置文件、同音/方言精确映射和可选拼音归一化候选层。
- DeepSeek 复杂流程草案：支持澄清、多轮补充动作映射、确认保存和保存并执行；DeepSeek 不直接执行。
- AIInterface 预留接口：对话流、设备状态流、规则热更新、安全参数分层读写。

## 剩余差异分类

### 需要现场语料后继续扩充

- 在 `data/nlp_standard_words.json` 中继续扩充 250 条级别同音字表。
- 在 `data/nlp_standard_words.json` 中继续扩充现场短语、ASR 常见误识别词、操作者口头习惯。
- 四川话/方言声母韵母混淆规则可后续单独配置。
- `pypinyin` 是否作为强依赖，需要部署环境确认。

### 保留兼容，不建议强制重构

- 旧 `AtomicMemory.positions` 继续兼容裸位姿，并已支持启动时轻量迁移到 `position_registry.json`；locked/system 位置不会被覆盖。
- 旧 Qt 流程管理表单继续兼容 `FlowDefinition` 视图模型，service 层已同步写入 `flow_registry.json`，管理列表/表单已可读取并展示 `FlowEntry` 确认状态、版本、演练速度和结构化步骤占位；结构化占位保存时会保留原始 `FlowStep`。
- 复杂流程草案当前保存为 `QueryRecord + FlowDefinition + FlowEntry` 双轨数据；执行和 L3 预检优先读取 `FlowEntry` 本体，并已支持从 `FlowStep.func_id + params` 原生生成运行记录，Qt 管理表单仍保留旧视图模型用于兼容保存。
- 主报文仍保留 V5.0/V2.1 工程字段，另提供 V1.1 命名兼容导出。

### 需要现场依赖确认

- iFlytek TTS 真接入仍需确认 SDK、授权方式、在线/离线模式和部署环境；当前保留 `SpeechBroadcastDeliveryService + Pyttsx3SpeechSink` 可选本地播报。

### 不建议实现

- 不建议实现手册式“绕过所有网关直接 MODBUS 写入”的应急通道。
- 不建议为了 V4.3 文档口径回退当前 V5.0 三道门执行链路。
- 不建议凭空补齐现场词表，应基于真实语料迭代 `data/nlp_standard_words.json`。

## 当前结论

非 Web 核心能力和可工程化收敛项已基本完成。剩余项主要不是普通代码缺口，而是现场语料、部署依赖选择、历史兼容迁移和安全基线取舍。

## 验证命令

```powershell
pytest -q
```

最近一次结果：`418 passed, 3 warnings`。
2026-05-25 更新结果：`460 passed, 3 warnings`。
2026-05-25 结构化流程接入后结果：`462 passed, 3 warnings`。
2026-05-25 位置迁移接入后结果：`467 passed, 3 warnings`。
2026-05-25 流程管理表单元数据显示接入后相关结果：`180 passed, 3 warnings`。
2026-05-25 最新全量结果：`474 passed, 3 warnings`。
2026-05-25 结构化流程保存/有效流程入口接入后相关结果：`196 passed, 3 warnings`。
