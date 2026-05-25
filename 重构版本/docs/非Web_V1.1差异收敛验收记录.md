# 非 Web V1.1 差异收敛验收记录

日期：2026-05-23，2026-05-24 继续接入，2026-05-25 继续收敛

## 保留的工程决策

- 协议主链路保持 V5.0，不回退到 V4.3 七步握手。
- 应急命令保持可审计 Func104 快速通道，不实现完全绕过网关的直接写入。
- Web 相关实现不在本轮验收范围内。

## 本轮已落地

- `permission_service.py`：后端角色权限白名单。
- `position_registry.py`：结构化位置库、`locked/is_system` 保护、`spd/move_type`、Func108 参数导出。
- `flow_registry.py`：结构化流程库、`FlowStep`、确认锁定、草稿版本、演练状态。
- `safety_precheck.py`：目标球面半径和动作类型速度钳位。
- `memory_params.py`：按动作类型速度偏好、总指令数、上次指令时间。
- `dialog_logger.py`：按日 `dialog_YYYY-MM-DD.jsonl`。
- `alarm_advice.py`：10 类报警建议表。
- `data/nlp_standard_words.json` / `nlp_standard_words.py` / `nlp_normalization.py`：50+ 标准词配置、同音/方言精确映射、可选拼音归一化入口。

## 2026-05-24 继续接入

- 自然语言适配器初始化时为 `AtomicMemory` 挂接 `PositionRegistry`，命名位置查询/移动优先走结构化位置库。
- “保存当前位置为位置名”现在同步写入旧 `AtomicMemory` 和新 `position_registry.json`。
- 自然语言原子/模板动作执行成功后更新 `memory_params.json` 的动作分类速度偏好和指令统计。
- 操作端文本、语音、工程师语音归档统一使用带 `DialogLogger` 的 writer，默认生成按日对话日志。
- 新增 `alarm_detector.py`，将 alarm_code、LONG(38)、急停/暂停和连接状态汇总为标准建议码、严重级别和 auto_clear 策略，并接入 7 类看板。
- 新增 `alarm_monitor.py`，操作端挂接独立 50ms 报警采样 timer，输出 `_operator_last_alarm_detection`。
- 普通模板发送成功路径也会更新 `memory_params.json`，并与 NLP 序列去重，避免重复统计。
- `json_schema.py` 新增 V1.1 命名兼容报文 `SystemReply` / `DashboardPush` 及校验函数，保留现有 V5.0/V2.1 主链路字段。
- 现场词表已从代码常量整理到 `data/nlp_standard_words.json`，后续可直接追加标准词、同音词和方言短语。
- `service.py` 已接入 `FlowRegistry` 双轨存储：加载旧 `flows.json` 和新 `flow_registry.json`，保存/删除时同步两套文件，已确认流程修改会生成 draft 版本。
- `response_builder.py` 的报警类播报会对已知报警码/关键词附加 `AlarmAdviceBook` 操作建议。

## 2026-05-25 继续接入

- `voice_nlp_adapter.py` 支持复杂流程草案：DeepSeek 仅输出 `create_flow` 结构化草案，不直接执行。
- GUI 复用同一个 `VoiceNlpAdapter`，多轮澄清时保留待补全流程草案状态。
- 操作员页支持流程草案澄清、多轮补充动作映射、确认保存、保存并执行、取消草案。
- 流程草案保存时同步落地：
  - 位置写入 `position_registry.json`。
  - 展开步骤写入 `query_table.json`。
  - 流程写入 `flows.json` 和 `flow_registry.json`。
- `data/flow_phrase_aliases.json` 作为现场口语动作映射配置文件，后续可追加“小臂上下点头”等动作展开规则。
- `AtomicMemory` 新增历史方向持久化，支持“前进3毫米”后“小正，继续”，也支持“继续5毫米/继续5度”覆盖步长。
- `ai_interface.py` 新增 AI 迭代预留接口：
  - `get_dialog_stream()` 读取按日对话 JSONL。
  - `get_device_status_stream()` 输出 7 类看板快照。
  - `hot_update_rule()` 热更新白名单规则文件。
  - `get_safety_params()` 输出安全参数和分层。
  - `set_safety_params()` 按 `ParamManager` 分层策略写入可优化区。
