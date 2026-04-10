# 双车床机械手 Qt 项目副本

这是从 `新方案` 中整理出来的独立 Qt 项目目录。

当前目录已经包含：
- Qt GUI 代码
- 模拟控制器
- 数据文件
- SDK 依赖目录
- 附件资料
- 打包配置

## 入口

- 运行源码：
  - [gui_main.py](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/gui_main.py)
- 打包配置：
  - [robot_modbus_gui.spec](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/robot_modbus_gui.spec)

## 主要目录

- [robot_modbus_lite](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/robot_modbus_lite)
  - Qt GUI、服务层、模型、模板读写
- [mock_controller](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/mock_controller)
  - 本地下位机模拟器
- [data](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/data)
  - 查询模板和流程数据
- [附件](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/附件)
  - 标准 PDF、参考图、SDK 手册等
- [Windows Python（64位）](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/Windows Python（64位）)
  - ZMotion Python SDK 相关文件
- [docs](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/docs)
  - 项目文档整理区

## 文档导航

- [文档导航.md](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/docs/文档导航.md)
- 当前方案定位说明：
  - [当前方案定位与过渡说明.md](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/docs/03_计划与冻结/当前方案定位与过渡说明.md)

## 运行方式

在仓库根目录执行：

```bash
python 新方案_Qt项目副本/gui_main.py
```

默认推荐在界面中选择：
- `控制器类型 = 模拟控制器`
- `发送协议 = 最终标准协议`

## 打包方式

推荐在仓库根目录执行 PyInstaller，并显式指定输出目录，这样打包结果会保存在当前 Qt 项目副本内部。

建议先在项目目录下准备一个统一的打包输出目录，例如：

```text
新方案_Qt项目副本/
  打包输出/
    dist/
    build/
```

推荐打包命令：

```bash
C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe -m PyInstaller ^
  新方案_Qt项目副本\robot_modbus_gui.spec ^
  --noconfirm ^
  --distpath 新方案_Qt项目副本\打包输出\dist ^
  --workpath 新方案_Qt项目副本\打包输出\build
```

打包完成后，`exe` 默认会在：

```text
新方案_Qt项目副本\打包输出\dist\RobotModbusLite\RobotModbusLite.exe
```

说明：
- `robot_modbus_gui.spec` 已经按当前项目副本内部路径配置
- 当前打包默认包含：
  - `data`
  - `Windows Python（64位）`
- 当前打包默认不包含：
  - `附件` 中的 PDF / JPG / 参考资料
- 不建议只拿单独的 `exe`
- 应整体保留 `RobotModbusLite` 目录，因为它依赖同目录下的资源文件

## 一键打包脚本

项目里已经提供两个脚本：

- PowerShell：
  - [build_qt.ps1](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/build_qt.ps1)
- Batch：
  - [build_qt.bat](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/build_qt.bat)
- Shell：
  - [build_qt.sh](c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/build_qt.sh)

Windows 下最推荐直接使用：

```bat
新方案_Qt项目副本\build_qt.bat
```

Shell 脚本适合在 `Git Bash`、`MSYS2`、`WSL` 等环境中使用，例如：

```bash
bash 新方案_Qt项目副本/build_qt.sh
```

如果你的 Python 路径不同，可以先指定环境变量：

```bash
PYTHON_EXE=/c/Users/a/AppData/Local/Programs/Python/Python310/python.exe bash 新方案_Qt项目副本/build_qt.sh
```
