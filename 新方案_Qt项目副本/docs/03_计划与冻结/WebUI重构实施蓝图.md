# WebUI 重构实施蓝图

## 1. 当前代码现实

- `robot_modbus_lite/qt_gui.py` 是单体窗口类，约 3800 行，承担了状态存储、业务编排、线程调度、设备通信、语音/NLP、授权入口、以及全部 Qt 组件构建。
- 可复用核心不在 UI，而在这些链路：
  - 指令执行链：`_execute_query_key()` -> `_build_execution_plan()` -> `_execute_send_by_protocol()` -> `_wait_for_*()` -> `_evaluate_feedback_result()`
  - 轮询链：`_start_realtime_polling()` -> `_poll_feedback_silent()` -> `_read_realtime_once()` -> `_apply_realtime_values()`
  - 流程链：`_start_flow()` / `_run_current_flow_step()` / `_run_next_flow_step()`
  - NLP/语音链：`_parse_nlp_text()` / `_execute_nlp_text()` / `_ensure_mic_stream()` / `_recognize_via_*()`
  - 授权链：`LicenseManager` + `_init_api_clients()` + `_show_license_dialog()`
- 真正必须废弃的是 Qt Widget/QSS/UI 刷新层：
  - `_build_*`
  - `_apply_styles()`
  - `_show_page()`
  - `_show_warning()` / `_show_info()` / `_show_critical()`
  - 所有直接写控件的 `_refresh_*`

## 2. 关键结论

- 这次不是“把 Qt 页面翻译成 HTML”，而是把 `RobotQtWindow` 拆成 4 层：
  1. 业务控制器层
  2. 状态仓库层
  3. WebChannel Bridge 层
  4. Web 前端层
- `Bridge` 只能做协议适配，不能继续承载业务逻辑，否则会得到一个新的 `qt_gui.py`。
- 现有文档提到的 `stitch_industrial_robot_nlp_system/` 在当前工作区不存在，前端参考资源现在是缺失状态，不能把它当成已具备前置条件。

## 3. 建议目录结构

```text
新方案_Qt项目副本/
├─ gui_main.py
├─ robot_modbus_lite/
│  ├─ app_state.py
│  ├─ gui_controller.py
│  ├─ bridge.py
│  ├─ web_window.py
│  ├─ web_assets/
│  │  ├─ index.html
│  │  ├─ pages/
│  │  │  ├─ run_dashboard.html
│  │  │  ├─ manage_dashboard.html
│  │  │  ├─ logs_dashboard.html
│  │  │  ├─ params_dashboard.html
│  │  │  └─ license_dashboard.html
│  │  ├─ js/
│  │  │  ├─ qwebchannel-init.js
│  │  │  ├─ api.js
│  │  │  ├─ store.js
│  │  │  └─ pages/
│  │  └─ css/
│  ├─ qt_gui.py
│  ├─ service.py
│  ├─ models.py
│  ├─ license_manager.py
│  └─ ...
└─ data/
```

## 4. Python 层拆分设计

### 4.1 `AppState`

职责：

- 保存当前 GUI 运行状态
- 统一序列化为前端可消费 JSON
- 管理增量更新，而不是让前端依赖零散 Signal

建议状态域：

```python
{
  "connection": {
    "host": "",
    "controllerType": "mock|real",
    "protocol": "legacy|standard",
    "connectionLabel": "",
    "monitorLabel": "",
    "connected": False
  },
  "robot": {
    "x": "1250.0",
    "y": "0.0",
    "z": "860.0",
    "r": "0.0 / 0.0 / 0.0",
    "speed": "30% / 40%",
    "runState": "空闲",
    "busy": "空闲",
    "mode": "自动",
    "result": "0",
    "alarmCode": "ERR_000",
    "alarmText": "系统正常",
    "ioStatus": "0",
    "clawEnable": "0",
    "clawBrake": "0",
    "servoEnable": "0",
    "monitorTask": "-",
    "motionPercent": "0%",
    "echoCmd": "-",
    "execState": "0"
  },
  "flow": {
    "currentName": null,
    "stepIndex": 0,
    "currentStep": "-",
    "status": "空闲",
    "running": False,
    "available": []
  },
  "nlp": {
    "parseBusy": False,
    "executeBusy": False,
    "lastPlan": null
  },
  "voice": {
    "recording": False,
    "deviceList": [],
    "selectedDevice": null,
    "mode": "proxy|local"
  },
  "license": {
    "valid": False,
    "message": "",
    "licenseType": "",
    "voiceEnabled": False,
    "deepseekEnabled": False,
    "expiresAt": null
  },
  "data": {
    "table": {},
    "history": [],
    "logs": [],
    "safePoints": {},
    "axisRanges": {}
  },
  "ui": {
    "currentPage": "run",
    "statusText": ""
  }
}
```

