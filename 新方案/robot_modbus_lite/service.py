from __future__ import annotations

from pathlib import Path

from .command_parser import parse_command
from .models import ModbusWriteRequest, ParsedCommand, QueryRecord
from .query_table import load_query_table


class RobotModbusService:
    def __init__(self, csv_path: str | Path, start_register: int = 0) -> None:
        self.csv_path = Path(csv_path)
        self.start_register = start_register
        self.table = load_query_table(self.csv_path)

    def parse(self, text: str) -> ParsedCommand:
        return parse_command(text, self.table)

    def resolve(self, query_key: str) -> QueryRecord:
        return self.table[query_key]

    def build_request(self, text: str) -> tuple[ParsedCommand, QueryRecord, ModbusWriteRequest]:
        parsed = self.parse(text)
        record = self.resolve(parsed.query_key)
        request = ModbusWriteRequest(
            start_register=self.start_register,
            values=tuple(record.payload()),
        )
        return parsed, record, request
