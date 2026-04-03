# 机械臂 AI Modbus TCP 轻量联机方案

这个目录用于单独保存“机械臂 AI Modbus TCP 轻量联机版”的设计资料、实现流程和后续开发产物。

当前目标不是继续扩展旧原型，而是单独沉淀一套更适合真机早期联调的轻量方案：

- 上位机负责语义解析、查表和参数组包
- 控制器负责按函数序号执行对应动作
- 通讯方式采用 `Modbus TCP`
- `ZMOTION SDK` 仅用于通讯和传参

## 当前目录说明

- [方案说明.md](c:/Users/a/Desktop/ai_pipeline_prototype/新方案/方案说明.md)
  - 新方案设计说明
  - 协议、分工、评审确认项

- [实现流程.md](c:/Users/a/Desktop/ai_pipeline_prototype/新方案/实现流程.md)
  - 从用户输入到控制器执行的流程
  - 第一阶段实现步骤

## 当前已知关键参数

- 控制器 IP：`192.168.1.11`
- 通讯协议：`Modbus TCP`
- 第一阶段发送协议：`函数序号 + 寄存器1~7`
- 第一阶段功能：`移动 / 抓取 / 放下`

## 备注

第一阶段优先采用“有效数据查询表”直接查值发送，目标是先把链路打通并完成试机。

## 当前已实现

- CSV 有效数据表解析
- JSON 查询表初始化与保存
- 固定规则解析
- 8 个发送值组包
- ZMOTION SDK Modbus TCP 发送接口
- CLI 例程
- GUI 查询表编辑器

## CLI 示例

在仓库根目录执行：

```bash
python 新方案/main.py "移动到位置A"
python 新方案/main.py --query-key 位置B
python 新方案/main.py "抓取"
```

如果要发送到真实控制器：

```bash
python 新方案/main.py "移动到位置A" --send
```

可选参数：

- `--host`：控制器 IP，默认 `192.168.1.11`
- `--start-register`：Modbus 起始寄存器，默认 `0`
- `--csv-path`：自定义地址表路径

## GUI 示例

启动 GUI 查询表编辑器：

```bash
python 新方案/gui_main.py
```

GUI 首次启动会从原始 CSV 导入一份 JSON 到：

```text
新方案/data/query_table.json
```

之后新增、编辑、保存都只操作这个 JSON，不修改原始 CSV。
