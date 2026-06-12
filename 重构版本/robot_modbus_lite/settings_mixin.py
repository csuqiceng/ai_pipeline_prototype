"""系统配置、安全范围和避障配置界面逻辑。"""

from __future__ import annotations

import json

from PySide6.QtWidgets import QTreeWidgetItem

from .avoidance_config import (
    AvoidanceConfig,
    SafePoint,
    load_avoidance_config,
    save_avoidance_config,
    validate_safe_point,
)
from .system_config import AxisRangeConfig, load_system_config, save_system_config, validate_system_config


class SettingsMixin:
    """为主窗口增加配置编辑和校验能力。"""
    def _load_system_config_into_form(self) -> None:
        """加载系统配置表单。"""
        self.axis_ranges = load_system_config(self.system_config_path)
        self.range_x_min_edit.setText(self._fmt(self.axis_ranges.x[0]))
        self.range_x_max_edit.setText(self._fmt(self.axis_ranges.x[1]))
        self.range_y_min_edit.setText(self._fmt(self.axis_ranges.y[0]))
        self.range_y_max_edit.setText(self._fmt(self.axis_ranges.y[1]))
        self.range_z_min_edit.setText(self._fmt(self.axis_ranges.z[0]))
        self.range_z_max_edit.setText(self._fmt(self.axis_ranges.z[1]))
        self.safe_r_min_edit.setText(self._fmt(self.axis_ranges.safe_r_min))
        self.safe_r_max_edit.setText(self._fmt(self.axis_ranges.safe_r_max))
        self.safe_z_min_edit.setText(self._fmt(self.axis_ranges.safe_z_min))
        self.safe_z_max_edit.setText(self._fmt(self.axis_ranges.safe_z_max))
        self.safe_speed_max_edit.setText(self._fmt(self.axis_ranges.safe_speed_max))
        self.safe_acc_max_edit.setText(self._fmt(self.axis_ranges.safe_acc_max))
        self.safe_dec_max_edit.setText(self._fmt(self.axis_ranges.safe_dec_max))
        if hasattr(self, "default_spd_pct_edit"):
            self.default_spd_pct_edit.setText(self._fmt(self.axis_ranges.default_spd_pct))
        if hasattr(self, "default_acc_pct_edit"):
            self.default_acc_pct_edit.setText(self._fmt(self.axis_ranges.default_acc_pct))
        if hasattr(self, "default_dec_pct_edit"):
            self.default_dec_pct_edit.setText(self._fmt(self.axis_ranges.default_dec_pct))
        self.motion_timeout_edit.setText(self._fmt(self.axis_ranges.motion_timeout_sec))
        if hasattr(self, "operator_tts_enabled_check"):
            self.operator_tts_enabled_check.setChecked(bool(self.axis_ranges.operator_tts_enabled))
        if hasattr(self, "broadcast_dedupe_window_edit"):
            self.broadcast_dedupe_window_edit.setText(self._fmt(self.axis_ranges.broadcast_dedupe_window_sec))
        if hasattr(self, "tts_retry_delay_edit"):
            self.tts_retry_delay_edit.setText(self._fmt(self.axis_ranges.tts_retry_delay_sec))
        if hasattr(self, "tts_max_failures_edit"):
            self.tts_max_failures_edit.setText(str(int(self.axis_ranges.tts_max_failures)))
        if hasattr(self, "operator_confirm_timeout_edit"):
            self.operator_confirm_timeout_edit.setText(self._fmt(self.axis_ranges.operator_confirm_timeout_sec))
        if hasattr(self, "l3_min_step_delay_edit"):
            self.l3_min_step_delay_edit.setText(str(int(self.axis_ranges.l3_min_step_delay_ms)))
        if hasattr(self, "l3_cumulative_error_limit_edit"):
            self.l3_cumulative_error_limit_edit.setText(self._fmt(self.axis_ranges.l3_cumulative_error_limit_mm))
        if hasattr(self, "joint_limit_edits"):
            for index, (min_edit, max_edit) in enumerate(self.joint_limit_edits):
                if index < len(self.axis_ranges.joint_limits):
                    min_value, max_value = self.axis_ranges.joint_limits[index]
                    min_edit.setText(self._fmt(min_value))
                    max_edit.setText(self._fmt(max_value))
                else:
                    min_edit.setText("")
                    max_edit.setText("")

    def _reload_system_config(self) -> None:
        """处理系统配置。"""
        self._load_system_config_into_form()
        self.status_label.setText(f"已重载系统配置: {self.system_config_path}")
        self._append_log("后台", "重载系统配置", "成功", json.dumps(self.axis_ranges.to_dict(), ensure_ascii=False))

    def _collect_system_config(self) -> AxisRangeConfig:
        """收集系统配置。"""
        def num(text: str) -> float:
            """处理相关数据。"""
            return float(text.strip()) if text.strip() else 0.0

        return AxisRangeConfig(
            x=(num(self.range_x_min_edit.text()), num(self.range_x_max_edit.text())),
            y=(num(self.range_y_min_edit.text()), num(self.range_y_max_edit.text())),
            z=(num(self.range_z_min_edit.text()), num(self.range_z_max_edit.text())),
            safe_r_min=num(self.safe_r_min_edit.text()),
            safe_r_max=num(self.safe_r_max_edit.text()),
            safe_z_min=num(self.safe_z_min_edit.text()),
            safe_z_max=num(self.safe_z_max_edit.text()),
            safe_speed_max=num(self.safe_speed_max_edit.text()),
            safe_acc_max=num(self.safe_acc_max_edit.text()),
            safe_dec_max=num(self.safe_dec_max_edit.text()),
            default_spd_pct=(
                num(self.default_spd_pct_edit.text())
                if hasattr(self, "default_spd_pct_edit")
                else self.axis_ranges.default_spd_pct
            ),
            default_acc_pct=(
                num(self.default_acc_pct_edit.text())
                if hasattr(self, "default_acc_pct_edit")
                else self.axis_ranges.default_acc_pct
            ),
            default_dec_pct=(
                num(self.default_dec_pct_edit.text())
                if hasattr(self, "default_dec_pct_edit")
                else self.axis_ranges.default_dec_pct
            ),
            motion_timeout_sec=num(self.motion_timeout_edit.text()),
            six_accept_timeout_sec=self.axis_ranges.six_accept_timeout_sec,
            six_busy_timeout_sec=self.axis_ranges.six_busy_timeout_sec,
            six_ready_recovery_timeout_sec=self.axis_ranges.six_ready_recovery_timeout_sec,
            six_post_trigger_settle_sec=self.axis_ranges.six_post_trigger_settle_sec,
            six_status_poll_interval_sec=self.axis_ranges.six_status_poll_interval_sec,
            six_accept_poll_interval_sec=self.axis_ranges.six_accept_poll_interval_sec,
            echo_retry_interval_sec=self.axis_ranges.echo_retry_interval_sec,
            echo_retry_count=self.axis_ranges.echo_retry_count,
            echo_write_rounds=self.axis_ranges.echo_write_rounds,
            echo_compare_epsilon=self.axis_ranges.echo_compare_epsilon,
            emergency_codes=self.axis_ranges.emergency_codes,
            operator_tts_enabled=(
                bool(self.operator_tts_enabled_check.isChecked())
                if hasattr(self, "operator_tts_enabled_check")
                else self.axis_ranges.operator_tts_enabled
            ),
            broadcast_dedupe_window_sec=(
                num(self.broadcast_dedupe_window_edit.text())
                if hasattr(self, "broadcast_dedupe_window_edit")
                else self.axis_ranges.broadcast_dedupe_window_sec
            ),
            tts_retry_delay_sec=(
                num(self.tts_retry_delay_edit.text())
                if hasattr(self, "tts_retry_delay_edit")
                else self.axis_ranges.tts_retry_delay_sec
            ),
            tts_max_failures=(
                int(num(self.tts_max_failures_edit.text()))
                if hasattr(self, "tts_max_failures_edit")
                else self.axis_ranges.tts_max_failures
            ),
            operator_confirm_timeout_sec=(
                num(self.operator_confirm_timeout_edit.text())
                if hasattr(self, "operator_confirm_timeout_edit")
                else self.axis_ranges.operator_confirm_timeout_sec
            ),
            operator_dashboard_refresh_ms=self.axis_ranges.operator_dashboard_refresh_ms,
            operator_view_refresh_ms=self.axis_ranges.operator_view_refresh_ms,
            controller_realtime_poll_ms=self.axis_ranges.controller_realtime_poll_ms,
            dashboard_stale_after_ms=self.axis_ranges.dashboard_stale_after_ms,
            l3_min_step_delay_ms=(
                int(num(self.l3_min_step_delay_edit.text()))
                if hasattr(self, "l3_min_step_delay_edit")
                else self.axis_ranges.l3_min_step_delay_ms
            ),
            l3_cumulative_error_limit_mm=(
                num(self.l3_cumulative_error_limit_edit.text())
                if hasattr(self, "l3_cumulative_error_limit_edit")
                else self.axis_ranges.l3_cumulative_error_limit_mm
            ),
            l3_forbidden_boxes=self.axis_ranges.l3_forbidden_boxes,
            joint_limits=self._collect_joint_limits(),
        )

    def _save_system_config(self) -> None:
        """保存系统配置。"""
        try:
            config = self._collect_system_config()
        except ValueError:
            self._show_warning("保存失败", "系统配置必须是数字。")
            self._append_log("后台", "保存系统配置", "失败", "系统配置必须是数字")
            return
        validation_error = validate_system_config(config)
        if validation_error:
            self._show_warning("保存失败", validation_error)
            self._append_log("后台", "保存系统配置", "失败", validation_error)
            return
        save_system_config(self.system_config_path, config)
        self.axis_ranges = config
        self._apply_runtime_timing_config()
        if hasattr(self, "operator_tts_check"):
            self.operator_tts_check.setChecked(bool(config.operator_tts_enabled))
        controller_sync_error = ""
        host = self.host_edit.text().strip() if hasattr(self, "host_edit") else ""
        if host:
            try:
                client = self._get_client(host)
                client.write_modbus_float(self.service.build_six_safety_limits_write(config))
                self._append_log(
                    "后台",
                    "下发安全限位",
                    "成功",
                    json.dumps(self.service.parse_six_safety_limits(list(self.service.build_six_safety_limits_write(config).values)), ensure_ascii=False),
                )
            except Exception as exc:
                controller_sync_error = str(exc)
                self._append_log("后台", "下发安全限位", "失败", str(exc))
        if controller_sync_error:
            self.status_label.setText(f"已保存本地系统配置，但控制器限位下发失败: {controller_sync_error}")
            self._show_warning("部分成功", f"本地配置已保存，但控制器 1700~1730 下发失败：\n{controller_sync_error}")
        else:
            self.status_label.setText(f"已保存系统配置: {self.system_config_path}")
        self._append_log("后台", "保存系统配置", "成功", json.dumps(config.to_dict(), ensure_ascii=False))

    def _read_controller_safety_limits(self) -> None:
        """读取控制器。"""
        host = self.host_edit.text().strip()
        if not host:
            self._show_warning("读取失败", "请输入控制器地址。")
            self._append_log("后台", "读取控制器限位", "失败", "地址为空")
            return
        try:
            client = self._get_client(host)
            values = client.read_modbus_float(self.service.build_six_safety_limits_read())
            limits = self.service.parse_six_safety_limits(values)
        except Exception as exc:
            self._show_warning("读取失败", str(exc))
            self._append_log("后台", "读取控制器限位", "失败", str(exc))
            return

        self.safe_r_min_edit.setText(self._fmt(limits["safe_r_min"]))
        self.safe_r_max_edit.setText(self._fmt(limits["safe_r_max"]))
        self.safe_z_min_edit.setText(self._fmt(limits["safe_z_min"]))
        self.safe_z_max_edit.setText(self._fmt(limits["safe_z_max"]))
        self.safe_speed_max_edit.setText(self._fmt(limits["safe_speed_max"]))
        self.safe_acc_max_edit.setText(self._fmt(limits["safe_acc_max"]))
        self.safe_dec_max_edit.setText(self._fmt(limits["safe_dec_max"]))
        self.controller_pose_angle_limits = {
            "pose_upper_angle": limits.get("pose_upper_angle", 90.0),
            "pose_lower_angle": limits.get("pose_lower_angle", 90.0),
            "pose_cw_angle": limits.get("pose_cw_angle", 90.0),
            "pose_ccw_angle": limits.get("pose_ccw_angle", 90.0),
        }
        self.axis_ranges = AxisRangeConfig(
            x=self.axis_ranges.x,
            y=self.axis_ranges.y,
            z=self.axis_ranges.z,
            safe_r_min=limits["safe_r_min"],
            safe_r_max=limits["safe_r_max"],
            safe_z_min=limits["safe_z_min"],
            safe_z_max=limits["safe_z_max"],
            safe_speed_max=limits["safe_speed_max"],
            safe_acc_max=limits["safe_acc_max"],
            safe_dec_max=limits["safe_dec_max"],
            default_spd_pct=self.axis_ranges.default_spd_pct,
            default_acc_pct=self.axis_ranges.default_acc_pct,
            default_dec_pct=self.axis_ranges.default_dec_pct,
            motion_timeout_sec=self.axis_ranges.motion_timeout_sec,
            six_accept_timeout_sec=self.axis_ranges.six_accept_timeout_sec,
            six_busy_timeout_sec=self.axis_ranges.six_busy_timeout_sec,
            six_ready_recovery_timeout_sec=self.axis_ranges.six_ready_recovery_timeout_sec,
            six_post_trigger_settle_sec=self.axis_ranges.six_post_trigger_settle_sec,
            six_status_poll_interval_sec=self.axis_ranges.six_status_poll_interval_sec,
            six_accept_poll_interval_sec=self.axis_ranges.six_accept_poll_interval_sec,
            echo_retry_interval_sec=self.axis_ranges.echo_retry_interval_sec,
            echo_retry_count=self.axis_ranges.echo_retry_count,
            echo_write_rounds=self.axis_ranges.echo_write_rounds,
            echo_compare_epsilon=self.axis_ranges.echo_compare_epsilon,
            emergency_codes=self.axis_ranges.emergency_codes,
            operator_tts_enabled=self.axis_ranges.operator_tts_enabled,
            broadcast_dedupe_window_sec=self.axis_ranges.broadcast_dedupe_window_sec,
            tts_retry_delay_sec=self.axis_ranges.tts_retry_delay_sec,
            tts_max_failures=self.axis_ranges.tts_max_failures,
            operator_confirm_timeout_sec=self.axis_ranges.operator_confirm_timeout_sec,
            operator_dashboard_refresh_ms=self.axis_ranges.operator_dashboard_refresh_ms,
            operator_view_refresh_ms=self.axis_ranges.operator_view_refresh_ms,
            controller_realtime_poll_ms=self.axis_ranges.controller_realtime_poll_ms,
            dashboard_stale_after_ms=self.axis_ranges.dashboard_stale_after_ms,
            l3_min_step_delay_ms=self.axis_ranges.l3_min_step_delay_ms,
            l3_cumulative_error_limit_mm=self.axis_ranges.l3_cumulative_error_limit_mm,
            l3_forbidden_boxes=self.axis_ranges.l3_forbidden_boxes,
            joint_limits=self.axis_ranges.joint_limits,
        )
        self._apply_runtime_timing_config()
        self.status_label.setText("已读取控制器安全限位。")
        self._append_log("后台", "读取控制器限位", "成功", json.dumps(limits, ensure_ascii=False))

    def _apply_runtime_timing_config(self) -> None:
        """Apply timing-related config to already-created Qt timers and dashboard cache."""
        config = getattr(self, "axis_ranges", None)
        if config is None:
            return
        interval_pairs = (
            ("operator_dashboard_timer", int(getattr(config, "operator_dashboard_refresh_ms", 50))),
            ("operator_refresh_timer", int(getattr(config, "operator_view_refresh_ms", 500))),
            ("realtime_timer", int(getattr(config, "controller_realtime_poll_ms", 500))),
        )
        for attr_name, interval_ms in interval_pairs:
            timer = getattr(self, attr_name, None)
            if timer is not None and hasattr(timer, "setInterval"):
                timer.setInterval(max(1, interval_ms))
        cache = getattr(self, "operator_dashboard_cache", None)
        if cache is not None:
            if hasattr(cache, "refresh_ms"):
                cache.refresh_ms = max(1, int(getattr(config, "operator_dashboard_refresh_ms", 50)))
            if hasattr(cache, "stale_after_ms"):
                cache.stale_after_ms = max(1, int(getattr(config, "dashboard_stale_after_ms", 1000)))

    def _collect_joint_limits(self) -> tuple[tuple[float, float], ...]:
        """Collect optional J1-J6 soft limits from the Qt settings form."""
        if not hasattr(self, "joint_limit_edits"):
            return self.axis_ranges.joint_limits
        limits: list[tuple[float, float]] = []
        for min_edit, max_edit in self.joint_limit_edits:
            min_text = min_edit.text().strip()
            max_text = max_edit.text().strip()
            if not min_text and not max_text:
                continue
            limits.append((float(min_text or 0), float(max_text or 0)))
        return tuple(limits)

    def _load_avoidance_config_into_form(self) -> None:
        """加载避障配置表单。"""
        self.avoidance_config = load_avoidance_config(self.avoidance_config_path)
        mode_text = {
            "off": "关闭",
            "auto": "自动判断",
            "always": "每次都经过中间点",
        }.get(self.avoidance_config.mode, "关闭")
        self.avoidance_mode_combo.setCurrentText(mode_text)
        self.rule_rx_threshold_edit.setText(self._fmt(self.avoidance_config.rx_threshold))
        self.rule_ry_threshold_edit.setText(self._fmt(self.avoidance_config.ry_threshold))
        self.rule_rz_threshold_edit.setText(self._fmt(self.avoidance_config.rz_threshold))
        self.rule_low_z_threshold_edit.setText(self._fmt(self.avoidance_config.low_z_threshold))
        self.rule_xy_move_threshold_edit.setText(self._fmt(self.avoidance_config.xy_move_threshold))
        self._refresh_safe_point_tree()
        if self.current_safe_point_key and self.current_safe_point_key in self.avoidance_config.safe_points:
            self._load_safe_point_into_form(self.avoidance_config.safe_points[self.current_safe_point_key])
        elif self.avoidance_config.safe_points:
            first_key = sorted(self.avoidance_config.safe_points)[0]
            self.current_safe_point_key = first_key
            self._load_safe_point_into_form(self.avoidance_config.safe_points[first_key])
        else:
            self._new_safe_point()

    def _refresh_safe_point_tree(self) -> None:
        """刷新安全点位树。"""
        self.safe_point_tree.clear()
        for point in sorted(self.avoidance_config.safe_points.values(), key=lambda item: item.name):
            item = QTreeWidgetItem([point.name, point.description or "-"])
            self.safe_point_tree.addTopLevelItem(item)
            if self.current_safe_point_key == point.name:
                self.safe_point_tree.setCurrentItem(item)

    def _load_safe_point_into_form(self, point: SafePoint) -> None:
        """加载安全点位表单。"""
        self.safe_point_name_edit.setText(point.name)
        self.safe_point_x_edit.setText(self._fmt(point.x))
        self.safe_point_y_edit.setText(self._fmt(point.y))
        self.safe_point_z_edit.setText(self._fmt(point.z))
        self.safe_point_rx_edit.setText(self._fmt(point.rx))
        self.safe_point_ry_edit.setText(self._fmt(point.ry))
        self.safe_point_rz_edit.setText(self._fmt(point.rz))
        self.safe_point_speed_edit.setText(self._fmt(point.speed_percent))
        self.safe_point_acc_edit.setText(self._fmt(point.acc_percent))
        self.safe_point_desc_edit.setText(point.description)

    def _new_safe_point(self) -> None:
        """处理安全点位。"""
        self.current_safe_point_key = None
        self.safe_point_name_edit.setText("")
        self.safe_point_x_edit.setText("0")
        self.safe_point_y_edit.setText("0")
        self.safe_point_z_edit.setText("200")
        self.safe_point_rx_edit.setText("0")
        self.safe_point_ry_edit.setText("0")
        self.safe_point_rz_edit.setText("0")
        self.safe_point_speed_edit.setText("20")
        self.safe_point_acc_edit.setText("20")
        self.safe_point_desc_edit.setText("")
        self.status_label.setText("已创建空白安全中间点。")
        self._append_log("后台", "新增中间点", "成功", "已创建空白安全中间点")

    def _collect_safe_point(self) -> SafePoint:
        """收集安全点位。"""
        def num(text: str) -> float:
            """处理相关数据。"""
            text = text.strip().replace("%", "")
            return float(text) if text else 0.0

        return SafePoint(
            name=self.safe_point_name_edit.text().strip(),
            x=num(self.safe_point_x_edit.text()),
            y=num(self.safe_point_y_edit.text()),
            z=num(self.safe_point_z_edit.text()),
            rx=num(self.safe_point_rx_edit.text()),
            ry=num(self.safe_point_ry_edit.text()),
            rz=num(self.safe_point_rz_edit.text()),
            speed_percent=num(self.safe_point_speed_edit.text()),
            acc_percent=num(self.safe_point_acc_edit.text()),
            description=self.safe_point_desc_edit.text().strip(),
        )

    def _build_avoidance_config(self, safe_points: dict[str, SafePoint] | None = None) -> AvoidanceConfig:
        """构建避障配置。"""
        def num(text: str) -> float:
            """处理相关数据。"""
            text = text.strip().replace("%", "")
            return float(text) if text else 0.0

        mode = {
            "关闭": "off",
            "自动判断": "auto",
            "每次都经过中间点": "always",
        }.get(self.avoidance_mode_combo.currentText(), "off")
        return AvoidanceConfig(
            mode=mode,
            rx_threshold=num(self.rule_rx_threshold_edit.text()),
            ry_threshold=num(self.rule_ry_threshold_edit.text()),
            rz_threshold=num(self.rule_rz_threshold_edit.text()),
            low_z_threshold=num(self.rule_low_z_threshold_edit.text()),
            xy_move_threshold=num(self.rule_xy_move_threshold_edit.text()),
            safe_points=safe_points if safe_points is not None else dict(self.avoidance_config.safe_points),
            rules=self.avoidance_config.rules,
        )

    def _save_safe_point(self) -> None:
        """保存安全点位。"""
        try:
            point = self._collect_safe_point()
        except ValueError:
            self._show_warning("保存失败", "中间点参数必须是数字。")
            self._append_log("后台", "保存中间点", "失败", "中间点参数必须是数字")
            return
        validation_error = validate_safe_point(point)
        if validation_error:
            self._show_warning("保存失败", validation_error)
            self._append_log("后台", "保存中间点", "失败", validation_error)
            return
        safe_points = dict(self.avoidance_config.safe_points)
        if self.current_safe_point_key and self.current_safe_point_key != point.name and self.current_safe_point_key in safe_points:
            del safe_points[self.current_safe_point_key]
        safe_points[point.name] = point
        self.avoidance_config = self._build_avoidance_config(safe_points)
        save_avoidance_config(self.avoidance_config_path, self.avoidance_config)
        self.current_safe_point_key = point.name
        self._refresh_safe_point_tree()
        self.status_label.setText(f"已保存安全中间点: {point.name}")
        self._append_log("后台", "保存中间点", "成功", point.name)

    def _delete_safe_point(self) -> None:
        """删除安全点位。"""
        key = self.safe_point_name_edit.text().strip()
        if not key:
            self._show_warning("无法删除", "当前没有选中的中间点。")
            self._append_log("后台", "删除中间点", "失败", "当前没有选中的中间点")
            return
        safe_points = dict(self.avoidance_config.safe_points)
        if key not in safe_points:
            self._show_warning("无法删除", f"中间点不存在: {key}")
            self._append_log("后台", "删除中间点", "失败", f"中间点不存在: {key}")
            return
        del safe_points[key]
        self.avoidance_config = self._build_avoidance_config(safe_points)
        save_avoidance_config(self.avoidance_config_path, self.avoidance_config)
        self.current_safe_point_key = None
        self._refresh_safe_point_tree()
        self._new_safe_point()
        self.status_label.setText(f"已删除安全中间点: {key}")
        self._append_log("后台", "删除中间点", "成功", key)

    def _save_avoidance_config_only(self) -> None:
        """保存避障配置。"""
        self.avoidance_config = self._build_avoidance_config()
        save_avoidance_config(self.avoidance_config_path, self.avoidance_config)
        self.status_label.setText(f"已保存规避配置: {self.avoidance_config_path}")
        self._append_log(
            "后台",
            "保存规避配置",
            "成功",
            json.dumps(
                {
                    "mode": self.avoidance_config.mode,
                    "rx_threshold": self.avoidance_config.rx_threshold,
                    "ry_threshold": self.avoidance_config.ry_threshold,
                    "rz_threshold": self.avoidance_config.rz_threshold,
                    "safe_points": list(self.avoidance_config.safe_points),
                },
                ensure_ascii=False,
            ),
        )

    def _on_safe_point_selected(self) -> None:
        """处理安全点位选中。"""
        items = self.safe_point_tree.selectedItems()
        if not items:
            return
        key = items[0].text(0)
        if key in self.avoidance_config.safe_points:
            self.current_safe_point_key = key
            self._load_safe_point_into_form(self.avoidance_config.safe_points[key])

