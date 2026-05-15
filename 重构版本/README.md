# 双车床机械手 Qt 重构版本

本目录是 `新方案_Qt项目副本` 的重构版，主目标是保留 Qt 桌面应用和 V4.3 六轴 Modbus TCP 主链路，同时把历史原型、旧文档、运行日志和缓存归档到 `删除/`。

## 当前定位

- 主运行入口：`gui_main.py`
- 主窗口：`robot_modbus_lite/qt_gui.py`
- 主协议：V4.3 六轴 Modbus TCP
- 默认推荐控制器：界面中选择“模拟控制器”做本地验证
- 真实控制器：通过 `Windows Python（64位）` 下的 ZMotion SDK 连接

旧 VR/标准协议的数据模型和 mock 兼容行为仍保留在源码中，用于兼容和历史测试；Qt GUI 的主发送路径固定走 V4.3 六轴协议。

## 主要目录

| 路径 | 说明 |
|---|---|
| `robot_modbus_lite/` | 重构后的 GUI、协议服务、模型、语音、授权、日志、配置和流程模块。 |
| `mock_controller/` | 本地模拟控制器核心，支持 V4.3 回显、状态位、槽互斥和回归测试。 |
| `data/` | 运行配置、查询模板、流程和避障配置。 |
| `docs/最终接口设计/` | 当前保留的 V4.3 协议、评审和联调文档。 |
| `tools/` | V4.3 mock 回归和真机地址映射检查工具。 |
| `Windows Python（64位）/` | ZMotion Python SDK 包装文件和 DLL。 |
| `删除/` | 已归档的历史资料、旧入口、旧 demo、运行日志、缓存和参考文档。 |

## 运行方式

在当前目录执行：

```bash
python gui_main.py
```

语音 worker 子进程入口仍由同一个文件分发：

```bash
python gui_main.py --iflytek-worker
```

运行前建议安装依赖：

```bash
pip install -r requirements.txt
```

本地语音 SDK 模式可能还需要额外安装 `xfyunsdkspeech` 或 `pyaudio`，具体取决于现场使用的麦克风后端和讯飞 SDK 方案。

## 环境变量

根目录 `.env` 用于本地 DeepSeek、讯飞和授权服务配置。当前打包配置仍会读取 `.env`；发布前需要单独处理密钥安全问题。

`.env.example` 只保留模板字段，不应写入真实密钥。

## 验证

V4.3 mock 回归：

```bash
python tools\verify_v43_mock_m9.py
```

真机地址映射检查：

```bash
python tools\verify_v43_mapping.py --host <controller-ip>
```

## 打包方式

Windows 下推荐执行：

```bat
build_qt.bat
```

或直接执行 PowerShell 脚本：

```powershell
.\build_qt.ps1
```

脚本会使用 `robot_modbus_gui.spec`，并把输出写到当前目录下的 `打包输出/`。

当前打包默认包含：

- `data/`
- `Windows Python（64位）/`
- 根目录 `.env`

当前打包不依赖 `附件/`，该目录已经归档到 `删除/附件`。

## 已归档内容

以下内容不是当前 Qt/V4.3 主链路的一部分，已经移动到 `删除/`：

- 旧 CLI：`main.py`、`robot_modbus_lite/cli.py`
- 静态 Web demo：`robot_modbus_lite/web_ui`
- 一次性迁移脚本和早期导入测试：`tools/migrate_v43_pct_fields.py`、`_test_imports.py`
- 旧 mock 演示：`mock_controller/demo.py`
- 历史参考资料和旧协议文档：`附件/`、旧 `docs/` 子目录、V3/V4.1/V4.2 资料
- 运行日志和 Python 缓存

如需恢复这些内容，先查看 `删除/` 下对应移动清单，再按需移回。
