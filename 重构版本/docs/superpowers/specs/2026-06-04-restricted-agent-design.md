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
7. 所有有争议的地址和函数号必须通过可注入的协议解析层读取，例如 `AddressResolver`，业务 Agent 不直接写死 `IEEE(1500)`、`IEEE(1512)` 或 `Func112`。

## 前置协议确认

实现前必须先确认以下协议口径，否则参数继承和函数选择会产生风险：

| 项目 | 当前项目口径 | 新文档口径 | 决策要求 |
| --- | --- | --- | --- |
| Func8 是否需要上位机写入 | 当前默认自然语言绝对运动仍用 Func108 | 自然语言文档 1.4 表格出现 `绝对运动 8/102` | 已通过 `AddressResolver.absolute_motion_func` 支持注入 8/102；底层按 Func108 同参数布局写对应函数号 |
| 连续路径函数 | 代码实现 Func11；V4.3/V5.0 状态位还包含预留 Func111 | 新文档 Func112 | 当前按新文档实现 Func112：同 Func108 参数格式，进入确认后写 IEEE(0)=112；若固件要求 Func11/111 兼容入口，通过 `AddressResolver` 调整 |
| 笛卡尔继承源 | IEEE(1512~1522) DPOS / IEEE(1612~1622) 反馈 | IEEE(1500~1510) 当前位姿 | 确认继承时使用指令位置、反馈位置还是新地址 |
| 运动中判定 | `SixAxisStatus.can_send` / `is_executing` 可判断已知函数槽状态；`build_six_motion_state_read()` 仅返回 int | 新文档写 BIT(252) 任意轴运动中 | 首版先复用 `SixAxisStatus.can_send` 阻断继承；确认是否必须新增 BIT(252) 读取作为底层运动判据 |
| Func110 延时参数 | 当前写 IEEE(2) | 文档写 para(2) | 确认实际控制器参数位 |
| 姿态四夹角 | 未接入 | IEEE(1732/1734/1736/1738) | 确认控制器已定义并可读 |
| IEEE(22) 语义 | 当前代码命名为 `fuzzy_pos` | 新文档写位置增量 | 确认控制器实际语义，未确认前不改现有字段名和写入行为 |

协议未确认前，允许先实现不依赖这些差异的 `AlarmExplanationAgent` 和规则意图识别。

## 两份对接文档需求映射

本节用于把两份 2026-06-04 对接文档中的硬要求映射到 Agent 模块，避免实现时只做自然语言解析而漏掉状态、安全和执行闭环。

### 自然语言参数类指令解析说明书

| 文档要求 | Agent 落点 | 实现约束 |
| --- | --- | --- |
| 所有正常笛卡尔运动首选 Func108 | `CommandUnderstandingAgent` | 自然语言参数类运动主路径默认生成 `move_linear` / Func108；Func106/107 仅作为辅助点动能力保留，不属于该主路径 |
| 需要路径规避时使用连续路径函数 | `CommandUnderstandingAgent` + `AddressResolver` | 文档写 Func112，当前 Agent 按 Func112 进入确认和现有六轴三道门；Func11/111 兼容由协议配置层承接 |
| 辅助点动能力 | `CommandUnderstandingAgent` + `ParameterCompletionAgent` | Func106/107 只用于关节轴/虚拟轴点动、示教和微调类指令；仍必须经过参数补全、安全预检、复述确认和现有执行链路 |
| 参数继承性 | `ParameterCompletionAgent` | 未指定的 X/Y/Z/RX/RY/RZ/速度/加减速必须从控制器或确认后的协议继承源读取，不依赖 HMI 页面状态 |
| 复述确认 | `ConfirmationAgent` | 必须展示补全后的完整参数、参数来源、模式和安全预检结果，用户确认后才转换为 `QueryRecord` |
| 半参数/单参数指令 | `CommandUnderstandingAgent` + `ParameterCompletionAgent` | 规则能明确提取的部分参数不调用大模型；其余参数走继承补全 |
| 增量运动 | `ParameterCompletionAgent` + `draft_to_query_record()` | 文档将 para(10) 定义为位置增量；Agent 草案保留绝对目标用于预检，确认后的执行副本把 `position_increment` 映射到 `fuzzy_pos/para(10)` |
| 复合指令 | 后续 `CompoundCommandCoordinator` 或流程层 | 不在首批 5 个 Agent 范围内；每条子指令独立走补全、预检、确认；不能一次确认后批量绕过单步安全检查 |
| 执行后监控 | 现有 `six_axis_command_mixin.py` + `AlarmExplanationAgent` | 继续轮询 LONG(34)/LONG(38)/IEEE(324)，将 EXEC/DONE/ERR 转为操作者话术 |

