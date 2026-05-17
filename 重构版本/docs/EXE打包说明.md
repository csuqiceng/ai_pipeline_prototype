# EXE 打包说明

本文记录本项目 Qt GUI 的 Windows EXE 打包方式。当前实际使用的是 PyInstaller，入口为 `gui_main.py`，打包配置为 `robot_modbus_gui.spec`。

## 1. 前置条件

1. 操作系统：Windows。
2. Python：脚本默认使用 `C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe`。
3. 依赖已安装：

```powershell
pip install -r requirements.txt
```

4. PyInstaller 已安装在 Python 3.10 环境中：

```powershell
C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe -m pip install PyInstaller
```

5. 打包前关闭正在运行的 GUI，避免旧 EXE 或资源文件被占用。

## 2. 推荐打包命令

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_qt.ps1
```

也可以双击或执行：

```bat
build_qt.bat
```

本次打包实际执行的是：

```powershell
Stop-Process -Id <当前GUI进程ID> -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File .\build_qt.ps1
```

## 3. 打包脚本做了什么

`build_qt.ps1` 会执行以下逻辑：

1. 定位项目根目录。
2. 使用固定 Python：

```text
C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe
```

3. 使用 `robot_modbus_gui.spec` 作为 PyInstaller 配置。
4. 输出目录固定为：

```text
打包输出\dist
打包输出\build
```

5. 执行：

```powershell
& $pythonExe -m PyInstaller $specPath --noconfirm --distpath $distPath --workpath $buildPath
```

## 4. 输出文件

打包完成后，主程序路径为：

```text
C:\Users\a\Desktop\ai_pipeline_prototype\重构版本\打包输出\dist\RobotModbusLite\RobotModbusLite.exe
```

这是目录式打包，发布时需要保留整个目录：

```text
打包输出\dist\RobotModbusLite\
```

不要只复制单个 `RobotModbusLite.exe`，否则 `_internal`、`data`、SDK DLL 等资源可能缺失。

## 5. spec 包含的资源

`robot_modbus_gui.spec` 会把以下资源打进发布目录：

| 源路径 | 打包目标 | 说明 |
|---|---|---|
| `data/` | `data/` | 查询表、流程、系统配置等运行数据 |
| `Windows Python（64位）/` | `Windows Python（64位）/` | ZMotion SDK/DLL |
| `.env` | `.` 和 `robot_modbus_lite/` | 本地环境配置 |

注意：`.env` 可能包含密钥或现场配置，正式发布前需要确认是否允许随包分发。

## 6. 打包后验证

### 6.1 确认 EXE 存在

```powershell
$exe = "C:\Users\a\Desktop\ai_pipeline_prototype\重构版本\打包输出\dist\RobotModbusLite\RobotModbusLite.exe"
Get-Item -LiteralPath $exe | Select-Object FullName,Length,LastWriteTime
```

### 6.2 冒烟启动

```powershell
$exe = "C:\Users\a\Desktop\ai_pipeline_prototype\重构版本\打包输出\dist\RobotModbusLite\RobotModbusLite.exe"
$p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -PassThru
Start-Sleep -Seconds 4
Get-Process -Id $p.Id | Select-Object Id,ProcessName,MainWindowTitle,Responding
```

正常结果应看到：

```text
MainWindowTitle = 机械手控制系统
Responding      = True
```

测试完成后可关闭测试进程：

```powershell
Stop-Process -Id $p.Id
```

## 7. 本次打包结果

本次打包时间：2026-05-17。

结果：

```text
PyInstaller build: 成功
EXE 路径: C:\Users\a\Desktop\ai_pipeline_prototype\重构版本\打包输出\dist\RobotModbusLite\RobotModbusLite.exe
冒烟启动: 成功
窗口标题: 机械手控制系统
Responding: True
```

## 8. 常见问题

### 8.1 PowerShell 禁止执行脚本

使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_qt.ps1
```

### 8.2 Python 路径不存在

`build_qt.ps1` 固定写了：

```text
C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe
```

如果换机器，需要修改脚本中的 `$pythonExe`。

### 8.3 打包目录乱码

PowerShell 输出里中文路径可能显示乱码，但实际目录名仍是：

```text
打包输出
```

以资源管理器或 `Get-Item -LiteralPath` 看到的路径为准。

### 8.4 PyInstaller warn 文件有 missing module

警告文件在：

```text
打包输出\build\robot_modbus_gui\warn-robot_modbus_gui.txt
```

里面常见 `pwd`、`grp`、`posix`、`OpenSSL`、`chardet` 等可选模块提示。只要 EXE 能启动并完成核心功能验证，这类提示通常不是阻断问题。

### 8.5 运行后配置不是预期

发布目录中会包含一份 `data/`。EXE 运行时优先读取发布目录内的数据文件，不一定读取源码目录下的 `data/`。如果修改了查询表或系统配置，需要重新打包，或同步修改发布目录内的：

```text
打包输出\dist\RobotModbusLite\data\
```
