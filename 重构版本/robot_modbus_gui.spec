# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


spec_dir = Path(SPECPATH)
project_root = spec_dir

data_dir = spec_dir / "data"
sdk_dir = spec_dir / "Windows Python（64位）"
env_file = spec_dir / ".env"
web_dist_dir = spec_dir / "web" / "kinetix-os---industrial-controller" / "dist"

datas = []
if data_dir.exists():
    datas.append((str(data_dir), "data"))
if web_dist_dir.exists():
    datas.append((str(web_dist_dir), "web_dist"))
if sdk_dir.exists():
    datas.append((str(sdk_dir), "Windows Python（64位）"))
if env_file.exists():
    datas.append((str(env_file), "."))
    datas.append((str(env_file), "robot_modbus_lite"))

a = Analysis(
    [str(spec_dir / "gui_main.py")],
    pathex=[str(spec_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "ctypes",
        "ctypes.wintypes",
        "platform",
        "fastapi",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "websockets",
        "webbrowser",
        "robot_modbus_lite.web_server",
        "robot_modbus_lite.web_launcher",
        "robot_modbus_lite.web_state",
        "robot_modbus_lite.web_nlp_service",
        "robot_modbus_lite.web_log_service",
        "robot_modbus_lite.web_precheck_service",
        "robot_modbus_lite.web_control_bridge",
        "robot_modbus_lite.web_voice_service",
        "robot_modbus_lite.iflytek_worker",
        "robot_modbus_lite.iflytek_iat",
        "robot_modbus_lite.six_axis_executor",
        "sounddevice",
        "xfyunsdkspeech",
        "xfyunsdkspeech.iat_client",
        "mock_controller",
        "mock_controller.client",
        "mock_controller.controller",
        "mock_controller.protocol",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RobotModbusLite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RobotModbusLite",
)
