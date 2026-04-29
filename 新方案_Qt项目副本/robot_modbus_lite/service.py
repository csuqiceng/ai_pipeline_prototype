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
    SixAxisAlarmDetail,
    SixAxisCommand,
    SixAxisRealtimeData,
    SixAxisStatus,
    StandardProtocolCommand,
    StandardMirrorAck,
    StandardRealtimeStatus,
    StandardProtocolStatus,
    VrReadRequest,
    VrWriteRequest,
)
from .query_table import load_query_table


class RobotModbusService:
    FUNC_LABELS = {
        104: "Func104 停止",
        106: "Func106 关节点动",
        107: "Func107 虚拟轴点动",
        108: "Func108 直线插补/PTP",
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
        self.standard_status_vr_start = 16
        self.standard_mirror_vr_start = 500
        self.standard_ack_vr = 516
        self.standard_exec_vr = 517
        self.standard_monitor_vr_start = 700

    def reload(self) -> None:
        self.table = load_query_table(self.csv_path)
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

    def build_fixed_command_from_key(self, query_key: str) -> tuple[QueryRecord, FixedVrCommand]:
        record = self.resolve(query_key)
        command = FixedVrCommand(
            trigger_vr=self.trigger_vr,
            trigger_value=1.0,
            payload_start_vr=self.command_vr_start,
            payload_values=tuple(record.registers),
        )
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
            cmd=self.FUNC_LABELS.get(code, f"FUNC_{code}"),
            code=code,
            safety_level=5,
            desc=desc or self.FUNC_LABELS.get(code, f"FUNC_{code}"),
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

    def _build_standard_command_from_record(
        self,
        record: QueryRecord,
        *,
        task_id: int,
    ) -> StandardProtocolCommand:
        pose = record.pose_tuple() or (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return StandardProtocolCommand(
            task_id=task_id,
            cmd=self.FUNC_LABELS.get(record.func_num, f"FUNC_{record.func_num}"),
            code=record.func_num,
            x=pose[0],
            y=pose[1],
            z=pose[2],
            rx=pose[3],
            ry=pose[4],
            rz=pose[5],
            speed_percent=int(round(record.speed_value())),
            acc_percent=int(round(record.acc_value())),
            safety_level=record.safety_level,
            desc=record.description or record.query_key,
        )

    def build_six_command_from_record(
        self,
        record: QueryRecord,
    ) -> SixAxisCommand:
        """将模板记录转为六轴命令，仅支持 Func104/106/107/108。"""
        params = record.params
        func_num = record.func_num

        if func_num == 104:
            return SixAxisCommand(
                func_num=104,
                desc=record.description or record.query_key,
                stop_mode=int(float(params.get("stop_mode", 0))),
            )

        if func_num in (106, 107):
            return SixAxisCommand(
                func_num=func_num,
                desc=record.description or record.query_key,
                axis_no=int(float(params.get("axis_no", 0))),
                pos_val=float(params.get("pos_val", 0.0)),
                spd=float(params.get("spd", 0.0)),
                acc_v=float(params.get("acc_v", 0.0)),
                dec_v=float(params.get("dec_v", 0.0)),
                fuzzy_pos=float(int(float(params.get("fuzzy_pos", 0)))),
                fuzzy_spd=float(int(float(params.get("fuzzy_spd", 0)))),
                fuzzy_acc=float(int(float(params.get("fuzzy_acc", 0)))),
                fuzzy_dec=float(int(float(params.get("fuzzy_dec", 0)))),
                stop_cmd=int(float(params.get("stop_cmd", 0))),
            )

        if func_num == 108:
            return SixAxisCommand(
                func_num=108,
                desc=record.description or record.query_key,
                target_x=float(params.get("target_x", 0.0)),
                target_y=float(params.get("target_y", 0.0)),
                target_z=float(params.get("target_z", 0.0)),
                target_rx=float(params.get("target_rx", 0.0)),
                target_ry=float(params.get("target_ry", 0.0)),
                target_rz=float(params.get("target_rz", 0.0)),
                spd=float(params.get("spd", 0.0)),
                acc_v=float(params.get("acc_v", 0.0)),
                dec_v=float(params.get("dec_v", 0.0)),
                stop_cmd=int(float(params.get("stop_cmd", 0))),
                fuzzy_pos=float(int(float(params.get("fuzzy_pos", 0)))),
                fuzzy_spd=float(int(float(params.get("fuzzy_spd", 0)))),
                fuzzy_acc=float(int(float(params.get("fuzzy_acc", 0)))),
                fuzzy_dec=float(int(float(params.get("fuzzy_dec", 0)))),
                move_type=int(float(params.get("move_type", 0))),
            )

        raise ValueError(f"仅支持 Func104/106/107/108，查询键={record.query_key}，实际={func_num}")

    def build_six_system_command(self, code: int) -> SixAxisCommand:
        """构建六轴系统命令"""
        if code == 4002:
            return SixAxisCommand(func_num=104, desc="SYS_ESTOP", stop_mode=0)
        if code == 4003:
            return SixAxisCommand(func_num=104, desc="SYS_PAUSE", stop_mode=1)
        if code in (1008, 4001):
            return SixAxisCommand(func_num=-10, desc="ALARM_RESET")
        if code in (4004, 6001, 6002):
            return SixAxisCommand(func_num=-5, desc=f"LOCAL_{code}")
        raise ValueError(f"系统命令码 {code} 当前未实现")

    def build_six_status_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=34, count=1)

    def parse_six_status(self, values: list[float]) -> SixAxisStatus:
        return SixAxisStatus.from_value(values[0] if values else 0.0)

    def build_six_alarm_detail_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=38, count=1)

    def parse_six_alarm_detail(self, values: list[float]) -> SixAxisAlarmDetail:
        return SixAxisAlarmDetail.from_value(values[0] if values else 0.0)

    def build_six_current_func_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=36, count=1)

    def parse_six_current_func(self, values: list[float]) -> int:
        return int(values[0] if values else 0.0)

    def build_six_motion_state_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=56, count=1)

    def parse_six_motion_state(self, values: list[float]) -> int:
        return int(values[0] if values else 0.0)

    def build_six_joint_feedback_read(self) -> VrReadRequest:
        """读 IEEE(1500~1510) 实际关节角度。"""
        return VrReadRequest(start_vr=1500, count=6)

    def build_six_pose_feedback_read(self) -> VrReadRequest:
        """读 IEEE(1512~1522) 实际笛卡尔位姿。"""
        return VrReadRequest(start_vr=1512, count=6)

    def build_six_safety_limits_read(self) -> VrReadRequest:
        return VrReadRequest(start_vr=1700, count=7)

    def build_six_safety_limits_write(self, config) -> VrWriteRequest:
        return VrWriteRequest(
            start_vr=1700,
            values=(
                float(config.safe_r_min),
                float(config.safe_r_max),
                float(config.safe_z_min),
                float(config.safe_z_max),
                float(config.safe_speed_max),
                float(config.safe_acc_max),
                float(config.safe_dec_max),
            ),
        )

    def parse_six_safety_limits(self, values: list[float]) -> dict[str, float]:
        padded = list(values[:7]) + [0.0] * max(0, 7 - len(values))
        return {
            "safe_r_min": float(padded[0]),
            "safe_r_max": float(padded[1]),
            "safe_z_min": float(padded[2]),
            "safe_z_max": float(padded[3]),
            "safe_speed_max": float(padded[4]),
            "safe_acc_max": float(padded[5]),
            "safe_dec_max": float(padded[6]),
        }

    def build_six_realtime_xyz_read(self) -> VrReadRequest:
        """读 IEEE(40~50) Func108 运行态回传位姿。"""
        return VrReadRequest(start_vr=40, count=6)

    def parse_six_realtime(self, joint_vals: list[float], xyz_vals: list[float]) -> SixAxisRealtimeData:
        """合并两组读取结果为六轴实时数据"""
        return SixAxisRealtimeData.from_joints_and_xyz(joint_vals, xyz_vals)
