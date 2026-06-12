# zbasic-GLM 与 HMI 通讯差异记录

记录日期：2026-06-11

## 结论

当前 Python 上位机、`zbasic-GLM` 控制器程序、`HMI` 触摸屏项目是三端共享同一套 Modbus 寄存器协议的关系。`zbasic-GLM` 与 `HMI` 是匹配的一组，应作为真实现场协议基准；当前 Python 项目需要收敛到这组协议。

差异不属于架构级重写，主要是寄存器地址映射、状态位解析、mock 控制器一致性和文档口径。

## 三端关系

- 当前项目：PC 上位机，负责自然语言、Qt/Web UI、Modbus 读写和执行监控。
- `zbasic-GLM`：ZMotion 控制器端 ZBasic 程序，实际解释 `IEEE/LONG/BIT` 寄存器并驱动运动。
- `HMI`：触摸屏工程，使用 `W720/W752` 等 HMI 命令缓冲区，也读取 `W280/W314/W326` 等回显和状态区。

三者不通过代码导入关联，而是通过 Modbus 寄存器协议关联。

## 已对齐内容

- 主命令通道：
  - `IEEE(0)`：函数号
  - `IEEE(2..30)`：参数
  - `IEEE(32)`：触发
- 回显和状态：
  - `IEEE(280..310)`：主命令回显
  - `IEEE(312)`：接收确认
  - `LONG(34)` / `IEEE(314)`：主状态
  - `LONG(36)` / `IEEE(316)` / `IEEE(320)`：系统状态
  - `LONG(38)` / `IEEE(318)` / `IEEE(322)`：报警详情
  - `IEEE(324)`：当前函数号
- HMI 命令通道：
  - `W720`：HMI 函数号
  - `W722..W750`：HMI 参数
  - `W752`：HMI 触发
- 当前 `Func104/106/107/108/120` 的主通道映射与 `zbasic-GLM` 基本一致。

## 必须修改的差异

### 1. Func109 延时参数地址

当前 Python：

- `delay_sec -> IEEE(2)`

`zbasic-GLM + HMI`：

- 主通道：`delay_sec -> IEEE(4)`
- HMI：`delay_sec -> W724`
- 回显：`IEEE(284)`

原因：`zbasic-GLM` 的 `Func109_Delay()` 读取 `snap_para(1)`，主通道 `snap_para(1)` 来自 `IEEE(4)`。

需要修改：

- `robot_modbus_lite/models.py`
- `mock_controller/controller.py`
- 对应测试

### 2. Func110 延时参数地址

当前 Python：

- `delay_sec -> IEEE(2)`

`zbasic-GLM + HMI`：

- 主通道：`delay_sec -> IEEE(6)`
- HMI：`delay_sec -> W726`
- 回显：`IEEE(286)`

原因：`zbasic-GLM` 的 `Func110` 读取 `parallel_para(2)`，主通道 `parallel_para(2)` 来自 `IEEE(6)`。

需要修改：

- `robot_modbus_lite/models.py`
- `mock_controller/controller.py`
- mock 中 Func110 运行时更新延时的地址也要从 `IEEE(2)` 改为 `IEEE(6)`
- 对应测试

### 3. Func112 状态位

`zbasic-GLM`：

- `M112_MASK = $30000`
- 状态位位置：bit 16/17
- Python 应解析为 `(16, 0x00030000)`

当前 Python：

- `SixAxisStatus.FUNC_STATE_FIELDS` 缺少 `112`
- mock 控制器状态字段也缺少 `112`

需要修改：

- `robot_modbus_lite/models.py`
- `mock_controller/controller.py`
- 对应测试

### 4. Func8 / Func102 状态位

`zbasic-GLM`：

- `M008_MASK = $C00000`
- 状态位位置：bit 22/23
- 用于历史绝对移动类功能，覆盖 `Func8/Func102`

当前 Python：

- 可以构造 `Func8/Func102` 写请求
- 但状态解析没有对应位

建议修改：

- `robot_modbus_lite/models.py`
- `mock_controller/controller.py`
- 给 `8` 和 `102` 增加同一个状态字段 `(22, 0x00C00000)`

### 5. Func11 支持口径

当前 Python 和 mock：

- 支持 `Func11` 多点插补

`zbasic-GLM`：

- 主调度没有 `Func11` 分支
- 真实控制器会把它当非法功能处理

建议：

- 暂不删除 `Func11` 代码和模板，避免破坏旧逻辑。
- 将其标记为当前 `zbasic-GLM` 协议不支持。
- 后续在真实发送入口或模板校验层禁用/提示，mock 可保留实验能力。

## 不需要修改

- `Func104` 系统控制映射
- `Func106/107` 点动映射
- `Func108/112` 直线参数布局
- `Func120` IO 参数映射
- 自然语言层的 `delay_sec` 语义
- HMI 工程本身
- `zbasic-GLM` 工程本身
- PyInstaller 打包关系

## 最小修改范围

- `robot_modbus_lite/models.py`
- `mock_controller/controller.py`
- `tests/test_mock_controller_v50.py`
- 协议文档或本差异记录

