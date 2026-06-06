from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_modbus_lite.agent.axis_status import AxisStatusBitDecomposer
from robot_modbus_lite.models import SixAxisStatus


FUNC_NAME_ZH: dict[int, str] = {
    104: "系统控制",
    106: "关节点动",
    107: "虚拟轴点动",
    108: "直线插补",
    11: "多点插补",
    109: "定时检测",
    110: "延时等待",
    120: "IO控制",
}


@dataclass(frozen=True)
class AlarmExplanationAgent:
    axis_decomposer: AxisStatusBitDecomposer = field(default_factory=AxisStatusBitDecomposer)

    def explain(
        self,
        *,
        long34: int,
        long36: int = 0,
        long38: int = 0,
        axis_status: list[int] | tuple[int, ...] = (),
        current_func: int | None = None,
        safety_values: dict[str, Any] | None = None,
        hardware_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = SixAxisStatus.from_value(int(long34), func_num=current_func)
        func_name = self._func_name_zh(current_func)
        axis_detail = self.axis_decomposer.decompose(tuple(int(value) for value in axis_status))
        axis_messages = self._axis_messages(axis_detail)

        if status.is_estop:
            source_text = self._estop_source_text(int(long36))
            detail = "系统急停中，无法执行指令。请先解除急停：1.确认硬件急停按钮已复位 2.按复位键。"
            affected_axes: list[int] | None = None
            suggestions = [self._estop_suggestion(source_text)]
            if source_text:
                detail += f" 急停原因：{source_text}。"
            if int(long38) & (1 << 7) and axis_messages:
                drive_axes = [item for item in axis_messages if item.get("code") == "drive_alarm"] or axis_messages
                first = drive_axes[0]
                detail += f" 急停原因：驱动器故障，{first['message']}。请先排除J{first['axis']}轴驱动器故障后再按复位键。"
                affected_axes = [item["axis"] for item in drive_axes]
                suggestions = [item["suggestion"] for item in drive_axes]
            return self._result(
                severity="critical",
                summary="设备处于急停状态",
                detail=detail,
                suggestions=suggestions,
                affected_axes=affected_axes,
                func_name_zh=func_name,
                can_move=False,
            )

        if int(long38) & (1 << 7) and axis_messages:
            first = axis_messages[0]
            detail = first["suggestion"]
            if current_func == 104:
                detail = f"复位未成功，J{first['axis']}轴仍有故障，请继续排查。{detail}"
            return self._result(
                severity="critical",
                summary=f"J{first['axis']}轴{first['message']}",
                detail=detail,
                suggestions=[item["suggestion"] for item in axis_messages],
                affected_axes=[item["axis"] for item in axis_messages],
                func_name_zh=func_name,
                can_move=False,
            )

        if int(long38) & (1 << 6):
            if not axis_messages:
                return self._result(
                    severity="critical",
                    summary="检测到 EtherCAT 通讯异常",
                    detail="LONG(38) 通讯报警位已触发，但逐轴 AXISSTATUS 明细暂不可用。",
                    suggestions=["请读取 IEEE(200/202/204/206/208/210) 后定位通讯丢失轴，并检查驱动器网线和供电。"],
                    affected_axes=[],
                    func_name_zh=func_name,
                    can_move=False,
                )
            return self._result(
                severity="critical",
                summary="检测到 EtherCAT 通讯异常",
                detail="LONG(38) 通讯报警位已触发。",
                suggestions=[item["suggestion"] for item in axis_messages],
                affected_axes=[item["axis"] for item in axis_messages],
                func_name_zh=func_name,
                can_move=False,
            )

        spatial_result = self._spatial_alarm_result(long38=int(long38), safety_values=safety_values or {}, func_name=func_name)
        if spatial_result is not None:
            return spatial_result

        clamp_result = self._clamp_alarm_result(long38=int(long38), safety_values=safety_values or {}, func_name=func_name)
        if clamp_result is not None:
            return clamp_result

        axis_flag_result = self._axis_alarm_flag_result(hardware_values=hardware_values or {}, func_name=func_name)
        if axis_flag_result is not None:
            return axis_flag_result

        long38_result = self._unrecognized_long38_result(long38=int(long38), current_func=current_func, func_name=func_name)
        if long38_result is not None:
            return long38_result

        if status.has_alarm:
            return self._result(
                severity="critical",
                summary="控制器存在报警",
                detail=f"LONG(34)={int(long34)}，LONG(38)={int(long38)}。",
                suggestions=["查看报警详情并完成复位。"],
                func_name_zh=func_name,
                can_move=False,
            )

        if status.is_paused:
            return self._result(
                severity="warning",
                summary="设备处于暂停状态",
                detail=f"系统暂停中，无法执行新指令。请先按继续键解除暂停。LONG(36)={int(long36)}。",
                suggestions=["需要继续时先执行继续/恢复运行；如需终止当前运动，可执行取消。"],
                func_name_zh=func_name,
                can_move=False,
            )

        hardware_result = self._hardware_state_result(hardware_values=hardware_values or {}, func_name=func_name)
        if hardware_result is not None:
            return hardware_result

        func_result = self._function_state_result(status=status, func_name=func_name)
        if func_result is not None:
            return func_result

        if not status.is_ready:
            return self._result(
                severity="warning",
                summary="系统未就绪，暂不可接受运动指令",
                detail=f"LONG(34) bit28 未置位，LONG(34)={int(long34)}，LONG(38)={int(long38)}。",
                suggestions=["请确认控制器已初始化、伺服已使能且报警已清除。"],
                func_name_zh=func_name,
                can_move=False,
            )

        return self._result(
            severity="ok" if status.can_send else "info",
            summary="设备就绪" if status.can_send else f"正在执行 {func_name}",
            detail=f"LONG(34)={int(long34)}，LONG(36)={int(long36)}，LONG(38)={int(long38)}。",
            suggestions=[],
            func_name_zh=func_name,
            can_move=status.can_send,
        )

    @staticmethod
    def _func_name_zh(func_num: int | None) -> str:
        if func_num is None:
            return "未知函数"
        return FUNC_NAME_ZH.get(int(func_num), f"FUNC{int(func_num)}")

    @staticmethod
    def _estop_source_text(long36: int) -> str:
        sources = []
        if int(long36) & (1 << 3):
            sources.append("硬件急停按钮")
        if int(long36) & 1:
            sources.append("上位机急停")
        return "、".join(sources)

    @staticmethod
    def _estop_suggestion(source_text: str) -> str:
        if "硬件急停按钮" in source_text:
            return "请先确认硬件急停按钮已旋回复位，再执行报警复位，等待LONG(38)=0。"
        return "解除急停后执行报警复位，等待LONG(38)=0，再确认设备就绪。"

    @staticmethod
    def _axis_messages(axis_detail: dict[str, Any]) -> list[dict[str, Any]]:
        messages = []
        for axis in axis_detail.get("axes", []):
            axis_no = int(axis.get("axis", 0))
            for message in axis.get("messages", []):
                messages.append({"axis": axis_no, **message})
        return messages

    @classmethod
    def _function_state_result(cls, *, status: SixAxisStatus, func_name: str) -> dict[str, Any] | None:
        state = status.function_state()
        if state == SixAxisStatus.STATE_ERR:
            return cls._result(
                severity="critical",
                summary="运动失败，请检查报警",
                detail=f"{func_name} 状态=ERR，LONG(34)={int(status.raw)}。",
                suggestions=["请查看报警详情，处理故障后再复位。"],
                func_name_zh=func_name,
                can_move=False,
            )
        if state == SixAxisStatus.STATE_EXEC:
            return cls._result(
                severity="info",
                summary=f"正在执行：{func_name}",
                detail=f"{func_name} 状态=EXEC，LONG(34)={int(status.raw)}。",
                suggestions=[],
                func_name_zh=func_name,
                can_move=False,
            )
        if state == SixAxisStatus.STATE_DONE:
            return cls._result(
                severity="ok",
                summary="运动完成",
                detail=f"{func_name} 状态=DONE，LONG(34)={int(status.raw)}。",
                suggestions=[],
                func_name_zh=func_name,
                can_move=True,
            )
        return None

    @classmethod
    def _hardware_state_result(cls, *, hardware_values: dict[str, Any], func_name: str) -> dict[str, Any] | None:
        if not hardware_values:
            return None
        failed: list[str] = []
        axis_values = hardware_values.get("axis_enabled")
        if isinstance(axis_values, (list, tuple)):
            for index, value in enumerate(tuple(axis_values)[:6], start=1):
                if cls._unknown_bit(value):
                    continue
                if not cls._truthy_bit(value):
                    failed.append(f"J{index}轴未使能")
        if (
            "servo_enabled" in hardware_values
            and not cls._unknown_bit(hardware_values.get("servo_enabled"))
            and not cls._truthy_bit(hardware_values.get("servo_enabled"))
        ):
            failed.append("伺服未使能")
        if (
            "ethercat_initialized" in hardware_values
            and not cls._unknown_bit(hardware_values.get("ethercat_initialized"))
            and not cls._truthy_bit(hardware_values.get("ethercat_initialized"))
        ):
            failed.append("EtherCAT未初始化")
        if (
            "wdog" in hardware_values
            and not cls._unknown_bit(hardware_values.get("wdog"))
            and not cls._truthy_bit(hardware_values.get("wdog"))
        ):
            failed.append("WDOG看门狗异常")
        if (
            "any_axis_moving" in hardware_values
            and not cls._unknown_bit(hardware_values.get("any_axis_moving"))
            and cls._truthy_bit(hardware_values.get("any_axis_moving"))
        ):
            failed.append("存在轴正在运动")
        if not failed:
            return None
        summary = "伺服未使能" if "伺服未使能" in failed else "硬件使能状态异常"
        return cls._result(
            severity="warning",
            summary=summary,
            detail="；".join(failed) + "。",
            suggestions=["请确认BIT(190-195)、BIT(252-255)状态，完成使能和初始化后再下发运动指令。"],
            func_name_zh=func_name,
            can_move=False,
        )

    @classmethod
    def _axis_alarm_flag_result(cls, *, hardware_values: dict[str, Any], func_name: str) -> dict[str, Any] | None:
        flags = hardware_values.get("axis_alarm_flags")
        if not isinstance(flags, (list, tuple)):
            return None
        axes = []
        for index, value in enumerate(tuple(flags)[:6], start=1):
            if cls._unknown_bit(value):
                continue
            if cls._truthy_bit(value):
                axes.append(index)
        if not axes:
            return None
        axis_text = "、".join(f"J{axis}" for axis in axes)
        return cls._result(
            severity="critical",
            summary=f"{axis_text}轴存在报警标志",
            detail=f"BIT(274-279) 显示 {axis_text}轴有报警。需要继续读取 AXISSTATUS IEEE(200/202/204/206/208/210) 精确定位故障位。",
            suggestions=["请读取对应轴 AXISSTATUS，按驱动器故障、硬限位、软限位、随动误差或电源异常分类处理。"],
            affected_axes=axes,
            func_name_zh=func_name,
            can_move=False,
        )

    @classmethod
    def _unrecognized_long38_result(cls, *, long38: int, current_func: int | None, func_name: str) -> dict[str, Any] | None:
        if int(long38) == 0:
            return None
        if current_func == 104:
            return cls._result(
                severity="critical",
                summary="复位未成功",
                detail=f"复位后 LONG(38) 仍未清零，LONG(38)={int(long38)}。当前缺少逐轴 AXISSTATUS 明细，无法进一步定位具体轴。",
                suggestions=["请读取 AXISSTATUS 和轴报警标志，确认故障清除后再次复位。"],
                func_name_zh=func_name,
                can_move=False,
            )
        return cls._result(
            severity="critical",
            summary="LONG(38) 存在未识别报警位",
            detail=f"LONG(38)={int(long38)}，当前报警位不在已知空间、速度、通讯或驱动器分类中。",
            suggestions=["请读取控制器详细报警和 AXISSTATUS，确认 LONG(38)=0 后再执行运动。"],
            func_name_zh=func_name,
            can_move=False,
        )

    @staticmethod
    def _truthy_bit(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "已使能", "使能"}
        return bool(value)

    @staticmethod
    def _unknown_bit(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in {"", "-", "未知", "unknown", "None"}
        return False

    @classmethod
    def _spatial_alarm_result(cls, *, long38: int, safety_values: dict[str, Any], func_name: str) -> dict[str, Any] | None:
        if long38 & (1 << 0):
            current_r = cls._float_or_none(safety_values.get("current_r"))
            r_min = cls._float_or_none(safety_values.get("safe_r_min"))
            r_max = cls._float_or_none(safety_values.get("safe_r_max"))
            if current_r is not None and r_max is not None and current_r >= r_max:
                exceed = current_r - r_max
                return cls._result(
                    severity="critical",
                    summary="手臂伸太远了",
                    detail=f"当前R={current_r:.1f}mm，上限{r_max:.1f}mm，超出{exceed:.1f}mm。",
                    suggestions=[f"请收回手臂，将目标半径缩至{r_max:.1f}mm以内。"],
                    func_name_zh=func_name,
                    can_move=False,
                )
            if current_r is not None and r_min is not None and current_r <= r_min:
                short = r_min - current_r
                return cls._result(
                    severity="critical",
                    summary="手臂收太近了",
                    detail=f"当前R={current_r:.1f}mm，下限{r_min:.1f}mm，不足{short:.1f}mm。",
                    suggestions=[f"请伸出手臂，将目标半径扩至{r_min:.1f}mm以外。"],
                    func_name_zh=func_name,
                    can_move=False,
                )
            return cls._result(
                severity="critical",
                summary="半径超限",
                detail="LONG(38) 半径超限位已触发，但当前R或安全R范围不可用。",
                suggestions=["请读取 IEEE(1700/1702/1740) 后重新判断超限方向。"],
                func_name_zh=func_name,
                can_move=False,
            )

        if long38 & (1 << 1):
            current_z = cls._float_or_none(safety_values.get("current_z"))
            z_min = cls._float_or_none(safety_values.get("safe_z_min"))
            z_max = cls._float_or_none(safety_values.get("safe_z_max"))
            if current_z is not None and z_max is not None and current_z >= z_max:
                exceed = current_z - z_max
                return cls._result(
                    severity="critical",
                    summary="机械手太高了",
                    detail=f"当前Z={current_z:.1f}mm，上限{z_max:.1f}mm，超出{exceed:.1f}mm。",
                    suggestions=[f"请降低高度，将目标Z降至{z_max:.1f}mm以下。"],
                    func_name_zh=func_name,
                    can_move=False,
                )
            if current_z is not None and z_min is not None and current_z <= z_min:
                short = z_min - current_z
                return cls._result(
                    severity="critical",
                    summary="机械手太低了",
                    detail=f"当前Z={current_z:.1f}mm，下限{z_min:.1f}mm，不足{short:.1f}mm。",
                    suggestions=[f"请升高高度，将目标Z升至{z_min:.1f}mm以上。"],
                    func_name_zh=func_name,
                    can_move=False,
                )
            return cls._result(
                severity="critical",
                summary="高度超限",
                detail="LONG(38) 高度超限位已触发，但当前Z或安全Z范围不可用。",
                suggestions=["请读取 IEEE(1704/1706/1742) 后重新判断超限方向。"],
                func_name_zh=func_name,
                can_move=False,
            )
        return None

    @classmethod
    def _clamp_alarm_result(cls, *, long38: int, safety_values: dict[str, Any], func_name: str) -> dict[str, Any] | None:
        messages: list[str] = []
        suggestions: list[str] = []
        if long38 & (1 << 3):
            value = cls._format_limit(safety_values.get("safe_speed_max"))
            messages.append(f"速度已自动降速到安全上限{value}%。")
            suggestions.append("如需提速，请在安全参数中提高安全速度百分比。")
        if long38 & (1 << 4):
            value = cls._format_limit(safety_values.get("safe_acc_max"))
            messages.append(f"加速度已自动降低到安全上限{value}%。")
            suggestions.append("如需提高加速度，请在安全参数中调整。")
        if long38 & (1 << 5):
            value = cls._format_limit(safety_values.get("safe_dec_max"))
            messages.append(f"减速度已自动降低到安全上限{value}%。")
            suggestions.append("如需提高减速度，请在安全参数中调整。")
        if not messages:
            return None
        return cls._result(
            severity="warning",
            summary="控制器已执行速度类安全钳制",
            detail=" ".join(messages),
            suggestions=suggestions,
            func_name_zh=func_name,
            can_move=True,
        )

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _format_limit(cls, value: Any) -> str:
        parsed = cls._float_or_none(value)
        return "-" if parsed is None else f"{parsed:.1f}"

    @staticmethod
    def _result(
        *,
        severity: str,
        summary: str,
        detail: str,
        suggestions: list[str],
        func_name_zh: str,
        can_move: bool,
        affected_axes: list[int] | None = None,
    ) -> dict[str, Any]:
        return {
            "severity": severity,
            "summary": summary,
            "detail": detail,
            "suggestions": suggestions,
            "affected_axes": affected_axes or [],
            "func_name_zh": func_name_zh,
            "can_move": can_move,
        }
