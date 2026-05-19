"""Local Web API state store for the HTML replacement UI.

This module is intentionally controller-free. B1/B2 use it as a stable mock
state source; later milestones can replace its internals with Qt bridge or
service-layer snapshots without changing API routes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_time_label() -> str:
    return datetime.now().strftime("%H:%M:%S")


class MockWebStateStore:
    """Thread-safe mock dashboard state for the first Web API milestone."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = self._initial_state()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state["snapshot"])

    def dashboard(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def conversation_events(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            events = self._state["recent_events"]
            if session_id:
                events = [event for event in events if event["session_id"] == session_id]
            return deepcopy(events[-limit:])

    def submit_conversation(
        self,
        text: str,
        session_id: str = "mock-session",
        nlp_preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("empty_input")

        with self._lock:
            self._append_event("user", "message", clean_text, session_id=session_id)

            if self._has_active_plan():
                event = self._append_event(
                    "assistant",
                    "receipt",
                    "当前已有计划处于确认或执行状态。请先确认、取消或等待当前计划结束。",
                    session_id=session_id,
                )
                return {"accepted": False, "event": deepcopy(event), "dashboard": deepcopy(self._state)}

            if self._is_query(clean_text):
                event = self._append_event(
                    "assistant",
                    "message",
                    self._build_status_reply(),
                    session_id=session_id,
                )
                return {"accepted": True, "event": deepcopy(event), "dashboard": deepcopy(self._state)}

            if self._is_unknown_nlp(nlp_preview):
                event = self._append_event(
                    "assistant",
                    "message",
                    "未识别到可执行模板、流程或系统动作。请换一种说法，或使用工程师页确认模板是否存在。",
                    session_id=session_id,
                )
                return {"accepted": False, "event": deepcopy(event), "nlp": nlp_preview, "dashboard": deepcopy(self._state)}

            plan = self._create_plan(clean_text, session_id, nlp_preview=nlp_preview)
            precheck = self._create_precheck(plan["plan_id"])
            execution = self._create_waiting_execution(plan["plan_id"])

            self._state["active_plan"] = plan
            self._state["precheck"] = precheck
            self._state["execution"] = execution
            self._state["snapshot"]["motion"]["active_plan_id"] = plan["plan_id"]
            self._state["snapshot"]["motion"]["running_state"] = "waiting_confirm"
            self._state["snapshot"]["timestamp"] = utc_now()

            self._append_event(
                "assistant",
                "receipt",
                "收到指令，已生成动作计划并完成模拟预检。请确认后执行。",
                session_id=session_id,
                plan_id=plan["plan_id"],
            )
            self._append_event(
                "system",
                "precheck_result",
                "安全预检通过：急停、报警、暂停、通讯状态均满足执行条件。",
                session_id=session_id,
                plan_id=plan["plan_id"],
            )

            return {
                "accepted": True,
                "plan": deepcopy(plan),
                "precheck": deepcopy(precheck),
                "nlp": deepcopy(nlp_preview),
                "dashboard": deepcopy(self._state),
            }

    def adopt_plan(self, plan_id: str, bridge_detail: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            plan = self._state.get("active_plan")
            if not plan or plan["plan_id"] != plan_id:
                raise KeyError("plan_not_found")
            if plan["status"] != "waiting_confirm":
                raise ValueError("plan_not_waiting_confirm")

            bridge_execution = self._extract_bridge_execution(bridge_detail)
            completed_by_mock = bool(bridge_execution and bridge_execution.get("ok"))
            plan["status"] = "complete" if completed_by_mock else "running"
            self._state["execution"]["status"] = "idle" if completed_by_mock else "running"
            self._state["execution"]["progress"] = 100 if completed_by_mock else 20
            self._state["execution"]["current_stage"] = None if completed_by_mock else "动作执行"
            self._state["execution"]["eta_seconds"] = None if completed_by_mock else 6
            for stage in self._state["execution"]["stages"]:
                if stage["id"] == "execute":
                    stage["status"] = "complete" if completed_by_mock else "running"
                    stage["progress"] = 100 if completed_by_mock else 20
            self._state["snapshot"]["motion"]["running_state"] = "idle" if completed_by_mock else "running"
            self._state["snapshot"]["motion"]["task_id"] = None if completed_by_mock else f"TASK-{datetime.now().strftime('%H%M%S')}"
            self._state["snapshot"]["motion"]["active_plan_id"] = None if completed_by_mock else plan_id
            if completed_by_mock:
                self._apply_mock_controller_feedback(bridge_execution)
                self._state["snapshot"]["current_function"] = None
            self._state["snapshot"]["timestamp"] = utc_now()

            self._append_event(
                "assistant",
                "execution_update",
                "已确认执行，模拟控制器已完成动作。" if completed_by_mock else "已确认执行，模拟任务开始运行。",
                session_id=plan["session_id"],
                plan_id=plan_id,
            )
            if completed_by_mock:
                self._append_mock_execution_events(bridge_execution, session_id=plan["session_id"], plan_id=plan_id)
                self._state["active_plan"] = None
            return deepcopy(self._state)

    def start_direct_execution(
        self,
        *,
        source_text: str,
        intent: str,
        summary: str,
        target: dict[str, Any],
        dispatch_id: str,
        bridge_detail: dict[str, Any] | None = None,
        session_id: str = "engineer-session",
    ) -> dict[str, Any]:
        with self._lock:
            if self._has_active_plan():
                event = self._append_event(
                    "assistant",
                    "receipt",
                    "当前已有计划处于确认或执行状态。请先停止当前任务或等待完成。",
                    session_id=session_id,
                )
                return {"accepted": False, "event": deepcopy(event), "dashboard": deepcopy(self._state)}

            plan_id = f"plan-{uuid4().hex[:10]}"
            plan = {
                "plan_id": plan_id,
                "session_id": session_id,
                "source_text": source_text,
                "intent": intent,
                "status": "running",
                "summary": summary,
                "target": dict(target),
                "steps": [
                    {"id": "receive", "title": "接收指令", "description": "工程师运行页直接提交。", "status": "complete"},
                    {"id": "precheck", "title": "安全预检", "description": "执行 L1 状态预检。", "status": "complete"},
                    {"id": "bridge", "title": "控制桥接", "description": "提交到 WebControlBridge。", "status": "complete"},
                    {"id": "execute", "title": "动作执行", "description": "等待控制侧执行完成。", "status": "running"},
                ],
                "created_at": utc_now(),
                "expires_at": None,
            }
            precheck = self._create_precheck(plan_id)
            bridge_execution = self._extract_bridge_execution(bridge_detail)
            completed_by_mock = bool(bridge_execution and bridge_execution.get("ok"))
            execution = {
                "plan_id": plan_id,
                "task_id": dispatch_id,
                "status": "idle" if completed_by_mock else "running",
                "progress": 100 if completed_by_mock else 12,
                "current_stage": None if completed_by_mock else "动作执行",
                "eta_seconds": None if completed_by_mock else 8,
                "stages": [
                    {"id": "receive", "name": "接收指令", "status": "complete", "progress": 100},
                    {"id": "precheck", "name": "安全预检", "status": "complete", "progress": 100},
                    {"id": "bridge", "name": "控制桥接", "status": "complete", "progress": 100},
                    {"id": "execute", "name": "动作执行", "status": "complete" if completed_by_mock else "running", "progress": 100 if completed_by_mock else 12},
                ],
            }
            self._state["active_plan"] = None if completed_by_mock else plan
            self._state["precheck"] = precheck
            self._state["execution"] = execution
            self._state["snapshot"]["motion"]["active_plan_id"] = plan_id
            self._state["snapshot"]["motion"]["task_id"] = dispatch_id
            self._state["snapshot"]["motion"]["running_state"] = "idle" if completed_by_mock else "running"
            self._state["snapshot"]["current_function"] = str(target.get("name") or target.get("query_key") or target.get("flow_name") or "-")
            if completed_by_mock:
                self._state["snapshot"]["motion"]["active_plan_id"] = None
                self._state["snapshot"]["motion"]["task_id"] = None
                self._apply_mock_controller_feedback(bridge_execution)
            self._state["snapshot"]["timestamp"] = utc_now()
            self._append_event(
                "assistant",
                "execution_update",
                (
                    f"{summary}。模拟控制器执行完成，dispatch_id={dispatch_id}。"
                    if completed_by_mock
                    else f"{summary}。控制桥接已接收，dispatch_id={dispatch_id}。"
                ),
                session_id=session_id,
                plan_id=plan_id,
            )
            if completed_by_mock:
                self._append_mock_execution_events(bridge_execution, session_id=session_id, plan_id=plan_id)
            return {"accepted": True, "dispatch_id": dispatch_id, "dashboard": deepcopy(self._state)}

    def cancel_plan(self) -> dict[str, Any]:
        with self._lock:
            had_plan = self._state.get("active_plan") is not None
            self._state["active_plan"] = None
            self._state["execution"] = self._create_idle_execution()
            self._state["snapshot"]["motion"]["active_plan_id"] = None
            self._state["snapshot"]["motion"]["task_id"] = None
            self._state["snapshot"]["motion"]["running_state"] = "idle"
            self._state["snapshot"]["current_function"] = None
            self._state["snapshot"]["timestamp"] = utc_now()
            self._append_event(
                "assistant",
                "execution_update" if had_plan else "message",
                "当前计划已取消，系统回到空闲状态。" if had_plan else "当前没有可取消的计划。",
            )
            return deepcopy(self._state)

    def toggle_pause(self) -> dict[str, Any]:
        with self._lock:
            safety = self._state["snapshot"]["safety"]
            paused = not bool(safety["paused"])
            safety["paused"] = paused

            execution = self._state.get("execution")
            if execution and paused:
                execution["status"] = "paused"
            elif execution and execution.get("status") == "paused":
                execution["status"] = "running" if self._state.get("active_plan") else "idle"

            self._state["snapshot"]["motion"]["running_state"] = (
                "paused"
                if paused
                else execution["status"]
                if execution and execution.get("status") == "running"
                else "idle"
            )
            self._state["snapshot"]["timestamp"] = utc_now()
            self._append_event("system", "execution_update", "已进入暂停状态。" if paused else "暂停已解除。")
            return deepcopy(self._state)

    def emergency_stop(self) -> dict[str, Any]:
        with self._lock:
            snapshot = deepcopy(self._state["snapshot"])
            alarm = {
                "id": f"alarm-{uuid4().hex[:10]}",
                "level": "emergency",
                "lifecycle": "occurred",
                "title": "模拟急停触发",
                "message": "操作员触发急停，当前动作计划已被阻断。",
                "occurred_at": utc_now(),
                "snapshot": snapshot,
            }
            self._state["active_plan"] = None
            self._state["execution"] = self._create_idle_execution()
            self._state["execution"]["status"] = "blocked"
            self._state["snapshot"]["safety"]["estop"] = True
            self._state["snapshot"]["safety"]["alarm_active"] = True
            self._state["snapshot"]["safety"]["alarm_level"] = "emergency"
            self._state["snapshot"]["motion"]["active_plan_id"] = None
            self._state["snapshot"]["motion"]["task_id"] = None
            self._state["snapshot"]["motion"]["running_state"] = "blocked"
            self._state["snapshot"]["timestamp"] = utc_now()
            self._state["active_alarms"].insert(0, alarm)
            self._append_event("system", "alarm_occurred", "急停已触发。模拟执行被立即阻断，请复位后继续。")
            return deepcopy(self._state)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._state = self._initial_state()
            return deepcopy(self._state)

    def _extract_bridge_execution(self, bridge_detail: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(bridge_detail, dict):
            return None
        execution = bridge_detail.get("execution")
        return execution if isinstance(execution, dict) else None

    def _apply_mock_controller_feedback(self, execution: dict[str, Any]) -> None:
        results = execution.get("results")
        last_result = results[-1] if isinstance(results, list) and results and isinstance(results[-1], dict) else {}
        feedback = last_result.get("feedback") if isinstance(last_result, dict) else None
        snapshot = execution.get("snapshot") if isinstance(execution.get("snapshot"), dict) else {}

        pose = [float(value) for value in feedback[:6]] if isinstance(feedback, list) and len(feedback) >= 6 else []
        if pose:
            x, y, z, rx, ry, rz = pose[:6]
        else:
            x = float(snapshot.get("CUR_X", self._state["snapshot"]["position"]["cartesian"]["x"]))
            y = float(snapshot.get("CUR_Y", self._state["snapshot"]["position"]["cartesian"]["y"]))
            z = float(snapshot.get("CUR_Z", self._state["snapshot"]["position"]["cartesian"]["z"]))
            rx = float(snapshot.get("CUR_RX", self._state["snapshot"]["position"]["joint"]["j4"]))
            ry = float(snapshot.get("CUR_RY", self._state["snapshot"]["position"]["joint"]["j5"]))
            rz = float(snapshot.get("CUR_RZ", self._state["snapshot"]["position"]["joint"]["j6"]))

        self._state["snapshot"]["position"] = {
            "joint": {
                "j1": x,
                "j2": y,
                "j3": z,
                "j4": rx,
                "j5": ry,
                "j6": rz,
            },
            "cartesian": {
                "x": x,
                "y": y,
                "z": z,
                "r": (x * x + y * y) ** 0.5,
            },
        }

    def _append_mock_execution_events(self, execution: dict[str, Any], *, session_id: str, plan_id: str) -> None:
        results = execution.get("results") if isinstance(execution.get("results"), list) else []
        self._append_event(
            "system",
            "execution_update",
            f"模拟控制器返回成功：共执行 {len(results)} 个步骤。",
            session_id=session_id,
            plan_id=plan_id,
        )
        for result in results[-3:]:
            if not isinstance(result, dict):
                continue
            logs = result.get("logs") if isinstance(result.get("logs"), list) else []
            key = str(result.get("query_key", "-"))
            self._append_event(
                "system",
                "execution_update",
                f"{key} 执行日志 {len(logs)} 条，反馈={result.get('feedback', [])}。",
                session_id=session_id,
                plan_id=plan_id,
            )

    def websocket_message(self) -> dict[str, Any]:
        with self._lock:
            self._tick_execution_locked()
            return {
                "type": "dashboard",
                "payload": deepcopy(self._state),
                "timestamp": utc_now(),
            }

    def _tick_execution_locked(self) -> None:
        execution = self._state.get("execution")
        if not execution or execution.get("status") != "running":
            return

        progress = min(100, int(execution["progress"]) + 8)
        execution["progress"] = progress
        execution["eta_seconds"] = max(0, (100 - progress) // 8)
        for stage in execution["stages"]:
            if stage["id"] == "execute":
                stage["status"] = "complete" if progress >= 100 else "running"
                stage["progress"] = progress

        self._state["snapshot"]["timestamp"] = utc_now()
        if progress < 100:
            return

        plan = self._state.get("active_plan")
        if plan:
            self._append_event(
                "assistant",
                "execution_update",
                "模拟执行完成，当前位置与执行状态已更新。",
                session_id=plan["session_id"],
                plan_id=plan["plan_id"],
            )

        self._state["active_plan"] = None
        self._state["snapshot"]["motion"]["active_plan_id"] = None
        self._state["snapshot"]["motion"]["task_id"] = None
        self._state["snapshot"]["motion"]["running_state"] = "idle"
        self._state["snapshot"]["current_function"] = None
        execution["status"] = "idle"
        execution["current_stage"] = None
        execution["eta_seconds"] = None

    def _append_event(
        self,
        role: str,
        event_type: str,
        text: str,
        *,
        session_id: str = "mock-session",
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": f"evt-{uuid4().hex[:10]}",
            "session_id": session_id,
            "role": role,
            "type": event_type,
            "text": text,
            "timestamp": local_time_label(),
            "plan_id": plan_id,
        }
        self._state["recent_events"].append(event)
        return event

    def _has_active_plan(self) -> bool:
        plan = self._state.get("active_plan")
        if not plan:
            return False
        return plan["status"] in {"waiting_confirm", "running"}

    def _is_query(self, text: str) -> bool:
        return any(token in text for token in ("状态", "查询", "在哪", "当前"))

    def _build_status_reply(self) -> str:
        snapshot = self._state["snapshot"]
        position = snapshot["position"]["cartesian"]
        safety = snapshot["safety"]
        return (
            f"当前状态：{snapshot['motion']['running_state']}。"
            f"R={position['r']:.1f}mm，Z={position['z']:.1f}mm，"
            f"急停{'触发' if safety['estop'] else '正常'}，"
            f"报警{'有' if safety['alarm_active'] else '无'}。"
        )

    def _is_unknown_nlp(self, nlp_preview: dict[str, Any] | None) -> bool:
        if not nlp_preview:
            return False
        actions = nlp_preview.get("actions")
        if not isinstance(actions, list) or not actions:
            return False
        return all(str(action.get("action_type")) == "unknown" for action in actions if isinstance(action, dict))

    def _create_plan(self, text: str, session_id: str, nlp_preview: dict[str, Any] | None = None) -> dict[str, Any]:
        plan_id = f"plan-{uuid4().hex[:10]}"
        actions = nlp_preview.get("actions", []) if nlp_preview else []
        first_action = actions[0] if actions and isinstance(actions[0], dict) else {}
        action_type = str(first_action.get("action_type", "") or "")
        target = str(first_action.get("target", "") or "")
        intent = "flow" if action_type == "flow" or "流程" in text else "system" if action_type == "system" else "motion"
        summary_suffix = f"{action_type}:{target}" if action_type and target else text
        return {
            "plan_id": plan_id,
            "session_id": session_id,
            "source_text": text,
            "intent": intent,
            "status": "waiting_confirm",
            "summary": f"模拟动作计划：{summary_suffix}",
            "target": {"action_type": action_type, "target": target, "nlp_source": nlp_preview.get("source") if nlp_preview else "mock"},
            "steps": [
                {"id": "parse", "title": "语义解析", "description": "识别动作意图和目标参数。", "status": "complete"},
                {"id": "precheck", "title": "安全预检", "description": "检查状态、边界和通讯。", "status": "complete"},
                {"id": "confirm", "title": "等待确认", "description": "操作员确认后进入执行。", "status": "ready"},
                {"id": "execute", "title": "下发执行", "description": "通过控制服务下发动作。", "status": "pending"},
            ],
            "created_at": utc_now(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(),
        }

    def _create_precheck(self, plan_id: str) -> dict[str, Any]:
        snapshot = self._state["snapshot"]
        return {
            "plan_id": plan_id,
            "status": "pass",
            "items": [
                {"id": "estop", "level": "L1", "label": "无紧急停止", "status": "pass", "message": "急停回路正常。"},
                {"id": "alarm", "level": "L1", "label": "无活动报警", "status": "pass", "message": "当前没有活动报警。"},
                {"id": "paused", "level": "L1", "label": "未处于暂停状态", "status": "pass", "message": "系统未暂停。"},
                {
                    "id": "controller",
                    "level": "L1",
                    "label": "控制器在线",
                    "status": "pass" if snapshot["connection"]["controller"] == "online" else "warning",
                    "message": "控制器连接正常。",
                },
                {"id": "range", "level": "L2", "label": "目标范围预演", "status": "pass", "message": "mock 目标点位于 R/Z 安全范围内。"},
            ],
        }

    def _create_waiting_execution(self, plan_id: str) -> dict[str, Any]:
        return {
            "plan_id": plan_id,
            "status": "waiting_confirm",
            "progress": 0,
            "current_stage": "等待确认",
            "stages": [
                {"id": "receive", "name": "接收指令", "status": "complete", "progress": 100},
                {"id": "parse", "name": "语义解析", "status": "complete", "progress": 100},
                {"id": "precheck", "name": "安全预检", "status": "complete", "progress": 100},
                {"id": "execute", "name": "动作执行", "status": "pending", "progress": 0},
            ],
        }

    def _create_idle_execution(self) -> dict[str, Any]:
        return {
            "status": "idle",
            "progress": 0,
            "stages": [
                {"id": "receive", "name": "接收指令", "status": "pending", "progress": 0},
                {"id": "parse", "name": "语义解析", "status": "pending", "progress": 0},
                {"id": "precheck", "name": "安全预检", "status": "pending", "progress": 0},
                {"id": "execute", "name": "动作执行", "status": "pending", "progress": 0},
            ],
        }

    def _initial_state(self) -> dict[str, Any]:
        snapshot = {
            "timestamp": utc_now(),
            "connection": {
                "controller": "online",
                "modbus_tcp": "online",
                "ethercat": "online",
                "realtime_feedback": "online",
                "voice": "online",
            },
            "safety": {
                "estop": False,
                "paused": False,
                "alarm_active": False,
                "alarm_level": None,
                "safe_range": {"r_min": 200, "r_max": 1500, "z_min": 100, "z_max": 1200},
            },
            "motion": {
                "speed_percent": 30,
                "acceleration_percent": 100,
                "task_id": None,
                "active_plan_id": None,
                "running_state": "idle",
            },
            "position": {
                "joint": {"j1": 1250.0, "j2": 0.0, "j3": 860.0, "j4": 0.2, "j5": 0.0, "j6": 0.0},
                "cartesian": {"x": 898.1, "y": 866.2, "z": 860.0, "r": 1250.0},
            },
            "current_function": None,
        }
        return {
            "snapshot": snapshot,
            "active_plan": None,
            "precheck": {
                "plan_id": "mock-plan-idle",
                "status": "pending",
                "items": [
                    {"id": "estop", "level": "L1", "label": "无紧急停止", "status": "pass", "message": "急停回路正常。"},
                    {"id": "alarm", "level": "L1", "label": "无活动报警", "status": "pass", "message": "当前没有活动报警。"},
                    {"id": "paused", "level": "L1", "label": "未处于暂停状态", "status": "pass", "message": "系统未暂停。"},
                    {"id": "controller", "level": "L1", "label": "控制器在线", "status": "pass", "message": "控制器连接正常。"},
                ],
            },
            "execution": self._create_idle_execution(),
            "recent_events": [
                {
                    "id": "evt-seed-001",
                    "session_id": "mock-session",
                    "role": "system",
                    "type": "receipt",
                    "text": "Web 服务已启动，当前处于 mock 控制器模式。",
                    "timestamp": local_time_label(),
                    "plan_id": None,
                }
            ],
            "active_alarms": [],
        }