### 机械手基础运行信息交互状态说明书

| 文档要求 | Agent 落点 | 实现约束 |
| --- | --- | --- |
| 系统就绪判断 | `L1ControllerGate` / `SafetyPrecheckService` | 运动前检查 LONG(34).bit28、bit24、bit25、bit26 和 LONG(38)，不满足则拒绝运动草案进入确认 |
| 急停精确响应 | `AlarmExplanationAgent` + 系统动作规则旁路 | 运动指令遇急停必须立即回复解除顺序；`sys_estop` 不走大模型 |
| 暂停精确响应 | `AlarmExplanationAgent` + `CommandUnderstandingAgent` | 暂停中拒绝新运动；允许继续/取消类系统动作按规则处理 |
| 当前运行动作显示 | `AlarmExplanationAgent` | 读取 IEEE(324) 和 LONG(34) 函数状态，输出 `func_name_zh`、EXEC/DONE/ERR 文本 |
| 驱动器报警逐轴诊断 | `AlarmExplanationAgent` | LONG(38).bit7 触发时必须读取 IEEE(200/202/204/206/208/210)，逐轴解释 AXISSTATUS bit |
| EtherCAT 通讯丢失逐轴诊断 | `AlarmExplanationAgent` | LONG(38).bit6 触发时必须读取 AXISSTATUS bit2，并输出受影响轴和驱动器号 |
| 半径/高度实时超限方向判断 | `AlarmExplanationAgent` | LONG(38).bit0/bit1 触发时结合 IEEE(1700~1706)、IEEE(1740/1742) 输出“伸太远/收太近/太高/太低” |
| 速度/加速度/减速度钳制提示 | `AlarmExplanationAgent` | LONG(38).bit3/4/5 触发时读取 IEEE(1708/1710/1712) 生成安全上限提示 |
| 每次运动前安全预判 | `SafetyReviewAgent` | 所有 Func108 运动草案确认前和执行前都必须检查，不允许跳过 |
| 空间模型检查 | `SafetyReviewAgent` | 已接入 `SafetyPrecheckService`，实现外球面、内圆柱+半球、Z 范围、底座角度四类本地检查；Agent 服务入口必须在这些检查失败时返回 `precheck_failed`，不得进入确认 |
| FrameTrans2 逆解 | `SafetyReviewAgent` + `KinematicsEngine` | 复用 `FrameTrans2KinematicsEngine` / `MotionPlanService`，遍历 FSTATUS，结果与控制器逻辑一致 |
| 关节限位和姿态四夹角 | `SafetyReviewAgent` | 逆解后检查关节软限位；读取 IEEE(1732~1738) 后检查上/下/顺时针/逆时针夹角 |

### 不可跳过的闭环

所有自然语言运动指令必须经过以下闭环：

```text
自然语言输入
  -> 意图识别
  -> 参数提取
  -> 控制器实时值继承补全
  -> L1 状态门
  -> 空间/逆解/限位/姿态安全预检
  -> 完整参数复述确认
  -> confirmed draft 转 QueryRecord
  -> 现有六轴三道门执行链路
  -> 执行状态和报警解释
```

任一环节失败时，不生成执行触发：

- 意图不清：返回澄清问题。
- 继承读取失败：拒绝补全并提示控制器实时值不可用。
- L1 状态不通过：提示急停、暂停、报警或通道占用原因。
- 安全预检不通过：展示具体超限方向和修正建议。
- 用户拒绝或确认超时：丢弃草案。

### 当前阶段明确禁用或阻断

- 自然语言参数类运动主路径不生成 Func106/Func107；关节轴/虚拟轴点动、示教和微调类指令可作为辅助能力生成 Func106/Func107，但必须走参数补全、安全预检、复述确认和现有执行链路。
- Func112 必须走参数补全、安全预检、复述确认和现有六轴三道门；不允许大模型直接生成点列或写 MODBUS。
- 协议未确认前不把 `fuzzy_pos` 改写为“位置增量”。
- 大模型不参与 MODBUS 地址选择、安全预检、AXISSTATUS 解释和执行触发。
- 大模型不得改写复述确认中的数字、单位、参数名和参数顺序。

## 总体架构

