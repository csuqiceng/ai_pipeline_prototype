from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .command_parser import parse_command
from .flow_store import load_flows_json, save_flows_json
from .models import (
    FixedVrCommand,
    FlowDefinition,
    ParsedCommand,
    QueryRecord,
    StandardProtocolCommand,
    StandardMirrorAck,
    StandardRealtimeStatus,
    StandardProtocolStatus,
    V30Command,
    V30Status,
    V30RealtimeData,
    VrReadRequest,
    VrWriteRequest,
)
from .query_table import load_query_table


class RobotModbusService:
    STANDARD_CMD_NAMES = {
        1001: "MOVE_ABS",
        1002: "MOVE_REL",
        1003: "HOME",
        1004: "GRIP_SET",
        1005: "DOOR_CTRL",
        1006: "WAIT_MS",
        1007: "CHECK_IN",
        1008: "EMG_RESET",
        4001: "SYS_RESET",
        4002: "SYS_ESTOP",
        4003: "SYS_PAUSE",
        4004: "SYS_RESUME",
        5001: "FIXED_FUNC",
        6001: "AUTO_START",
        6002: "AUTO_STOP",
    }
    LEGACY_FUNCTION_ID_TO_STANDARD = {
        1: 1003,
        2: 1003,
        3: 1001,
        4: 1001,
        5: 1001,
        6: 1001,
        7: 1001,
        8: 1002,
        9: 1004,
        10: 1004,
        11: 1006,
    }

    def __init__(
        self,
        csv_path: str | Path,
        start_register: int = 0,
        flows_path: str | Path | None = None,
        *,
        command_vr_start: int = 500,
        status_vr_start: int = 600,
        table: dict[str, QueryRecord] | None = None,
    ) -> None:
        self.csv_path = Path(csv_path)
        self.start_register = start_register
        self.trigger_vr = command_vr_start
        self.command_vr_start = command_vr_start + 1
        self.status_vr_start = status_vr_start
        self.table = table if table is not None else load_query_table(self.csv_path)
        self.flows_path = Path(flows_path) if flows_path else None
        self.flows = load_flows_json(self.flows_path) if self.flows_path else {}
        self.standard_command_vr_start = 0
        self.standard_status_vr_start = 16
        self.standard_mirror_vr_start = 500
        self.standard_ack_vr = 516
        self.standard_exec_vr = 517
        self.standard_monitor_vr_start = 700

    def reload(self) -> None:
        if self.flows_path:
            self.flows = load_flows_json(self.flows_path)

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

    def build_standard_command(
        self,
        text: str,
        *,
        task_id: int = 1001,
    ) -> tuple[ParsedCommand, QueryRecord, StandardProtocolCommand]:
        parsed = self.parse(text)
        record = self.resolve(parsed.query_key)
        command = self.build_standard_command_from_record(record, task_id=task_id)
        return parsed, record, command

    def build_standard_command_from_key(
        self,
        query_key: str,
        *,
        task_id: int = 1001,
    ) -> tuple[QueryRecord, StandardProtocolCommand]:
        record = self.resolve(query_key)
        command = self.build_standard_command_from_record(record, task_id=task_id)
        return record, command

    def build_standard_command_from_record(
        self,
        record: QueryRecord,
        *,
        task_id: int = 1001,
    ) -> StandardProtocolCommand:
        return self._build_standard_command_from_record(record, task_id=task_id)

    def build_standard_status_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=self.standard_status_vr_start, count=10)

    def parse_standard_status(self, values: Iterable[float]) -> StandardProtocolStatus:
        return StandardProtocolStatus.from_vr_values(list(values))

    def build_standard_mirror_ack_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=self.standard_mirror_vr_start, count=17)

    def parse_standard_mirror_ack(self, values: Iterable[float]) -> StandardMirrorAck:
        return StandardMirrorAck.from_vr_values(list(values), command_length=16)

    def build_standard_execute_trigger_write(self, trigger_value: float = 1.0) -> VrWriteRequest:
        return VrWriteRequest(start_vr=self.standard_exec_vr, values=(trigger_value,))

    def build_standard_monitor_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=self.standard_monitor_vr_start, count=20)

    def parse_standard_realtime_status(self, values: Iterable[float]) -> StandardRealtimeStatus:
        return StandardRealtimeStatus.from_vr_values(list(values))

    def build_standard_system_command(
        self,
        *,
        code: int,
        task_id: int = 1001,
        desc: str = "",
    ) -> StandardProtocolCommand:
        return StandardProtocolCommand(
            task_id=task_id,
            cmd=self.STANDARD_CMD_NAMES.get(code, f"CMD_{code}"),
            code=code,
            safety_level=5,
            desc=desc or self.STANDARD_CMD_NAMES.get(code, f"CMD_{code}"),
        )

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

    def _build_standard_command_from_record(
        self,
        record: QueryRecord,
        *,
        task_id: int,
    ) -> StandardProtocolCommand:
        params = record.to_standard_params()
        standard_code = self._resolve_standard_code(record)
        speed_percent = self._resolve_speed_percent(record, params, standard_code)
        acc_percent = self._resolve_acc_percent(record, params, standard_code)
        return StandardProtocolCommand(
            task_id=task_id,
            cmd=self._resolve_standard_cmd_name(record, standard_code),
            code=standard_code,
            x=float(params["x"]),
            y=float(params["y"]),
            z=float(params["z"]),
            rx=float(params["rx"]),
            ry=float(params["ry"]),
            rz=float(params["rz"]),
            speed_percent=speed_percent,
            acc_percent=acc_percent,
            pos_id=int(params["posId"]),
            device_id=int(params["deviceId"]),
            io_grip=self._resolve_io_grip(record, params),
            io_door=self._resolve_io_door(record, params),
            ext_p1=self._resolve_ext_p1(record, params, standard_code),
            ext_p2=float(params["extP2"]),
            safety_level=record.safety_level,
            desc=record.description or record.query_key,
        )

    def _resolve_standard_code(self, record: QueryRecord) -> int:
        if record.function_id >= 1000:
            return record.function_id
        return self.LEGACY_FUNCTION_ID_TO_STANDARD.get(record.function_id, record.function_id)

    def _resolve_standard_cmd_name(self, record: QueryRecord, standard_code: int) -> str:
        if standard_code in self.STANDARD_CMD_NAMES:
            return self.STANDARD_CMD_NAMES[standard_code]
        return record.function_name.upper()

    def _resolve_io_grip(self, record: QueryRecord, params: dict[str, float | int]) -> int:
        if record.io_grip:
            return int(params["ioGrip"])
        if record.function_id == 9:
            return 1
        if record.function_id == 10:
            return 0
        return int(params["ioGrip"])

    def _resolve_io_door(self, record: QueryRecord, params: dict[str, float | int]) -> int:
        if record.io_door:
            return int(params["ioDoor"])
        if record.function_id == 1005:
            return 1
        return int(params["ioDoor"])

    def _resolve_speed_percent(
        self,
        record: QueryRecord,
        params: dict[str, float | int],
        standard_code: int,
    ) -> int:
        if standard_code == 1003:
            return 30
        return int(round(float(params["speedPercent"])))

    def _resolve_acc_percent(
        self,
        record: QueryRecord,
        params: dict[str, float | int],
        standard_code: int,
    ) -> int:
        return int(round(float(params["accPercent"])))

    def _resolve_ext_p1(
        self,
        record: QueryRecord,
        params: dict[str, float | int],
        standard_code: int,
    ) -> float:
        if record.ext_p1:
            return float(params["extP1"])
        if standard_code == 1006:
            return float(record.registers[0])
        return float(params["extP1"])

    # ── V3.0 Modbus TCP 协议方法 ──────────────────────────────────

    # VR命令码 → V3.0函数号映射
    VR_TO_V30_MAP = {
        1001: 102,   # MOVE_ABS → 直线插补
        1002: 102,   # MOVE_REL → 直线插补(先读当前位置+偏移)
        1003: 102,   # HOME → 直线插补到原点
        1004: -1,    # GRIP_SET → 上位机写BIT口
        1005: -3,    # DOOR_CTRL → 上位机写BIT口
        1006: -2,    # WAIT_MS → 上位机本地延时
        1007: -4,    # CHECK_IN → 上位机本地无操作
        1008: 103,   # EMG_RESET → 报警清除
        4001: 103,   # SYS_RESET → 报警清除
        4002: 104,   # SYS_ESTOP → 停止(急停)
        4003: 104,   # SYS_PAUSE → 停止(慢停)
        4004: -5,    # SYS_RESUME → 上位机本地恢复状态
        5001: -6,    # FIXED_FUNC → 上位机本地无操作
        6001: -5,    # AUTO_START → 上位机本地状态切换
        6002: -5,    # AUTO_STOP → 上位机本地状态切换
    }

    def build_v30_command_from_record(
        self,
        record: QueryRecord,
        base_speed_mm: float = 3000.0,
    ) -> V30Command:
        """将查询表记录转为V3.0命令"""
        standard_code = self._resolve_standard_code(record)
        params = record.to_standard_params()
        func_num = self.VR_TO_V30_MAP.get(standard_code)
        if func_num is None:
            raise ValueError(f"命令码 {standard_code} 无V3.0映射，查询键={record.query_key}")

        if func_num == -1:
            return V30Command(func_num=-1, desc="GRIP_SET",
                              io_grip=int(params["ioGrip"]))
        if func_num == -2:
            return V30Command(func_num=-2, desc="WAIT_MS",
                              ext_p1=float(params["extP1"]))
        if func_num == -3:
            return V30Command(func_num=-3, desc="DOOR_CTRL",
                              io_door=int(params["ioDoor"]))
        if func_num < 0:
            return V30Command(func_num=func_num, desc="LOCAL")

        if func_num == 102:
            speed_pct = float(params["speedPercent"])
            speed = base_speed_mm * speed_pct / 100.0
            acc_pct = float(params["accPercent"])
            return V30Command(
                func_num=102,
                desc=record.description or record.query_key,
                x=float(params["x"]),
                y=float(params["y"]),
                z=float(params["z"]),
                rx=float(params["rx"]),
                ry=float(params["ry"]),
                rz=float(params["rz"]),
                speed=speed,
                accel=1000.0 * acc_pct / 100.0,
                decel=1000.0 * acc_pct / 100.0,
            )
        elif func_num == 103:
            return V30Command(func_num=103, desc="ALARM_CLEAR")
        elif func_num == 104:
            stop_mode = 0 if standard_code == 4002 else 1  # 4002=急停 4003=慢停
            return V30Command(func_num=104, desc="STOP", x=float(stop_mode))
        return V30Command(func_num=func_num, desc=record.query_key)

    def build_v30_system_command(self, code: int) -> V30Command:
        """构建V3.0系统命令"""
        func_num = self.VR_TO_V30_MAP.get(code)
        if func_num is None:
            raise ValueError(f"系统命令码 {code} 无V3.0映射")
        if func_num == 104:
            stop_mode = 0 if code == 4002 else 1  # 4002=急停 4003=慢停
            return V30Command(func_num=104, desc=self.STANDARD_CMD_NAMES.get(code, ""), x=float(stop_mode))
        return V30Command(func_num=func_num, desc=self.STANDARD_CMD_NAMES.get(code, ""))

    def build_v30_precheck_reads(self) -> tuple[VrReadRequest, int]:
        """返回前置检查需要的读取请求: (IEEE(34)状态读取, BIT(243)地址)"""
        return VrReadRequest(start_vr=34, count=1), 243

    def build_v30_status_read(self) -> VrReadRequest:
        """读 IEEE(34) 函数状态"""
        return VrReadRequest(start_vr=34, count=1)

    def parse_v30_status(self, values: list[float]) -> V30Status:
        """解析 IEEE(34) 状态"""
        return V30Status.from_value(values[0] if values else 0.0)

    def build_v30_realtime_read(self) -> VrReadRequest:
        """读 IEEE(1512~1517) 实时坐标 X/Y/Z/RX/RY/RZ"""
        return VrReadRequest(start_vr=1512, count=6)

    def parse_v30_realtime(self, values: list[float]) -> V30RealtimeData:
        """解析V3.0实时坐标"""
        return V30RealtimeData.from_values(values)
