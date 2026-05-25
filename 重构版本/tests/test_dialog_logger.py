import json
from datetime import datetime
from pathlib import Path

from robot_modbus_lite.dialog_logger import DialogLogger


def test_dialog_logger_writes_daily_jsonl(tmp_path: Path):
    logger = DialogLogger(tmp_path, clock=lambda: datetime(2026, 5, 23, 10, 30, 0))

    logger.append(role="user", text="小正，移动到位置A", result="received", extra={"ack_delay_ms": 12})

    path = tmp_path / "dialog_2026-05-23.jsonl"
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["role"] == "user"
    assert rows[0]["text"] == "小正，移动到位置A"
    assert rows[0]["extra"]["ack_delay_ms"] == 12
