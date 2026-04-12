from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import ModbusWriteRequest
from .service import RobotModbusService
from .zmotion_client import ZMotionClientError, ZMotionModbusClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Robot Modbus Lite CLI")
    parser.add_argument("text", nargs="?", help="要解析并发送的文本命令，例如：移动到位置A")
    parser.add_argument(
        "--csv-path",
        default=str(Path(__file__).resolve().parents[2] / "附件" / "机械臂AI地址表.csv"),
        help="有效数据表 CSV 路径",
    )
    parser.add_argument("--host", default="192.168.1.11", help="控制器 IP")
    parser.add_argument("--start-register", type=int, default=0, help="Modbus 起始寄存器")
    parser.add_argument("--query-key", help="直接使用查询键，不走文本解析")
    parser.add_argument("--send", action="store_true", help="发送到真实控制器")
    args = parser.parse_args()

    if not args.text and not args.query_key:
        parser.error("必须提供 text 或 --query-key。")

    service = RobotModbusService(csv_path=args.csv_path, start_register=args.start_register)

    if args.query_key:
        record = service.resolve(args.query_key)
        request_model = ModbusWriteRequest(
            start_register=args.start_register,
            values=tuple(record.payload()),
        )
        payload = {
            "mode": "query_key",
            "query_key": args.query_key,
            "function_id": record.function_id,
            "registers": list(record.registers),
            "request": {
                "start_register": request_model.start_register,
                "values": list(request_model.values),
            },
        }
    else:
        parsed, record, request = service.build_request(args.text)
        payload = {
            "mode": "text",
            "text": parsed.raw_text,
            "query_key": parsed.query_key,
            "function_id": record.function_id,
            "registers": list(record.registers),
            "request": {
                "start_register": request.start_register,
                "values": list(request.values),
            },
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not args.send:
        return

    request = payload["request"]
    client = ZMotionModbusClient(
        host=args.host,
        repo_root=Path(__file__).resolve().parents[2],
        start_register=args.start_register,
    )
    try:
        client.connect()
        client.write_floats(
            ModbusWriteRequest(
                start_register=request["start_register"],
                values=tuple(request["values"]),
            )
        )
        print(json.dumps({"ok": True, "action": "send", "host": args.host}, ensure_ascii=False, indent=2))
    except ZMotionClientError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "host": args.host}, ensure_ascii=False, indent=2))
        raise
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
