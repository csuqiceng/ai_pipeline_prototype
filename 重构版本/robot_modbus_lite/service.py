"""协议服务层，将查询表记录转换为控制器读写请求。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .command_parser import parse_command
from .flow_registry import FlowEntry, FlowRegistry
from .flow_store import flow_definition_to_entry, flow_entry_to_definition, load_flows_json, save_flows_json
from .models import (
    FixedVrCommand,
    FlowDefinition,
    ParsedCommand,
    QueryRecord,
    SixAxisAlarmDetail,
    SixAxisCommand,
    SixAxisRealtimeData,
    SixAxisStatus,
    SixAxisSystemState,
    StandardProtocolCommand,
    StandardMirrorAck,
    StandardRealtimeStatus,
    StandardProtocolStatus,
    VrReadRequest,
    VrWriteRequest,
)
from .permission_service import PermissionService
from .query_table import load_query_table


class RobotModbusService:
    """协议构建和解析服务。"""
    FUNC_LABELS = {
        11: "Func11 多点插补",
        104: "Func104 系统控制",
        106: "Func106 关节点动",
        107: "Func107 虚拟轴点动",
        108: "Func108 直线插补/PTP",
        109: "Func109 定时检测",
        110: "Func110 延时",
        120: "Func120 IO控制",
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
        flow_registry_path: str | Path | None = None,
    ) -> None:
        """初始化对象。"""
        self.csv_path = Path(csv_path)
        self.start_register = start_register
        self.trigger_vr = command_vr_start
        self.command_vr_start = command_vr_start + 1
        self.status_vr_start = status_vr_start
        self.table = table if table is not None else load_query_table(self.csv_path)
        self.flows_path = Path(flows_path) if flows_path else None
        self.flow_registry_path = Path(flow_registry_path) if flow_registry_path else self._default_flow_registry_path()
        self.flow_registry = self._load_flow_registry()
        self.flows = self._load_flows()
        self.standard_status_vr_start = 16
        self.standard_mirror_vr_start = 500
        self.standard_ack_vr = 516
        self.standard_exec_vr = 517
        self.standard_monitor_vr_start = 700

    def reload(self) -> None:
        """处理相关数据。"""
        self.table = load_query_table(self.csv_path)
        if self.flow_registry_path:
            self.flow_registry = self._load_flow_registry()
        self.flows = self._load_flows()

    def parse(self, text: str) -> ParsedCommand:
        """解析相关数据。"""
        return parse_command(text, self.table)

    def resolve(self, query_key: str) -> QueryRecord:
        """解析相关数据。"""
        return self.table[query_key]

    def build_request(self, text: str) -> tuple[ParsedCommand, QueryRecord, VrWriteRequest]:
        """构建请求。"""
        parsed = self.parse(text)
        record = self.resolve(parsed.query_key)
        request = VrWriteRequest(
            start_vr=self.command_vr_start,
            values=tuple(record.registers),
        )
        return parsed, record, request

    def build_fixed_command_from_key(self, query_key: str) -> tuple[QueryRecord, FixedVrCommand]:
        """构建命令。"""
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
        """构建命令记录。"""
        return self._build_standard_command_from_record(record, task_id=task_id)

    def build_standard_status_read(self) -> VrReadRequest:
        """构建状态。"""
        return VrReadRequest(start_vr=self.standard_status_vr_start, count=10)

    def parse_standard_status(self, values: Iterable[float]) -> StandardProtocolStatus:
        """解析状态。"""
        return StandardProtocolStatus.from_vr_values(list(values))

    def build_standard_mirror_ack_read(self) -> VrReadRequest:
        """构建相关数据。"""
        return VrReadRequest(start_vr=self.standard_mirror_vr_start, count=17)

    def parse_standard_mirror_ack(self, values: Iterable[float]) -> StandardMirrorAck:
        """解析相关数据。"""
        return StandardMirrorAck.from_vr_values(list(values), command_length=16)

    def build_standard_execute_trigger_write(self, trigger_value: float = 1.0) -> VrWriteRequest:
        """构建相关数据。"""
        return VrWriteRequest(start_vr=self.standard_exec_vr, values=(trigger_value,))

    def build_standard_monitor_read(self) -> VrReadRequest:
        """构建相关数据。"""
        return VrReadRequest(start_vr=self.standard_monitor_vr_start, count=20)

    def parse_standard_realtime_status(self, values: Iterable[float]) -> StandardRealtimeStatus:
        """解析实时数据状态。"""
        return StandardRealtimeStatus.from_vr_values(list(values))

    def build_standard_system_command(
        self,
        *,
        code: int,
        task_id: int = 1001,
        desc: str = "",
    ) -> StandardProtocolCommand:
        """构建系统命令。"""
        return StandardProtocolCommand(
            task_id=task_id,
            cmd=self.FUNC_LABELS.get(code, f"FUNC_{code}"),
            code=code,
            safety_level=5,
            desc=desc or self.FUNC_LABELS.get(code, f"FUNC_{code}"),
        )

    def list_flow_names(self) -> list[str]:
        """处理流程。"""
        return sorted(self.flows)

    def get_flow(self, name: str) -> FlowDefinition:
        """获取流程。"""
        return self.flows[name]

    def save_flow(self, flow: FlowDefinition) -> None:
        """保存流程。"""
        self.flows[flow.name] = flow
        if self.flows_path:
            save_flows_json(self.flows_path, self.flows)
        if self.flow_registry is not None:
            entry = flow_definition_to_entry(flow)
            existing = self.flow_registry.get(flow.name)
            if existing is None:
                self.flow_registry.add(entry)
            else:
                self.flow_registry.update(
                    flow.name,
                    steps=entry.steps,
                    step_delay_ms=entry.step_delay_ms,
                    create_draft=existing.confirmed,
                )

    def save_flow_entry(self, entry: FlowEntry) -> None:
        """保存结构化流程并同步旧视图文件。"""
        self.flows[entry.name] = flow_entry_to_definition(entry)
        if self.flows_path:
            save_flows_json(self.flows_path, self.flows)
        if self.flow_registry is None:
            return
        existing = self.flow_registry.get(entry.name)
        if existing is None:
            self.flow_registry.add(entry)
            return
        self.flow_registry.update(
            entry.name,
            description=entry.description,
            steps=entry.steps,
            step_delay_ms=entry.step_delay_ms,
            rehearsal_spd=entry.rehearsal_spd,
            create_draft=existing.confirmed,
        )

    def delete_flow(self, name: str) -> None:
        """删除流程。"""
        if name in self.flows:
            del self.flows[name]
            if self.flows_path:
                save_flows_json(self.flows_path, self.flows)
        if self.flow_registry is not None and self.flow_registry.get(name) is not None:
            self.flow_registry.remove(name)

    def get_flow_entry(self, name: str) -> FlowEntry | None:
        if self.flow_registry is None:
            return None
        return self.flow_registry.get(name)

    def get_effective_flow(self, name: str) -> FlowDefinition | FlowEntry:
        entry = self.get_flow_entry(name)
        if entry is not None:
            return entry
        return self.get_flow(name)

    def confirm_flow(self, name: str) -> tuple[bool, str]:
        if self.flow_registry is None:
            return False, "流程注册表未启用"
        return self.flow_registry.confirm(name)

    def start_flow_rehearsal(self, name: str) -> tuple[bool, str]:
        if self.flow_registry is None:
            return False, "流程注册表未启用"
        return self.flow_registry.start_rehearsal(name)

    def _default_flow_registry_path(self) -> Path | None:
        if self.flows_path is None:
            return None
        return self.flows_path.with_name("flow_registry.json")

    def _load_flow_registry(self) -> FlowRegistry | None:
        if self.flow_registry_path is None:
            return None
        return FlowRegistry(self.flow_registry_path, permission=PermissionService("engineer"))

    def _load_flows(self) -> dict[str, FlowDefinition]:
        flows = load_flows_json(self.flows_path) if self.flows_path else {}
        if self.flow_registry is not None:
            for entry in self.flow_registry.list_all():
                flows[entry.name] = flow_entry_to_definition(entry)
        return flows

    def _build_standard_command_from_record(
        self,
        record: QueryRecord,
        *,
        task_id: int,
    ) -> StandardProtocolCommand:
        """构建命令记录。"""
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
            speed_percent=int(round(record.spd_pct_value())),
            acc_percent=int(round(record.acc_pct_value())),
            safety_level=record.safety_level,
            desc=record.description or record.query_key,
        )

    def build_six_command_from_record(
        self,
        record: QueryRecord,
    ) -> SixAxisCommand:
        """构建六轴命令记录。"""
        params = record.params
        func_num = record.func_num

        if func_num == 11:
            raise ValueError("当前 zbasic-GLM 协议不支持 Func11，请改用 Func112 或已验证流程。")

        if func_num == 104:
            estop_ctrl = int(float(params.get("estop_ctrl", 0)))
            pause_ctrl = int(float(params.get("pause_ctrl", 0)))
            cancel_ctrl = int(float(params.get("cancel_ctrl", 0)))
            reset_ctrl = int(float(params.get("reset_ctrl", 0)))
            if not any((estop_ctrl, pause_ctrl, cancel_ctrl, reset_ctrl)) and "stop_mode" in params:
                if int(float(params.get("stop_mode", 0))) == 0:
                    estop_ctrl = 1
                else:
                    pause_ctrl = 1
            return SixAxisCommand(
                func_num=104,
                desc=record.description or record.query_key,
                stop_mode=int(float(params.get("stop_mode", 0))),
                estop_ctrl=estop_ctrl,
                pause_ctrl=pause_ctrl,
                cancel_ctrl=cancel_ctrl,
                reset_ctrl=reset_ctrl,
            )

        if func_num in (106, 107):
            return SixAxisCommand(
                func_num=func_num,
                desc=record.description or record.query_key,
                axis_no=int(float(params.get("axis_no", 0))),
                pos_val=float(params.get("pos_val", 0.0)),
                spd_pct=float(params.get("spd_pct", 0.0)),
                acc_pct=float(params.get("acc_pct", 0.0)),
                dec_pct=float(params.get("dec_pct", 0.0)),
                fuzzy_pos=int(float(params.get("fuzzy_pos", 0))),
                fuzzy_spd=int(float(params.get("fuzzy_spd", 0))),
                fuzzy_acc=int(float(params.get("fuzzy_acc", 0))),
                fuzzy_dec=int(float(params.get("fuzzy_dec", 0))),
                stop_cmd=int(float(params.get("stop_cmd", 0))),
            )

        if func_num in (8, 102, 108, 112):
            return SixAxisCommand(
                func_num=func_num,
                desc=record.description or record.query_key,
                target_x=float(params.get("target_x", 0.0)),
                target_y=float(params.get("target_y", 0.0)),
                target_z=float(params.get("target_z", 0.0)),
                target_rx=float(params.get("target_rx", 0.0)),
                target_ry=float(params.get("target_ry", 0.0)),
                target_rz=float(params.get("target_rz", 0.0)),
                spd_pct=float(params.get("spd_pct", 0.0)),
                acc_pct=float(params.get("acc_pct", 0.0)),
                dec_pct=float(params.get("dec_pct", 0.0)),
                stop_cmd=int(float(params.get("stop_cmd", 0))),
                fuzzy_pos=int(float(params.get("fuzzy_pos", 0))),
                fuzzy_spd=int(float(params.get("fuzzy_spd", 0))),
                fuzzy_acc=int(float(params.get("fuzzy_acc", 0))),
                fuzzy_dec=int(float(params.get("fuzzy_dec", 0))),
                move_type=int(float(params.get("move_type", 0))),
            )

        if func_num == 11:
            points_raw = params.get("points", ())
            points: list[tuple[float, float, float, float, float, float]] = []
            if isinstance(points_raw, (list, tuple)):
                for point in points_raw:
                    if isinstance(point, (list, tuple)):
                        padded = list(point[:6]) + [0.0] * max(0, 6 - len(point))
                        points.append(tuple(float(value) for value in padded[:6]))
            point_count = int(float(params.get("point_count", len(points))))
            return SixAxisCommand(
                func_num=11,
                desc=record.description or record.query_key,
                point_count=point_count,
                interp_points=tuple(points[:point_count]),
                spd_pct=float(params.get("spd_pct", 0.0)),
                acc_pct=float(params.get("acc_pct", 0.0)),
                dec_pct=float(params.get("dec_pct", 0.0)),
            )

        if func_num == 109:
            return SixAxisCommand(
                func_num=109,
                desc=record.description or record.query_key,
                delay_sec=float(params.get("delay_sec", params.get("delay", 0.0))),
            )

        if func_num == 110:
            return SixAxisCommand(
                func_num=110,
                desc=record.description or record.query_key,
                delay_sec=float(params.get("delay_sec", params.get("delay", 0.0))),
            )

        if func_num == 120:
            return SixAxisCommand(
                func_num=120,
                desc=record.description or record.query_key,
                io_no=int(float(params.get("io_no", 0))),
                io_action=int(float(params.get("io_action", params.get("action", 0)))),
            )

        raise ValueError(f"不支持的六轴函数号: 查询键={record.query_key}，实际={func_num}")

    def build_six_system_command(self, code: int) -> SixAxisCommand:
        """构建六轴系统命令。"""
        if code == 4002:
            return SixAxisCommand(func_num=104, desc="SYS_ESTOP", estop_ctrl=1)
        if code == 4003:
            return SixAxisCommand(func_num=104, desc="SYS_PAUSE", pause_ctrl=1)
        if code == 4004:
            return SixAxisCommand(func_num=104, desc="SYS_RESUME", pause_ctrl=2)
        if code == 4005:
            return SixAxisCommand(func_num=104, desc="SYS_CANCEL", cancel_ctrl=1)
        if code in (1008, 4001):
            return SixAxisCommand(func_num=104, desc="ALARM_RESET", reset_ctrl=1)
        if code in (6001, 6002):
            raise ValueError(f"系统命令码 {code} 是旧系统本地流程命令，V4.3 不再下发到控制器")
        raise ValueError(f"系统命令码 {code} 当前未实现")

    def build_six_status_read(self) -> VrReadRequest:
        """构建六轴状态。"""
        return VrReadRequest(start_vr=34, count=1)

    def parse_six_status(self, values: list[float] | list[int], func_num: int | None = None) -> SixAxisStatus:
        """解析六轴状态。"""
        return SixAxisStatus.from_value(values[0] if values else 0, func_num=func_num)

    def build_six_system_state_read(self) -> VrReadRequest:
        """构建六轴系统状态。"""
        return VrReadRequest(start_vr=36, count=1)

    def parse_six_system_state(self, values: list[float] | list[int]) -> int:
        """解析六轴系统状态。"""
        return int(values[0] if values else 0)

    def parse_six_system_state_detail(self, values: list[float] | list[int]) -> SixAxisSystemState:
        """解析六轴系统状态位。"""
        return SixAxisSystemState.from_value(values[0] if values else 0)

    def build_six_alarm_detail_read(self) -> VrReadRequest:
        """构建六轴报警详情。"""
        return VrReadRequest(start_vr=38, count=1)

    def parse_six_alarm_detail(self, values: list[float] | list[int]) -> SixAxisAlarmDetail:
        """解析六轴报警详情。"""
        return SixAxisAlarmDetail.from_value(values[0] if values else 0)

    def build_six_current_func_read(self) -> VrReadRequest:
        """构建六轴当前函数。"""
        return VrReadRequest(start_vr=324, count=1)

    def build_six_accept_confirm_read(self) -> VrReadRequest:
        """构建六轴命令接受确认读取。"""
        return VrReadRequest(start_vr=312, count=1)

    def parse_six_current_func(self, values: list[float] | list[int]) -> int:
        """解析六轴当前函数。"""
        return int(values[0] if values else 0.0)

    def build_six_motion_state_read(self) -> VrReadRequest:
        """构建六轴运动状态。"""
        return VrReadRequest(start_vr=56, count=1)

    def parse_six_motion_state(self, values: list[float]) -> int:
        """解析六轴运动状态。"""
        return int(values[0] if values else 0.0)

    def build_six_axis_status_read(self) -> VrReadRequest:
        """构建六轴轴状态。"""
        return VrReadRequest(start_vr=200, count=12)

    def parse_six_axis_status(self, values: list[float]) -> list[int]:
        """解析六轴轴状态。"""
        return [int(value) for value in values[:12]]

    def build_six_motion_type_read(self) -> VrReadRequest:
        """构建六轴运动。"""
        return VrReadRequest(start_vr=240, count=12)

    def parse_six_motion_type(self, values: list[float]) -> list[int]:
        """解析六轴运动。"""
        return [int(value) for value in values[:12]]

    def build_six_hmi_command_read(self) -> VrReadRequest:
        """构建六轴命令。"""
        return VrReadRequest(start_vr=720, count=17)

    def build_six_hmi_command_write(self, command: SixAxisCommand) -> list[VrWriteRequest]:
        """构建六轴命令。"""
        values_by_addr: dict[int, float] = {}
        for request in command.to_func_writes():
            for index, value in enumerate(request.values):
                values_by_addr[request.start_vr + index * 2] = float(value)
        values = [values_by_addr.get(addr, 0.0) for addr in range(0, 34, 2)]
        return [VrWriteRequest(start_vr=720, values=tuple(values))]

    def build_six_hmi_local_settings_read(self) -> VrReadRequest:
        """构建六轴本地。"""
        return VrReadRequest(start_vr=1800, count=8)

    def build_six_joint_feedback_read(self) -> VrReadRequest:
        """构建六轴关节反馈。"""
        return VrReadRequest(start_vr=1600, count=6)

    def build_six_pose_feedback_read(self) -> VrReadRequest:
        """构建六轴位姿反馈。"""
        return VrReadRequest(start_vr=1612, count=6)

    def build_six_joint_dpos_read(self) -> VrReadRequest:
        """构建六轴关节指令位置反馈。"""
        return VrReadRequest(start_vr=1500, count=6)

    def build_six_pose_dpos_read(self) -> VrReadRequest:
        """构建六轴笛卡尔指令位置反馈。"""
        return VrReadRequest(start_vr=1512, count=6)

    def build_six_safety_limits_read(self) -> VrReadRequest:
        """构建六轴。"""
        return VrReadRequest(start_vr=1700, count=22)

    def build_six_safety_limits_write(self, config) -> VrWriteRequest:
        """构建六轴。"""
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
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            ),
        )

    def parse_six_safety_limits(self, values: list[float]) -> dict[str, float]:
        """解析六轴。"""
        padded = list(values[:22]) + [0.0] * max(0, 22 - len(values))
        return {
            "safe_r_min": float(padded[0]),
            "safe_r_max": float(padded[1]),
            "safe_z_min": float(padded[2]),
            "safe_z_max": float(padded[3]),
            "safe_speed_max": float(padded[4]),
            "safe_acc_max": float(padded[5]),
            "safe_dec_max": float(padded[6]),
            "joint_speed_pct": float(padded[10]),
            "joint_acc_pct": float(padded[11]),
            "joint_dec_pct": float(padded[12]),
            "cartesian_speed_pct": float(padded[13]),
            "cartesian_acc_pct": float(padded[14]),
            "cartesian_dec_pct": float(padded[15]),
            "pose_upper_angle": float(padded[16]),
            "pose_lower_angle": float(padded[17]),
            "pose_cw_angle": float(padded[18]),
            "pose_ccw_angle": float(padded[19]),
            "current_r3d": float(padded[20]),
            "current_z": float(padded[21]),
            "reserved": [float(value) for value in padded[7:10]],
        }

    def build_six_realtime_xyz_read(self) -> VrReadRequest:
        """构建六轴实时数据。"""
        return self.build_six_pose_feedback_read()

    def parse_six_realtime(self, joint_vals: list[float], xyz_vals: list[float]) -> SixAxisRealtimeData:
        """解析六轴实时数据。"""
        return SixAxisRealtimeData.from_joints_and_xyz(joint_vals, xyz_vals)
