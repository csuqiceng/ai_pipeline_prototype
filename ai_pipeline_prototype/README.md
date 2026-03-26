# AI Pipeline Prototype

这个原型实现了机械手软件链路的最小可运行版本，用来先验证模块边界、调用顺序和真机接入方式：

`语音/视觉输入 -> 任务理解与任务 JSON -> 任务调度器 -> 执行器 -> 控制器服务 -> Motion SDK`

当前项目仍以第一阶段原型验证为主，但已经不再只是纯占位代码。`sdk_adapter.py` 已接入仓库内置的 `zmcdll` Python 示例和 DLL，并支持在真机不可连接时自动回退到 mock 后端，便于持续联调。

## 目录

- `models.py`: 统一数据模型
- `inputs.py`: 语音和视觉输入适配
- `planner.py`: 任务理解与 JSON 生成
- `dispatcher.py`: 任务状态机调度器
- `executor.py`: 控制执行接口，支持模拟执行器和 SDK 风格执行器
- `sdk_adapter.py`: Motion SDK 封装，优先接真实 `zmcdll`，失败时自动回退 mock
- `controller_service.py`: 控制器服务层，负责连接、状态刷新、报警和命令历史
- `app_service.py`: 应用服务层，统一编排输入、规划和调度
- `factory.py`: 执行器工厂
- `demo.py`: 命令行演示与联调入口
- `gui.py`: 基于 Tkinter 的轻量上位机演示界面

## 当前能力

- 解析固定语音命令
- 支持接收结构化语音结果 JSON
- 支持接入科大讯飞 IAT 语音听写
- 接收结构化视觉定位结果
- 生成结构化任务 JSON
- 通过状态机模拟执行主流程
- 通过执行接口封装 `move/grip/release/home/stop`
- 通过 `ControllerService` 统一管理连接、状态、报警和命令历史
- 通过 `MotionSDKClient` 接入真实 `zmcdll` SDK
- 在 DLL 可加载但控制器不可连接时自动回退 mock，保留联调能力
- 提供命令行联调流程和轻量 GUI 展示

## 当前已接入的 SDK 函数

基于仓库内 `Windows Python（64位）/Windows Python（64位）/zmcdll/zauxdllPython.py`，当前已确认并接入这些核心函数：

- 连接类: `ZAux_OpenEth`, `ZAux_OpenCom`, `ZAux_OpenPci`, `ZAux_FastOpen`, `ZAux_Close`
- 通用控制: `ZAux_SetTimeOut`, `ZAux_Direct_SetOp`
- 单轴运动: `ZAux_Direct_Single_MoveAbs`, `ZAux_Direct_Single_Cancel`, `ZAux_Direct_Single_Datum`
- 多轴运动: `ZAux_Direct_MultiMoveAbs`
- 状态读取: `ZAux_GetModbusDpos`, `ZAux_Direct_GetDpos`, `ZAux_Direct_GetAxisEnable`, `ZAux_Direct_GetIfIdle`, `ZAux_Direct_GetAxisStatus`, `ZAux_Direct_GetAxisStopReason`
- 总线回零: `ZAux_BusCmd_Datum`

## 运行

运行命令行演示：

```bash
python -m ai_pipeline_prototype.demo
```

切换到 SDK 风格执行器：

```bash
python -m ai_pipeline_prototype.demo --mode sdk
```

打印当前已接入的 SDK 函数：

```bash
python -m ai_pipeline_prototype.demo --sdk-functions
```

使用结构化语音结果 JSON 直接联调语音接口：

```bash
python -m ai_pipeline_prototype.demo --voice-json voice_input.json
```

`voice_input.json` 示例：

```json
{
  "text": "抓取左边托盘里的工件放到右边工位",
  "intent": "pick_and_place",
  "target_area": "left_tray",
  "destination_area": "right_station",
  "confidence": 0.91,
  "timestamp": "2026-03-26T21:30:00+08:00"
}
```