### 4.2 `GuiController`

职责：

- 承接当前 `RobotQtWindow` 中所有非 Widget 逻辑
- 对外暴露“纯 Python 方法”
- 只操作 `AppState`，不直接操作 HTML/Qt 控件

建议迁移到控制器的方法簇：

- 连接与客户端
  - `_make_client()`
  - `_get_client()`
  - `_disconnect_client()`
  - `_check_connection()` 的业务部分
- 执行与系统命令
  - `_execute_query_key()`
  - `_build_execution_plan()`
  - `_select_safe_point_for_record()`
  - `_build_safe_point_record()`
  - `_execute_send_by_protocol()`
  - `_handle_system_action()`
  - `_apply_feedback_values()`
  - `_validate_record()`
- 轮询与线程
  - `_start_realtime_polling()`
  - `_pause_polling()`
  - `_resume_polling()`
  - `_poll_feedback_silent()`
  - `_read_feedback_once()`
  - `_read_realtime_once()`
  - `_apply_realtime_values()`
  - `_run_in_background()`
  - `_run_on_main_thread()`
- 流程引擎
  - `_start_flow()`
  - `_step_flow()`
  - `_stop_flow()`
  - `_reset_flow()`
  - `_run_next_flow_step()`
  - `_run_current_flow_step()`
  - `_current_flow_definition()`
- NLP/语音
  - `_build_voice_nlp_adapter()`
  - `_parse_nlp_text()`
  - `_execute_nlp_text()`
  - `_execute_nlp_plan()`
  - `_run_next_nlp_action()`
  - `_ensure_mic_stream()`
  - `_recognize_via_proxy()`
  - `_recognize_via_local()`
- 日志和历史
  - `_append_log()`
  - `_append_log_entry()`

### 4.3 `WebBridge`

职责：

- `@Slot` 入站调用
- `Signal` 出站推送
- 参数解码、错误包装、结果序列化

Signal 建议只保留这几类：

- `stateBootstrap(str json)`
- `statePatched(str json)`
- `eventRaised(str json)`
- `toastRaised(str json)`

不要为每个状态字段单独定义 Signal。

### 4.4 `WebWindow`

职责：

- `QWebEngineView + QWebChannel` 容器
- 加载本地 `web_assets/index.html`
- 注册 `bridge` 到 JS `window.qtBridge`

## 5. Slot 重新分组

现有约 50 个 Slot 不建议原样搬运，建议收敛为资源型接口。

### 5.1 connection

- `bootstrap()`
- `getConnectionState()`
- `updateConnectionConfig(payload)`
- `checkConnection()`
- `readFeedback()`

### 5.2 command

- `executeTemplate(queryKey)`
- `executeSystemAction(actionKey)`
- `parseNlpText(text, useDeepseek)`
- `executeNlpText(text, useDeepseek)`
- `clearNlpState()`

### 5.3 voice

- `listMicrophones()`
- `setMicrophoneDevice(deviceId)`
- `toggleMicrophoneRecording()`
- `transcribeAudioFile(filePath)`

### 5.4 template

- `listTemplates()`
- `getTemplate(queryKey)`
- `saveTemplate(payload)`
- `cloneTemplate(queryKey, newKey)`
- `deleteTemplate(queryKey)`
- `importTemplates(filePath)`
- `exportTemplates(filePath)`

### 5.5 flow

- `listFlows()`
- `getFlow(name)`
- `saveFlow(payload)`
- `deleteFlow(name)`
- `startFlow(name)`
- `stepFlow()`
- `stopFlow()`
- `resetFlow()`

### 5.6 config

- `getSystemConfig()`
- `saveSystemConfig(payload)`
- `getAvoidanceConfig()`
- `saveAvoidanceConfig(payload)`
- `saveSafePoint(payload)`
- `deleteSafePoint(name)`

### 5.7 logs

- `getLogs()`
- `clearLogs()`
- `exportLogs(filePath)`

### 5.8 license

- `getLicenseStatus()`
- `activateLicense(payload)`
- `deactivateLicense()`
- `refreshLicenseStatus()`