Agent 是现有入口和现有执行链路之间的编排层：

```text
用户输入
  -> CommandUnderstandingAgent
     -> clarification_needed -> 反问操作者 -> 用户补充 -> CommandUnderstandingAgent
  -> ParameterCompletionAgent
  -> SafetyReviewAgent
     -> precheck_failed -> 展示错误和建议 -> 用户修改 -> ParameterCompletionAgent
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
    params=copy.deepcopy(draft.params),
    description=summary_from_draft(draft),
)
```

草案修改采用创建新对象，不原地修改。这样可以保留审计链路，也避免确认状态被中途污染。

`CommandDraft.params` 的 key 名必须和 `RobotModbusService.build_six_command_from_record()` 中对应 `func_id` 的参数 key 完全一致。转 `QueryRecord` 前必须做完整性校验，缺少必需参数时拒绝转换。

`QueryRecord` 虽然是 `frozen=True`，但内部 `params` 是可变 dict。所有 `CommandDraft.params` 在转 `QueryRecord` 时必须深拷贝；confirmed draft、待确认 draft 和实际执行用 `QueryRecord` 之间不得共享同一个 dict 引用。若后续需要添加 `atomic_risk_level` 等 UI 元数据，也只能写入执行副本，不能污染原始草案。

当前已实现函数的必需 key：

| func_id | 必需 key |
| --- | --- |
| 104 | `estop_ctrl`, `pause_ctrl`, `cancel_ctrl`, `reset_ctrl` |
| 108 | `target_x`, `target_y`, `target_z`, `target_rx`, `target_ry`, `target_rz`, `spd_pct`, `acc_pct`, `dec_pct`, `stop_cmd`, `fuzzy_pos`, `fuzzy_spd`, `fuzzy_acc`, `fuzzy_dec`, `move_type` |
| 109 | `delay_sec` |
| 110 | `delay_sec` |
| 120 | `io_no`, `io_action` |

### AddressResolver

职责：封装协议差异和可变地址，业务 Agent 只依赖语义字段，不直接写死有争议的 MODBUS 地址或函数号。

```python
@dataclass(frozen=True)
class AddressConfig:
    cartesian_current: int
    cartesian_feedback: int
    safe_speed_max: int
    safe_acc_max: int
    safe_dec_max: int
    pose_upper_angle: int
    pose_lower_angle: int
    pose_cw_angle: int
    pose_ccw_angle: int
    continuous_path_func: int
    absolute_motion_func: int
    any_axis_moving_bit: int | None = None
```

P0 协议确认前使用与当前项目兼容的默认配置。确认后通过配置注入更新，`ParameterCompletionAgent`、`SafetyReviewAgent` 和 `CommandUnderstandingAgent` 从 `AddressResolver` 读取地址和函数号。

### AgentPlanAdapter

职责：把 Agent 内部结果接入现有 Qt/Web 自然语言入口，避免首版改写 `nlp_mixin.py` 和 `web_nlp_service.py` 的执行模型。

首版允许两种接入方式：

- P2 阶段先实现语义策略映射和空壳接口，不依赖 `CommandDraft`。
- P3 `CommandDraft` 完成后，再由 `AgentPlanAdapter` 转成现有 `VoiceNlpPlan` / `VoiceNlpAction`，动作类型使用可被旧入口识别的 `atomic_template` 或新增受控类型。
- Qt/Web 新增并行的 `AgentPlan` parse endpoint，但 confirmed 后仍必须转换为 `QueryRecord` 并走现有执行 API。

无论采用哪种方式，运动类 Agent 输出不得直接进入 `_execute_nlp_plan()` 的即时执行路径。必须先进入 `ConfirmationAgent`，确认后才注册临时 `QueryRecord` 或提交执行。

Agent 的语义策略必须映射到现有 `semantic_response_policy.py`：

| Agent 结果 | semantic level | requires_precheck | requires_confirmation | emergency_fast_path |
| --- | --- | --- | --- | --- |
| 查询/报警解释 | 1 或 2 | False | False | False |
| Func108 运动草案 | 3 | True | True | False |
| 普通系统动作 | 4 | False | 按现有策略 | False |
| 急停 | 5 | False | False | True |

