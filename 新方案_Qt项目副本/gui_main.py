from __future__ import annotations

import sys

from robot_modbus_lite.iflytek_worker import main as iflytek_worker_main
from robot_modbus_lite.qt_gui import main as qt_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--iflytek-worker":
        sys.exit(iflytek_worker_main(sys.argv[2:]))
    qt_main()
