$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\a\AppData\Local\Programs\Python\Python310\python.exe"
$specPath = Join-Path $projectRoot "robot_modbus_gui.spec"
$webRoot = Join-Path $projectRoot "web\kinetix-os---industrial-controller"
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

if (Test-Path -LiteralPath $webRoot) {
    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npmCmd) {
        Push-Location $webRoot
        try {
            if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules"))) {
                & $npmCmd.Source install
            }
            $env:VITE_DATA_MODE = "api"
            Remove-Item Env:VITE_API_BASE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:VITE_WS_URL -ErrorAction SilentlyContinue
            & $npmCmd.Source run build
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Warning "npm.cmd not found; skip Web frontend build."
    }
}

& $pythonExe -m PyInstaller $specPath --noconfirm --distpath $distPath --workpath $buildPath

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host (Join-Path $distPath 'RobotModbusLite\RobotModbusLite.exe')
