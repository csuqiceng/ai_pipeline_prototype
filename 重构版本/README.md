# 双车床机械手 Qt 重构版本

本目录是 `新方案_Qt项目副本` 的重构版，主目标是保留 Qt 桌面应用、六轴 Modbus 主链路、流程执行、语音/自然语言入口和本地模拟控制器，并把协议实现对齐到 `docs/上位机通讯寄存器说明书V5.0.md`。

## 当前定位

- 主运行入口：`gui_main.py`
- 主窗口：`robot_modbus_lite/qt_gui.py`
- 主协议：V5.0 六轴 Modbus TCP 寄存器协议
- 默认推荐控制器：界面中选择“模拟控制器”做本地验证
- 真实控制器：通过 `Windows Python（64位）` 下的 ZMotion SDK 连接
- 打包产物：`打包输出\dist\RobotModbusLite\RobotModbusLite.exe`

旧 VR/标准协议的数据模型和 mock 兼容行为仍保留在源码中，用于兼容和历史测试；Qt GUI 的主发送路径走 V5.0 六轴协议实现。

## 主要目录

| 路径 | 说明 |
|---|---|
| `robot_modbus_lite/` | 重构后的 GUI、协议服务、模型、语音、授权、日志、配置和流程模块。 |
| `mock_controller/` | 本地模拟控制器核心，支持 V5.0 回显、状态位、槽互斥、接受确认和回归测试。 |
| `data/` | 运行配置、查询模板、流程和避障配置。 |
| `docs/` | V5.0 协议文档、综合评审、打包说明和重构记录。 |
| `docs/最终接口设计/` | 历史 V4.3 协议、评审和联调文档。 |
| `tools/` | V5.0 协议对齐验证、历史 V4.3 mock 回归和真机地址映射检查工具。 |
| `Windows Python（64位）/` | ZMotion Python SDK 包装文件和 DLL。 |

## 运行方式

在当前目录执行：

```bash
python gui_main.py
```

语音 worker 子进程入口仍由同一个文件分发，主要用于降级和兼容：

```bash
python gui_main.py --iflytek-worker
```

运行前安装依赖：

```bash
pip install -r requirements.txt
```

## 语音识别

当前 GUI 的语音主线是讯飞 IAT：

- 本地模式：`sounddevice` 采集麦克风，开始录音时立即启动讯飞 IAT 流式识别，边录边上传；停止录音时只等待最终文本。
- 订阅/代理模式：录音结束后通过授权服务器接口 `/api/v1/proxy/voice/transcribe` 上传识别。
- 本地模式会读取 `.env` 中的 `IFLYTEK_APP_ID`、`IFLYTEK_API_KEY`、`IFLYTEK_API_SECRET`。
- 开始录音时会先清空自然语言输入框，避免残留上一次识别文本。
- 日志会记录语音耗时，包括音频时长、client 初始化、识别耗时、总耗时和 `voice_mode`。
- 为避免快速点击导致讯飞 SDK `invalid handle`，本地流式识别设置了最短录音时长保护。

依赖说明：

```text
必需：sounddevice、numpy
本地讯飞模式：xfyunsdkspeech
备用麦克风后端：pyaudio（可选）
```

`tests/` 下保留了 Vosk、FunASR、Sherpa-ONNX 的试验脚本和模型，但它们目前没有接入正式 GUI 按钮流程。

## V5.0 协议实现

当前代码已按 `docs/上位机通讯寄存器说明书V5.0.md` 对齐核心链路：

- 三道门发送流程：第一道门状态检查、第二道门回显比对、第三道门触发前状态复查。
- `IEEE(312)` 命令接受确认。
- `IEEE(324)` 当前内部函数号。
- Func109/Func110 使用 `IEEE(2)=delay_sec`。
- Func109 DONE 手动清除。
- `LONG(36)` 系统状态和 `LONG(38)` 报警详情分离解析。
- Func120 读取 `LONG(42/44/46)`。
- DPOS、MPOS、安全限位、延时回显和诊断回显读取。
- mock controller 同步 V5.0 语义，支持本地回归。

综合评审见：

```text
docs\V5.0协议文档与代码实现综合评审.md
```

## 日志

运行日志写入：

```text
data\exported_logs\session_<时间>_<id>.jsonl
```

日志包含命令、流程、六轴状态、最终快照、语音耗时和异常字段。流程执行会带 `flow_run_id`、步骤序号、总步数、已完成步数和 elapsed_ms，便于后续排查。

## 环境变量

根目录 `.env` 用于本地 DeepSeek、讯飞和授权服务配置。当前打包配置仍会读取 `.env`；正式发布前需要确认密钥是否允许随包分发。

`.env.example` 只保留模板字段，不应写入真实密钥。

## 验证

V5.0 协议对齐验证：

```bash
python tools\verify_v50_protocol_alignment.py
```

历史 V4.3 mock 回归：

```bash
python tools\verify_v43_mock_m9.py
```

真机地址映射检查：

```bash
python tools\verify_v43_mapping.py --host <controller-ip>
```

语法检查示例：

```bash
python -m py_compile robot_modbus_lite\voice_mixin.py robot_modbus_lite\qt_gui.py
```

## 打包方式

Windows 下推荐执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_qt.ps1
```

也可以执行：

```bat
build_qt.bat
```

脚本会使用 `robot_modbus_gui.spec`，并把输出写到当前目录下的 `打包输出/`。

主程序路径：

```text
C:\Users\a\Desktop\ai_pipeline_prototype\重构版本\打包输出\dist\RobotModbusLite\RobotModbusLite.exe
```

这是目录式打包，发布时需要保留整个目录：

```text
打包输出\dist\RobotModbusLite\
```

不要只复制单个 `RobotModbusLite.exe`，否则 `_internal`、`data`、SDK DLL 等资源可能缺失。

完整打包说明见：

```text
docs\EXE打包说明.md
```
