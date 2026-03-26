@echo off
chcp 65001 >nul
echo ========================================
echo   AI Pipeline Prototype 依赖安装脚本
echo ========================================
echo.

echo [1/3] 检查 Python 版本...
python --version 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo.
echo [2/3] 升级 pip...
python -m pip install --upgrade pip -q

echo.
echo [3/3] 安装项目依赖...
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [警告] 部分依赖安装失败，可能需要手动安装
    echo.
    echo 常见问题:
    echo   - sounddevice: 需要 Visual C++ Build Tools
    echo   - pyaudio: 需要 PortAudio 库
    echo   - tkinter: 不是 pip 依赖，需要使用自带 tkinter 的 Python 解释器
    echo.
    echo 手动安装命令:
    echo   python -m pip install xfyunsdkspeech
    echo   python -m pip install websocket-client
    echo   python -m pip install sounddevice
) else (
    echo.
    echo ========================================
    echo   安装完成！
    echo ========================================
    echo.
    echo 运行演示:
    echo   python -m ai_pipeline_prototype.demo
    echo.
    echo 启动 GUI:
    echo   python -m ai_pipeline_prototype.gui
    echo.
    echo   如果提示缺少 tkinter:
    echo     1. 请确认使用的是自带 Tk GUI 组件的 Python 3.10+
    echo     2. Windows 建议安装 python.org 官方发行版
    echo     3. 然后用对应解释器重新执行上述命令
)

pause
