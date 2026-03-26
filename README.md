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

## 下一步重点

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
