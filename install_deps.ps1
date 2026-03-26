#!/usr/bin/env pwsh
# ========================================
# AI Pipeline Prototype 依赖安装脚本
# ========================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI Pipeline Prototype 依赖安装脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Python
Write-Host "[1/3] 检查 Python 版本..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[错误] 未找到 Python，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# 升级 pip
Write-Host ""
Write-Host "[2/3] 升级 pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip -q

# 安装依赖
Write-Host ""
Write-Host "[3/3] 安装项目依赖..." -ForegroundColor Yellow
python -m pip install -r requirements.txt

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  安装完成！" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "运行演示:" -ForegroundColor White
    Write-Host "  python -m ai_pipeline_prototype.demo" -ForegroundColor Gray
    Write-Host ""
    Write-Host "启动 GUI:" -ForegroundColor White
    Write-Host "  python -m ai_pipeline_prototype.gui" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  如果提示缺少 tkinter:" -ForegroundColor Gray
    Write-Host "    1. 请确认使用的是自带 Tk GUI 组件的 Python 3.10+" -ForegroundColor Gray
    Write-Host "    2. Windows 建议安装 python.org 官方发行版" -ForegroundColor Gray
    Write-Host "    3. 然后用对应解释器重新执行上述命令" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "[警告] 部分依赖安装失败，可能需要手动安装" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "常见问题:" -ForegroundColor White
    Write-Host "  - sounddevice: 需要 Visual C++ Build Tools" -ForegroundColor Gray
    Write-Host "  - pyaudio: 需要 PortAudio 库" -ForegroundColor Gray
    Write-Host "  - tkinter: 不是 pip 依赖，需要使用自带 tkinter 的 Python 解释器" -ForegroundColor Gray
    Write-Host ""
    Write-Host "手动安装命令:" -ForegroundColor White
    Write-Host "  python -m pip install xfyunsdkspeech" -ForegroundColor Gray
    Write-Host "  python -m pip install websocket-client" -ForegroundColor Gray
    Write-Host "  python -m pip install sounddevice" -ForegroundColor Gray
}
