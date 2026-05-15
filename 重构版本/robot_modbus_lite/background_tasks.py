"""图形界面后台线程辅助函数，避免阻塞主线程。"""

from __future__ import annotations

import threading
from typing import Any, Callable

from .exceptions import BackgroundTaskError


def run_background_thread(
    work_fn: Callable[[], Any],
    done_fn: Callable[[Any], None],
    post_to_main: Callable[[Callable[[], None]], None],
) -> threading.Thread:
    """运行后台线程。"""
    def wrapper() -> None:
        """处理相关数据。"""
        try:
            result = work_fn()
        except Exception as exc:
            result = BackgroundTaskError(exc)
        post_to_main(lambda: done_fn(result))

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    return thread
