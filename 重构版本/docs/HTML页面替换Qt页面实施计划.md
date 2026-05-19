# HTML 页面替换 Qt 页面融合实现方案

版本：V2.0  
日期：2026-05-18  
状态：融合版实施方案  

依据文档：

- `desginpro.md`
- `desgin.md`
- `docs/机械手自然语言交互系统_项目需求与技术路线建议书_V2.1.md`
- `docs/KINETIX_OS_Web项目分析报告.md`
- `web/kinetix-os---industrial-controller/`
- 现有代码：`robot_modbus_lite/qt_gui.py`、`gui_ui_mixin.py`、`voice_nlp_adapter.py`、`controller_runtime_mixin.py`、`six_axis_command_mixin.py`、`flow_execution_mixin.py`

## 1. 综合判断

四份文档的结论可以合并成一句话：

**用 KINETIX OS Web 原型作为新界面基础，用 Python 保留现有控制核心，通过本地 HTTP/WebSocket 服务把 HTML 页面接入现有 V5.0 控制链路，逐步替换当前 PySide6 页面。**

需要澄清一个方向变化：

- `desgin.md` 和 `desginpro.md` 的早期落点偏向“继续用 PySide6/QSS 重做页面”。
- `KINETIX_OS_Web项目分析报告.md` 认为 KINETIX OS 原本只是视觉参考。
- 当前实际目标已经变为“接入 HTML 页面，替换当前 Qt 页面”。

所以本方案采用 Web 前端路线，但完整保留 `desginpro.md` 和 V2.1 需求文档中的工业安全约束：即时回应、七类看板、主动播报、L1/L2/L3 预检、应急独立通道、V5.0 三道门、日志归档和 AI 迭代预留。

### 1.1 外部评审补充决策

根据方案评审意见，实施前必须补齐三类 P0 问题：

| 问题 | 决策 |
| --- | --- |
| 语音模块架构缺失 | 增加 Web 语音接入方案，短期后端继续采集/识别，前端通过 API 控制；长期再评估浏览器 Web Audio |
| EXE 打包部署缺失 | 已补充第 12 章，明确最终仍交付 `RobotModbusLite.exe`，React 只作为本地静态资源随包发布 |
| Qt mixin 受控桥接不具体 | 增加 `WebControlBridge` 过渡方案，先线程安全桥接现有 Qt 控制链路，后续再下沉为纯 service |

同时接受以下 P1/P2 建议并纳入本方案：

- 补充全语音控制、小窗口/全屏、防误操作三不原则作为验收标准。
- 明确 WebSocket 消息格式和 API 错误格式。
- 增加对话历史、采纳建议、语音控制等 API。
- 增加对话恢复、断线重连、并发控制、超时恢复流程。
- 明确 FastAPI 为 B1 阶段后端技术选型。
- 增加测试、性能、本地访问安全和旧 Qt 废弃条件。

## 2. 最终产品形态

最终软件分为三层：

```text
桌面入口层
  - 浏览器模式：启动本地服务并打开 Web 页面
  - Qt 壳模式：QWebEngineView 加载本地 Web build
  - 旧 Qt 页面：过渡期作为工程师备用入口

Web 页面层
  - React / TypeScript / Vite / Tailwind
  - 操作者页面
  - 工程师页面
  - 登录与权限入口

Python 控制服务层
  - HTTP API
  - WebSocket 状态推送
  - 现有 Modbus/V5.0 控制核心
  - 语音/NLP/预检/流程/日志/配置
```

控制原则：

1. HTML 页面只负责展示、输入、确认，不直接拼寄存器、不直接下发底层命令。
2. Python 后端是唯一控制核心，所有运动动作必须经过后端预检和 V5.0 三道门。
3. 急停、暂停、继续、复位走系统动作通道，不依赖大模型。
4. Web 页面与 Qt 备用页面显示的状态必须来自同一份 `RobotSnapshot`。
5. 过渡期不删除旧 Qt 页面，先并行，再替换默认入口，最后再决定是否移除。

### 2.1 启动模式配置

为兼容开发、联调、发布三种场景，启动模式需要显式可配置。

优先级：

```text
命令行参数 > system_config.json > 默认值
```

建议命令行参数：

```text
--ui web-browser       # 启动本地服务并打开系统浏览器
--ui web-shell         # 启动本地服务并用 QWebEngineView 打开
--ui qt-legacy         # 启动旧 Qt 页面，作为工程师备用入口
--controller mock      # 使用模拟控制器
--controller real      # 使用真实控制器
--host 192.168.1.11    # 控制器地址
--port 8765            # 本地 Web 服务端口
```

配置文件建议字段：

```json
{
  "startup": {
    "ui_mode": "web-browser",
    "controller_mode": "mock",
    "web_host": "127.0.0.1",
    "web_port": 8765,
    "remember_last_role": true
  }
}
```

Qt WebEngine 壳模式下，窗口标题、图标、最小尺寸、关闭确认由桌面入口层控制；Web 页面只负责业务 UI。建议默认窗口最小尺寸为 `1280x760`，推荐尺寸为 `1380x860`。

`remember_last_role` 只表示记住上一次登录选择的操作者/工程师角色，不表示记住 `ui_mode`。`ui_mode` 必须由命令行或配置文件显式控制，避免生产环境误入工程师入口。

## 3. 文档融合后的核心需求

### 3.1 不能丢的产品能力

来自 `desginpro.md` 和 V2.1 需求：

| 能力 | 要求 |
| --- | --- |
| 即时回应 | 文字输入回执 <=50ms，语音 VAD 回执 <=200ms，应急编码命中 <=30ms |
| 安抚性回应 | 后台运算期间必须同步已知设备状态和处理进度 |
| 七类看板 | 用内存状态缓存替代 HMI 常驻视觉信息，支持查询和主动播报 |
| 主动播报 | 报警、急停、通讯异常、运动完成、预检不通过必须主动通知 |
| 精确回答 | 所有风险必须量化到参数、当前值、安全值、差值 |
| 动作闭环 | 语言输入 -> 回执 -> 计划卡 -> 预检 -> 确认 -> 执行 -> 反馈 |
| L1/L2/L3 预检 | 单动作即时安全、运动规划预演、流程级预演 |
| 应急独立通道 | 急停三段式编码，单独提及关键词不触发动作 |
| 双页面体系 | 操作者页面默认入口，工程师页面维护入口 |
| 数据归档 | 对话、预检、执行、设备快照进入日志，为未来 AI 迭代留数据 |

### 3.2 KINETIX OS 可复用内容

来自 `KINETIX_OS_Web项目分析报告.md`：