## 6. 前端页面映射

### 6.1 `run_dashboard.html`

必须首批完成，因为它覆盖主路径：

- 连接配置
- 指令卡片
- 系统命令
- NLP 文本输入/解析结果
- 麦克风录音
- 流程运行面板
- 实时状态 HUD

### 6.2 `manage_dashboard.html`

- 模板 CRUD
- 流程 CRUD
- 安全中间点维护

### 6.3 `logs_dashboard.html`

- 日志表格
- 导出
- 清空

### 6.4 `params_dashboard.html`

- 轴范围参数
- 规避规则

### 6.5 `license_dashboard.html`

- 授权状态
- 激活
- 续期状态展示
- 配额展示

## 7. 迁移顺序

### Phase 0: 拆出业务内核

目标：

- 不改视觉
- 先让 `qt_gui.py` 不再直接持有主要业务编排

动作：

- 新建 `app_state.py`
- 新建 `gui_controller.py`
- 将执行链、轮询链、流程链、NLP/语音链迁入控制器
- Qt 现有界面临时继续作为壳调用控制器

完成标准：

- `qt_gui.py` 主要只剩控件构建和控件绑定

### Phase 1: 搭 Web 容器

动作：

- 新建 `web_window.py`
- 新建 `bridge.py`
- 接通 `QWebEngineView` 与 `QWebChannel`
- 提供 `bootstrap()` 和最小状态推送

完成标准：

- 本地 HTML 能收到初始状态并调用一个测试 Slot

### Phase 2: 跑通运行页

动作：

- 先做 `run_dashboard.html`
- 接入连接、执行、实时状态、系统命令、NLP、语音、流程启动

完成标准：

- 不依赖 Qt 原页面，也能完成一条完整主链路

### Phase 3: 迁移管理页与参数页

动作：

- 模板 CRUD
- 流程 CRUD
- 安全点维护
- 系统参数配置

### Phase 4: 迁移日志和授权页

动作：

- 日志页面
- `LicenseDialog` HTML 化
- 启动阶段授权检查重接

### Phase 5: 删除旧 Qt UI

动作：

- 删除 `_build_*`
- 删除 `_apply_styles()`
- 删除旧控件字段和对应刷新代码

## 8. 当前已确认的风险

### 8.1 文档与代码有一处关键偏差

- 文档声称 Stitch 设计目录在项目根目录存在。
- 当前工作区中该目录不存在。
- 这意味着前端资产不能按“基于现有模板改造”来排期，必须先补回设计资源，或改为自行建立 `web_assets/`。

### 8.2 授权逻辑不能最后再迁

- `license_manager.py` 不是普通设置页逻辑，而是 DeepSeek/语音代理调用前置条件。
- `license_dashboard.html` 可以后做，但授权状态初始化、token 刷新、失效处理要在 `run_dashboard` 首批接入。

### 8.3 语音线程是高风险区

- 当前有两种模式：持久线程录音、子进程录音。
- 这些逻辑现在直接写 Qt 按钮文本和状态栏。
- 如果不先抽成控制器状态机，前端切换后极容易出现“按钮停了但录音没停”。

### 8.4 `_init_api_clients()` 当前依赖 UI 生命周期

- 文档里已经记录它必须在 `_build_ui()` 之后调用。
- 迁移后这个顺序必须改写成“先 controller，后 bridge，最后页面订阅状态”，不能再依赖控件存在。

## 9. 推荐第一批实际代码改造任务

1. 新建 `robot_modbus_lite/app_state.py`
2. 新建 `robot_modbus_lite/gui_controller.py`
3. 把这些方法先迁过去：
   - `_execute_query_key`
   - `_handle_system_action`
   - `_start_realtime_polling`
   - `_poll_feedback_silent`
   - `_start_flow`
   - `_parse_nlp_text`
   - `_execute_nlp_text`
   - `_toggle_microphone_recording`
4. 给控制器补统一返回结构：
   - `{"ok": true, "data": ...}`
   - `{"ok": false, "error": "..."}`
5. 再新建 `bridge.py` 只转发这些控制器能力

## 10. 一句话判断

- 这套重构可以做，但前提不是先画页面，而是先把 `qt_gui.py` 变成“薄壳”。
- 如果跳过控制器层，直接上 `QtWebChannel`，复杂度不会下降，只会把 Qt 控件耦合换成 JS/Bridge 耦合。
