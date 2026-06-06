from __future__ import annotations

from dataclasses import dataclass
from typing import Any


AXIS_STATUS_BITS: dict[int, tuple[str, str, str]] = {
    1: ("following_error_warning", "J{axis}轴随动误差偏大", "请注意J{axis}轴负载变化。"),
    2: ("ethercat_lost", "J{axis}轴（驱动器{driver}号）通讯丢失", "请检查{driver}号驱动器网线连接和供电。"),
    3: ("drive_alarm", "J{axis}轴（驱动器{driver}号）驱动器故障", "建议断电重启{driver}号驱动器，并检查J{axis}轴电机接线。"),
    4: ("positive_hard_limit", "J{axis}轴碰到正向硬限位", "请点动J{axis}轴向负方向运动，移出限位区。"),
    5: ("negative_hard_limit", "J{axis}轴碰到负向硬限位", "请点动J{axis}轴向正方向运动，移出限位区。"),
    8: ("following_error_error", "J{axis}轴（驱动器{driver}号）随动误差超限出错", "检查J{axis}轴负载、降低运动加速度，并检查{driver}号驱动器编码器接线。"),
    9: ("positive_soft_limit", "J{axis}轴超过正向软限位", "请调整目标位置，使J{axis}轴不超过正向软限位。"),
    10: ("negative_soft_limit", "J{axis}轴超过负向软限位", "请调整目标位置，使J{axis}轴不低于负向软限位。"),
    12: ("max_speed_pulse", "J{axis}轴脉冲频率超MAX_SPEED", "请降低运动速度。"),
    14: ("command_coordinate_error", "J{axis}轴坐标错误", "请检查FRAME配置。"),
    18: ("power_error", "J{axis}轴（驱动器{driver}号）电源异常", "请工程师检查供电系统。"),
    20: ("axis_speed_protect", "J{axis}轴速度超限保护", "请降低速度。"),
}


@dataclass(frozen=True)
class AxisStatusBitDecomposer:
    max_axes: int = 6

    def decompose(self, values: list[int] | tuple[int, ...]) -> dict[str, Any]:
        axes = []
        has_error = False
        for index, raw_value in enumerate(list(values)[: self.max_axes]):
            axis = index + 1
            raw = int(raw_value)
            messages = []
            active_bits = []
            for bit, (code, text, suggestion) in AXIS_STATUS_BITS.items():
                if raw & (1 << bit):
                    active_bits.append(bit)
                    messages.append(
                        {
                            "bit": bit,
                            "code": code,
                            "message": text.format(axis=axis, driver=axis),
                            "suggestion": suggestion.format(axis=axis, driver=axis),
                        }
                    )
            has_error = has_error or bool(messages)
            axes.append({"axis": axis, "raw": raw, "active_bits": active_bits, "messages": messages})
        return {"axes": axes, "has_error": has_error}
