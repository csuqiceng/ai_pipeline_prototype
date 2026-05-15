"""图形界面日志格式化、展示和持久化逻辑。"""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from PySide6.QtWidgets import QFileDialog, QTableWidgetItem

from .exceptions import BackgroundTaskError
from .models import QueryRecord, VrWriteRequest


class GuiLoggingMixin:
    """记录界面日志并写入会话日志文件。"""
    def _controller_mode_value(self) -> str:
        """处理控制器。"""
        if not hasattr(self, "controller_combo"):
            return "unknown"
        return "mock" if self.controller_combo.currentText() == "模拟控制器" else "real"

    def _next_dispatch_id(self) -> str:
        """分发相关数据。"""
        return f"dispatch_{uuid.uuid4().hex[:10]}"

    def _current_log_context(self) -> dict[str, Any]:
        """记录日志当前。"""
        return dict(getattr(self._log_context, "value", {}))

    @contextmanager
    def _push_log_context(self, **context: Any) -> Iterator[None]:
        """记录日志相关数据。"""
        previous = self._current_log_context()
        merged = dict(previous)
        merged.update({key: value for key, value in context.items() if value is not None})
        self._log_context.value = merged
        try:
            yield
        finally:
            self._log_context.value = previous

    def _log_exception_fields(self, exc: Exception) -> dict[str, Any]:
        """记录日志异常字段。"""
        if isinstance(exc, BackgroundTaskError):
            return {
                "error_type": exc.error_type,
                "error_message": exc.error_message,
                "traceback": exc.traceback_text,
            }
        return {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
        }

    def _build_record_dispatch_snapshot(self, record: QueryRecord) -> dict[str, Any]:
        """构建记录快照。"""
        snapshot: dict[str, Any] = {
            "query_key": record.query_key,
            "func_num": record.func_num,
            "description": record.description,
            "params": copy.deepcopy(record.params),
        }
        try:
            six_cmd = self.service.build_six_command_from_record(record)
            snapshot["writes"] = [
                {"start_vr": req.start_vr, "values": list(req.values)}
                for req in [*six_cmd.to_func_writes(), six_cmd.to_trigger_write()]
            ]
        except Exception as exc:
            snapshot["snapshot_error"] = f"{type(exc).__name__}: {exc}"
        return snapshot

    def _build_system_dispatch_snapshot(self, action_key: str, code: int) -> dict[str, Any]:
        """构建系统快照。"""
        snapshot: dict[str, Any] = {"action_key": action_key, "code": code}
        try:
            six_cmd = self.service.build_six_system_command(code)
            snapshot["func_num"] = six_cmd.func_num
            snapshot["writes"] = [
                {"start_vr": req.start_vr, "values": list(req.values)}
                for req in [*six_cmd.to_func_writes(), six_cmd.to_trigger_write()]
            ]
        except Exception as exc:
            snapshot["snapshot_error"] = f"{type(exc).__name__}: {exc}"
        return snapshot

    def _read_persisted_session_logs(self) -> list[dict[str, Any]]:
        """读取相关数据。"""
        if not self._log_session_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._log_session_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append(payload)
        return entries

    def _persist_log_entry(self, entry: dict[str, Any]) -> None:
        """记录日志相关数据。"""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            with self._log_session_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            if not self._log_persist_error_reported:
                self._log_persist_error_reported = True
                print(f"log persist failed: {exc}", file=sys.stderr)

    def _export_logs_snapshot(self, export_path: Path) -> Path:
        """导出快照。"""
        payload = {
            "session_id": self.session_id,
            "source_jsonl": str(self._log_session_path),
            "entries": self._read_persisted_session_logs(),
        }
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return export_path

    def _log_realtime_state_change_if_needed(self) -> None:
        """记录日志实时数据状态。"""
        current = self._capture_realtime_snapshot()
        if self._last_realtime_snapshot is None:
            self._last_realtime_snapshot = current
            return
        if current == self._last_realtime_snapshot:
            return
        prev_overall, prev_busy, prev_run, prev_alarm = self._last_realtime_snapshot
        curr_overall, curr_busy, curr_run, curr_alarm = current
        detail = (
            f"系统状态 {prev_overall} -> {curr_overall} | "
            f"忙闲 {prev_busy} -> {curr_busy} | "
            f"实时 {prev_run} -> {curr_run} | "
            f"报警 {prev_alarm} -> {curr_alarm}"
        )
        self._append_log("反馈", "实时状态变化", "成功", detail, extra=copy.deepcopy(self._last_realtime_snapshot_raw or {}))
        self._last_realtime_snapshot = current

    def _refresh_logs(self) -> None:
        """刷新相关数据。"""
        if not hasattr(self, "log_table"):
            return
        self.log_table.setRowCount(0)
        for row_index, row in enumerate(self.logs[:200]):
            self.log_table.insertRow(row_index)
            for col_index, key in enumerate(["time", "category", "action", "result", "detail"]):
                self.log_table.setItem(row_index, col_index, QTableWidgetItem(str(row.get(key, ""))))
        self.log_count_label.setText(str(len(self.logs)))
        self.log_last_time_label.setText(self.logs[0]["time"] if self.logs else "-")

    def _append_log(
        self,
        category: str,
        action: str,
        result: str,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """追加相关数据。"""
        now = datetime.now()
        with self._log_seq_lock:
            self._log_seq += 1
            seq = self._log_seq
        merged_extra = self._current_log_context()
        if extra:
            merged_extra.update({key: value for key, value in extra.items() if value is not None})
        is_main_thread = threading.current_thread() is threading.main_thread()
        host = merged_extra.get("host")
        if host is None:
            host = self.host_edit.text().strip() if is_main_thread and hasattr(self, "host_edit") else ""
        controller_mode = merged_extra.get("controller_mode")
        if controller_mode is None:
            controller_mode = self._controller_mode_value() if is_main_thread else "unknown"
        entry = {
            "time": now.strftime("%H:%M:%S.%f")[:-3],
            "ts": now.isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "seq": seq,
            "monotonic_ms": int((time.perf_counter() - self._session_start_perf) * 1000),
            "host": host,
            "controller_mode": controller_mode,
            "thread": threading.current_thread().name,
            "category": category,
            "action": action,
            "result": result,
            "detail": detail,
        }
        entry.update(merged_extra)
        if is_main_thread:
            self._append_log_entry(entry)
        else:
            self._run_on_main_thread(lambda e=entry: self._append_log_entry(e))

    def _append_log_entry(self, entry: dict[str, Any]) -> None:
        """追加相关数据。"""
        self.logs.insert(0, entry)
        self.logs = self.logs[:200]
        self._persist_log_entry(entry)
        self._refresh_logs()
    @staticmethod
    def _format_write_request(request: VrWriteRequest) -> str:
        """格式化请求。"""
        values = ", ".join(str(v) for v in request.values)
        return f"VR[{request.start_vr}..{request.start_vr + len(request.values) - 1}] = [{values}]"

    @staticmethod
    def _format_read_request(start_vr: int, count: int) -> str:
        """格式化请求。"""
        return f"读取 VR[{start_vr}..{start_vr + count - 1}]"

    def _clear_logs(self) -> None:
        """清除相关数据。"""
        cleared_count = len(self.logs)
        export_dir = self.runtime_root / "data" / "exported_logs"
        snapshot_path = export_dir / f"robot_qt_logs_before_clear_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        self._export_logs_snapshot(snapshot_path)
        self.logs.clear()
        self._refresh_logs()
        self.status_label.setText("日志已清空。")
        self._append_log("日志", "清空日志", "成功", f"已清空 {cleared_count} 条日志，快照: {snapshot_path}")

    def _export_logs(self) -> None:
        """导出相关数据。"""
        export_dir = self.runtime_root / "data" / "exported_logs"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"robot_qt_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        self._export_logs_snapshot(export_path)
        self.status_label.setText(f"日志已导出: {export_path}")
        self._append_log("日志", "导出日志", "成功", str(export_path))

