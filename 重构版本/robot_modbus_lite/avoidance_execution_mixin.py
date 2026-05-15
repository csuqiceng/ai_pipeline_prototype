"""为主窗口提供安全中间点路线规划和当前位置读取能力。"""

from __future__ import annotations

from .avoidance_config import SafePoint
from .models import ControllerClient, QueryRecord


class AvoidanceExecutionMixin:
    """为主窗口增加避障路线执行能力。"""
    def _build_execution_plan(self, record: QueryRecord) -> tuple[list[QueryRecord], str]:
        """构建相关数据。"""
        safe_point, reason = self._select_safe_point_for_record(record)
        if safe_point is None:
            return [record], "未命中规避规则，直接发送"
        safe_record = self._build_safe_point_record(safe_point, record)
        return [safe_record, record], reason

    def _select_safe_point_for_record(self, record: QueryRecord) -> tuple[SafePoint | None, str]:
        """处理安全点位记录。"""
        self.avoidance_config = self._build_avoidance_config(dict(self.avoidance_config.safe_points))
        if self.avoidance_config.mode == "off":
            return None, "规避模式=关闭"
        if record.func_num != 108:
            return None, "当前指令不是运动类指令"
        safe_point = self._get_active_safe_point()
        if safe_point is None:
            return None, "未配置安全中间点"
        if self.avoidance_config.mode == "always":
            return safe_point, f"规避模式=每次都经过中间点，使用 {safe_point.name}"

        current_rx, current_ry, current_rz = self._current_robot_r_components()
        pose = record.pose_tuple()
        if pose is None:
            return None, "当前指令不是位姿运动"
        target_x, target_y, target_z, target_rx, target_ry, target_rz = pose
        if (
            abs(target_rx - current_rx) >= self.avoidance_config.rx_threshold
            or abs(target_ry - current_ry) >= self.avoidance_config.ry_threshold
            or abs(target_rz - current_rz) >= self.avoidance_config.rz_threshold
        ):
            return safe_point, (
                f"姿态变化过大，使用 {safe_point.name} 过渡 "
                f"(ΔRX={self._fmt(abs(target_rx - current_rx))}, "
                f"ΔRY={self._fmt(abs(target_ry - current_ry))}, "
                f"ΔRZ={self._fmt(abs(target_rz - current_rz))})"
            )
        current_z = self._current_robot_xyz()[2]
        current_x, current_y, _ = self._current_robot_xyz()
        if (
            current_z < self.avoidance_config.low_z_threshold
            and target_z < self.avoidance_config.low_z_threshold
            and (
                abs(target_x - current_x) > self.avoidance_config.xy_move_threshold
                or abs(target_y - current_y) > self.avoidance_config.xy_move_threshold
            )
        ):
            return safe_point, f"低位大范围移动，使用 {safe_point.name} 过渡"
        return None, "未命中自动规避规则"

    def _get_active_safe_point(self) -> SafePoint | None:
        """获取安全点位。"""
        if self.current_safe_point_key and self.current_safe_point_key in self.avoidance_config.safe_points:
            return self.avoidance_config.safe_points[self.current_safe_point_key]
        if self.avoidance_config.safe_points:
            first_key = sorted(self.avoidance_config.safe_points)[0]
            return self.avoidance_config.safe_points[first_key]
        return None

    def _build_safe_point_record(self, point: SafePoint, target_record: QueryRecord) -> QueryRecord:
        """构建安全点位记录。"""
        return QueryRecord(
            query_key=f"中间点-{point.name}",
            func_num=108,
            params={
                "target_x": point.x,
                "target_y": point.y,
                "target_z": point.z,
                "target_rx": point.rx,
                "target_ry": point.ry,
                "target_rz": point.rz,
                "spd_pct": point.speed_percent,
                "acc_pct": point.acc_percent,
                "dec_pct": point.acc_percent,
                "stop_cmd": 0,
                "fuzzy_pos": 0,
                "fuzzy_spd": 0,
                "fuzzy_acc": 0,
                "fuzzy_dec": 0,
                "move_type": 0,
            },
            keywords=point.name,
            description=f"规避中间点：{point.name}",
            safety_level=target_record.safety_level,
        )

    def _current_robot_xyz(self) -> tuple[float, float, float]:
        """处理当前。"""
        return (float(self.robot_x), float(self.robot_y), float(self.robot_z))

    def _current_robot_r_components(self) -> tuple[float, float, float]:
        """处理当前。"""
        parts = [part.strip() for part in self.robot_r.split("/")]
        values = [float(part) for part in parts[:3]]
        while len(values) < 3:
            values.append(0.0)
        return values[0], values[1], values[2]

    def _execute_send_by_protocol(self, client: ControllerClient, record: QueryRecord) -> list[float]:
        """执行相关数据。"""
        return self._execute_send_six(client, record)

