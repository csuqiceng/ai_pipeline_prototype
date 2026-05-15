$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe"
$specPath = Join-Path $projectRoot "robot_modbus_gui.spec"
$outputDirName = [string]([char]0x6253) + [char]0x5305 + [char]0x8F93 + [char]0x51FA
$outputRoot = Join-Path $projectRoot $outputDirName
$distPath = Join-Path $outputRoot "dist"
$buildPath = Join-Path $outputRoot "build"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Python not found: $pythonExe"
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $distPath -Force | Out-Null
New-Item -ItemType Directory -Path $buildPath -Force | Out-Null

& $pythonExe -m PyInstaller $specPath --noconfirm --distpath $distPath --workpath $buildPath

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host (Join-Path $distPath 'RobotModbusLite\RobotModbusLite.exe')
