from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class QueryRecord:
    query_key: str
    function_id: int
    registers: tuple[float, float, float, float, float, float, float]
    function_name: str = "movabs"
    data_format: str = "IEE"

    def payload(self) -> list[float]:
        return [float(self.function_id), *self.registers]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ParsedCommand:
    raw_text: str
    query_key: str


@dataclass(frozen=True)
class ModbusWriteRequest:
    start_register: int
    values: tuple[float, ...]


@dataclass(frozen=True)
class FlowDefinition:
    name: str
    steps: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)
