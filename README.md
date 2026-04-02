# AI Pipeline Prototype

二手机械手 AI 智能化升级改造项目原型。

这个项目的目标是先搭建一套最小可运行的软件闭环，用来验证下面这条主链路：

`语音/视觉输入 -> AI 理解与任务 JSON -> 任务调度器 -> 控制执行接口 -> 控制器服务 -> Motion SDK`

当前版本以“原型验证”为主，重点是把系统边界、模块职责和调用链路先跑通，便于后续接入真实麦克风、摄像头、视觉算法、语音识别和 ZMC406 控制器。

## 项目结构

- `ai_pipeline_prototype/models.py`
  统一数据模型，包括语音输入、视觉输入、任务对象、调度结果、控制器状态和报警事件。

- `ai_pipeline_prototype/inputs.py`
  输入适配层。当前使用规则法解析语音文本，并接收模拟视觉检测结果。

- `ai_pipeline_prototype/planner.py`
  任务规划层。负责把语音意图和视觉结果整合成统一任务 JSON。

- `ai_pipeline_prototype/dispatcher.py`
  调度层。负责任务校验、状态机流转和执行链路编排。

- `ai_pipeline_prototype/executor.py`
  执行器接口层。定义 `move_to()`、`grip()`、`release()`、`home()`、`stop()`，支持模拟执行器和 SDK 风格执行器。

- `ai_pipeline_prototype/sdk_adapter.py`
  Motion SDK 适配层。目前仍是占位实现，后续会替换为真实控制器接口调用。

- `ai_pipeline_prototype/controller_service.py`
  控制器服务层。负责连接、状态维护、报警管理和命令历史记录。

- `ai_pipeline_prototype/app_service.py`
  应用服务层。统一串联输入、规划、调度和控制器状态。

- `ai_pipeline_prototype/demo.py`
  命令行演示入口，可运行抓取放置、回零和失败演示场景。

- `ai_pipeline_prototype/gui.py`
  轻量 GUI 演示界面，可查看任务结果、控制器状态、报警、命令历史和任务历史。

## 当前能力

- 支持固定语音命令解析
- 支持模拟视觉输入
- 支持生成结构化任务 JSON
- 支持任务调度器状态机执行
- 支持控制器服务层状态与报警管理
- 支持 GUI 演示
- 支持模拟执行模式和 SDK 风格占位模式
- 支持 JSON 格式指令解析和执行
- 支持多种指令类型：MOVE、GRASP、RELEASE、HOME、STOP、OFFSET_MOVE、PICK_PLACE 等
- 支持自然语言指令解析，可将自然语言转换为 JSON 指令

## GUI使用指南

### 标签页说明

1. **任务输入**
   - **执行按钮**：提交任务
   - **处理流程**：语音文本 → 规则匹配 → 任务规划 → 执行
   - **特点**：传统流程，需要视觉输入参数

2. **语音识别**
   - **执行按钮**：执行识别结果
   - **处理流程**：语音识别 → DeepSeek 解析 → 执行
   - **特点**：语音输入 + DeepSeek 解析
   - **自动执行**：默认开启，语音识别完成后自动解析并执行

3. **自然语言**
   - **执行按钮**：执行指令
   - **处理流程**：自然语言 → DeepSeek 解析 → 执行
   - **特点**：直接使用 DeepSeek 解析自然语言

4. **JSON指令**
   - **执行按钮**：执行指令
   - **处理流程**：JSON 指令 → 执行
   - **特点**：直接执行 JSON 格式指令

### 执行结果展示
- **识别结果**：显示语音识别文本和 DeepSeek 解析结果
- **执行结果**：右侧标签页默认展示，包含详细的执行过程和状态
- **控制器状态**：实时显示连接状态、伺服状态、报警状态等
- **命令历史**：记录所有执行过的命令
- **任务历史**：记录所有执行过的任务

### 操作流程
1. **语音控制**：在"语音识别"标签页点击"开始麦克风识别"，说话后等待自动执行
2. **自然语言控制**：在"自然语言"标签页输入指令，点击"执行指令"
3. **JSON指令控制**：在"JSON指令"标签页输入或加载 JSON 指令，点击"执行指令"
4. **传统任务控制**：在"任务输入"标签页填写语音文本和视觉参数，点击"提交任务"

## 依赖说明

### CLI 依赖

命令行演示、讯飞语音识别、麦克风录音所需的 pip 依赖通过下面的命令安装：

```bash
python -m pip install -r requirements.txt
```

当前 `requirements.txt` 主要覆盖：

- `xfyunsdkspeech`
- `sounddevice`
- `websocket-client`

### GUI 依赖

