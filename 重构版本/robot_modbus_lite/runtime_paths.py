"""源码运行和打包运行两种场景下的路径解析。"""

from __future__ import annotations

import sys
from pathlib import Path


def runtime_dir() -> Path:
    """定位运行目录相关数据。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    """定位资源相关数据。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def resolve_runtime_data_file(filename: str) -> Path:
    """解析文件。"""
    runtime_file = runtime_dir() / "data" / filename
    if runtime_file.exists():
        return runtime_file
    return resource_dir() / "data" / filename
