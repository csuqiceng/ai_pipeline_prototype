"""Application entrypoint for Qt UI, Web UI and voice worker modes."""

from __future__ import annotations

import sys
from datetime import datetime
import os
from pathlib import Path


def _boot_trace(message: str) -> None:
    if os.environ.get("ROBOT_WEB_BOOT_TRACE") != "1":
        return
    try:
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        with (root / "web_boot_trace.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except Exception:
        pass


if __name__ == "__main__":
    _ui_mode = os.environ.get("ROBOT_UI_MODE", "qt").lower()

    if len(sys.argv) > 1 and sys.argv[1] == "--iflytek-worker":
        _boot_trace("enter iflytek worker")
        from robot_modbus_lite.iflytek_worker import main as iflytek_worker_main

        sys.exit(iflytek_worker_main(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "--web-server" or _ui_mode == "web-server":
        _boot_trace(f"enter web-server argv={sys.argv[2:]}")
        from robot_modbus_lite.web_server import main as web_server_main

        _boot_trace("imported web_server")
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        web_server_main()
        _boot_trace("web_server_main returned")
        sys.exit(0)

    if (len(sys.argv) > 2 and sys.argv[1] == "--ui" and sys.argv[2] == "web-browser") or _ui_mode == "web":
        _boot_trace(f"enter web-browser argv={sys.argv[3:]}")
        from robot_modbus_lite.web_launcher import run_browser_mode

        sys.exit(run_browser_mode(sys.argv[3:]))

    if len(sys.argv) > 2 and sys.argv[1] == "--ui" and sys.argv[2] == "qt-legacy":
        sys.argv = [sys.argv[0], *sys.argv[3:]]

    _boot_trace("enter qt ui")
    from robot_modbus_lite.qt_gui import main as qt_main

    qt_main()
