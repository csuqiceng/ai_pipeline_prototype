from __future__ import annotations

from pathlib import Path

from .command_parser import parse_command
from .flow_store import load_flows_json, save_flows_json
from .models import FlowDefinition, ModbusWriteRequest, ParsedCommand, QueryRecord
from .query_table import load_query_table


class RobotModbusService:
    def __init__(self, csv_path: str | Path, start_register: int = 0, flows_path: str | Path | None = None) -> None:
        self.csv_path = Path(csv_path)
        self.start_register = start_register
        self.table = load_query_table(self.csv_path)
        self.flows_path = Path(flows_path) if flows_path else None
        self.flows = load_flows_json(self.flows_path) if self.flows_path else {}

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

    def build_request_from_key(self, query_key: str) -> tuple[QueryRecord, ModbusWriteRequest]:
        record = self.resolve(query_key)
        request = ModbusWriteRequest(
            start_register=self.start_register,
            values=tuple(record.payload()),
        )
        return record, request

    def list_flow_names(self) -> list[str]:
        return sorted(self.flows)

    def get_flow(self, name: str) -> FlowDefinition:
        return self.flows[name]

    def save_flow(self, flow: FlowDefinition) -> None:
        self.flows[flow.name] = flow
        if self.flows_path:
            save_flows_json(self.flows_path, self.flows)

    def delete_flow(self, name: str) -> None:
        if name in self.flows:
            del self.flows[name]
            if self.flows_path:
                save_flows_json(self.flows_path, self.flows)

    def build_flow_requests(self, flow_name: str) -> list[tuple[str, QueryRecord, ModbusWriteRequest]]:
        flow = self.get_flow(flow_name)
        result: list[tuple[str, QueryRecord, ModbusWriteRequest]] = []
        for step in flow.steps:
            record, request = self.build_request_from_key(step)
            result.append((step, record, request))
        return result
