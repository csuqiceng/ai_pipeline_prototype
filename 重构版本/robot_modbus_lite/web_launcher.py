"""Browser-mode launcher for the Web replacement UI."""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn

from robot_modbus_lite.web_server import app
from robot_modbus_lite.web_server import configure_control_bridge


def run_browser_mode(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Robot Modbus Lite in browser UI mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bridge-mode", choices=["dry_run", "service", "mock_controller"], default="dry_run")
    parser.add_argument("--controller-host", default="127.0.0.1")
    args = parser.parse_args(argv)

    configure_control_bridge(args.bridge_mode, controller_host=args.controller_host)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        loop="asyncio",
        http="h11",
        ws="websockets",
        lifespan="off",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="web-ui-server", daemon=True)
    thread.start()

    url = f"http://{args.host}:{args.port}/"
    time.sleep(1.5)
    webbrowser.open(url)

    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=5)
    return 0