| 可复用项 | 使用方式 |
| --- | --- |
| React + Vite 项目结构 | 作为新 HTML 页面基础 |
| 登录页角色选择 | 保留，补真实权限校验 |
| 左侧 128px 导航 | 保留，后续扩展工程师 8 项 |
| 顶部常驻状态栏 | 保留，改为绑定 `RobotSnapshot` |
| 操作者 5 个页面 | 保留并补业务逻辑 |
| 工程师 3 个页面 | 短期保留，长期扩展到 8 个维护模块 |
| 色彩 token | 保留，修正少量偏差 |
| `StatusSection` / `StatusRow` 模式 | 抽为公共组件 |

### 3.3 KINETIX OS 必须修正的问题

| 问题 | 处理 |
| --- | --- |
| 全部硬编码状态 | 集中到 mock store，然后替换为 API/WebSocket |
| 对话发送无逻辑 | 实现回执、计划卡、预检、确认、执行流 |
| 执行监控是占位 | 实现 `ExecutionState` 绑定 |
| 工程师运行页是占位 | 接入连接、固定指令、实时反馈 |
| 模板管理是占位 | 接 `query_table.json` API |
| 安全预检文件名不准 | `SafetyConfig.tsx` 改为 `SafetyPreCheck.tsx` |
| Tailwind 未定义色类 | 补 `on-surface`、`on-surface-variant` 等 token |
| README 仍是 AI Studio 模板 | 改为本项目运行和构建说明 |
| 未使用依赖 | 清理或注明保留原因 |

## 4. 目标架构

### 4.1 运行架构

```text
React 前端
  - 操作者页面
  - 工程师页面
  - WebSocket 状态订阅
  - HTTP 命令请求

Python Web 服务
  - API 路由
  - WebSocket 推送
  - 状态聚合
  - 安全网关
  - 日志事件

Python 控制核心
  - RobotModbusService
  - ZMotionVrClient / MockZMotionVrClient
  - VoiceNlpAdapter
  - six_axis_command 链路
  - flow execution 链路
  - system_config / avoidance_config / query_table / flows

控制器
  - V5.0 Modbus TCP
  - mock_controller
  - ZMotion SDK
```

### 4.2 后端模块拆分

新增建议：

```text
robot_modbus_lite/
  web_server.py              # 本地 HTTP/WebSocket 服务入口
  web_api_models.py          # API 数据模型
  web_state_bridge.py        # 现有运行状态 -> RobotSnapshot
  web_event_bus.py           # WebSocket 推送与事件分发
  web_control_service.py     # 系统动作、计划、执行 API 的服务层
  web_control_bridge.py      # 过渡期桥接现有 Qt/mixin 控制链路
  web_voice_service.py       # 语音识别后台服务，从 voice_mixin/iflytek 相关逻辑拆出
  web_precheck_service.py    # L1/L2/L3 预检服务入口
  web_template_service.py    # query_table 模板 API
  web_flow_service.py        # flows 流程 API
  web_log_service.py         # session/logs 查询 API
  safety_precheck.py         # L1 安全预检，后续扩展 L2/L3
  action_plan.py             # ActionPlan / PrecheckResult / ExecutionState
  response_builder.py        # 回执、安抚性回应、结果话术
```

注意：

- 第一阶段允许薄桥接现有 mixin，但不能让 API 长期直接依赖 Qt widget。
- `CommandDispatchMixin`、`SixAxisCommandMixin`、`FlowExecutionMixin` 中可复用的控制逻辑，应逐步下沉为纯 service。
- `GuiLoggingMixin` 的持久化逻辑应抽出，供 Web API 和旧 Qt 共同写日志。
- `safety_precheck.py`、`action_plan.py`、`response_builder.py` 属于后端服务层，不属于 UI 层。前端只消费它们输出的结构化数据和文案。
- `response_builder.py` 负责工业业务话术、回执、安抚性回应和风险解释；前端 i18n 只负责固定 UI 标签，不负责生成安全相关业务结论。
- 如果后续支持多语言，安全相关话术仍由后端根据用户配置或 `Accept-Language` 返回对应语言，前端不自行翻译风险结论。

### 4.3 前端目录调整

目标目录：

```text
web/kinetix-os---industrial-controller/
  src/
    api/
      client.ts
      mock.ts
      types.ts
      websocket.ts
    components/
      AppShell.tsx
      SideNav.tsx
      TopStatusBar.tsx
      RightStatusPanel.tsx
      EmergencyActions.tsx
      PlanCard.tsx
      PrecheckPanel.tsx
      EventTimeline.tsx
      StatusSection.tsx
      StatusRow.tsx
    state/
      robotStore.ts
      mockData.ts
      selectors.ts
    views/
      Login.tsx
      OperatorDashboard.tsx
      SafetyPreCheck.tsx
      ExecutionMonitor.tsx
      AlarmHandling.tsx
      SystemStatus.tsx
      EngineerRunDebug.tsx
      EngineerNlpDebug.tsx
      TemplateManagement.tsx
      FlowManagement.tsx
      SafetyParameters.tsx
      AvoidancePoints.tsx
      SystemLogs.tsx
      LicenseSystem.tsx
```

`AppShell.tsx` 只负责整体布局容器，包括侧边导航、顶部状态栏、主内容区、右侧状态面板和底部应急操作区；路由和业务状态由 `App.tsx`/`robotStore.ts` 管理，避免布局组件承担业务判断。

## 5. 统一数据模型

### 5.1 RobotSnapshot

所有页面的状态来源。前端顶部状态栏、右侧状态栏、完整状态页、执行页、报警页都必须消费这一份数据。

```json
{
  "timestamp": "2026-05-18T00:00:00.000+08:00",
  "connection": {
    "online": true,
    "controllerType": "mock",
    "host": "192.168.1.11",
    "modbusOk": true,
    "ecatOk": true,
    "latencyMs": 8
  },
  "system": {
    "state": "idle",
    "ready": true,
    "busy": false,
    "estop": false,
    "pause": false,
    "alarm": false,
    "alarmCode": "0",
    "alarmText": "",
    "currentFunc": null,
    "taskId": 1001
  },
  "position": {
    "jointDpos": [0, 0, 0, 0, 0, 0],
    "jointMpos": [0, 0, 0, 0, 0, 0],
    "cartesian": {"x": 0, "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0},
    "r": 0,
    "z": 0
  },
  "motion": {
    "speedPct": 0,
    "accPct": 0,
    "decPct": 0,
    "motionPercent": 0
  },
  "io": {
    "input": 0,
    "output": 0
  },
  "diagnostics": {
    "driveFault": [],
    "lastError": ""
  }
}
```

说明：

- `position.r` 和 `position.z` 是后端根据当前状态计算并提供的派生字段，前端不自行计算，避免不同页面算法不一致。
- `position.z` 与 `cartesian.z` 在多数场景相同，但仍保留独立字段，便于后续兼容控制器安全看板中的 Z 高度定义。

