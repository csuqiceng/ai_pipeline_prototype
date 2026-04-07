from __future__ import annotations

from pathlib import Path

from .command_parser import parse_command
from .flow_store import load_flows_json, save_flows_json
from .models import FixedVrCommand, FlowDefinition, ParsedCommand, QueryRecord, VrReadRequest, VrWriteRequest
from .query_table import load_query_table


class RobotModbusService:
    def __init__(
        self,
        csv_path: str | Path,
        start_register: int = 0,
        flows_path: str | Path | None = None,
        *,
        command_vr_start: int = 500,
        status_vr_start: int = 600,
    ) -> None:
        self.csv_path = Path(csv_path)
        self.start_register = start_register
        self.trigger_vr = command_vr_start
        self.command_vr_start = command_vr_start + 1
        self.status_vr_start = status_vr_start
        self.table = load_query_table(self.csv_path)
        self.flows_path = Path(flows_path) if flows_path else None
        self.flows = load_flows_json(self.flows_path) if self.flows_path else {}

    def parse(self, text: str) -> ParsedCommand:
        return parse_command(text, self.table)

    def resolve(self, query_key: str) -> QueryRecord:
        return self.table[query_key]

    def build_request(self, text: str) -> tuple[ParsedCommand, QueryRecord, VrWriteRequest]:
        parsed = self.parse(text)
        record = self.resolve(parsed.query_key)
        request = VrWriteRequest(
            start_vr=self.command_vr_start,
            values=tuple(record.registers),
        )
        return parsed, record, request

    def build_request_from_key(self, query_key: str) -> tuple[QueryRecord, VrWriteRequest]:
        record = self.resolve(query_key)
        request = VrWriteRequest(
            start_vr=self.command_vr_start,
            values=tuple(record.registers),
        )
        return record, request

    def build_fixed_command(self, text: str) -> tuple[ParsedCommand, QueryRecord, FixedVrCommand]:
        parsed = self.parse(text)
        record = self.resolve(parsed.query_key)
        command = FixedVrCommand(
            trigger_vr=self.trigger_vr,
            trigger_value=1.0,
            payload_start_vr=self.command_vr_start,
            payload_values=tuple(record.registers),
        )
        return parsed, record, command

    def build_fixed_command_from_key(self, query_key: str) -> tuple[QueryRecord, FixedVrCommand]:
        record = self.resolve(query_key)
        command = FixedVrCommand(
            trigger_vr=self.trigger_vr,
            trigger_value=1.0,
            payload_start_vr=self.command_vr_start,
            payload_values=tuple(record.registers),
        )
        return record, command

    def build_status_read(self, count: int = 5) -> VrReadRequest:
        return VrReadRequest(start_vr=self.status_vr_start, count=count)

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

    def build_flow_requests(self, flow_name: str) -> list[tuple[str, QueryRecord, FixedVrCommand]]:
        flow = self.get_flow(flow_name)
        result: list[tuple[str, QueryRecord, FixedVrCommand]] = []
        for step in flow.steps:
            record, command = self.build_fixed_command_from_key(step)
            result.append((step, record, command))
        return result