若 `VoiceNlpPlan.requires_precheck` / `requires_confirmation` 与 Agent 内部判断冲突，以更保守的一方为准。

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
    "func_id": 108 | 104 | 109 | 110 | 120 | None,
    "target": "sys_estop" | "alarm_status" | None,
    "extracted_params": {...},
    "confidence": 0.0,
    "needs_model": False,
    "clarification": {"missing": [...], "question": "..."} | None,
}
```

置信度分级：

| confidence | 行为 |
| --- | --- |
| `>= 0.85` | 规则结果直接使用，不调用大模型 |
| `0.50 ~ 0.85` | 规则结果不足时允许调用大模型兜底 |
| `< 0.50` | 返回 `clarification_needed`，不让大模型硬猜 |

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

非 Func108 参数补全目标：

| func_id | 参数 | 来源 |
| --- | --- | --- |
| 104 | `estop_ctrl`, `pause_ctrl`, `cancel_ctrl`, `reset_ctrl` | 系统动作直接构造，未触发的控制字为 0 |
| 109 | `delay_sec` | 用户指定，无继承源 |
| 110 | `delay_sec` | 用户指定，无继承源 |
| 120 | `io_no`, `io_action` | 用户指定，无继承源 |

控制器读取失败策略：

- 运动指令补全时，如果继承源或安全速度参数读取失败，返回错误并拒绝补全。
- 不静默使用 `system_config.json` 作为运动参数回退值。
- 查询类操作可以显示本地配置作为参考，但必须标注“非实时控制器值”。

运动中继承策略：

- 首版运动中判断复用 `SixAxisStatus.can_send` / `is_executing`，避免新增读取通道；P0 确认后如要求“任意轴运动中”，再接入 BIT(252)。
- 如果控制器处于运动中，默认不继承瞬时 DPOS。
- 直接进入阻断提示：“当前设备运动中，请等待停止后再继承当前位置。”该提示不进入确认状态机。
- 如果状态读取失败，按无法安全继承处理，拒绝补全。
- 后续如需要支持运动中修改，必须明确使用反馈位置还是规划位置，并单独设计。

### SafetyReviewAgent

职责：

- 在确认前和最终执行前对运动草案进行安全评审。
- 复用 `KinematicsEngine` / `MotionPlanService`，不直接绑定 SDK。
- L1 状态门优先复用 `SixAxisStatus.can_send` / `can_send_for()` 的直接寄存器解析结果；需要 dashboard snapshot 的 UI 场景可复用 `SafetyPrecheckService.run_l1()`，避免 Agent 内部另写一套急停、报警、暂停和通道空闲判断。
- 不做缓存，每次实时计算。

安全评审分层：

1. L1 状态门：急停、报警、暂停、控制器在线、通道可用。
2. 空间模型：外径、内径、Z、高度、底座角度。
3. 逆解可行性：FrameTrans2 mode=2，遍历 FSTATUS。
4. 关节软限位：逆解结果对比各轴限制。
5. 姿态四夹角：上、下、顺时针、逆时针夹角。

与现有 `MotionPlanService` 的关系：

- `SafetyReviewAgent` 是统一汇总层，负责 L1 状态门、空间模型、姿态四夹角和最终 `valid` 判断。
- `MotionPlanService.plan()` 只作为 L2 子模块，负责 FSTATUS 扫描、关节限位和奇异点检查。
- `SafetyReviewAgent` 调用 `MotionPlanService.plan()` 后，将其 `status`、`selected_fstatus`、`joints`、`items`、`suggestion` 适配为统一输出格式。
- 姿态四夹角读取依赖 `parse_six_safety_limits()` 后续显式返回 `pose_upper_angle`、`pose_lower_angle`、`pose_cw_angle`、`pose_ccw_angle`，不再从 `reserved` 中按位置读取。

与现有执行三道门的关系：

- `SafetyReviewAgent` 是确认前和执行前的预判层，不替代 `six_axis_command_mixin.py` 中的最终三道门。
- confirmed draft 执行时仍必须经过 `_precheck_six_command()` / `_wait_six_precheck_ready()` / 触发确认读取等现有链路。
- 如果 Agent 预检通过但执行时三道门阻断，以执行时三道门为准；阻断原因进入 `AlarmExplanationAgent` 的结构化输出，由 UI 报警页或状态区展示。

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
  -> precheck_failed -> 展示错误和建议 -> modify_requested
```

超时使用现有 `operator_confirm_timeout_sec` 配置。

与现有 Qt 确认 UI 的集成：

