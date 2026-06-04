# 受限上位机 Agent 设计实现文档

日期：2026-06-04

## 背景

新增两份上位机对接文档提出了三类能力要求：

- 将控制器状态、报警和 AXISSTATUS 解释成现场操作者可理解的精确回复。
- 将自然语言参数类指令解析为 Func108/104/109/110/120 等控制器函数调用。
- 所有运动指令必须先继承补全参数、做安全预检、复述确认，再进入现有六轴三道门执行链路。

当前项目已有关键基础设施：

- `RobotModbusService.build_six_command_from_record()` 将 `QueryRecord` 转为 `SixAxisCommand`。
- `SixAxisCommand.to_func_writes()` 负责生成最终 MODBUS 写入。
- `six_axis_command_mixin.py` 已实现写参数、回显比对、触发前状态检查、触发和等待完成。
- `atomic_parser.py` 已能提取部分显式坐标、速度、IO、延时等自然语言参数。
- `kinematics_engine.py` 和 `motion_plan.py` 已具备 FrameTrans2 逆解适配和 FSTATUS 预演基础。

本设计的核心目标是新增一个安全受限的上位机 Agent 编排层，补齐语义理解、参数继承、复述确认、报警解释和安全预检，但不新建第二套执行协议。

## 设计原则

1. Agent 只能提出计划，不能越权执行。
2. 执行动作必须转换为现有 `QueryRecord` 或等价输入，再走 `RobotModbusService` 和 `SixAxisCommand`。
3. 大模型只做低置信度自然语言理解兜底，不参与安全判断、寄存器写入、三道门决策。
4. 急停等高优先级安全动作必须规则旁路，不经过大模型。
5. 报警解释、安全预检、参数继承和确认状态机都应是确定性逻辑，可单元测试。
6. 协议差异未确认前，不硬编码有争议地址和函数号。

## 前置协议确认

实现前必须先确认以下协议口径，否则参数继承和函数选择会产生风险：

| 项目 | 当前项目口径 | 新文档口径 | 决策要求 |
| --- | --- | --- | --- |
| 连续路径函数 | Func11 | Func112 | 确认固件最终函数号，未确认前保留 Func11 兼容和保护性拒绝 |
| 笛卡尔继承源 | IEEE(1512~1522) DPOS / IEEE(1612~1622) 反馈 | IEEE(1500~1510) 当前位姿 | 确认继承时使用指令位置、反馈位置还是新地址 |
| Func110 延时参数 | 当前写 IEEE(2) | 文档写 para(2) | 确认实际控制器参数位 |
| 姿态四夹角 | 未接入 | IEEE(1732/1734/1736/1738) | 确认控制器已定义并可读 |

协议未确认前，允许先实现不依赖这些差异的 `AlarmExplanationAgent` 和规则意图识别。

## 总体架构

Agent 是现有入口和现有执行链路之间的编排层：

```text
用户输入
  -> CommandUnderstandingAgent
  -> ParameterCompletionAgent
  -> SafetyReviewAgent
  -> ConfirmationAgent
  -> QueryRecord
  -> RobotModbusService.build_six_command_from_record()
  -> SixAxisCommand.to_func_writes()
  -> six_axis_command_mixin.py 三道门执行链路
```

状态查询和报警解释不进入执行链路：

```text
用户查询 / 定时刷新
  -> AlarmExplanationAgent
  -> UI / 语音播报 / Web 返回
```

## 核心数据结构

新增不可变草案对象，作为 Agent 内部计划层输出。

```python
@dataclass(frozen=True)
class CommandDraft:
    draft_id: str
    func_id: int
    intent: str
    params: dict[str, float | int | str]
    param_sources: dict[str, str]  # specified | inherited | default | system
    raw_text: str
    confidence: float
    precheck_result: dict | None = None
    confirmed: bool = False
```

草案转现有执行入口：

```python
QueryRecord(
    query_key=f"agent:{draft.draft_id}",
    func_num=draft.func_id,
    params=draft.params,
    description=summary_from_draft(draft),
)
```

草案修改采用创建新对象，不原地修改。这样可以保留审计链路，也避免确认状态被中途污染。

## 模块设计

### CommandUnderstandingAgent

职责：

- 识别意图：运动、状态查询、报警查询、急停、暂停、取消、继续、复位、IO、延时、未知。
- 提取规则层能确定的显式参数。
- 输出置信度和是否需要澄清。
- 决定是否需要调用大模型兜底。

接口输出：

```python
{
    "intent": "move_linear" | "system" | "query" | "io" | "delay" | "clarification_needed" | "unknown",
    "target": "sys_estop" | "alarm_status" | None,
    "extracted_params": {...},
    "confidence": 0.0,
    "needs_model": False,
    "clarification": {"missing": [...], "question": "..."} | None,
}
```