使用已安装的官方 `xfyunsdkspeech` SDK 接科大讯飞 IAT，并继续走当前规则语义解析：

先在主代码目录 [ai_pipeline_prototype](/Users/a/Desktop/ai_pipeline_prototype/ai_pipeline_prototype) 下创建 `.env`，或手动设置环境变量：

```bash
IFLYTEK_APP_ID=你的appid
IFLYTEK_API_KEY=你的apikey
IFLYTEK_API_SECRET=你的apisecret
```

再执行：

```bash
pip install xfyunsdkspeech
python -m ai_pipeline_prototype.demo --iflytek-iat-audio path\\to\\audio.pcm
```

如果要直接接麦克风录音：

```bash
python -m ai_pipeline_prototype.demo --iflytek-mic --mic-seconds 4
```

也可以指定后端或设备号：

```bash
python -m ai_pipeline_prototype.demo --iflytek-mic --mic-seconds 4 --mic-backend sounddevice
python -m ai_pipeline_prototype.demo --iflytek-mic --mic-seconds 4 --mic-device 1
```

说明：

- 这个入口当前通过已安装的 `xfyunsdkspeech` 官方 SDK 调用讯飞 IAT
- 适合对接 IAT 的 16k 单声道 PCM 文件联调
- 识别出的文本会继续复用 `inputs.py` 里的规则意图解析
- 麦克风模式当前支持 `sounddevice` 或 `pyaudio`
- 如果当前 Python 环境没有安装这些依赖，麦克风模式会直接报缺失依赖
- 当前代码会优先读取 `ai_pipeline_prototype/.env`

运行“连接 -> 读状态 -> 回零 -> 停止 -> 断开”联调流程：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo
```

指定控制器 IP：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --host 192.168.0.11
```

强制使用 mock 后端：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --force-mock-sdk
```

串口方式连接：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --connection-type com --com-port 3
```

PCI 方式连接：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --connection-type pci --pci-card 0
```

自定义轴列表、回零模式和停止模式：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --axes 0,1,2 --homing-mode 0 --stop-mode 3
```

打开轻量 GUI：

```bash
python -m ai_pipeline_prototype.gui
```

## 测试建议

建议按下面顺序测试：

1. 先验证代码可编译：

```bash
python -m compileall ai_pipeline_prototype
```

2. 验证 SDK 封装是否可用：

```bash
python -m ai_pipeline_prototype.demo --sdk-functions
python -m ai_pipeline_prototype.demo --hardware-link-demo --force-mock-sdk
```

3. 再尝试真实控制器联调：

```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --host 控制器IP
```

输出判断方式：

- 如果命令输出中使用 `backend=vendor` 且没有回退错误，说明真实 SDK 链路已经打通
- 如果 `connect(...)` 中出现 `fallback_error=...`，说明 DLL 已成功加载，但当前控制器连接失败，系统已自动降级到 mock

## 当前限制

- 语音理解仍是规则法，不是 ASR/LLM
- 真实语音 SDK 还没有直接接麦克风采集，本阶段先对接识别结果 JSON
- 讯飞 IAT 已支持固定时长麦克风采集，但依赖本机安装 `sounddevice` 或 `pyaudio`
- 视觉输入仍是结构化模拟输入，不是真实视觉算法
- 真机 `pick_and_place` 还没有完全接成稳定的实机动作闭环
- `gui.py --smoke-test` 仍依赖 `tkinter` 模块；如果当前 Python 环境没有 `tkinter`，导入阶段会失败
- 轴号、回零模式、停止模式、夹具输出口等参数仍需按实际控制器配置调整

## 下一步重点

- 用真实控制器参数完成 `--hardware-link-demo` 联调
- 确认真机动作接口更适合“点位式”还是“固定动作式”
- 在调度层补齐急停、报警、非法状态拦截和失败处理
- 再继续接真实视觉和语音输入
