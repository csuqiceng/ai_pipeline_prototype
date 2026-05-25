"""Natural-language answers backed by the local V2.1 dashboard cache."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .alarm_advice import AlarmAdviceBook

from .dashboard_query_specs import match_dashboard_query_spec


@dataclass(frozen=True)
class DashboardQueryAnswer:
    board_key: str
    text: str
    priority: str = "normal"


class DashboardQueryService:
    """Answer operator status questions from the seven dashboard boards."""

    def answer(self, text: str, snapshot: dict[str, Any]) -> DashboardQueryAnswer | None:
        raw_text = text or ""
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return None
        boards = snapshot.get("boards", {}) if isinstance(snapshot, dict) else {}
        v20_answer = self._answer_v20_query(raw_text, compact, boards)
        if v20_answer is not None:
            return v20_answer
        spec = match_dashboard_query_spec(compact)
        if spec is None:
            return None
        if spec.board_key == "communication_faults":
            return self._answer_communication(boards.get("communication_faults", {}) or {})
        if spec.board_key == "action_feasibility":
            return self._answer_feasibility(boards.get("action_feasibility", {}) or {})
        if spec.board_key == "safety_boundary":
            return self._answer_safety(boards.get("safety_boundary", {}) or {})
        if spec.board_key == "motion_limits":
            return self._answer_motion_limits(boards.get("motion_limits", {}) or {})
        if spec.board_key == "process_preview":
            return self._answer_process_preview(boards.get("process_preview", {}) or {})
        if spec.board_key == "process_adaptation":
            return self._answer_adaptation(boards.get("process_adaptation", {}) or {})
        if spec.board_key == "device_status":
            return self._answer_device_status(boards.get("device_status", {}) or {})
        return None

    def _answer_v20_query(self, raw_text: str, compact: str, boards: dict[str, Any]) -> DashboardQueryAnswer | None:
        text = self._strip_wake_word(compact)
        device = boards.get("device_status", {}) or {}
        motion = boards.get("motion_limits", {}) or {}
        feasibility = boards.get("action_feasibility", {}) or {}
        safety = boards.get("safety_boundary", {}) or {}
        communication = boards.get("communication_faults", {}) or {}
        if text in {"当前位置", "当前坐标", "现在位置"}:
            return DashboardQueryAnswer("device_status", f"当前位置：{self._format_pose(device.get('dpos_c'))}。")
        joint_match = re.fullmatch(r"J([1-6])(?:角度|多少|位置)?", text, flags=re.IGNORECASE)
        if joint_match:
            index = int(joint_match.group(1)) - 1
            joints = self._as_sequence(device.get("dpos_j"))
            value = joints[index] if index < len(joints) else "-"
            return DashboardQueryAnswer("device_status", f"J{index + 1}={value}。")
        if text in {"各轴角度", "关节角度", "所有关节角度"}:
            return DashboardQueryAnswer("device_status", "各轴角度：" + self._format_joints(device.get("dpos_j")) + "。")
        if text in {"设备状态", "当前状态", "现在状态"}:
            return self._answer_device_status(device)
        if text in {"有没有报警", "有报警吗", "报警了吗"}:
            return self._answer_alarm(device)
        if text in {"速度多少", "当前速度", "速度是多少"}:
            return DashboardQueryAnswer(
                "motion_limits",
                f"当前速度 {motion.get('speed', '-')}，运动进度 {motion.get('motion_percent', '-')}，速度上限 {motion.get('safe_speed_max', '-')}。",
            )
        if text in {"通讯正常吗", "通信正常吗"}:
            return self._answer_communication(communication)
        if text.startswith("能不能到") or text.startswith("能到"):
            target = self._parse_target_xyz(raw_text)
            if target is not None:
                return self._answer_target_feasibility(target, feasibility, safety)
            return self._answer_feasibility(feasibility)
        return None

    @staticmethod
    def _strip_wake_word(text: str) -> str:
        compact = re.sub(r"\s+", "", text or "")
        for wake_word in ("小正", "小郑", "校正"):
            if compact.startswith(wake_word):
                return compact[len(wake_word):].lstrip("，,。:：")
        return compact

    @staticmethod
    def _as_sequence(value: object) -> tuple[object, ...]:
        return tuple(value) if isinstance(value, (list, tuple)) else ()

    @staticmethod
    def _format_pose(value: object) -> str:
        values = DashboardQueryService._as_sequence(value)
        labels = ("X", "Y", "Z", "RX", "RY", "RZ")
        if not values:
            return "-"
        return "，".join(f"{labels[index]}={values[index]}" for index in range(min(len(values), len(labels))))

    @staticmethod
    def _format_joints(value: object) -> str:
        values = DashboardQueryService._as_sequence(value)
        if not values:
            return "-"
        return "，".join(f"J{index + 1}={values[index]}" for index in range(min(len(values), 6)))

    @staticmethod
    def _answer_alarm(board: dict[str, Any]) -> DashboardQueryAnswer:
        if board.get("alarm"):
            code = board.get("alarm_code") or board.get("alarmCode") or "-"
            text = board.get("alarm_text") or board.get("alarmText") or "-"
            advice = AlarmAdviceBook.default().get(str(code))
            return DashboardQueryAnswer(
                "device_status",
                f"当前有报警，报警码 {code}，报警内容 {text}。建议：{advice.operator_hint}",
                "high",
            )
        return DashboardQueryAnswer("device_status", "当前无报警。")

    @staticmethod
    def _parse_target_xyz(text: str) -> tuple[float, float, float] | None:
        cleaned = (text or "").strip()
        for wake_word in ("小正", "小郑", "校正"):
            if cleaned.startswith(wake_word):
                cleaned = cleaned[len(wake_word):].lstrip("，,。:： ")
                break
        match = re.search(
            r"(?:能不能到|能到|到达|移动到)\s*"
            r"(-?\d+(?:\.\d+)?)\s*[,， ]+\s*"
            r"(-?\d+(?:\.\d+)?)\s*[,， ]+\s*"
            r"(-?\d+(?:\.\d+)?)",
            cleaned,
        )
        if match is None:
            return None
        return (float(match.group(1)), float(match.group(2)), float(match.group(3)))

    @staticmethod
    def _answer_target_feasibility(
        target_xyz: tuple[float, float, float],
        feasibility: dict[str, Any],
        safety: dict[str, Any],
    ) -> DashboardQueryAnswer:
        labels = ("X", "Y", "Z")
        limit_keys = ("x_range", "y_range", "z_range")
        failures: list[str] = []
        missing_limits: list[str] = []
        for value, label, key in zip(target_xyz, labels, limit_keys):
            axis_range = safety.get(key)
            if not isinstance(axis_range, (list, tuple)) or len(axis_range) < 2:
                missing_limits.append(label)
                continue
            lower = DashboardQueryService._float_or_none(axis_range[0])
            upper = DashboardQueryService._float_or_none(axis_range[1])
            if lower is None or upper is None:
                missing_limits.append(label)
                continue
            if not lower <= float(value) <= upper:
                failures.append(f"目标 {label}={float(value):.1f} 超出软限位 {lower:.1f}~{upper:.1f}")

        base = f"目标点 X={target_xyz[0]:.1f}，Y={target_xyz[1]:.1f}，Z={target_xyz[2]:.1f}"
        current = DashboardQueryService._answer_feasibility(feasibility)
        if failures:
            return DashboardQueryAnswer(
                "action_feasibility",
                f"{base} 当前不建议执行。原因：{'；'.join(failures)}。建议：调整目标点到安全边界内后再预检。",
                "high",
            )
        if missing_limits:
            return DashboardQueryAnswer(
                "action_feasibility",
                f"{base} 已解析，但缺少 {'/'.join(missing_limits)} 软限位配置，不能给出完整离线边界结论。{current.text}",
                "high" if current.priority == "high" else "normal",
            )
        if current.priority == "high":
            return DashboardQueryAnswer(
                "action_feasibility",
                f"{base} 基础边界检查通过，但当前执行条件不满足。{current.text}",
                "high",
            )
        return DashboardQueryAnswer(
            "action_feasibility",
            f"{base} 基础边界检查通过。{current.text}",
            current.priority,
        )

    @staticmethod
    def _float_or_none(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _has_any(text: str, *keywords: str) -> bool:
        lowered = text.lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    @staticmethod
    def _answer_communication(board: dict[str, Any]) -> DashboardQueryAnswer:
        ecat_ok = bool(board.get("ecat_ok"))
        realtime = str(board.get("realtime_feedback", "unknown"))
        controller = str(board.get("controller", "unknown"))
        io_status = board.get("io_status", "-")
        feedback_age_ms = board.get("feedback_age_ms")
        if realtime == "stale" or controller == "stale":
            age_text = DashboardQueryService._format_age_ms(feedback_age_ms)
            return DashboardQueryAnswer(
                "communication_faults",
                f"通讯需注意，实时反馈已过期{age_text}，控制器状态 {controller}，IO状态 {io_status}。请先确认连接和轮询状态。",
                "high",
            )
        if ecat_ok:
            text = f"通讯正常，实时反馈在线，控制器状态 {controller}，IO状态 {io_status}。"
        else:
            text = f"通讯异常，实时反馈状态 {realtime}，控制器状态 {controller}，请检查总线和控制器连接。"
        return DashboardQueryAnswer("communication_faults", text, "normal" if ecat_ok else "high")

    @staticmethod
    def _format_age_ms(value: object) -> str:
        if value in (None, "", "-"):
            return ""
        try:
            return f" {int(value)}ms"
        except (TypeError, ValueError):
            return f" {value}"

    @staticmethod
    def _answer_feasibility(board: dict[str, Any]) -> DashboardQueryAnswer:
        channel_idle = bool(board.get("channel_idle"))
        l1 = str(board.get("precheck_status", "unknown"))
        l2 = str(board.get("motion_status", "unknown"))
        reasons: list[str] = []
        suggestions: list[str] = []
        if not channel_idle:
            reasons.append("通道忙")
            suggestions.append("等待通道空闲或先停止当前任务")
        if l1 == "fail":
            reasons.append("L1安全预检未通过")
            suggestions.append("先处理安全预检失败项")
        if l2 == "fail":
            reasons.append("L2运动规划未通过")
            suggestions.append("检查目标位姿、FSTATUS或采纳安全中间点建议")
        if reasons:
            suggestion_text = "；".join(suggestions) if suggestions else "请先查看安全确认页的风险项"
            return DashboardQueryAnswer(
                "action_feasibility",
                f"当前不建议执行。原因：{'，'.join(reasons)}。当前不能执行，暂不建议发出动作。建议：{suggestion_text}。",
                "high",
            )
        if l2 == "unavailable":
            return DashboardQueryAnswer("action_feasibility", "当前基础条件可执行，但L2运动规划预演未接入，请按现场安全流程确认。")
        return DashboardQueryAnswer("action_feasibility", "当前可执行：通道空闲，安全预检通过，运动规划状态正常。")

    @staticmethod
    def _answer_safety(board: dict[str, Any]) -> DashboardQueryAnswer:
        current_r = board.get("current_r", "-")
        current_z = board.get("current_z", "-")
        safe_r = board.get("safe_r_range", ("-", "-"))
        safe_z = board.get("safe_z_range", ("-", "-"))
        joint_text = DashboardQueryService._format_joint_limits(board.get("joint_limits"))
        joint_suffix = f"；关节软限位：{joint_text}" if joint_text != "-" else ""
        return DashboardQueryAnswer(
            "safety_boundary",
            f"当前位置安全边界：当前R={current_r}，允许R={safe_r[0]}~{safe_r[1]}；当前Z={current_z}，允许Z={safe_z[0]}~{safe_z[1]}{joint_suffix}。",
        )

    @staticmethod
    def _format_joint_limits(value: object) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            return "-"
        parts: list[str] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            parts.append(f"J{index}={item[0]}~{item[1]}")
        return "，".join(parts) if parts else "-"

    @staticmethod
    def _answer_motion_limits(board: dict[str, Any]) -> DashboardQueryAnswer:
        return DashboardQueryAnswer(
            "motion_limits",
            "运动极限："
            f"当前速度 {board.get('speed', '-')}，进度 {board.get('motion_percent', '-')}，"
            f"速度/加速度/减速度上限为 {board.get('safe_speed_max', '-')} / "
            f"{board.get('safe_acc_max', '-')} / {board.get('safe_dec_max', '-')}。",
        )

    @staticmethod
    def _answer_process_preview(board: dict[str, Any]) -> DashboardQueryAnswer:
        risks = board.get("risk_summary", []) or []
        risk_text = "暂无风险" if not risks else "；".join(str(item) for item in risks[:3])
        status = str(board.get("l3_status", "unknown"))
        if risks or status == "fail":
            return DashboardQueryAnswer(
                "process_preview",
                f"流程预演状态 {status}，当前流程 {board.get('current_flow_name', '-')}，"
                f"步骤 {board.get('flow_current_step', '-')}，进度 {DashboardQueryService._format_percent(board.get('progress_percent'))}。"
                f"风险原因：{risk_text}。处理建议：查看安全确认页风险项，必要时采纳安全建议、调整目标点或降低流程累计误差后重试。",
                "high",
            )
        return DashboardQueryAnswer(
            "process_preview",
            f"流程预演状态 {status}，当前流程 {board.get('current_flow_name', '-')}，"
            f"步骤 {board.get('flow_current_step', '-')}，进度 {DashboardQueryService._format_percent(board.get('progress_percent'))}，"
            f"风险摘要：{risk_text}。",
        )

    @staticmethod
    def _format_percent(value: object) -> str:
        if value in (None, "", "-"):
            return "-"
        text = str(value)
        return text if text.endswith("%") else f"{text}%"

    @staticmethod
    def _answer_adaptation(board: dict[str, Any]) -> DashboardQueryAnswer:
        status = str(board.get("l2_status", "unknown"))
        suggestion = str(board.get("suggestion", "-"))
        details: list[str] = []
        rejected = DashboardQueryService._format_rejected_fstatuses(board.get("rejected_fstatuses"))
        if rejected != "-":
            details.append(f"已规避 {rejected}")
        if board.get("need_midpoint") and board.get("midpoint_pose") is not None:
            details.append(
                f"建议中点={board.get('midpoint_pose')}，中点FSTATUS={board.get('midpoint_fstatus', '-')}。"
            )
        if status == "fail" or board.get("singularity") is True:
            details.append("FSTATUS表示控制器逆解姿态候选，奇异点通常表示该姿态或直线路径接近关节风险区。")
        detail_text = " ".join(details)
        suffix = f" {detail_text}" if detail_text else ""
        return DashboardQueryAnswer(
            "process_adaptation",
            f"工艺适配评估：L2状态 {status}，FSTATUS={board.get('fstatus', '-')}，奇异点={board.get('singularity', '-')}，建议：{suggestion}。{suffix}",
            "high" if status == "fail" else "normal",
        )

    @staticmethod
    def _format_rejected_fstatuses(value: object) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            return "-"
        values = "、".join(str(item) for item in value)
        return f"FSTATUS={values}"

    @staticmethod
    def _answer_device_status(board: dict[str, Any]) -> DashboardQueryAnswer:
        alarm = "有报警" if board.get("alarm") else "无报警"
        estop = "急停开启" if board.get("estop") else "急停关闭"
        pause = "暂停中" if board.get("pause") else "未暂停"
        alarm_detail = ""
        if board.get("alarm"):
            code = board.get("alarm_code") or board.get("alarmCode") or "-"
            text = board.get("alarm_text") or board.get("alarmText") or "-"
            alarm_detail = f"报警码 {code}，报警内容 {text}。请先确认现场安全和报警原因，排除后再执行报警复位。"
        return DashboardQueryAnswer(
            "device_status",
            f"设备状态：{board.get('system_state', 'unknown')}，{estop}，{pause}，{alarm}，当前位置 {board.get('dpos_c', '-')}。{alarm_detail}",
            "high" if board.get("alarm") or board.get("estop") else "normal",
        )