系统动作分级：

- 立即规则旁路：`sys_estop`。
- 规则识别并允许快速处理：`sys_pause`、`sys_cancel`。
- 规则识别但需要状态校验或确认：`sys_resume`、`alarm_reset`。

大模型调用条件：

- 意图不明确，或置信度低于阈值。
- 存在代词、模糊方向、模糊数量、冲突参数。
- 规则无法确定用户明确表达的参数。

不调用大模型的条件：

- 意图明确。
- 用户明确表达的参数已全部由规则提取。
- 缺失参数可以由继承规则补全。
- 无模糊代词、无冲突、无安全敏感歧义。

### ParameterCompletionAgent

职责：

- 将自然语言中明确表达的参数和控制器当前值合并为完整 `CommandDraft`。
- 标记每个参数来源：`specified`、`inherited`、`default`、`system`。
- 按函数号生成与现有 `build_six_command_from_record()` 兼容的 `params`。

Func108 参数补全目标：

| 参数 | 来源 |
| --- | --- |
| target_x/target_y/target_z | 用户指定，否则从 P0 协议确认后的笛卡尔继承源读取 |
| target_rx/target_ry/target_rz | 用户指定，否则从 P0 协议确认后的笛卡尔继承源读取 |
| spd_pct | 用户指定，否则读 IEEE(1708) |
| acc_pct | 用户指定，否则读 IEEE(1710) |
| dec_pct | 用户指定，否则读 IEEE(1712) |
| stop_cmd | 默认 0 |
| fuzzy_pos/fuzzy_spd/fuzzy_acc/fuzzy_dec | 默认 0 或按现有原子规则 |
| move_type | 默认 0，直线插补 |

运动中继承策略：

- 如果控制器处于运动中，默认不继承瞬时 DPOS。
- 返回澄清或等待提示：“当前设备运动中，请等待停止后再继承当前位置。”
- 后续如需要支持运动中修改，必须明确使用反馈位置还是规划位置，并单独设计。

### SafetyReviewAgent

职责：

- 在确认前和最终执行前对运动草案进行安全评审。
- 复用 `KinematicsEngine` / `MotionPlanService`，不直接绑定 SDK。
- 不做缓存，每次实时计算。

安全评审分层：

1. L1 状态门：急停、报警、暂停、控制器在线、通道可用。
2. 空间模型：外径、内径、Z、高度、底座角度。
3. 逆解可行性：FrameTrans2 mode=2，遍历 FSTATUS。
4. 关节软限位：逆解结果对比各轴限制。
5. 姿态四夹角：上、下、顺时针、逆时针夹角。

输出：

```python
{
    "valid": True,
    "items": [
        {"id": "workspace_outer", "status": "pass", "message": "..."}
    ],
    "errors": [],
    "suggestions": [],
    "ik_result": {"fstatus": 0, "joints": [...]}
}
```

当安全预检失败时，不生成执行请求，只返回错误和修正建议，允许用户修改后重新进入参数补全和确认。

### ConfirmationAgent

职责：

- 生成完整参数复述文本。
- 管理确认、拒绝、超时、修改回路。
- 确认后把 `CommandDraft` 标记为 confirmed，再转 `QueryRecord`。

确认文本格式：

```text
【复述确认】Func108 直线插补
X=1000mm（指定）  Y=200mm（指定）  Z=800mm（指定）
RX=0°（继承当前）  RY=0°（继承当前）  RZ=0°（继承当前）
速度=80%（继承安全参数）  加速度=80%（继承安全参数）  减速度=80%（继承安全参数）
模式：绝对定位
安全预检：通过
确认执行？
```

状态机：

```text
waiting_confirmation
  -> confirmed -> execute
  -> rejected -> discard
  -> timeout -> discard
  -> modify_requested -> ParameterCompletionAgent -> SafetyReviewAgent -> waiting_confirmation
```

超时使用现有 `operator_confirm_timeout_sec` 配置。

### AlarmExplanationAgent

职责：

- 将 `LONG(34)`、`LONG(36)`、`LONG(38)`、`AXISSTATUS` 转为操作者可理解的摘要、详情和建议。
- 输出结构化结果供 Qt、Web、语音播报复用。

输入：

```python
{
    "long34": int,
    "long36": int,
    "long38": int,
    "axis_status": [int, int, int, int, int, int],
    "safety_values": {...}
}
```

输出：

```python
{
    "severity": "critical" | "warning" | "info" | "ok",
    "summary": "J3轴驱动器故障",
    "detail": "...",
    "suggestions": ["断电重启3号驱动器", "检查J3轴电机接线"],
    "affected_axes": [2],
    "can_move": False,
}
```