### 5.2 DashboardState

七类看板的统一结构。前端可按摘要展示，后端必须保留完整字段。

```text
dashboard.device_status
dashboard.action_feasibility
dashboard.safety_boundary
dashboard.motion_limits
dashboard.process_preview
dashboard.process_adaptation
dashboard.communication_faults
```

### 5.3 ActionPlan

```json
{
  "planId": "plan-uuid",
  "source": "text",
  "rawText": "J1转到30度",
  "semanticLevel": 3,
  "actionType": "template",
  "target": "J1转动30度",
  "funcId": 106,
  "params": {
    "axisNo": 0,
    "posVal": 30,
    "spdPct": 50
  },
  "safetyLevel": "confirm_required",
  "status": "planned",
  "confirmRequired": true,
  "reason": "命中模板规则"
}
```

### 5.4 PrecheckResult

```json
{
  "planId": "plan-uuid",
  "status": "pass",
  "progressPct": 100,
  "checks": [
    {"id": "state", "label": "状态检查", "status": "pass", "detail": "无急停、无报警、未暂停"},
    {"id": "limits", "label": "软限位检查", "status": "pass", "detail": "目标30度在限位内"},
    {"id": "speed", "label": "速度检查", "status": "pass", "detail": "指令50%，上限150%"},
    {"id": "spatial", "label": "空间范围检查", "status": "pass", "detail": "R/Z在安全范围内"}
  ],
  "riskSummary": "",
  "suggestion": ""
}
```

### 5.5 ExecutionState

```json
{
  "taskId": 1001,
  "planId": "plan-uuid",
  "status": "running",
  "currentStep": "J1运动至30度",
  "stepIndex": 1,
  "totalSteps": 1,
  "progressPct": 62,
  "progressEstimated": true,
  "currentValue": 22.5,
  "targetValue": 30,
  "result": null
}
```

### 5.6 ConversationEvent

用于对话区、最近事件、日志归档。

```json
{
  "eventId": "event-uuid",
  "sessionId": "session-id",
  "timestamp": "2026-05-18T00:00:00.000+08:00",
  "role": "assistant",
  "type": "ack",
  "text": "收到，正在读取当前状态",
  "payload": {}
}
```

枚举要求：

```text
role: user | assistant | system
type: input | ack | soothing | result | plan | precheck | confirmation | execution | alarm_occurred | alarm_confirmed | alarm_reset | system_notice | error
```

其中 `system` 用于报警播报、急停状态变化、通讯异常、运动完成等主动推送消息。

### 5.7 AlarmEvent

报警事件有独立生命周期，不只是一条普通对话消息。

```json
{
  "alarmId": "alarm-uuid",
  "timestamp": "2026-05-18T00:00:00.000+08:00",
  "level": "critical",
  "code": "ERR_001",
  "message": "J2轴速度超限",
  "detail": {
    "axis": "J2",
    "currentSpeedPct": 85,
    "limitSpeedPct": 80,
    "overPct": 5
  },
  "lifecycle": "occurred",
  "acknowledged": false,
  "resettable": true,
  "snapshot": {}
}
```

枚举要求：

```text
level: warning | critical | emergency
lifecycle: occurred | acknowledged | reset_requested | reset_done | cleared
```

报警处置规则：

- `warning` 可由操作者确认，是否允许复位由后端根据报警码判断。
- `critical` 需要操作者确认，复位前必须满足设备状态允许条件。
- `emergency` 级别不允许前端直接复位，必须由后端安全规则或工程师权限确认后开放复位动作。

## 6. API 设计

### 6.1 状态与看板

```text
GET /api/health
GET /api/snapshot
GET /api/dashboard
WS  /ws/telemetry
```

推送策略：

- 后端控制核心可按现有 50ms 核心轮询维护内部状态。
- WebSocket 推送建议 100ms 到 200ms 一次。
- 前端渲染节流 100ms 到 250ms。
- 急停、报警、暂停、通讯异常立即推送事件。

### 6.2 对话、NLP 与计划

```text
POST /api/conversation/input
GET  /api/conversation/events?session_id=
GET  /api/conversation/sessions
POST /api/nlp/parse
POST /api/plans
GET  /api/plans/{plan_id}
POST /api/plans/{plan_id}/precheck
POST /api/plans/{plan_id}/confirm
POST /api/plans/{plan_id}/adopt-suggestion
POST /api/plans/{plan_id}/execute
POST /api/plans/{plan_id}/cancel
```

建议前端主流程只调用 `/api/conversation/input`，后端返回回执事件和计划事件；工程师调试页才直接调用 `/api/nlp/parse` 查看原始解析。

`/api/conversation/input` 是操作者入口，请求体必须包含 `session_id`：

```json
{
  "session_id": "session-uuid",
  "source": "text",
  "text": "J1转到30度",
  "client_event_id": "client-event-uuid"
}
```

`/api/nlp/parse` 是工程师调试入口，只解析，不生成计划，不执行。

### 6.3 系统动作

```text
POST /api/system/estop
POST /api/system/pause
POST /api/system/resume
POST /api/system/reset
POST /api/system/cancel
```

要求：

- 后端独立处理，不等待大模型。
- 返回执行确认、当前状态快照、日志 ID。
- 急停按钮始终可用，但后端仍要写日志并返回结果。

### 6.4 语音控制 API

短期采用后端语音采集/识别方案，前端只负责开始/停止和展示状态。

```text
GET  /api/voice/devices
POST /api/voice/start
POST /api/voice/stop
GET  /api/voice/status
```

Web 前端按按钮触发，后端继续复用/拆分现有 iFlytek、VAD、`voice_ipc.py`、`iflytek_worker.py` 能力。长期如需要浏览器麦克风，可再增加 Web Audio 上传通道。

`GET /api/voice/devices` 返回格式：

```json
{
  "devices": [
    {"id": "0", "name": "Microphone Array", "isDefault": true, "available": true}
  ],
  "currentDeviceId": "0",
  "mode": "local_iflytek"
}
```

语音完成通知：

- 前端可调用 `POST /api/voice/stop` 获取最终文本。
- 后端也应通过 WebSocket 推送 `conversation_event`，类型为 `voice_input_complete` 或 `error`。
- VAD 自动结束时，后端必须主动推送 `voice_input_complete`，前端收到后再进入 `/api/conversation/input` 流程。

### 6.5 工程师后台

```text
GET    /api/templates
GET    /api/templates/{key}
POST   /api/templates
PUT    /api/templates/{key}
DELETE /api/templates/{key}
POST   /api/templates/import
GET    /api/templates/export

GET    /api/flows
GET    /api/flows/{name}
POST   /api/flows
PUT    /api/flows/{name}
DELETE /api/flows/{name}

GET    /api/system-config
PUT    /api/system-config
POST   /api/system-config/read-controller-limits

GET    /api/avoidance-config
PUT    /api/avoidance-config

GET    /api/logs
GET    /api/logs/sessions
GET    /api/logs/sessions/{session_id}
GET    /api/logs/export
```

