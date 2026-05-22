"""Resolve parsed atomic commands into existing QueryRecord objects."""

from __future__ import annotations

from .atomic_memory import AtomicMemory
from .atomic_models import AtomicElements, AtomicResolved
from .models import QueryRecord


class AtomicResolver:
    """Convert atomic elements into executable records or memory updates."""

    def __init__(self, memory: AtomicMemory | None = None) -> None:
        self.memory = memory or AtomicMemory()

    def resolve(self, elements: AtomicElements) -> AtomicResolved:
        family = elements.family
        if family == "memory":
            return self._resolve_memory(elements)
        if family == "joint":
            record = self._joint_record(elements)
            return self._template_result(
                elements,
                record,
                "关节轴原子动作",
                risk_level=self._motion_risk_level(record),
            )
        if family == "virtual":
            record = self._virtual_record(elements)
            return self._template_result(
                elements,
                record,
                "虚拟轴原子动作",
                risk_level=self._motion_risk_level(record),
            )
        if family == "cartesian":
            record = self._cartesian_record(elements)
            return self._template_result(elements, record, "笛卡尔空间原子动作", risk_level=self._motion_risk_level(record))
        if family == "position":
            return self._resolve_position(elements)
        if family == "history":
            return self._resolve_history(elements)
        if family == "delay":
            return self._template_result(elements, self._delay_record(elements), "延时原子动作", risk_level="low")
        if family == "io":
            return self._template_result(elements, self._io_record(elements), "IO 原子动作", risk_level="medium")
        return AtomicResolved(
            kind="unsupported",
            action_type="unknown",
            target=None,
            reason="未识别的原子函数。",
            params={"raw_text": elements.raw_text, "command_text": elements.command_text},
            requires_confirmation=False,
        )

    def _resolve_memory(self, elements: AtomicElements) -> AtomicResolved:
        name = elements.name or ""
        if name == "speed" and elements.spd_pct is not None:
            self.memory.set_speed(elements.spd_pct)
        elif name == "speed_up":
            self.memory.speed_up()
        elif name == "speed_down":
            self.memory.speed_down()
        elif name == "step_mm" and elements.step is not None:
            self.memory.set_step_mm(elements.step)
        elif name == "step_deg" and elements.step is not None:
            self.memory.set_step_deg(elements.step)
        elif name == "confirm_mode":
            mode_by_target = {0: "expert", 1: "skilled", 2: "beginner"}
            self.memory.set_confirm_mode(mode_by_target.get(int(elements.target or 2), "beginner"))
        else:
            return AtomicResolved(
                kind="unsupported",
                action_type="unknown",
                target=None,
                reason="未识别的记忆参数命令。",
                params={"name": name},
                requires_confirmation=False,
            )
        return AtomicResolved(
            kind="memory",
            action_type="memory",
            target=name,
            reason="已更新原子函数记忆参数。",
            params={
                "current_speed": self.memory.current_speed,
                "current_step_mm": self.memory.current_step_mm,
                "current_step_deg": self.memory.current_step_deg,
                "current_acc": self.memory.current_acc,
                "current_dec": self.memory.current_dec,
                "confirm_mode": self.memory.confirm_mode,
            },
            requires_confirmation=False,
        )

    def _resolve_position(self, elements: AtomicElements) -> AtomicResolved:
        name = elements.name or ""
        if ":" not in name:
            return self._unsupported(elements, "位置命令缺少位置名称。")
        op, pos_name = name.split(":", 1)
        normalized_name = pos_name.strip().upper()
        if op == "query":
            pose = self.memory.get_position(normalized_name)
            if pose is None:
                return self._unsupported(elements, f"位置{normalized_name}不存在。")
            return AtomicResolved(
                kind="query",
                action_type="query",
                target="position",
                reason=f"位置{normalized_name}坐标查询。",
                params={"position_name": normalized_name, "pose": pose},
                requires_confirmation=False,
            )
        if op == "save":
            return AtomicResolved(
                kind="memory",
                action_type="memory",
                target="position_save",
                reason=f"请求保存当前位置为位置{normalized_name}。",
                params={"position_name": normalized_name},
                requires_confirmation=False,
            )
        if op == "delete":
            self.memory.delete_position(normalized_name)
            return AtomicResolved(
                kind="memory",
                action_type="memory",
                target="position_delete",
                reason=f"已删除位置{normalized_name}。",
                params={"position_name": normalized_name},
                requires_confirmation=False,
            )
        if op == "move":
            pose = self.memory.get_position(normalized_name)
            if pose is None:
                return self._unsupported(elements, f"位置{normalized_name}不存在。")
            record = self._pose_record(
                elements,
                pose,
                query_key=f"atomic:position:{normalized_name}",
                description=f"原子函数：移动到位置{normalized_name}",
            )
            return self._template_result(elements, record, f"移动到位置{normalized_name}", risk_level="high")
        return self._unsupported(elements, "未识别的位置库命令。")

    def _resolve_history(self, elements: AtomicElements) -> AtomicResolved:
        if elements.name == "repeat":
            record = self.memory.last_record
            if record is None:
                return self._unsupported(elements, "没有上一次动作记录，无法再走一次。")
            repeated = QueryRecord(
                query_key=f"atomic:repeat:{record.query_key}",
                func_num=record.func_num,
                keywords=elements.command_text,
                description=f"{record.description or record.query_key}（再走一次）",
                safety_level=record.safety_level,
                params=dict(record.params),
            )
            return AtomicResolved(
                kind="template",
                action_type="atomic_template",
                target=repeated.query_key,
                reason="重复上一次原子动作",
                params={"record": repeated, "raw_text": elements.raw_text},
                risk_level=self._record_risk_level(record),
                requires_confirmation=self._requires_confirmation(self._record_risk_level(record)),
            )
        if elements.name == "back":
            pose = self.memory.pop_position()
            if pose is None:
                return self._unsupported(elements, "没有历史位置，无法返回。")
            record = self._pose_record(
                elements,
                pose,
                query_key="atomic:history:back",
                description="原子函数：返回上一步位置",
            )
            return self._template_result(elements, record, "返回上一步位置", risk_level="medium")
        return self._unsupported(elements, "未识别的历史动作命令。")

    def _joint_record(self, elements: AtomicElements) -> QueryRecord:
        pos_val = self._position_value(elements, self.memory.current_step_deg)
        return QueryRecord(
            query_key=self._query_key("joint", elements.axis_no, elements.fuzzy_pos, pos_val),
            func_num=106,
            keywords=elements.command_text,
            description="原子函数：关节轴点动",
            safety_level=5,
            params=self._jog_params(elements, pos_val),
        )

    def _virtual_record(self, elements: AtomicElements) -> QueryRecord:
        default_step = self.memory.current_step_deg if int(elements.axis_no or 0) >= 9 else self.memory.current_step_mm
        pos_val = self._position_value(elements, default_step)
        return QueryRecord(
            query_key=self._query_key("virtual", elements.axis_no, elements.fuzzy_pos, pos_val),
            func_num=107,
            keywords=elements.command_text,
            description="原子函数：虚拟轴点动",
            safety_level=5,
            params=self._jog_params(elements, pos_val),
        )

    def _cartesian_record(self, elements: AtomicElements) -> QueryRecord:
        params = {
            "target_x": float(elements.x or 0.0),
            "target_y": float(elements.y or 0.0),
            "target_z": float(elements.z or 0.0),
            "target_rx": float(elements.rx or 0.0),
            "target_ry": float(elements.ry or 0.0),
            "target_rz": float(elements.rz or 0.0),
            "spd_pct": self._pct(elements.spd_pct, self.memory.current_speed),
            "acc_pct": self._pct(elements.acc_pct, self.memory.current_acc),
            "dec_pct": self._pct(elements.dec_pct, self.memory.current_dec),
            "stop_cmd": 0,
            "fuzzy_pos": int(elements.fuzzy_pos),
            "fuzzy_spd": 0 if elements.spd_pct is not None else 1,
            "fuzzy_acc": 0 if elements.acc_pct is not None else 1,
            "fuzzy_dec": 0 if elements.dec_pct is not None else 1,
            "move_type": int(elements.move_type),
        }
        return QueryRecord(
            query_key=f"atomic:cartesian:{params['target_x']}:{params['target_y']}:{params['target_z']}",
            func_num=108,
            keywords=elements.command_text,
            description="原子函数：笛卡尔运动",
            safety_level=5,
            params=params,
        )

    def _pose_record(
        self,
        elements: AtomicElements,
        pose: tuple[float, float, float, float, float, float],
        *,
        query_key: str,
        description: str,
    ) -> QueryRecord:
        x, y, z, rx, ry, rz = pose
        params = {
            "target_x": float(x),
            "target_y": float(y),
            "target_z": float(z),
            "target_rx": float(rx),
            "target_ry": float(ry),
            "target_rz": float(rz),
            "spd_pct": self.memory.current_speed,
            "acc_pct": self.memory.current_acc,
            "dec_pct": self.memory.current_dec,
            "stop_cmd": 0,
            "fuzzy_pos": 0,
            "fuzzy_spd": 1,
            "fuzzy_acc": 1,
            "fuzzy_dec": 1,
            "move_type": 0,
        }
        return QueryRecord(
            query_key=query_key,
            func_num=108,
            keywords=elements.command_text,
            description=description,
            safety_level=5,
            params=params,
        )

    def _delay_record(self, elements: AtomicElements) -> QueryRecord:
        delay_sec = max(0.0, float(elements.delay_sec or 0.0))
        return QueryRecord(
            query_key=f"atomic:delay:{delay_sec}",
            func_num=110,
            keywords=elements.command_text,
            description="原子函数：延时",
            safety_level=5,
            params={"delay_sec": delay_sec},
        )

    def _io_record(self, elements: AtomicElements) -> QueryRecord:
        io_no = int(elements.io_no or 0)
        io_action = int(elements.io_action or 0)
        return QueryRecord(
            query_key=f"atomic:io:{io_no}:{io_action}",
            func_num=120,
            keywords=elements.command_text,
            description="原子函数：IO 控制",
            safety_level=5,
            params={"io_no": io_no, "io_action": io_action},
        )

    def _jog_params(self, elements: AtomicElements, pos_val: float) -> dict[str, float | int]:
        return {
            "axis_no": int(elements.axis_no or 0),
            "pos_val": float(pos_val),
            "spd_pct": self._pct(elements.spd_pct, self.memory.current_speed),
            "acc_pct": self._pct(elements.acc_pct, self.memory.current_acc),
            "dec_pct": self._pct(elements.dec_pct, self.memory.current_dec),
            "fuzzy_pos": int(elements.fuzzy_pos),
            "fuzzy_spd": 0 if elements.spd_pct is not None else 1,
            "fuzzy_acc": 0 if elements.acc_pct is not None else 1,
            "fuzzy_dec": 0 if elements.dec_pct is not None else 1,
            "stop_cmd": 0,
        }

    def _template_result(
        self,
        elements: AtomicElements,
        record: QueryRecord,
        reason: str,
        *,
        risk_level: str = "medium",
    ) -> AtomicResolved:
        normalized_risk = str(risk_level)
        record.params["atomic_risk_level"] = normalized_risk
        record.params["atomic_risk_reason"] = self._risk_reason(record, normalized_risk)
        if record.func_num in {106, 107, 108}:
            self.memory.remember_record(record)
        return AtomicResolved(
            kind="template",
            action_type="atomic_template",
            target=record.query_key,
            reason=reason,
            params={"record": record, "raw_text": elements.raw_text},
            risk_level=normalized_risk,  # type: ignore[arg-type]
            requires_confirmation=self._requires_confirmation(normalized_risk),
        )

    def _unsupported(self, elements: AtomicElements, reason: str) -> AtomicResolved:
        return AtomicResolved(
            kind="unsupported",
            action_type="unknown",
            target=None,
            reason=reason,
            params={"raw_text": elements.raw_text, "command_text": elements.command_text},
            requires_confirmation=False,
        )

    def _requires_confirmation(self, risk_level: str) -> bool:
        mode = self.memory.confirm_mode
        if mode == "beginner":
            return True
        if mode in {"skilled", "expert"}:
            return risk_level in {"high", "emergency"}
        return True

    @staticmethod
    def _record_risk_level(record: QueryRecord) -> str:
        risk = record.params.get("atomic_risk_level") if isinstance(record.params, dict) else None
        if risk in {"low", "medium", "high", "emergency"}:
            return str(risk)
        if record.func_num in {106, 107, 108, 11}:
            return "high"
        if record.func_num == 120:
            return "medium"
        return "low"

    @staticmethod
    def _motion_risk_level(record: QueryRecord) -> str:
        if record.func_num == 108:
            return "high"
        if record.func_num == 106:
            return "high" if int(record.params.get("fuzzy_pos", 1)) == 0 else AtomicResolver._jog_risk_level(record)
        if record.func_num == 107:
            return AtomicResolver._jog_risk_level(record)
        if record.func_num == 120:
            return "medium"
        if record.func_num == 110:
            return "low"
        return AtomicResolver._record_risk_level(record)

    @staticmethod
    def _jog_risk_level(record: QueryRecord) -> str:
        axis_no = int(record.params.get("axis_no", 0))
        distance = abs(float(record.params.get("pos_val", 0.0)))
        speed = float(record.params.get("spd_pct", 50.0))
        acc = float(record.params.get("acc_pct", 100.0))
        dec = float(record.params.get("dec_pct", 100.0))
        if speed >= 50.0 or acc > 100.0 or dec > 100.0:
            return "high"
        if axis_no >= 9:
            return "medium" if distance <= 1.0 and speed <= 20.0 else "high"
        if distance <= 1.0 and speed <= 20.0:
            return "low"
        if distance <= 5.0 and speed <= 30.0:
            return "medium"
        return "high"

    @staticmethod
    def _risk_reason(record: QueryRecord, risk_level: str) -> str:
        if record.func_num == 108:
            return "笛卡尔/位置类动作需要完整预检确认。"
        if record.func_num == 106 and int(record.params.get("fuzzy_pos", 1)) == 0:
            return "关节绝对位置动作需要确认。"
        if record.func_num in {106, 107}:
            if risk_level == "low":
                return "小步长低速点动。"
            if risk_level == "medium":
                return "中等步长或旋转点动。"
            return "速度、加减速或步长较高。"
        if record.func_num == 120:
            return "IO输出会改变外部设备状态。"
        if record.func_num == 110:
            return "延时动作不产生机械运动。"
        return "按默认风险策略评估。"

    @staticmethod
    def _position_value(elements: AtomicElements, default_step: float) -> float:
        if elements.target is not None:
            return float(elements.target)
        step = float(elements.step if elements.step is not None else default_step)
        direction = int(elements.direction or 1)
        return step * direction

    @staticmethod
    def _pct(value: float | None, default: float) -> float:
        return min(150.0, max(5.0, float(default if value is None else value)))

    @staticmethod
    def _query_key(kind: str, axis_no: int | None, fuzzy_pos: int, pos_val: float) -> str:
        return f"atomic:{kind}:{int(axis_no or 0)}:{int(fuzzy_pos)}:{float(pos_val):g}"
