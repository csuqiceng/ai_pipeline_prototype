"""自然语言输入解析、计划生成和执行逻辑。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer

from .atomic_memory import AtomicMemory
from .assistant_knowledge_base import AssistantKnowledgeBase
from .memory_params import MemoryManager
from .permission_service import PermissionService
from .position_registry import PositionRegistry, migrate_atomic_positions
from .voice_nlp_adapter import VoiceNlpAction, VoiceNlpAdapter, VoiceNlpPlan


class NlpMixin:
    """为主窗口增加自然语言规划和执行能力。"""
    def _build_voice_nlp_adapter(self) -> VoiceNlpAdapter:
        """构建语音自然语言。"""
        if not hasattr(self, "_atomic_memory"):
            self._atomic_memory = AtomicMemory.load(self._atomic_memory_path())
        self._atomic_memory.position_registry = self._position_registry()
        adapter = getattr(self, "_voice_nlp_adapter_instance", None)
        if adapter is None:
            adapter = VoiceNlpAdapter(self.table, self.service.list_flow_names(), atomic_memory=self._atomic_memory)
            self._voice_nlp_adapter_instance = adapter
        else:
            adapter.table = self.table
            adapter.flow_names = tuple(sorted(str(name) for name in self.service.list_flow_names()))
            adapter.atomic_memory = self._atomic_memory
        adapter.knowledge_base = AssistantKnowledgeBase.load()
        if self._deepseek_client:
            adapter.set_deepseek_client(self._deepseek_client)
        adapter.set_diagnostic_callback(
            lambda action, result, detail: self._append_log("自然语言", action, result, detail)
        )
        if hasattr(adapter, "set_runtime_context_provider") and hasattr(self, "_operator_deepseek_runtime_context"):
            adapter.set_runtime_context_provider(self._operator_deepseek_runtime_context)
        return adapter

    def _set_nlp_result_plan(self, plan: VoiceNlpPlan) -> None:
        """设置自然语言结果。"""
        self.nlp_last_plan = plan
        self.nlp_result_edit.setPlainText(json.dumps(plan.to_preview_dict(), ensure_ascii=False, indent=2))

    def _set_nlp_parse_busy(self, busy: bool) -> None:
        """设置自然语言忙。"""
        self.nlp_parse_running = busy
        if hasattr(self, "nlp_parse_btn"):
            self.nlp_parse_btn.setEnabled(not busy)
            self.nlp_parse_btn.setText("解析中" if busy else "解析文本")
        if hasattr(self, "nlp_execute_btn"):
            self.nlp_execute_btn.setEnabled(not busy)
        if hasattr(self, "nlp_clear_btn"):
            self.nlp_clear_btn.setEnabled(not busy)
        if hasattr(self, "nlp_use_deepseek_check"):
            self.nlp_use_deepseek_check.setEnabled(not busy)
        if hasattr(self, "mic_device_combo"):
            self.mic_device_combo.setEnabled(not busy)

    def _set_nlp_execute_busy(self, busy: bool) -> None:
        """设置自然语言忙。"""
        self.nlp_sequence_running = busy
        if hasattr(self, "nlp_execute_btn"):
            self.nlp_execute_btn.setEnabled(not busy)
            self.nlp_execute_btn.setText("执行中" if busy else "执行")
        if hasattr(self, "nlp_parse_btn"):
            self.nlp_parse_btn.setEnabled(not busy and not self.nlp_parse_running)
        if hasattr(self, "nlp_clear_btn"):
            self.nlp_clear_btn.setEnabled(not busy)
        if hasattr(self, "nlp_use_deepseek_check"):
            self.nlp_use_deepseek_check.setEnabled(not busy and not self.nlp_parse_running)
        if hasattr(self, "mic_device_combo"):
            self.mic_device_combo.setEnabled(not busy and not self.nlp_parse_running)

    def _parse_nlp_text(self) -> None:
        """解析自然语言。"""
        text = self.nlp_input_edit.toPlainText().strip()
        if not text:
            self._show_warning("输入为空", "请输入自然语言文本。")
            self._append_log("自然语言", "解析文本", "失败", "输入为空")
            return
        if self.nlp_parse_running:
            return
        use_deepseek = self.nlp_use_deepseek_check.isChecked()
        self._set_nlp_parse_busy(True)
        self.status_label.setText("自然语言解析中，请稍候...")

        def work():
            """处理相关数据。"""
            return self._build_voice_nlp_adapter().parse(
                text,
                use_deepseek=use_deepseek,
            )

        def on_result(result):
            """处理结果。"""
            self._set_nlp_parse_busy(False)
            if isinstance(result, Exception):
                self.status_label.setText(f"自然语言解析失败: {result}")
                self._append_log("自然语言", "解析文本", "失败", str(result))
                self._show_critical("解析失败", str(result))
                return
            plan = result
            self._set_nlp_result_plan(plan)
            first_action = plan.actions[0] if plan.actions else VoiceNlpAction("unknown", None, plan.source, text, plan.reason)
            self.status_label.setText(
                f"解析完成: {len(plan.actions)} 步 / {first_action.action_type} / {first_action.target or '-'}"
            )
            self._append_log(
                "自然语言",
                "解析文本",
                "成功" if plan.actions and plan.actions[0].action_type != "unknown" else "失败",
                f"{plan.source} | {len(plan.actions)}步 | {plan.reason}",
            )

        self._run_in_background(work, on_result)

    def _execute_nlp_text(self) -> None:
        """执行自然语言。"""
        text = self.nlp_input_edit.toPlainText().strip()
        if not text:
            self._show_warning("输入为空", "请输入自然语言文本。")
            self._append_log("自然语言", "执行解析", "失败", "输入为空")
            return
        if self.nlp_parse_running:
            self._show_info("解析中", "当前正在进行自然语言解析，请等待解析完成。")
            return
        if self.nlp_sequence_running:
            self._show_info("自然语言执行中", "当前自然语言动作序列正在执行。")
            return
        if self.flow_running:
            self._show_info("流程运行中", "当前流程执行中，请先停止流程或等待流程完成。")
            self._append_log("自然语言", "执行解析", "失败", "流程执行中，拒绝自然语言执行")
            return
        use_deepseek = self.nlp_use_deepseek_check.isChecked()
        chat_delta_callback = (
            self._operator_streaming_chat_delta_callback()
            if use_deepseek and hasattr(self, "_operator_streaming_chat_delta_callback")
            else None
        )
        if hasattr(self, "_operator_maybe_begin_streaming_chat_for_text"):
            self._operator_maybe_begin_streaming_chat_for_text(text, use_deepseek=use_deepseek)
        self._set_nlp_execute_busy(True)
        self.status_label.setText("自然语言执行准备中，请稍候...")

        def work():
            """处理相关数据。"""
            return self._build_voice_nlp_adapter().parse(
                text,
                use_deepseek=use_deepseek,
                chat_delta_callback=chat_delta_callback,
            )

        def on_result(result):
            """处理结果。"""
            if isinstance(result, Exception):
                self._set_nlp_execute_busy(False)
                self.status_label.setText(f"自然语言执行准备失败: {result}")
                self._append_log("自然语言", "执行解析", "失败", str(result))
                self._show_critical("执行失败", str(result))
                return
            plan = result
            self._set_nlp_result_plan(plan)
            self._execute_nlp_plan(plan)

        self._run_in_background(work, on_result)

    def _clear_nlp_text(self) -> None:
        """清除自然语言。"""
        self.nlp_input_edit.clear()
        self.nlp_result_edit.clear()
        self.nlp_last_plan = None
        self.status_label.setText("自然语言输入已清空。")

    def _execute_nlp_plan(self, plan: VoiceNlpPlan) -> None:
        """执行自然语言。"""
        if not plan.actions:
            self._set_nlp_execute_busy(False)
            self._show_warning("无法执行", f"未识别到可执行动作。\n{plan.reason}")
            self._append_log("自然语言", "执行解析", "失败", plan.reason)
            return
        if any(action.action_type == "unknown" for action in plan.actions):
            self._set_nlp_execute_busy(False)
            self._show_warning("无法执行", f"未识别到可执行动作。\n{plan.reason}")
            self._append_log("自然语言", "执行解析", "失败", plan.reason)
            return
        self._nlp_pending_actions = list(plan.actions)
        self._nlp_pending_index = 0
        self._nlp_current_plan = plan
        self._append_log(
            "自然语言",
            "执行解析",
            "成功",
            f"{plan.source} | {len(plan.actions)}步 | {plan.reason}",
        )
        self._run_next_nlp_action()

    def _run_next_nlp_action(self) -> None:
        """运行自然语言。"""
        if not self.nlp_sequence_running:
            return
        if self._nlp_pending_index >= len(self._nlp_pending_actions):
            total = len(self._nlp_pending_actions)
            self._set_nlp_execute_busy(False)
            self.status_label.setText(f"自然语言执行完成，共 {total} 步。")
            self._append_log("自然语言", "动作序列完成", "成功", f"共执行 {total} 步")
            return

        step_no = self._nlp_pending_index + 1
        action = self._nlp_pending_actions[self._nlp_pending_index]
        self.status_label.setText(f"自然语言执行第 {step_no} 步: {action.action_type} / {action.target or '-'}")
        self._append_log(
            "自然语言",
            f"动作序列第{step_no}步开始",
            "成功",
            f"{action.action_type} | {action.target or '-'} | {action.source}",
        )

        def on_step_done(ok: bool) -> None:
            """处理步骤完成。"""
            step_result = "成功" if ok else "失败"
            self._append_log(
                "自然语言",
                f"动作序列第{step_no}步{step_result}",
                step_result,
                f"{action.action_type} | {action.target or '-'} | {action.source}",
            )
            if not ok:
                self._set_nlp_execute_busy(False)
                self.status_label.setText(f"自然语言执行失败，停止于第 {step_no} 步。")
                self._append_log("自然语言", "动作序列终止", "失败", f"停止于第 {step_no} 步")
                return
            if action.action_type in {"template", "atomic_template"} and action.target:
                self._update_memory_params_from_action(action.target)
            self._nlp_pending_index += 1
            QTimer.singleShot(0, self._run_next_nlp_action)

        if action.action_type in {"template", "atomic_template"} and action.target:
            if self.flow_running:
                on_step_done(False)
                return
            if action.action_type == "atomic_template" and not self._nlp_register_atomic_record(action.target):
                on_step_done(False)
                return
            self._execute_query_key(action.target, on_done=on_step_done)
            return
        if action.action_type == "memory":
            if not self._nlp_apply_memory_action(action, plan=getattr(self, "_nlp_current_plan", None)):
                on_step_done(False)
                return
            self._save_atomic_memory()
            self.status_label.setText(f"自然语言记忆参数已更新: {action.target or '-'}")
            on_step_done(True)
            return
        if action.action_type == "system" and action.target:
            self._handle_system_action(action.target, on_done=on_step_done)
            return
        if action.action_type == "flow" and action.target:
            self.flow_combo.setCurrentText(action.target)
            self._start_flow(on_done=on_step_done)
            return

        on_step_done(False)

    def _nlp_register_atomic_record(self, target: str) -> bool:
        plan = getattr(self, "_nlp_current_plan", None)
        record = getattr(plan, "atomic_records", {}).get(target) if plan is not None else None
        if record is None:
            self._append_log("自然语言", "原子模板解析", "失败", f"未找到原子模板记录: {target}")
            return False
        self.table[target] = record
        if hasattr(self, "_atomic_memory"):
            self._atomic_memory.remember_record(record)
            self._save_atomic_memory()
        return True

    def _nlp_apply_memory_action(self, action: VoiceNlpAction, *, plan: VoiceNlpPlan | None = None) -> bool:
        target = str(action.target or "")
        if not target.startswith("position_save:"):
            return True
        memory = getattr(self, "_atomic_memory", None)
        if memory is None:
            self._append_log("自然语言", "保存位置", "失败", "原子记忆对象不存在")
            return False
        name = target.split(":", 1)[1].strip().upper()
        if not name:
            self._append_log("自然语言", "保存位置", "失败", "未识别位置名称")
            return False
        try:
            pose = self._operator_current_pose_tuple() if hasattr(self, "_operator_current_pose_tuple") else (
                float(getattr(self, "robot_x", 0.0)),
                float(getattr(self, "robot_y", 0.0)),
                float(getattr(self, "robot_z", 0.0)),
                0.0,
                0.0,
                0.0,
            )
            memory.save_position(name, pose)
            registry = self._position_registry()
            ok, message = registry.set_position(
                name,
                pose,
                spd=int(getattr(memory, "current_speed", 50)),
                move_type=0,
                created_by=self._nlp_permission_service().normalized_actor(),
            )
            if not ok:
                self._append_log("自然语言", "保存位置", "失败", message)
                return False
            self._append_log("自然语言", "保存位置", "成功", f"位置{name}: {pose}")
            if plan is not None:
                try:
                    setattr(plan, "_atomic_position_saved", {"name": name, "pose": pose})
                except Exception:
                    pass
            return True
        except Exception as exc:
            self._append_log("自然语言", "保存位置", "失败", str(exc))
            return False

    def _atomic_memory_path(self):
        runtime_root = getattr(self, "runtime_root", None)
        if runtime_root is not None:
            return runtime_root / "data" / "atomic_state.json"
        return Path("data") / "atomic_state.json"

    def _position_registry_path(self):
        runtime_root = getattr(self, "runtime_root", None)
        if runtime_root is not None:
            return runtime_root / "data" / "position_registry.json"
        return Path("data") / "position_registry.json"

    def _memory_params_path(self):
        runtime_root = getattr(self, "runtime_root", None)
        if runtime_root is not None:
            return runtime_root / "data" / "memory_params.json"
        return Path("data") / "memory_params.json"

    def _nlp_permission_service(self) -> PermissionService:
        actor = (
            getattr(self, "_authenticated_role", None)
            or getattr(self, "current_user_role", None)
            or getattr(self, "user_role", None)
            or "engineer"
        )
        return PermissionService(str(actor))

    def _position_registry(self) -> PositionRegistry:
        permission = self._nlp_permission_service()
        if not hasattr(self, "_position_registry_instance"):
            self._migrate_legacy_atomic_positions(permission=permission)
            self._position_registry_instance = PositionRegistry(self._position_registry_path(), permission=permission)
        else:
            self._position_registry_instance.permission = permission
        return self._position_registry_instance

    def _migrate_legacy_atomic_positions(self, *, permission: PermissionService) -> None:
        marker = "_legacy_atomic_positions_migrated"
        if getattr(self, marker, False):
            return
        setattr(self, marker, True)
        try:
            result = migrate_atomic_positions(
                self._atomic_memory_path(),
                self._position_registry_path(),
                permission=permission,
                created_by="atomic_migration",
            )
            if result.get("created") and hasattr(self, "_append_log"):
                self._append_log(
                    "位置库",
                    "旧原子位置迁移",
                    "成功",
                    f"created={result.get('created')} skipped={result.get('skipped')} failed={result.get('failed')}",
                )
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("位置库", "旧原子位置迁移", "失败", str(exc))

    def _memory_manager(self) -> MemoryManager:
        if not hasattr(self, "_memory_manager_instance"):
            self._memory_manager_instance = MemoryManager(self._memory_params_path())
        return self._memory_manager_instance

    def _update_memory_params_from_action(self, target: str) -> None:
        updated = getattr(self, "_memory_params_updated_query_keys", set())
        if target in updated:
            return
        record = getattr(self, "table", {}).get(target)
        if record is None:
            return
        self._update_memory_params_from_record(record)

    def _update_memory_params_from_record(self, record) -> None:
        action_by_func = {
            11: "移动",
            106: "点动",
            107: "点动",
            108: "移动",
        }
        action = action_by_func.get(int(getattr(record, "func_num", 0) or 0))
        if action is None:
            return
        try:
            self._memory_manager().update_after_command(action, dict(getattr(record, "params", {}) or {}))
            updated = set(getattr(self, "_memory_params_updated_query_keys", set()))
            query_key = getattr(record, "query_key", None)
            if query_key:
                updated.add(str(query_key))
                self._memory_params_updated_query_keys = updated
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("自然语言", "记忆参数更新", "失败", str(exc))

    def _save_atomic_memory(self) -> None:
        memory = getattr(self, "_atomic_memory", None)
        if memory is None:
            return
        try:
            memory.save(self._atomic_memory_path())
        except Exception as exc:
            if hasattr(self, "_append_log"):
                self._append_log("自然语言", "原子记忆保存", "失败", str(exc))