### 6.6 单个看板查询

```text
GET /api/dashboard/{dashboard_id}
```

优先级较低，用于工程师诊断或前端按需展开完整看板。常规状态展示应优先使用 `/api/dashboard` 或 WebSocket 推送。

### 6.7 WebSocket 消息格式

所有 WebSocket 消息统一格式：

```json
{
  "type": "snapshot",
  "timestamp": "2026-05-18T00:00:00.000+08:00",
  "session_id": "session-uuid",
  "payload": {}
}
```

类型枚举：

```text
snapshot
dashboard
execution_update
conversation_event
alarm
system_notice
error
heartbeat
```

重连策略：

- 前端断开后 1s 开始重连。
- 固定间隔 2s 重试，连续失败 10 次后降级为 5s 间隔。
- 重连成功后立即调用 `/api/snapshot`、`GET /api/conversation/events?session_id=`、当前任务的 `GET /api/plans/{plan_id}` 恢复页面。

### 6.8 统一错误格式

所有 API 错误统一返回：

```json
{
  "error": true,
  "code": "PRECHECK_FAILED",
  "message": "目标位置超出安全范围",
  "detail": {
    "field": "r",
    "current": 850,
    "limit": 800,
    "over": 50
  },
  "request_id": "request-uuid",
  "timestamp": "2026-05-18T00:00:00.000+08:00"
}
```

前端禁止只显示“失败”或“异常”，必须展示 `message`，并在工程师页可展开 `detail`。

## 7. 关键业务流程

### 7.1 查询流程

```text
用户输入查询
  -> 前端立即显示本地 pending 消息
  -> 后端 <=50ms 返回 ack
  -> 后端读取 dashboard cache
  -> 返回精确结果
  -> 写 ConversationEvent
```

查询不生成执行计划，不需要确认。

### 7.2 运动动作流程

```text
用户输入动作
  -> ack：收到，正在读取当前状态
  -> NLP 解析
  -> 生成 ActionPlan
  -> 展示计划卡
  -> 运行 L1 预检
  -> 必要时运行 L2 预演
  -> 预检通过后进入等待确认
  -> 用户确认
  -> 后端执行 V5.0 三道门
  -> WebSocket 推送 ExecutionState
  -> 完成/失败/报警结果反馈
```

### 7.3 流程动作流程

```text
用户要求运行流程
  -> ack
  -> 匹配 flows.json
  -> 生成多步骤 ActionPlan
  -> L1 逐步骤预检
  -> L2 逐运动步骤预演
  -> L3 时序/累积误差/干涉检查
  -> 展示流程预演结论
  -> 用户确认
  -> 逐步骤执行并推送进度
```

### 7.4 风险动作流程

```text
预检发现风险
  -> 切换安全确认场景
  -> 说明风险参数、当前值、安全值、差值
  -> 给出建议
  -> 用户选择取消/采纳建议/授权确认
```

高风险默认不允许直接确认执行。授权确认是否开放由工程师安全参数配置。

### 7.5 应急流程

```text
用户触发急停按钮或有效三段式应急编码
  -> 不走 NLP
  -> system/estop API
  -> Func104 独立通道
  -> <=100ms 给出确认反馈
  -> 切换报警/安全状态场景
```

单独提到“急停”但未命中授权编码时：系统回应“已收到，未识别有效授权应急指令，不执行动作”。

### 7.6 对话恢复流程

页面刷新、重新登录、WebSocket 重连后，前端必须恢复上下文。

```text
页面启动/刷新
  -> 读取本地 session_id
  -> GET /api/snapshot
  -> GET /api/conversation/events?session_id=
  -> 如存在 active_plan_id，GET /api/plans/{plan_id}
  -> 如存在 running task，恢复 ExecutionState
  -> 重新订阅 WebSocket
```

要求：

- 正在执行的任务不能因为页面刷新而消失。
- 报警状态恢复后必须直接显示报警处理场景。
- 如果后端 session 不存在，前端创建新 session，但必须提示“已开始新会话”。

### 7.7 指令并发与队列策略

工业控制场景不允许多个运动计划无序并发。

策略：

| 当前状态 | 新输入类型 | 处理 |
| --- | --- | --- |
| 空闲 | 查询 | 立即回答 |
| 空闲 | 动作/流程 | 创建计划 |
| 预检中 | 查询 | 立即回答当前预检进度 |
| 预检中 | 新动作/流程 | 默认拒绝，并提示先取消当前预检；工程师模式可配置排队 |
| 等待确认 | 查询 | 立即回答 |
| 等待确认 | 新动作/流程 | 默认要求取消当前计划后再创建新计划 |
| 执行中 | 查询 | 回答当前执行状态 |
| 执行中 | 新动作/流程 | 拒绝，提示当前任务未完成 |
| 报警中 | 普通动作/流程 | 拒绝，只允许报警处理和状态查询 |

后端必须维护计划状态机，前端按钮状态只作为提示，不能作为安全依据。

补充规则：

- 运动计划默认排他，同一时间只允许一个 `active_plan` 处于预检、等待确认或执行中。
- 查询请求不进入运动队列，可并发处理，但回答必须基于最新 `RobotSnapshot`。
- 取消当前计划后，默认不自动推进队列中的旧计划，必须由操作者重新确认。
- 等待确认的计划默认 60s 后失效，失效后必须重新预检。
- 工程师模式如开启排队执行，队列中的每个计划在执行前仍必须重新读取状态并重新预检。
- 报警发生时，所有 pending/running 计划进入 `blocked_by_alarm` 或 `aborted` 状态，禁止自动恢复执行。

### 7.8 执行超时与断线恢复

执行过程中如果 WebSocket 断开：

```text
前端标记“状态同步中断”
  -> 禁止创建新运动动作
  -> 保留急停按钮
  -> 自动重连
  -> 重连后 GET /api/snapshot + active execution
  -> 恢复执行监控或报警场景
```

如果控制器执行超过 `motion_timeout`：

- 后端生成超时事件。
- 前端切换到执行异常/报警提示。
- 普通继续执行按钮禁用。
- 工程师页可查看详细回显和日志。

### 7.9 全语音控制与窗口控制

所有可点击按钮必须有语音等价指令。

| 按钮 | 语音等价 |
| --- | --- |
| 发送 | “发送” |
| 语音输入 | “开始录音”“停止录音” |
| 确认执行 | “确认执行” |
| 取消 | “取消” |
| 采纳建议 | “采纳建议” |
| 暂停 | “暂停” |
| 继续 | “继续” |
| 报警复位 | “复位” |
| 完整状态 | “显示完整状态” |
| 急停 | “急停 XXX 急停” |
| 全屏 | “全屏”“放大界面” |
| 小窗口 | “小窗口”“缩小界面” |

