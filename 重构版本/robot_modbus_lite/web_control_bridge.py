"""Controlled bridge boundary between Web API and robot control runtime."""

from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from threading import Lock
from typing import Any
from uuid import uuid4

from .models import QueryRecord
from .runtime_paths import resolve_runtime_data_file
from .service import RobotModbusService
from .six_axis_executor import SixAxisExecutionService
from .system_config import load_system_config


@dataclass(frozen=True)
class BridgeResult:
    accepted: bool
    dispatch_id: str
    mode: str
    message: str
    detail: dict[str, Any] | None = None


class WebControlBridge:
    """Single-entry bridge for future Qt/service control calls.

    The initial implementation is dry-run only. It enforces the shape of the
    bridge: one public submission method, one lock, and one internal queue.
    Later B5 work can attach a Qt main-window invoker or pure service runtime
    behind this class without changing Web API routes.
    """

    def __init__(
        self,
        *,
        mode: str = "dry_run",
        service: RobotModbusService | None = None,
        controller_host: str = "127.0.0.1",
    ) -> None:
        self._mode = mode
        self._service = service
        self._controller_host = controller_host
        self._lock = Lock()
        self._queue: Queue[dict[str, Any]] = Queue()
        self._mock_client: Any | None = None

    @property
    def dry_run(self) -> bool:
        return self._mode == "dry_run"

    @property
    def mode(self) -> str:
        return self._mode

    @classmethod
    def from_runtime_files(cls, *, mode: str = "dry_run", controller_host: str = "127.0.0.1") -> "WebControlBridge":
        normalized = mode if mode in {"dry_run", "service", "mock_controller"} else "dry_run"
        service = None
        if normalized in {"service", "mock_controller"}:
            query_path = resolve_runtime_data_file("query_table.json")
            flows_path = resolve_runtime_data_file("flows.json")
            service = RobotModbusService(query_path, flows_path=flows_path)
        return cls(mode=normalized, service=service, controller_host=controller_host)

    def status(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "queue_size": self._queue.qsize(),
            "entrypoints": ["submit_plan", "submit_template", "submit_flow"],
            "service_attached": self._service is not None,
            "controller_host": self._controller_host if self._mode == "mock_controller" else None,
            "mock_controller_connected": bool(getattr(self._mock_client, "connected", False)),
        }

    def submit_plan(self, plan: dict[str, Any]) -> BridgeResult:
        return self._submit({"kind": "plan", "payload": plan})

    def submit_template(self, record: dict[str, Any]) -> BridgeResult:
        detail = self._build_template_detail(record) if self._service is not None else None
        if self._mode == "mock_controller":
            detail = {**(detail or {}), "execution": self._execute_mock_template(record)}
        return self._submit({"kind": "template", "payload": record, "detail": detail}, detail=detail)

    def submit_flow(self, flow: dict[str, Any], *, mode: str = "start") -> BridgeResult:
        detail = self._build_flow_detail(flow, mode=mode) if self._service is not None else None
        if self._mode == "mock_controller":
            detail = {**(detail or {}), "execution": self._execute_mock_flow(flow, mode=mode)}
        return self._submit({"kind": "flow", "mode": mode, "payload": flow, "detail": detail}, detail=detail)

    def reset_mock_controller(self) -> None:
        with self._lock:
            if self._mock_client is not None:
                try:
                    self._mock_client.disconnect()
                except Exception:
                    pass
            self._mock_client = None

    def _submit(self, item_payload: dict[str, Any], *, detail: dict[str, Any] | None = None) -> BridgeResult:
        with self._lock:
            dispatch_id = f"web_dispatch_{uuid4().hex[:10]}"
            item = {"dispatch_id": dispatch_id, **item_payload}
            self._queue.put(item)
            if self._mode == "dry_run":
                return BridgeResult(
                    accepted=True,
                    dispatch_id=dispatch_id,
                    mode="dry_run",
                    message="桥接层已接收计划。当前为 dry-run，不会真实下发控制器。",
                    detail=detail,
                )
            if self._mode == "service":
                return BridgeResult(
                    accepted=True,
                    dispatch_id=dispatch_id,
                    mode="service",
                    message="桥接层已完成服务层命令构建。当前未连接真实控制器。",
                    detail=detail,
                )
            if self._mode == "mock_controller":
                execution = (detail or {}).get("execution", {})
                ok = bool(execution.get("ok", True)) if isinstance(execution, dict) else True
                return BridgeResult(
                    accepted=ok,
                    dispatch_id=dispatch_id,
                    mode="mock_controller",
                    message="桥接层已通过模拟控制器执行六轴链路。" if ok else "模拟控制器执行失败。",
                    detail=detail,
                )
            return BridgeResult(
                accepted=False,
                dispatch_id=dispatch_id,
                mode=self._mode,
                message="真实控制桥接尚未绑定运行时实例。",
                detail=detail,
            )

    def _build_template_detail(self, record_payload: dict[str, Any]) -> dict[str, Any]:
        if self._service is None:
            return {}
        record = QueryRecord(
            query_key=str(record_payload.get("query_key", "")),
            func_num=int(record_payload.get("func_num", 0)),
            params=dict(record_payload.get("params") or {}),
            keywords=str(record_payload.get("keywords", "")),
            description=str(record_payload.get("description", "")),
            safety_level=int(record_payload.get("safety_level", 5)),
        )
        six_command = self._service.build_six_command_from_record(record)
        writes = [self._write_request_payload(request) for request in [*six_command.to_func_writes(), six_command.to_trigger_write()]]
        return {
            "query_key": record.query_key,
            "func_num": record.func_num,
            "slot": self._command_slot(six_command.func_num),
            "writes": writes,
            "expected_echo": [{"addr": addr, "value": value} for addr, value in six_command.expected_echo_points()],
        }

    def _build_flow_detail(self, flow_payload: dict[str, Any], *, mode: str) -> dict[str, Any]:
        if self._service is None:
            return {}
        steps = [str(step) for step in flow_payload.get("steps", []) if str(step)]
        step_details = []
        for step in steps:
            record = self._service.resolve(step)
            step_details.append(self._build_template_detail(record.to_dict()))
        return {
            "flow_name": str(flow_payload.get("name", "")),
            "mode": mode,
            "step_delay_ms": int(flow_payload.get("step_delay_ms", 0)),
            "step_count": len(step_details),
            "steps": step_details,
        }

    def _execute_mock_template(self, record_payload: dict[str, Any]) -> dict[str, Any]:
        if self._service is None:
            return {"ok": False, "error": "service_not_attached"}
        record = QueryRecord(
            query_key=str(record_payload.get("query_key", "")),
            func_num=int(record_payload.get("func_num", 0)),
            params=dict(record_payload.get("params") or {}),
            keywords=str(record_payload.get("keywords", "")),
            description=str(record_payload.get("description", "")),
            safety_level=int(record_payload.get("safety_level", 5)),
        )
        return self._execute_mock_records([record])

    def _execute_mock_flow(self, flow_payload: dict[str, Any], *, mode: str) -> dict[str, Any]:
        if self._service is None:
            return {"ok": False, "error": "service_not_attached"}
        steps = [str(step) for step in flow_payload.get("steps", []) if str(step)]
        records = [self._service.resolve(step) for step in steps]
        return self._execute_mock_records(records)

    def _execute_mock_records(self, records: list[QueryRecord]) -> dict[str, Any]:
        if self._service is None:
            return {"ok": False, "error": "service_not_attached"}
        try:
            axis_ranges = load_system_config(resolve_runtime_data_file("system_config.json"))
            client = self._get_mock_client(axis_ranges)
            executor = SixAxisExecutionService(self._service, axis_ranges)
            results = []
            for record in records:
                result = executor.execute_record(client, record)
                results.append(result.__dict__)
                if not result.ok:
                    return {"ok": False, "results": results, "error": result.error, "snapshot": self._mock_snapshot(client)}
            return {"ok": True, "results": results, "snapshot": self._mock_snapshot(client)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}

    def _get_mock_client(self, axis_ranges):
        if self._mock_client is None or not getattr(self._mock_client, "connected", False):
            from mock_controller import MockZMotionVrClient

            self._mock_client = MockZMotionVrClient(
                host=self._controller_host,
                axis_ranges=axis_ranges.to_dict(),
                connect_delay=0,
            )
            self._mock_client.connect()
        return self._mock_client

    @staticmethod
    def _mock_snapshot(client) -> dict[str, Any]:
        return client.snapshot() if hasattr(client, "snapshot") else {}

    @staticmethod
    def _write_request_payload(request) -> dict[str, Any]:
        return {"start_vr": int(request.start_vr), "values": [float(value) for value in request.values]}

    @staticmethod
    def _command_slot(func_num: int) -> str:
        if func_num in {11, 106, 107, 108, 109}:
            return "motion"
        if func_num in {110, 120}:
            return "program"
        if func_num == 104:
            return "system"
        return "unknown"
