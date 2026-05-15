@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS1_PATH=%SCRIPT_DIR%build_qt.ps1"
set "PWSH_EXE=C:\Program Files\PowerShell\7\pwsh.exe"

if exist "%PWSH_EXE%" (
  "%PWSH_EXE%" -File "%PS1_PATH%"
) else (
  powershell -ExecutionPolicy Bypass -File "%PS1_PATH%"
)

endlocal
