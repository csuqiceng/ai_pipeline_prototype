# AI Pipeline Prototype

这个原型实现了下面这段链路的最小可运行版本：

`语音/视觉输入 -> AI理解（生成任务JSON） -> 任务调度器`

当前版本使用模拟输入，便于先验证软件架构和模块边界，后续可以逐步替换为真实麦克风、摄像头、ASR、视觉算法或大模型接口。

## 目录

- `models.py`: 统一数据模型
- `inputs.py`: 语音和视觉输入适配
- `planner.py`: 任务理解与 JSON 生成
- `dispatcher.py`: 任务状态机调度器
- `executor.py`: 控制执行接口和模拟执行器
- `sdk_adapter.py`: Motion SDK 风格客户端占位封装
- `controller_service.py`: 控制器服务层，负责连接、状态、报警和命令历史
- `app_service.py`: 应用服务层，统一编排输入、规划和调度
- `factory.py`: 执行器工厂
- `demo.py`: 端到端演示入口
- `gui.py`: 基于 Tkinter 的轻量上位机演示界面

## 运行

```bash
python3 -m ai_pipeline_prototype.demo
```

切换到 SDK 风格后端：

```bash
python3 -m ai_pipeline_prototype.demo --mode sdk
```

打开轻量 GUI：

```bash
python3 -m ai_pipeline_prototype.gui
```

做一次无界面的冒烟检查：

```bash
python3 -m ai_pipeline_prototype.gui --smoke-test
```

## 当前能力

- 解析固定语音指令
- 接收视觉定位结果
- 生成结构化任务 JSON
- 通过状态机模拟执行主流程
- 通过执行接口封装 `move/grip/release/home/stop`
- 预留了 Motion SDK 客户端与执行器替换点
- 增加了控制器服务层，统一管理连接、状态和报警
- 增加了轻量 GUI，可手动提交任务并查看状态、报警和命令历史
- 输出执行日志和最终状态
