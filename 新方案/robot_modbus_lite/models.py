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
class VrWriteRequest:
    start_vr: int
    values: tuple[float, ...]


@dataclass(frozen=True)
class VrReadRequest:
    start_vr: int
    count: int


@dataclass(frozen=True)
class FixedVrCommand:
    trigger_vr: int
    trigger_value: float
    payload_start_vr: int
    payload_values: tuple[float, ...]

    def preview_dict(self) -> dict:
        return {
            "trigger_vr": self.trigger_vr,
            "trigger_value": self.trigger_value,
            "payload_start_vr": self.payload_start_vr,
            "payload_values": list(self.payload_values),
        }


@dataclass(frozen=True)
class FlowDefinition:
    name: str
    steps: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)