GUI 本身使用 Python 自带的 `tkinter`，它不是普通的 pip 依赖。

也就是说：

- `requirements.txt` 负责安装命令行和语音相关依赖
- GUI 是否可运行，还取决于你使用的 Python 解释器是否自带 `tkinter`

如果执行：

```bash
python -m ai_pipeline_prototype.gui
```

时提示缺少 `tkinter`，请改用自带 Tk GUI 组件的 Python 3.10+ 环境。Windows 一般建议使用 python.org 官方发行版。

## 运行方式

运行命令行演示：

```bash
python3 -m ai_pipeline_prototype.demo
```

运行 SDK 风格演示：

```bash
python3 -m ai_pipeline_prototype.demo --mode sdk
```

运行 JSON 指令演示：

```bash
python3 -m ai_pipeline_prototype.demo --json-command
```

运行 JSON 指令完整演示：

```bash
python3 -m ai_pipeline_prototype.json_command_demo
```

运行自然语言处理演示：

```bash
python3 -m ai_pipeline_prototype.nlp_demo
```

运行自然语言处理交互式演示：

```bash
python3 -m ai_pipeline_prototype.nlp_demo --interactive
```

运行 GUI：

```bash
python3 -m ai_pipeline_prototype.gui
```

运行 GUI 冒烟测试：

```bash
python3 -m ai_pipeline_prototype.gui --smoke-test
```

## 当前状态

项目当前已经完成“软件原型闭环”，但还没有接入真实设备链路。

还未完成的关键部分包括：

- Motion SDK 真接口接入
- ZMC406 控制器真实连接与状态读取
- 真实 `home()`、`move_to_pose()`、`set_gripper()` 等动作调用
- 真实视觉输入接入
- 真实语音输入接入
- 异常处理与安全联动完善

## 连接真实控制器

### 前提条件

1. **硬件环境**
   - ZMC406 控制器硬件
   - 控制器已连接到网络或串口
   - Windows 操作系统（SDK 是 Windows DLL）

2. **软件环境**
   - Python 3.10+ 官方发行版（自带 tkinter）
   - 项目根目录下的 `Windows Python（64位）` 文件夹完整

### 连接方式

项目支持多种连接方式，具体参数在 [MotionSDKConfig](file:///workspace/ai_pipeline_prototype/sdk_adapter.py#L26-L44) 中配置。

#### 1. 以太网连接（推荐）
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --host 控制器IP
```

#### 2. 串口连接
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --connection-type com --com-port 3
```

#### 3. PCI 连接
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --connection-type pci --pci-card 0
```

#### 4. 快速连接
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --connection-type fast --host 控制器IP
```

### 自定义配置参数

#### 配置运动轴
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --axes 0,1,2
```

#### 配置回零模式
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --homing-mode 0
```

#### 配置停止模式
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --stop-mode 3
```

#### 组合配置示例
```bash
python -m ai_pipeline_prototype.demo --hardware-link-demo --host 192.168.1.100 --axes 0,1,2 --homing-mode 0 --stop-mode 3
```

### GUI 使用

1. 在 Windows 环境中启动 GUI：
```bash
python -m ai_pipeline_prototype.gui
```

2. 在 GUI 中点击"连接"按钮连接真实控制器
3. 连接成功后，状态栏会显示连接状态

### 验证连接

运行以下命令验证 SDK 功能：
```bash
python -m ai_pipeline_prototype.demo --sdk-functions
```

### 输出判断

- **`backend=vendor`**：说明成功使用真实 SDK，没有回退错误
- **`fallback_error=...`**：DLL 已加载但控制器连接失败，已自动降级到 mock
- **`backend=mock`**：使用模拟后端，没有加载真实 SDK

### 常见问题

1. **DLL 加载失败**
   - 确认在 Windows 环境中运行
   - 确认 `Windows Python（64位）` 文件夹完整
   - 确认文件夹路径正确

2. **控制器连接失败**
   - 确认控制器已通电并连接到网络/串口
   - 确认 IP 地址或 COM 端口正确
   - 确认网络连接正常（以太网方式）

3. **自动降级到 mock**
   - 检查控制器连接是否正常
   - 检查连接参数配置是否正确

### 下一步重点

最优先的工作是把 `ai_pipeline_prototype/sdk_adapter.py` 从占位实现升级为真实 Motion SDK 适配层。

建议优先打通这几个接口：

- `connect()`
- `disconnect()`
- `get_status()` 或等价状态读取
- `home()`
- `stop()`
- `move_to_pose()`
- `set_gripper()`

这一步完成后，当前已有的调度器、控制器服务层和 GUI 基本都可以直接复用到真机联调流程中。
