"""图形界面进程和语音识别子进程共享的文件通信约定。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceWorkerFiles:
    """一次语音识别子进程使用的结果、日志和停止标记路径。"""
    debug_pcm: Path
    result_json: Path
    stop_flag: Path


def make_voice_worker_files(log_dir: Path, timestamp: str) -> VoiceWorkerFiles:
    """处理语音子进程。"""
    return VoiceWorkerFiles(
        debug_pcm=log_dir / f"voice_debug_{timestamp}.pcm",
        result_json=log_dir / f"iflytek_result_mic_{timestamp}.json",
        stop_flag=log_dir / f"voice_stop_{timestamp}.flag",
    )


def reset_stop_flag(stop_flag: Path) -> None:
    """复位相关数据。"""
    if stop_flag.exists():
        stop_flag.unlink()


def write_stop_flag(stop_flag: Path) -> None:
    """写入相关数据。"""
    stop_flag.write_text("stop", encoding="utf-8")


def cleanup_stop_flag(stop_flag: Path) -> None:
    """清理相关数据。"""
    if stop_flag.exists():
        stop_flag.unlink()