前端阶段先保留语音等价指令清单和 UI 映射；后端语音服务接入后再实现语音触发。

## 8. 安全预检实现

### 8.1 L1 即时安全检查

先实现，纯 Python，离线可用。

| 检查项 | 数据源 | 说明 |
| --- | --- | --- |
| 系统状态 | 看板 1 | 无报警、无急停、未暂停、ready |
| 通道空闲 | 看板 2 | 目标通道可接受新命令 |
| 速度/加速度 | 看板 4 + 指令参数 | 不超过安全上限 |
| 软限位 | 看板 3 | 关节目标值在限位内 |
| R/Z 空间范围 | 看板 3 | 目标位置在安全半径和高度内 |

说明：

- L1 默认优先读取看板缓存，保证快速回执和 UI 进度。
- 真正进入执行前，后端必须基于当前快照重新复查一次状态，不能只信任预检开始时的缓存。
- 安全关键状态如报警、急停、暂停、通道忙闲，在执行前必须再次读取或确认。

### 8.2 L2 运动规划预演

第二阶段实现，调用控制器 `FRAME_TRANS2`，Python 侧做决策。

| 检查项 | 实现 |
| --- | --- |
| FSTATUS 遍历 | 调控制器逆解，Python 评分 |
| 奇异点检测 | 路径插值，多点逆解检查 |
| 中间点策略 | 结合安全中间点配置 |
| 运动规划结论 | 返回可达、需中间点、不可达、风险 |

性能要求：

- 单次 `FRAME_TRANS2` 调用耗时需要在真机联调时测量并写入日志。
- 初始性能目标：单次 `FRAME_TRANS2` <=100ms，8 次 FSTATUS 遍历 <=1s；若真机实测超过该目标，以 L2 总耗时 <=5s 和进度播报为验收底线。
- FSTATUS 8 次遍历应异步执行，并每 1s 推送进度。
- 目标：常规 L2 预演 <=5s；如果超过 2s，前端必须显示安抚性回应和当前进度。

### 8.3 L3 流程预演

第三阶段实现。

| 检查项 | 实现 |
| --- | --- |
| 逐动作安全 | 对流程每一步调用 L1 |
| 逐路径规划 | 对运动步骤调用 L2 |
| 时序适配 | 规则状态机 |
| 累积误差 | 简化误差传播 |
| 干涉检查 | 简化包围盒或配置规则 |

## 9. 前端实现阶段

### F1：整理 KINETIX 原型

目标：把 AI Studio 静态 demo 变成可维护项目。

F1 拆成两步，避免低估状态集中化工作量。

#### F1a：项目清理和组件抽取

任务：

1. 修改项目名、README、HTML title、语言。
2. 补 Tailwind token：`on-surface`、`on-surface-variant` 等。
3. 清理或标注未使用依赖。
4. `SafetyConfig.tsx` 改名为 `SafetyPreCheck.tsx`。
5. 抽公共组件：`TopStatusBar`、`SideNav`、`RightStatusPanel`、`EmergencyActions`、`PlanCard`、`EventTimeline`。
6. 增加统一类型：`RobotSnapshot`、`ActionPlan`、`PrecheckResult`、`ExecutionState`、`ConversationEvent`、`AlarmEvent`。

#### F1b：状态集中化

任务：

1. 把顶部状态栏、右侧状态栏、完整状态页的硬编码状态集中到 `state/mockData.ts`。
2. 新增 `robotStore.ts` 或 React Context 管理全局 `RobotSnapshot`。
3. 增加 `connection.online`、`system.alarm`、`system.busy` 等全局派生选择器。
4. 所有页面禁止直接写死“在线/离线/报警/速度/R/Z”等关键状态。

验收：

- `npm install`
- `npm run lint`
- `npm run build`
- 角色切换正常。
- 页面之间状态不再互相矛盾。

### F2：实现前端 mock 闭环

目标：不接后端也能演示完整业务。

任务：

1. 智能对话输入后立即生成回执。
2. 命中 mock 命令后生成计划卡。
3. 安全预检页展示逐项检查和进度。
4. 确认执行后进入执行监控页。
5. 执行进度用 mock timer 推进。
6. 报警按钮或 mock 报警事件切换到报警页。
7. 最近事件时间线记录全过程。

验收：

- “J1 转到 30 度”可完整走：回执 -> 计划 -> 预检 -> 确认 -> 执行 -> 完成。
- 控制器离线 mock 状态下执行按钮禁用。
- 报警状态下普通执行不可用。

### F3：接入真实 API

目标：前端从 mock store 切换到 Python API。

任务：

1. 新增 `api/client.ts`、`api/websocket.ts`。
2. `.env.local` 配置：

```text
VITE_API_BASE_URL=http://127.0.0.1:8765
VITE_WS_URL=ws://127.0.0.1:8765/ws/telemetry
```

3. `TopStatusBar`、`RightStatusPanel`、`SystemStatus` 接 `RobotSnapshot`。
4. `SystemLogs` 接 `/api/logs`。
5. 对话页接 `/api/conversation/input`。
6. WebSocket 实现自动重连和断线状态提示。
7. 页面刷新后按 7.6 恢复对话、计划和执行状态。

验收：

- mock_controller 状态能实时显示到 Web 页面。
- 断开控制器后 Web 页面进入离线状态。
- API 错误有明确提示，不 fallback 成假在线。

### F4：扩展工程师页面

目标：HTML 页面接管旧 Qt 后台主要功能。

优先级：

| 优先级 | 页面 | 内容 |
| --- | --- | --- |
| P0 | 运行调试页 | 连接、模拟/真实切换、固定指令、实时反馈 |
| P1 | 指令模板页 | 接 `query_table.json` |
| P1 | 流程管理页 | 接 `flows.json` |
| P2 | 日志诊断页 | session JSONL 查询、过滤、导出 |
| P3 | 安全参数页 | 接 `system_config.json` |
| P3 | 安全中间点页 | 接 `avoidance_rules.json` |
| P3 | 自然语言调试页 | 规则/DeepSeek 解析、白名单校验、原始 JSON |
| P3 | 授权系统页 | 授权状态和订阅能力 |

验收：

- 当前 Qt 后台关键能力在 Web 页面可完成。
- 保存模板/流程/参数后，后端执行和预检能读取最新配置。

## 10. 后端实现阶段

### B1：服务壳和 mock 状态

目标：建立 Python Web 服务，不动控制链路。

技术选型：**确定使用 FastAPI**。

原因：

