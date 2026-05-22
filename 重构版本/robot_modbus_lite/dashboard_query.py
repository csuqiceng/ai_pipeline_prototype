"""Natural-language answers backed by the local V2.1 dashboard cache."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .dashboard_query_specs import match_dashboard_query_spec


@dataclass(frozen=True)
class DashboardQueryAnswer:
    board_key: str
    text: str
    priority: str = "normal"


class DashboardQueryService:
    """Answer operator status questions from the seven dashboard boards."""

    def answer(self, text: str, snapshot: dict[str, Any]) -> DashboardQueryAnswer | None:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return None
        boards = snapshot.get("boards", {}) if isinstance(snapshot, dict) else {}
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
                f"当前不建议执行。原因：{'，'.join(reasons)}。建议：{suggestion_text}。",
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
        return DashboardQueryAnswer(
            "process_adaptation",
            f"工艺适配评估：L2状态 {status}，FSTATUS={board.get('fstatus', '-')}，奇异点={board.get('singularity', '-')}，建议：{suggestion}。",
            "high" if status == "fail" else "normal",
        )

    @staticmethod
    def _answer_device_status(board: dict[str, Any]) -> DashboardQueryAnswer:
        alarm = "有报警" if board.get("alarm") else "无报警"
        estop = "急停开启" if board.get("estop") else "急停关闭"
        pause = "暂停中" if board.get("pause") else "未暂停"
        return DashboardQueryAnswer(
            "device_status",
            f"设备状态：{board.get('system_state', 'unknown')}，{estop}，{pause}，{alarm}，当前位置 {board.get('dpos_c', '-')}。",
            "high" if board.get("alarm") or board.get("estop") else "normal",
        )
