#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
if command -v cygpath >/dev/null 2>&1; then
  SCRIPT_PATH="$(cygpath -u "$SCRIPT_PATH")"
fi

PROJECT_ROOT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
PYTHON_EXE="${PYTHON_EXE:-/c/Users/a/AppData/Local/Programs/Python/Python310/python.exe}"
SPEC_PATH="$PROJECT_ROOT/robot_modbus_gui.spec"
OUTPUT_ROOT="$PROJECT_ROOT/打包输出"
DIST_PATH="$OUTPUT_ROOT/dist"
BUILD_PATH="$OUTPUT_ROOT/build"

if [ ! -f "$PYTHON_EXE" ]; then
  echo "未找到 Python 解释器: $PYTHON_EXE" >&2
  exit 1
fi

mkdir -p "$DIST_PATH" "$BUILD_PATH"

"$PYTHON_EXE" -m PyInstaller "$SPEC_PATH" \
  --noconfirm \
  --distpath "$DIST_PATH" \
  --workpath "$BUILD_PATH"

echo
echo "打包完成："
echo "$DIST_PATH/RobotModbusLite/RobotModbusLite.exe"