- WebSocket 支持成熟。
- Pydantic 模型校验能直接承接第 5 章数据模型。
- 自动生成 OpenAPI 文档，方便前端联调。
- 异步能力足够支撑状态推送，不阻塞控制器轮询。

新增依赖：

```text
fastapi
uvicorn[standard]
pydantic
```

启动方式：

- B1 阶段先用独立脚本启动：`python -m robot_modbus_lite.web_server`。
- 后续 EXE 阶段再由启动器在同一进程线程或子进程中启动。
- `uvicorn` 的 reload 必须关闭。
- 必须支持优雅关闭，释放端口和后台线程。

任务：

1. 新增 `web_server.py`。
2. 提供 `/api/health`、`/api/snapshot`、`/api/logs`。
3. 提供 `/ws/telemetry`，先推 mock snapshot。
4. 服务可独立启动。

验收：

- 浏览器访问本地端口可打开前端。
- `/api/snapshot` 返回合法 JSON。
- WebSocket 能持续推送状态。

### B2：接入现有状态和日志

目标：用真实 Python 状态替代 mock。

任务：

1. `web_state_bridge.py` 聚合 `RobotRealtimeState`、连接状态、报警状态、位置数据。
2. `web_log_service.py` 读取 `data/exported_logs/session_*.jsonl`。
3. 接入 `MockZMotionVrClient` 和真实 `ZMotionVrClient` 的连接状态。
4. WebSocket 推送真实状态。
5. `web_log_service.py` 同时负责日志写入和读取，避免 Web 后端直接依赖 `GuiLoggingMixin` 写文件。
6. 写入 session JSONL 时使用线程锁，避免 Qt 备用页和 Web 后端并发写入冲突。

验收：

- 前端顶部状态与现有 Qt 实时状态一致。
- mock_controller 模式下，执行状态变化能推送到 Web。

### B3：接入 NLP 和计划卡

目标：语言输入生成后端计划，不执行。

任务：

1. 调用 `VoiceNlpAdapter.parse()`。
2. 生成 `ActionPlan`。
3. 查询类直接返回看板结果。
4. 运动/流程/系统动作进入计划卡。
5. 解析失败返回可读建议。
6. 为 Web 后端创建独立 `VoiceNlpAdapter` 实例，不依赖 Qt 事件循环。
7. 确认 DeepSeek 客户端、模板表、流程列表在线程/请求间的访问安全。

验收：

- 输入“执行流程1”能得到 flow 计划。
- 输入模板关键词能得到 template 计划。
- unknown 不执行，只提示。

### B4：接入 L1 预检

目标：所有运动/流程动作先做后端安全预检。

任务：

1. 实现 `safety_precheck.py` L1。
2. 读取 `system_config.py`、看板状态、动作参数。
3. 生成 `PrecheckResult`。
4. 风险结果必须量化。

验收：

- 超速、越界、报警、急停、暂停、离线均能拒绝执行。
- 风险说明包括当前值、上限/下限和差值。

### B5：接入执行链路

目标：HTML 页面确认后执行现有 V5.0 链路。

短期采用“受控桥接”，长期再重构纯 service。

#### B5a：受控桥接方案

新增 `web_control_bridge.py`：

```text
WebControlBridge
  - 持有现有 RobotQtWindow 或控制核心实例引用
  - 所有执行调用进入单线程执行队列
  - 使用 threading.Lock 防止并发下发
  - 通过 Qt 主线程调度触发依赖 Qt 对象的方法
  - 只暴露 execute_plan/system_action/read_snapshot 等有限接口
```

规则：

- Web API 不直接调用 Qt widget。
- Web API 不直接操作寄存器。
- 所有执行请求进入单一控制队列。
- 如果当前有执行/预检/确认中的计划，新运动计划按第 7.7 节拒绝或排队。
- 桥接层只作为过渡，完成验证后逐步把 V5.0 三道门逻辑下沉到纯 Python service。

#### B5b：执行任务

1. 将可复用的发送逻辑从 Qt mixin 下沉为 service，或先做受控桥接。
2. `/api/plans/{id}/execute` 检查计划状态、预检结果、当前快照。
3. 复用 `SixAxisCommandMixin` 中的 V5.0 三道门逻辑。
4. 执行过程推送 `ExecutionState`。
5. 所有结果写 session 日志。

验收：

- mock_controller 下可从 Web 页面发起并完成动作。
- 报警/离线/暂停时后端拒绝执行。
- 执行结果进入 Web 时间线和日志。

风险控制：

- B5 是最高风险阶段，必须先在 mock_controller 模式完成。
- 真机联调前必须保留旧 Qt 页面作为备用入口。
- 所有 Web 执行请求必须带 `plan_id`、`session_id`、`confirm_token`，后端验证后才执行。

### B6：系统动作独立 API

目标：急停、暂停、继续、复位不依赖 NLP。

任务：

1. 实现 `/api/system/*`。
2. 复用 Func104 系统动作。
3. 急停按钮始终可调用。
4. 三段式语音应急编码由后端校验。

验收：

- 有效急停编码命中后不等待 NLP。
- 单独提到急停不触发动作。
- 系统动作全部写日志。

### B7：语音服务接入

目标：让 Web 页面可控制现有 iFlytek + VAD 能力。

短期方案：

```text
Web 前端按钮
  -> /api/voice/start
  -> 后端启动本地麦克风采集/讯飞识别
  -> /api/voice/stop
  -> 后端返回识别文本
  -> 前端把识别文本送入 /api/conversation/input
```

原因：

- 当前语音能力已经在 Python 侧实现，直接复用风险最低。
- 浏览器 Web Audio 会引入麦克风权限、音频格式、上传延迟和现场兼容问题。

长期方案：

- 若需要平板/远程浏览器操作，再增加浏览器 Web Audio 上传通道。
- 远程语音必须重新评估网络、安全和实时性。

验收：

- Web 页面点击“开始录音/停止录音”可得到识别文本。
- 语音 VAD 回执 <=200ms。
- 语音识别失败有明确提示。
- 应急语音编码由后端校验，不依赖前端文本判断。

### B8：L2/L3 与工程师后台

目标：补齐高级预演和后台管理。

任务：

1. L2 接控制器 `FRAME_TRANS2`。
2. L3 接流程级预演。
3. 模板、流程、系统参数、安全中间点 API。
4. 参数保存后热更新预检服务。

验收：

- L2 预演结果与控制器一致。
- 流程预演能返回逐步风险。
- 工程师后台修改能影响后续执行。

## 11. 桌面替换策略

### 11.1 阶段 A：浏览器模式

```text
python -m robot_modbus_lite.web_server
自动打开 http://127.0.0.1:8765
```

用途：

- 最快验证 Web 页面和后端 API。
- 不引入 QWebEngine 打包复杂度。

### 11.2 阶段 B：Qt 备用入口

旧 Qt 页面继续保留：

