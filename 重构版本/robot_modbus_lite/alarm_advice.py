from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlarmAdvice:
    code: str
    title: str
    severity: str
    operator_hint: str
    engineer_hint: str
    auto_clear: bool = False


class AlarmAdviceBook:
    def __init__(self, entries: dict[str, AlarmAdvice]):
        self._entries = dict(entries)

    @classmethod
    def default(cls) -> "AlarmAdviceBook":
        entries = {
            "E_STOP": AlarmAdvice("E_STOP", "急停触发", "critical", "确认人员安全后联系工程师复位。", "检查急停回路和 Func104 状态。"),
            "PAUSED": AlarmAdvice("PAUSED", "设备暂停", "warning", "确认现场安全后执行继续。", "检查暂停来源和暂停输入。", True),
            "OVER_SPEED": AlarmAdvice("OVER_SPEED", "速度超限", "critical", "降低速度后重新确认。", "检查模板速度和动作类型钳位。"),
            "OVER_ACCEL": AlarmAdvice("OVER_ACCEL", "加速度超限", "critical", "降低加速度后重新确认。", "检查 acc_pct 和控制器限制。"),
            "OVER_DECEL": AlarmAdvice("OVER_DECEL", "减速度超限", "critical", "降低减速度后重新确认。", "检查 dec_pct 和控制器限制。"),
            "JOINT_LIMIT": AlarmAdvice("JOINT_LIMIT", "关节限位", "critical", "停止当前动作，选择安全点或中点绕行。", "检查关节软限位和目标位姿。"),
            "CART_LIMIT": AlarmAdvice("CART_LIMIT", "笛卡尔软限位", "critical", "选择安全点或调整目标位置。", "检查 R/Z/XYZ 边界。"),
            "SINGULARITY": AlarmAdvice("SINGULARITY", "奇异区风险", "warning", "采纳中点绕行建议。", "检查逆解 FSTATUS 和中点建议。"),
            "COMM_STALE": AlarmAdvice("COMM_STALE", "通讯反馈过期", "warning", "等待通讯恢复或刷新连接。", "检查 Modbus 连接和实时反馈时间戳。", True),
            "CONTROLLER_NOT_READY": AlarmAdvice("CONTROLLER_NOT_READY", "控制器未就绪", "warning", "等待控制器就绪后重试。", "检查控制器状态字和通道状态。", True),
        }
        return cls(entries)

    def codes(self) -> list[str]:
        return sorted(self._entries)

    def get(self, code: str) -> AlarmAdvice:
        key = str(code or "").strip().upper()
        if key in self._entries:
            return self._entries[key]
        return AlarmAdvice(
            key or "UNKNOWN",
            "未知报警",
            "unknown",
            "保持停止状态并联系工程师确认。",
            "读取 LONG(38) 和控制器日志。",
        )