- `process_precheck.py` 支持直接预检 `FlowEntry/FlowStep`，当步骤自带 `func_id + params` 时不再要求 `query_table` 预先存在模板。
- `flow_execution_mixin.py` 支持直接执行结构化 `FlowStep`，运行时按 `func_id + params` 生成 `QueryRecord` 并注入 table/service.table；旧 `query_key` 流程仍兼容。
- `position_registry.py` 新增 `migrate_atomic_positions()`，可把旧 `atomic_state.json` 的裸位姿迁移到 `position_registry.json`。
- `NlpMixin._position_registry()` 首次构建 registry 时自动执行轻量迁移；已存在、locked、system 位置不覆盖，重复运行可幂等跳过。
- `flow_management_mixin.py` 的流程管理列表/表单已可读取 `FlowEntry` 元数据，展示确认状态、版本、演练速度和结构化步骤占位；保存路径继续兼容旧 `FlowDefinition`。
- `service.py` 新增 `get_effective_flow()` / `save_flow_entry()`；流程执行和操作端 L3 预检优先读取 `FlowEntry` 本体，结构化步骤占位保存时会保留原始 `FlowStep.func_id + params`。

## 数据迁移

- 旧 `data/flows.json` 可通过 `flow_definition_to_entry()` 迁移为结构化流程。
- 旧 `AtomicMemory.positions` 已支持自动迁移为 `position_registry.json`，仍保留兼容读取。
- 旧 `data/atomic_state.json` 继续保留，用于原子命令基础记忆。
- 新 `memory_params.json` 保存动作分类速度偏好和统计。

## 验收命令

```powershell
pytest tests/test_permission_service.py tests/test_position_registry.py tests/test_flow_registry.py tests/test_safety_precheck.py tests/test_memory_params.py tests/test_dialog_logger.py tests/test_alarm_advice.py tests/test_nlp_normalization.py -q
pytest tests/test_atomic_memory.py tests/test_atomic_parser.py tests/test_atomic_resolver.py tests/test_voice_nlp_atomic.py tests/test_voice_nlp_system_actions.py tests/test_voice_nlp_semantic_candidates.py tests/test_process_precheck.py tests/test_flow_pause.py tests/test_operator_precheck_helpers.py tests/test_safety_suggestion.py tests/test_dashboard.py tests/test_dashboard_query.py tests/test_response_builder.py tests/test_interaction_archiver.py -q
pytest tests/test_nlp_atomic_memory_persistence.py tests/test_operator_precheck_helpers.py::test_operator_archive_text_input_writes_interaction_record tests/test_interaction_archiver.py -q
pytest tests/test_alarm_detector.py tests/test_alarm_advice.py tests/test_dashboard.py tests/test_dashboard_query.py -q
pytest tests/test_alarm_monitor.py tests/test_alarm_detector.py tests/test_dashboard.py tests/test_operator_precheck_helpers.py::test_operator_refresh_alarm_monitor_keeps_independent_50ms_sample -q
pytest tests/test_json_schema.py tests/test_command_intent_adapter.py tests/test_response_builder.py tests/test_dashboard.py -q
pytest tests/test_nlp_normalization.py tests/test_voice_nlp_atomic.py -q
pytest tests/test_service_flow_registry.py tests/test_flow_registry.py tests/test_flow_pause.py tests/test_process_precheck.py -q
pytest tests/test_response_builder.py tests/test_dashboard_query.py -q
pytest tests/test_complex_flow_draft.py tests/test_nlp_atomic_memory_persistence.py tests/test_operator_precheck_helpers.py -q
pytest tests/test_atomic_resolver.py tests/test_voice_nlp_atomic.py tests/test_ai_interface.py -q
pytest tests/test_flow_pause.py tests/test_process_precheck.py tests/test_service_flow_registry.py -q
pytest tests/test_position_registry.py tests/test_nlp_atomic_memory_persistence.py -q
pytest tests/test_operator_precheck_helpers.py tests/test_flow_pause.py tests/test_process_precheck.py -q
pytest tests/test_service_flow_registry.py tests/test_flow_registry.py tests/test_flow_pause.py tests/test_process_precheck.py tests/test_operator_precheck_helpers.py -q
pytest -q
```

最近一次全量结果：`474 passed, 3 warnings`。
流程管理表单元数据显示接入后相关结果：`180 passed, 3 warnings`。
结构化流程保存/有效流程入口接入后相关结果：`196 passed, 3 warnings`。