- 作为工程师故障排查入口。
- 作为 Web 页面未覆盖功能的备用入口。
- 作为真机联调时的安全回退。

### 11.3 阶段 C：Qt 壳模式

稳定后新增薄 Qt 壳：

```text
QMainWindow
  -> 启动 Python Web 服务
  -> QWebEngineView 加载 http://127.0.0.1:8765
```

注意：

- 当前 `requirements.txt` 只有 `PySide6`，没有 WebEngine 相关依赖。
- 引入 `PySide6-WebEngine` 后需重新验证 PyInstaller 打包体积和兼容性。

### 11.4 阶段 D：默认入口切换

最终：

- 普通启动进入 HTML 使用页面。
- 工程师入口进入 HTML 工程师页面。
- 旧 Qt 页面降级为隐藏调试入口或完全移除。

## 12. EXE 打包交付要求

最终交付形态必须仍然是 Windows `.exe` 桌面软件。HTML 页面只是新的界面层，不改变最终“上位机桌面程序”的交付方式。

### 12.1 最终运行形态

推荐最终形态：

```text
RobotModbusLite.exe
  -> 启动 Python 控制服务
  -> 加载 React build 静态页面
  -> 打开桌面窗口或本地浏览器
  -> 连接 mock_controller / 真实 ZMotion 控制器
```

React 页面不单独作为公网 Web 应用发布，而是作为本地静态资源随 EXE 发布。

### 12.2 两阶段打包策略

#### 阶段一：浏览器模式 EXE

先打包成浏览器模式，用于开发验证和早期交付。

```text
RobotModbusLite.exe
  -> 启动 http://127.0.0.1:8765
  -> 自动打开 http://127.0.0.1:8765
```

优点：

- 打包风险最低。
- 不引入 Qt WebEngine 体积和兼容性问题。
- 前后端接口、控制链路、安全逻辑可以先稳定。

缺点：

- 用户看到的是系统浏览器窗口。
- 桌面软件一体感弱。

#### 阶段二：Qt WebEngine 壳 EXE

功能稳定后，再做桌面壳。

```text
RobotModbusLite.exe
  -> 启动本地 Python Web 服务
  -> QWebEngineView 加载 http://127.0.0.1:8765
  -> 用户看到完整桌面窗口
```

优点：

- 最终体验接近传统桌面软件。
- 可以保留窗口标题、菜单、授权入口、关闭确认等桌面能力。

缺点：

- 需要引入 `PySide6-WebEngine`。
- PyInstaller 打包体积会显著增加。
- 需要重新验证 Windows 兼容性、资源路径、WebEngine 子进程和 DLL 收集。

### 12.3 打包资源结构

React 前端必须先构建：

```text
cd web/kinetix-os---industrial-controller
npm install
npm run build
```

构建产物建议复制或配置到：

```text
web/kinetix-os---industrial-controller/dist/
```

PyInstaller 发布目录建议保持目录式打包：

```text
打包输出/dist/RobotModbusLite/
  RobotModbusLite.exe
  data/
    query_table.json
    flows.json
    system_config.json
    avoidance_rules.json
    exported_logs/
  _internal/
    robot_modbus_lite/
    web_dist/
      index.html
      assets/
    default_data/
      query_table.json
      flows.json
      system_config.json
      avoidance_rules.json
    Windows Python（64位）/
      dll库文件/
      zmcdll/
    ...
```

说明：

- 不建议只发布单个 `RobotModbusLite.exe`。
- 继续采用当前项目已有的目录式发布习惯。
- `web_dist/`、可写 `data/`、ZMotion SDK DLL 都必须随包发布。
- `_internal/default_data/` 仅作为默认配置模板；运行时读写使用 EXE 同级 `data/`。
- `data/exported_logs/` 必须在 EXE 同级可写目录，不能放在只读或可能被覆盖的 `_internal/` 资源目录下。
- 目标机器不需要安装 Node.js；前端必须在打包前预构建完成。

### 12.4 PyInstaller 调整点

后续需要更新：

- `robot_modbus_gui.spec`
- `build_qt.ps1`
- `build_qt.bat`
- `docs/EXE打包说明.md`

需要新增收集项：

```text
web/kinetix-os---industrial-controller/dist -> _internal/web_dist
```

如果进入 Qt WebEngine 壳阶段，还需要收集：

```text
PySide6-WebEngine
QtWebEngineProcess
WebEngine resources
WebEngine translations
相关 DLL
```

### 12.5 启动器职责

最终 EXE 启动器需要负责：

1. 解析运行目录和 `_internal` 资源目录。
2. 启动本地 Python Web 服务。
3. 检查端口是否可用，默认 `127.0.0.1:8765`；端口被占用时优先探测是否为本程序残留服务，否则换端口或提示用户。
4. 加载 `web_dist/index.html` 或通过本地服务提供静态资源。
5. 初始化控制器模式：模拟控制器或真实控制器。
6. 初始化日志目录。
7. 打开浏览器或 `QWebEngineView` 窗口。
8. 关闭时优雅停止 Web 服务、轮询线程、语音线程和控制器连接。

### 12.6 EXE 验收标准

浏览器模式 EXE 验收：

- 双击 `RobotModbusLite.exe` 后能启动本地服务。
- 自动打开 Web 页面。
- `/api/health` 正常。
- Web 页面能显示 mock 或真实状态。
- 关闭程序后端口释放。

Qt WebEngine 壳 EXE 验收：

- 双击后出现桌面窗口，不依赖用户手动打开浏览器。
- Web 页面资源加载完整。
- API 和 WebSocket 正常。
- 急停、暂停、继续、复位按钮可用。
- 退出程序时无残留子进程。
- 在无 Node.js 环境的机器上可运行。

发布验收：

- 目标机器不需要安装 Node.js。
- 目标机器不需要单独执行 `npm install`。
- 目标机器仅需要发布目录和必要控制器网络环境。
- `data/` 配置可读写。
- `exported_logs/` 可正常生成日志。
- mock_controller 模式可离线演示。
- 真实控制器模式可连接 ZMotion SDK。

## 13. 测试、性能与本地安全

### 13.1 测试策略

后端测试：

- API 单元测试：`/api/snapshot`、`/api/conversation/input`、`/api/plans/*`、`/api/system/*`。
- 预检测试：超速、越界、报警、急停、暂停、离线、通道忙。
- 日志测试：session JSONL 写入、读取、并发写入锁。
- mock_controller 集成测试：Web API -> 控制服务 -> mock_controller -> 状态回推。

前端测试：

- 类型检查：`npm run lint`。
- 构建测试：`npm run build`。
- 组件测试后续可引入 Vitest + Testing Library，优先覆盖 `TopStatusBar`、`PlanCard`、`PrecheckPanel`。
- 端到端测试后续可引入 Playwright，覆盖登录、对话、预检、确认、执行、报警。