- `ConfirmationAgent` 生成的复述文本填入 `operator_confirm_detail`。
- 现有“确认执行”按钮映射为 `confirmed`，现有“取消”按钮映射为 `rejected`。
- `operator_confirm_timeout_sec` 继续作为确认超时配置。
- 修改回路需要新增 UI 入口，这是 P4 的 UI 依赖项；操作者输入修改文本后，重新进入 `CommandUnderstandingAgent` / `ParameterCompletionAgent`，不直接修改已确认草案。

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
    "axis_status": [...],  # RobotModbusService.parse_six_axis_status() 的返回列表
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
    "func_name_zh": "直线插补",
    "can_move": False,
}
```

解释策略：

- 急停、报警、通讯丢失优先级最高。
- `LONG(38)` bit7 或 bit6 触发时，读取并解释逐轴 `AXISSTATUS`。
- 详情中保留所有异常轴；摘要显示最高严重度的一条。
- 正常状态只显示“就绪”或“正在执行 FuncXXX”，避免报警页信息污染正常运行界面。

基础设施：

- 新增 `AxisStatusBitDecomposer` 或 `parse_six_axis_status_detail()`，输入为 `parse_six_axis_status()` 的返回列表，按协议映射到 6 轴。
- 至少解释 AXISSTATUS bit2、bit3、bit4、bit5、bit8、bit9、bit10、bit14、bit18。
- Agent 层只消费结构化 bit 解释结果，不在提示词或自然语言模板中直接手写 bit 运算。

## 大模型边界

允许调用大模型的场景：

- 规则无法稳定理解的自然语言。
- 模糊参数的澄清问题生成。
- 非控制类问答或说明书问答。
- 复述文本润色，但只能修改自然语言衔接，禁止修改任何数字、单位、参数名和参数顺序。

禁止调用大模型的场景：

- 急停、暂停、取消等实时安全动作。
- 安全预检判断。
- 报警 bit 解释。
- MODBUS 地址选择。
- 最终执行参数写入。
- 复述确认中的数字、单位、参数名改写。

大模型输出必须经过白名单校验和结构化解析。校验不通过则返回澄清或拒绝，不进入执行链路。

## UI 和 Web 接入

Qt 用户页：

- 对话区显示 Agent 复述确认文本。
- 右侧确认面板显示完整参数、来源、安全预检结果。
- 复用 `_build_operator_confirm_scene()` 的确认/取消按钮；`operator_confirm_detail` 显示 `ConfirmationAgent` 的复述文本。
- “修改参数”作为后续 UI 增量入口，必须重新走补全、预检、确认，不允许在 UI 上直接改写 confirmed draft。
- `AgentPlanAdapter` 接入后，旧 `VoiceNlpPlan` 执行链仍保留；Agent 运动草案在确认前不得调用 `_execute_nlp_plan()` 的 `_execute_query_key()` 路径。
- 报警页接入 `AlarmExplanationAgent` 的结构化输出。

Web：

- `web_nlp_service.py` 可新增 Agent parse endpoint，但执行仍走现有 confirm API。
- Web 响应返回 `draft_id`、`confirmation_text`、`precheck_result`。
- Web draft 生命周期必须显式建模：`draft_id -> waiting_confirmation -> confirmed/rejected/expired -> QueryRecord`。
- draft 需要携带创建时的状态快照摘要和过期时间；确认时如果已过期或控制器状态关键字段变化，必须重新预检或拒绝确认。
- confirmed 后只允许一次性转换和执行，不能重复确认同一个 `draft_id`。

draft 失效条件：

- 当前时间超过创建时间 + `operator_confirm_timeout_sec`。
- 创建时记录的 `SixAxisStatus.raw` 与确认时不一致。
- 创建时关键安全参数摘要与确认时不一致。
- `draft_id` 已进入 `confirmed`、`rejected` 或 `expired` 终态。

语音：

- 语音入口只传文本给 Agent。
- Agent 输出的确认文本可播报短版，完整参数在界面显示。
- 急停规则旁路必须保留。

## 实施顺序

### P0 协议差异确认

- 确认 Func11/Func112。
- 确认笛卡尔继承源。
- 确认运动中判定是否必须接入 BIT(252)，以及 `SixAxisStatus.can_send` 是否足以作为首版阻断条件。
- 确认 Func110 参数位。
- 确认 IEEE(1732~1738) 姿态四夹角地址。
- 确认 IEEE(22) `fuzzy_pos` 与“位置增量”的真实语义。

### P1 AlarmExplanationAgent

- 新增轴状态 bit 表。
- 复用现有 `build_six_axis_status_read()`。
- 新增 `AxisStatusBitDecomposer` 或 `parse_six_axis_status_detail()`。
- 增加单元测试覆盖 LONG(34)、LONG(38)、AXISSTATUS 组合。

### P2 CommandUnderstandingAgent 规则版

- 复用 `SYSTEM_ACTION_ALIASES` 和 `atomic_parser.py` 中确定性的参数提取规则。
- 增加意图、置信度、澄清输出。
- 增加大模型调用判定，但先不接大模型也可工作。
- 增加 `AgentPlanAdapter` 的语义策略映射和空壳接口，先不依赖 `CommandDraft`。

### P3 ParameterCompletionAgent

- 新增 `CommandDraft`。
- 实现 Func108 参数继承和来源标注。
- 支持 Func104/109/110/120 的确定性草案生成。
- 补齐 `CommandDraft -> VoiceNlpPlan` 或 `CommandDraft -> AgentPlan` 的适配。

### P4 ConfirmationAgent

- 实现确认文本模板。
- 接入超时、拒绝、修改回路。
- 将 confirmed draft 转为 `QueryRecord`。
- 实现 draft 生命周期和一次性确认约束，Web/Qt 共用同一套状态定义。

### P5 SafetyReviewAgent

- 接入空间模型。
- 接入 `MotionPlanService` / `KinematicsEngine`。
- 扩展 `parse_six_safety_limits()`，显式返回 IEEE(1732~1738) 姿态四夹角字段后，再补姿态四夹角检查。
- 执行前再次实时计算，不缓存。

## 测试策略

单元测试：

- AXISSTATUS 每个关键 bit 的解释文本。
- `AxisStatusBitDecomposer` 对 `parse_six_axis_status()` 返回列表的轴映射。
- `LONG(34)` 急停、暂停、报警、就绪组合。
- `parse_six_safety_limits()` 显式解析 IEEE(1732~1738) 姿态四夹角。
- `SafetyPrecheckService` 对外球面、内圆柱+半球、Z 范围和底座 `atan2` 角度的 L1 空间模型判断。
- `SafetyReviewAgent` 对 `MotionPlanService.plan()` 结果的 pass/fail/unavailable 适配。
- `CommandDraft.params` 转 `QueryRecord.params` 时深拷贝，confirmed draft 不被执行副本污染。
- `AgentPlanAdapter` 对 `semantic_response_policy.py` 的 level / confirmation / precheck 映射。
- 自然语言显式参数解析。
- 大模型返回非法 JSON、非法函数号、缺少必需字段或超限参数时被白名单校验拒绝。
- 半参数和单参数继承补全。
- 复述确认文本来源标注。
- draft 转 `QueryRecord` 再转 `SixAxisCommand` 参数一致性。

集成测试：

- 用户输入 “走到 X1000”。
- 参数继承生成完整 Func108。
- 安全预检通过。
- 确认后进入现有六轴执行链路。
- 控制器运动中收到 “走到 X1000” 时返回阻断提示，不生成 `CommandDraft`，不进入确认状态机。
- 目标点触发内圆柱/半球内径超限或底座角度超限时，Agent 入口返回 `precheck_failed`，不生成待确认执行草案。
- Agent 运动草案解析后未确认时，不会走 `_execute_nlp_plan()` / `_execute_query_key()`。
- Web `draft_id` 过期或重复确认时拒绝执行。

回归测试：

- 既有模板、流程、原子命令不因 Agent 新入口改变行为。
- `AtomicParser` / `AtomicResolver` 旧路径保留；Agent 可以复用 `AtomicParser` 中确定性的参数提取规则，但不替换 `AtomicResolver` 的执行决策、确认模式和风险等级逻辑。
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
- 不替换 `AtomicParser` / `AtomicResolver` 的既有原子命令路径。
- 不让 Agent 运动草案绕过现有 `VoiceNlpPlan` 确认策略或 Web waiting_confirm 生命周期直接执行。
- 不允许大模型修改复述确认文本中的数字、单位、参数名和参数顺序。

## 成功标准

- 报警解释可在无大模型、无执行动作的情况下独立运行并通过测试。
- 参数类自然语言指令可生成完整 `CommandDraft`，且每个参数有来源。
- 确认文本完整展示 Func、参数、来源和安全预检结果。
- confirmed draft 能转换为现有 `QueryRecord` 并走现有执行链路。
- 急停等安全动作仍保持规则路径和最低延迟。