解释策略：

- 急停、报警、通讯丢失优先级最高。
- `LONG(38)` bit7 或 bit6 触发时，读取并解释逐轴 `AXISSTATUS`。
- 详情中保留所有异常轴；摘要显示最高严重度的一条。
- 正常状态只显示“就绪”或“正在执行 FuncXXX”，避免报警页信息污染正常运行界面。

## 大模型边界

允许调用大模型的场景：

- 规则无法稳定理解的自然语言。
- 模糊参数的澄清问题生成。
- 非控制类问答或说明书问答。
- 复述文本润色，但必须保留模板字段和数值。

禁止调用大模型的场景：

- 急停、暂停、取消等实时安全动作。
- 安全预检判断。
- 报警 bit 解释。
- MODBUS 地址选择。
- 最终执行参数写入。

大模型输出必须经过白名单校验和结构化解析。校验不通过则返回澄清或拒绝，不进入执行链路。

## UI 和 Web 接入

Qt 用户页：

- 对话区显示 Agent 复述确认文本。
- 右侧确认面板显示完整参数、来源、安全预检结果。
- 报警页接入 `AlarmExplanationAgent` 的结构化输出。

Web：

- `web_nlp_service.py` 可新增 Agent parse endpoint，但执行仍走现有 confirm API。
- Web 响应返回 `draft_id`、`confirmation_text`、`precheck_result`。

语音：

- 语音入口只传文本给 Agent。
- Agent 输出的确认文本可播报短版，完整参数在界面显示。
- 急停规则旁路必须保留。

## 实施顺序

### P0 协议差异确认

- 确认 Func11/Func112。
- 确认笛卡尔继承源。
- 确认 Func110 参数位。
- 确认 IEEE(1732~1738) 姿态四夹角地址。

### P1 AlarmExplanationAgent

- 新增轴状态 bit 表。
- 复用现有 `build_six_axis_status_read()`。
- 增加单元测试覆盖 LONG(34)、LONG(38)、AXISSTATUS 组合。

### P2 CommandUnderstandingAgent 规则版

- 复用 `SYSTEM_ACTION_ALIASES` 和 `atomic_parser.py`。
- 增加意图、置信度、澄清输出。
- 增加大模型调用判定，但先不接大模型也可工作。

### P3 ParameterCompletionAgent

- 新增 `CommandDraft`。
- 实现 Func108 参数继承和来源标注。
- 支持 Func104/109/110/120 的确定性草案生成。

### P4 ConfirmationAgent

- 实现确认文本模板。
- 接入超时、拒绝、修改回路。
- 将 confirmed draft 转为 `QueryRecord`。

### P5 SafetyReviewAgent

- 接入空间模型。
- 接入 `MotionPlanService` / `KinematicsEngine`。
- 补姿态四夹角检查。
- 执行前再次实时计算，不缓存。

## 测试策略

单元测试：

- AXISSTATUS 每个关键 bit 的解释文本。
- `LONG(34)` 急停、暂停、报警、就绪组合。
- 自然语言显式参数解析。
- 半参数和单参数继承补全。
- 复述确认文本来源标注。
- draft 转 `QueryRecord` 再转 `SixAxisCommand` 参数一致性。

集成测试：

- 用户输入 “走到 X1000”。
- 参数继承生成完整 Func108。
- 安全预检通过。
- 确认后进入现有六轴执行链路。

回归测试：

- 既有模板、流程、原子命令不因 Agent 新入口改变行为。
- 急停、暂停、取消仍走低延迟规则路径。
- 大模型不可用时，规则路径仍可工作。

真机联调：

- 验证继承源地址。
- 验证 FrameTrans2 逆解结果。
- 验证 AXISSTATUS 逐轴诊断和实际故障一致。
- 验证确认后执行仍满足三道门。

## 不做的事

- 不让大模型直接写 MODBUS。
- 不让 Agent 绕过 `six_axis_command_mixin.py`。
- 不在协议差异确认前替换现有 Func11/1500/1512/1612 口径。
- 不把安全预检结果缓存作为默认行为。
- 不一次性重写现有 `voice_nlp_adapter.py`，先以新模块接入。

## 成功标准

- 报警解释可在无大模型、无执行动作的情况下独立运行并通过测试。
- 参数类自然语言指令可生成完整 `CommandDraft`，且每个参数有来源。
- 确认文本完整展示 Func、参数、来源和安全预检结果。
- confirmed draft 能转换为现有 `QueryRecord` 并走现有执行链路。
- 急停等安全动作仍保持规则路径和最低延迟。