打包测试：

- 无 Node.js 环境运行。
- mock_controller 离线运行。
- 真实控制器连接运行。
- 退出后端口释放、无残留进程。

### 13.2 性能指标

| 指标 | 目标 |
| --- | --- |
| 文字输入回执 | <=50ms |
| 语音 VAD 回执 | <=200ms |
| 语音识别完整链路 | 短句 <=800ms，长句按音频时长 + 500ms 作为目标 |
| 简单查询结果 | <=500ms |
| L1 预检 | <=2s |
| L2 预演 | <=5s，超过 2s 必须播报进度 |
| WebSocket 推送间隔 | 100ms 到 200ms |
| WebSocket 消息到前端渲染延迟 | P99 <=100ms |
| API 普通查询响应 | P99 <=200ms |
| 前端首屏渲染 | <=1s，本地资源加载 |

性能数据必须写入工程师日志或诊断面板，至少包含 API 耗时、NLP 耗时、预检耗时、执行耗时、语音耗时。

### 13.3 本地访问安全

第一阶段仅支持本地访问：

```text
host = 127.0.0.1
port = 8765
```

要求：

- 默认不绑定 `0.0.0.0`。
- 默认不允许局域网设备访问。
- 前端 API 不需要公网 HTTPS，因为只在本机环回地址运行。
- 如果后续支持平板或远程访问，必须另行设计 Token 认证、CORS 白名单、HTTPS、操作员权限和审计日志。

### 13.4 旧 Qt 页面废弃条件

旧 Qt 页面不能按时间直接删除，必须按能力验收。

满足以下条件后，才允许移除或隐藏旧 Qt 页面：

- Web 操作者页面完成查询、计划、预检、确认、执行、报警闭环。
- Web 工程师页面完成运行调试、模板、流程、系统参数、安全中间点、日志诊断。
- mock_controller 全流程回归通过。
- 至少一轮真实控制器联调通过。
- EXE 浏览器模式或 Qt WebEngine 壳模式打包通过。
- 语音能力在 Web 页面可用，或明确作为后续版本功能并保留替代输入方式。
- Web 授权系统页完成，能够覆盖当前 Qt 授权管理的查看、激活、停用和能力状态展示。
- 旧 Qt 页面所有关键功能已有 Web 对应入口。
- 用户确认 Web 页面可作为默认生产入口。

## 14. 里程碑

### M1：融合方案落地和前端清理

交付：

- 本文档 V2.0。
- KINETIX 项目 README/title/name 修正。
- 统一类型和 mock 数据。
- 公共组件抽取。
- Tailwind token 修正。

验收：

- `npm install`、`npm run lint`、`npm run build` 通过。
- 页面状态集中管理。

### M2：前端 mock 业务闭环

交付：

- 回执、计划卡、预检、确认、执行监控、报警 mock 流程。

验收：

- 不接后端可演示完整操作者流程。

### M3：Python Web 服务雏形

交付：

- `/api/health`
- `/api/snapshot`
- `/api/logs`
- `/ws/telemetry`
- 前端接真实 API。

验收：

- 前端显示 Python 推送状态。

### M4：NLP、计划、L1 预检接入

交付：

- `/api/conversation/input`
- `/api/plans`
- `/api/plans/{id}/precheck`
- `VoiceNlpAdapter` 接入
- L1 预检

验收：

- 查询直接回答。
- 动作生成计划卡。
- 风险动作不允许直接执行。

### M5：执行链路接入

交付：

- `/api/plans/{id}/execute`
- `/api/system/*`
- WebSocket 执行进度推送
- 日志归档

验收：

- mock_controller 可从 Web 页面完成动作。
- V5.0 三道门不被绕过。

### M6：工程师后台 Web 化

交付：

- 模板、流程、参数、安全中间点、日志诊断 Web 页面。

验收：

- 当前 Qt 后台主要能力可在 Web 页面完成。

### M7：语音服务接入

交付：

- `/api/voice/devices`
- `/api/voice/start`
- `/api/voice/stop`
- `/api/voice/status`
- Web 页面语音按钮联通后端识别。

验收：

- Web 页面可完成一次语音识别并进入对话流程。
- VAD 回执 <=200ms。
- 应急语音编码由后端校验。

### M8：默认入口切换和打包

交付：

- 浏览器模式启动器。
- 可选 Qt WebEngine 壳。
- PyInstaller 打包验证。
- React build 静态资源进入 EXE 发布目录。
- 无 Node.js 环境运行验证。

验收：

- 操作者无需打开旧 Qt 页面。
- 工程师仍有完整维护入口。
- 最终交付仍为 `RobotModbusLite.exe` 桌面程序。

## 15. 风险与决策

| 风险 | 判断 | 处理 |
| --- | --- | --- |
| Qt mixin 逻辑耦合 | 高风险 | 先桥接，再抽 service |
| Web 原型过于静态 | 已确认 | M1/M2 先补状态和 mock 闭环 |
| 真实状态和前端状态不一致 | 安全风险 | `RobotSnapshot` 单一数据源 |
| WebSocket 推送过快 | 性能风险 | 后端 100-200ms，前端节流 |
| 急停被普通请求阻塞 | 安全风险 | 系统动作独立 API |
| QWebEngine 打包复杂 | 发布风险 | 先浏览器模式，后 Qt 壳 |
| 前端残留假数据 | 操作风险 | real 模式禁止 fallback 为假在线 |
| 修改安全参数影响执行 | 高风险 | 工程师权限 + 二次确认 + 日志 |
| 目标机器没有 Node.js | 发布风险 | 前端必须预构建，EXE 只携带静态资源 |
| 单文件 EXE 资源释放慢 | 低风险（已规避） | 已决策采用目录式发布，不强求单文件 |
| 语音模块仍绑定 Qt 线程 | 高风险 | 新增 `web_voice_service.py`，短期后端采集，长期再评估浏览器音频 |
| B5 桥接误操作 Qt 对象 | 高风险 | `WebControlBridge` 单入口、单队列、锁保护、主线程调度 |

## 16. 立即下一步

建议从 M1 开始，先做前端整理，不急着接控制器：

1. 在 `web/kinetix-os---industrial-controller` 安装依赖并验证构建。
2. 修正 README、title、项目名称和 Tailwind token。
3. 抽公共组件和统一类型。
4. 把硬编码状态集中到 `mockData.ts`。
5. 实现 mock 版 `RobotSnapshot`、`ActionPlan`、`PrecheckResult`、`ExecutionState`。
6. 让顶部状态栏、右侧状态栏、完整状态页共享同一份 mock snapshot。

完成 M1 后，再进入 M2 的前端 mock 业务闭环。M1/M2 完成之前，不建议直接接真实控制器。
