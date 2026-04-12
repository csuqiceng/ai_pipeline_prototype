$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe"
$specPath = Join-Path $projectRoot "robot_modbus_gui.spec"
$outputRoot = Join-Path $projectRoot "打包输出"
$distPath = Join-Path $outputRoot "dist"
$buildPath = Join-Path $outputRoot "build"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "未找到 Python 解释器: $pythonExe"
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $distPath -Force | Out-Null
New-Item -ItemType Directory -Path $buildPath -Force | Out-Null

& $pythonExe -m PyInstaller $specPath --noconfirm --distpath $distPath --workpath $buildPath

Write-Host ""
Write-Host "打包完成：" -ForegroundColor Green
Write-Host (Join-Path $distPath 'RobotModbusLite\RobotModbusLite.exe')
