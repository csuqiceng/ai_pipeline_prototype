"""源码运行和打包运行两种场景下的路径解析。"""

from __future__ import annotations

import shutil
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


def resolve_writable_runtime_data_file(filename: str) -> Path:
    """Return a writable data file path, copying packaged defaults when needed."""
    runtime_file = runtime_dir() / "data" / filename
    if runtime_file.exists():
        return runtime_file

    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    fallback = resource_dir() / "data" / filename
    if fallback.exists():
        shutil.copy2(fallback, runtime_file)
    return runtime_file


def resolve_web_dist_dir() -> Path:
    """定位打包后的 Web 前端静态资源目录。"""
    runtime_web = runtime_dir() / "_internal" / "web_dist"
    if runtime_web.exists():
        return runtime_web
    flat_runtime_web = runtime_dir() / "web_dist"
    if flat_runtime_web.exists():
        return flat_runtime_web
    source_runtime_web = runtime_dir() / "web" / "kinetix-os---industrial-controller" / "dist"
    if source_runtime_web.exists():
        return source_runtime_web
    return resource_dir() / "web" / "kinetix-os---industrial-controller" / "dist"
