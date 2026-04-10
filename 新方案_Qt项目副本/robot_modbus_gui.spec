# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


spec_dir = Path(SPECPATH)
project_root = spec_dir

data_dir = spec_dir / "data"
sdk_dir = spec_dir / "Windows Python（64位）"

datas = []
if data_dir.exists():
    datas.append((str(data_dir), "data"))
if sdk_dir.exists():
    datas.append((str(sdk_dir), "Windows Python（64位）"))

a = Analysis(
    [str(spec_dir / "gui_main.py")],
    pathex=[str(spec_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "ctypes",
        "ctypes.wintypes",
        "platform",
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
