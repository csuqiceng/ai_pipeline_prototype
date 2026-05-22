"""FastAPI entrypoint for the HTML replacement UI."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from robot_modbus_lite.avoidance_config import AvoidanceConfig, SafePoint, load_avoidance_config, save_avoidance_config, validate_safe_point
from robot_modbus_lite.dashboard import DashboardSnapshot
from robot_modbus_lite.json_schema import DeviceSnapshot
from robot_modbus_lite.web_control_bridge import WebControlBridge
from robot_modbus_lite.flow_store import load_flows_json, save_flows_json
from robot_modbus_lite.models import FlowDefinition, QueryRecord
from robot_modbus_lite.query_table import QueryTableError, load_query_table_json, save_query_table_json
from robot_modbus_lite.runtime_paths import resolve_runtime_data_file, resolve_web_dist_dir, resolve_writable_runtime_data_file
from robot_modbus_lite.system_config import AxisRangeConfig, load_system_config, save_system_config, validate_system_config
from robot_modbus_lite.web_log_service import WebLogService
from robot_modbus_lite.web_nlp_service import WebNlpService
from robot_modbus_lite.web_precheck_service import WebPrecheckService
from robot_modbus_lite.web_state import MockWebStateStore, utc_now
from robot_modbus_lite.web_voice_service import WebVoiceService


def _server_trace(message: str) -> None:
    if os.environ.get("ROBOT_WEB_BOOT_TRACE") != "1":
        return
    try:
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        with (root / "web_boot_trace.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except Exception:
        pass


class ConversationInput(BaseModel):
    text: str = Field(min_length=1)
    session_id: str = "mock-session"
    input_mode: Literal["text", "voice", "system"] = "text"


class AdoptPlanInput(BaseModel):
    plan_id: str = Field(min_length=1)


class NlpParseInput(BaseModel):
    text: str = Field(min_length=1)
    use_deepseek: bool = False


class TemplateRecordInput(BaseModel):
    query_key: str = Field(min_length=1)
    func_num: int
    keywords: str = ""
    description: str = ""
    safety_level: int = 5
    params: dict = Field(default_factory=dict)


class FlowRecordInput(BaseModel):
    name: str = Field(min_length=1)
    steps: list[str] = Field(default_factory=list)
    step_delay_ms: int = 1000


class SystemConfigInput(BaseModel):
    config: dict


class SafePointInput(BaseModel):
    name: str = Field(min_length=1)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    speed_percent: float = 20.0
    acc_percent: float = 20.0
    description: str = ""


class AvoidanceConfigInput(BaseModel):
    mode: str = "off"
    rx_threshold: float = 30.0
    ry_threshold: float = 30.0
    rz_threshold: float = 45.0
    low_z_threshold: float = 150.0
    xy_move_threshold: float = 100.0
    safe_points: dict[str, SafePointInput] = Field(default_factory=dict)
    rules: list[dict] = Field(default_factory=list)


store = MockWebStateStore()
nlp_service = WebNlpService()
log_service = WebLogService()
precheck_service = WebPrecheckService()
control_bridge = WebControlBridge.from_runtime_files(mode=os.environ.get("ROBOT_WEB_BRIDGE_MODE", "dry_run"))
voice_service = WebVoiceService()
app = FastAPI(title="Robot Modbus Lite Web API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

web_dist_dir = resolve_web_dist_dir()
if (web_dist_dir / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(web_dist_dir / "assets")), name="assets")


def configure_control_bridge(mode: str, controller_host: str = "127.0.0.1") -> None:
    global control_bridge
    control_bridge = WebControlBridge.from_runtime_files(mode=mode, controller_host=controller_host)


def _web_snapshot_to_dashboard_snapshot(snapshot: dict[str, Any]) -> DashboardSnapshot:
    position = snapshot.get("position", {}) if isinstance(snapshot, dict) else {}
    cartesian = position.get("cartesian", {}) if isinstance(position, dict) else {}
    joint = position.get("joint", {}) if isinstance(position, dict) else {}
    safety = snapshot.get("safety", {}) if isinstance(snapshot, dict) else {}
    motion = snapshot.get("motion", {}) if isinstance(snapshot, dict) else {}
    connection = snapshot.get("connection", {}) if isinstance(snapshot, dict) else {}
    return DashboardSnapshot(
        ts=str(snapshot.get("timestamp") or utc_now()),
        position={
            "x": cartesian.get("x", 0.0),
            "y": cartesian.get("y", 0.0),
            "z": cartesian.get("z", 0.0),
            "r": cartesian.get("r", 0.0),
            "joints": tuple(joint.get(axis, 0.0) for axis in ("j1", "j2", "j3", "j4", "j5", "j6")),
        },
        safety={
            "estop": bool(safety.get("estop")),
            "paused": bool(safety.get("paused")),
            "alarm_active": bool(safety.get("alarm_active")),
            "alarm_code": safety.get("alarm_code", 0),
        },
        motion={
            "running_state": motion.get("running_state", "unknown"),
            "current_func": snapshot.get("current_function") or motion.get("active_plan_id") or "-",
            "speed": motion.get("speed_percent", "-"),
        },
        connection={
            "realtime_feedback": connection.get("realtime_feedback", "offline"),
            "controller": connection.get("controller", "unknown"),
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "mode": "mock",
        "controller": "mock",
        "timestamp": utc_now(),
        "log_path": str(log_service.path),
    }


@app.get("/api/snapshot")
def get_snapshot() -> dict:
    return store.snapshot()


@app.get("/api/snapshot/v21")
def get_snapshot_v21() -> dict:
    snapshot = store.snapshot()
    return DeviceSnapshot.from_dashboard_snapshot(_web_snapshot_to_dashboard_snapshot(snapshot), refresh_ms=500).to_dict()


@app.get("/api/dashboard")
def get_dashboard() -> dict:
    return store.dashboard()


@app.get("/api/conversation/events")
def get_conversation_events(session_id: str | None = None, limit: int = 100) -> dict:
    safe_limit = max(1, min(limit, 500))
    return {
        "session_id": session_id,
        "events": store.conversation_events(session_id=session_id, limit=safe_limit),
    }


@app.post("/api/conversation/input")
def post_conversation_input(payload: ConversationInput) -> dict:
    try:
        nlp_preview = nlp_service.parse(payload.text)
        result = store.submit_conversation(payload.text, session_id=payload.session_id, nlp_preview=nlp_preview)
        log_service.append(
            "NLP",
            "conversation_input",
            "accepted" if result.get("accepted") else "rejected",
            payload.text,
            extra={"nlp": nlp_preview, "session_id": payload.session_id},
        )
        return result
    except ValueError as exc:
        if str(exc) == "empty_input":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": True,
                    "code": "EMPTY_INPUT",
                    "message": "输入内容不能为空。",
                    "detail": {"field": "text"},
                },
            ) from exc
        raise


@app.post("/api/nlp/parse")
def post_nlp_parse(payload: NlpParseInput) -> dict:
    result = nlp_service.parse(payload.text, use_deepseek=payload.use_deepseek)
    log_service.append("NLP", "parse", "success", payload.text, extra={"nlp": result})
    return result


@app.post("/api/nlp/parse/v21")
def post_nlp_parse_v21(payload: NlpParseInput) -> dict:
    result = nlp_service.parse_intent(payload.text, use_deepseek=payload.use_deepseek)
    log_service.append("NLP", "parse_v21", "success", payload.text, extra={"command_intent": result})
    return result


@app.get("/api/precheck/l1")
def get_l1_precheck() -> dict:
    dashboard = store.dashboard()
    result = precheck_service.run_l1(dashboard["snapshot"], dashboard.get("active_plan"))
    log_service.append("安全", "precheck_l1", result["status"], "Web API L1 安全预检", extra={"precheck": result})
    return result


@app.post("/api/plans/adopt")
def post_adopt_plan(payload: AdoptPlanInput) -> dict:
    try:
        dashboard = store.dashboard()
        plan = dashboard.get("active_plan")
        precheck = precheck_service.run_l1(dashboard["snapshot"], plan)
        if precheck["status"] != "pass":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": True,
                    "code": "PRECHECK_FAILED",
                    "message": "安全预检未通过，计划未执行。",
                    "detail": precheck,
                },
            )
        bridge_result = _submit_plan_to_bridge(plan or {"plan_id": payload.plan_id})
        if not bridge_result.accepted:
            raise HTTPException(
                status_code=502,
                detail={"error": True, "code": "BRIDGE_REJECTED", "message": bridge_result.message, "detail": bridge_result.detail or {}},
            )
        result = store.adopt_plan(payload.plan_id, bridge_detail=bridge_result.detail)
        log_service.append(
            "控制",
            "adopt_plan",
            "dry_run" if bridge_result.mode == "dry_run" else "success",
            bridge_result.message,
            extra={"dispatch_id": bridge_result.dispatch_id, "plan_id": payload.plan_id},
        )
        return result
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": True,
                "code": "PLAN_NOT_FOUND",
                "message": "计划不存在或已结束。",
                "detail": {"plan_id": payload.plan_id},
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": True,
                "code": "PLAN_NOT_WAITING_CONFIRM",
                "message": "计划当前不处于等待确认状态。",
                "detail": {"plan_id": payload.plan_id},
            },
        ) from exc


def _submit_plan_to_bridge(plan: dict[str, Any]) -> Any:
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    action_type = str(target.get("action_type") or target.get("type") or "")
    action_target = str(target.get("target") or target.get("query_key") or target.get("flow_name") or "")

    if action_type == "template" and action_target:
        table = load_query_table_json(resolve_runtime_data_file("query_table.json"))
        record = table.get(action_target)
        if record is not None:
            return control_bridge.submit_template(_template_payload(record))

    if action_type == "flow" and action_target:
        flows = load_flows_json(resolve_runtime_data_file("flows.json"))
        flow = flows.get(action_target)
        if flow is not None:
            return control_bridge.submit_flow(
                {"name": flow.name, "steps": list(flow.steps), "step_delay_ms": flow.step_delay_ms},
                mode="start",
            )

    return control_bridge.submit_plan(plan)


@app.post("/api/plans/cancel")
def post_cancel_plan() -> dict:
    result = store.cancel_plan()
    log_service.append("控制", "cancel_plan", "success", "Web API 取消当前计划")
    return result


@app.get("/api/control/bridge/status")
def get_control_bridge_status() -> dict:
    return control_bridge.status()


@app.post("/api/control/templates/{query_key}/execute")
def post_execute_template(query_key: str) -> dict:
    table = load_query_table_json(resolve_runtime_data_file("query_table.json"))
    record = table.get(query_key)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "code": "TEMPLATE_NOT_FOUND", "message": f"模板不存在: {query_key}", "detail": {"query_key": query_key}},
        )

    synthetic_plan = {
        "plan_id": f"template-{query_key}",
        "intent": "motion",
        "summary": f"执行模板：{record.query_key}",
        "target": {"query_key": record.query_key, "func_num": record.func_num},
    }
    precheck = precheck_service.run_l1(store.snapshot(), synthetic_plan)
    if precheck["status"] == "fail":
        raise HTTPException(
            status_code=409,
            detail={"error": True, "code": "PRECHECK_FAILED", "message": "安全预检未通过，模板未执行。", "detail": precheck},
        )

    bridge_result = control_bridge.submit_template(_template_payload(record))
    if not bridge_result.accepted:
        raise HTTPException(
            status_code=502,
            detail={"error": True, "code": "BRIDGE_REJECTED", "message": bridge_result.message, "detail": bridge_result.detail or {}},
        )
    result = store.start_direct_execution(
        source_text=record.query_key,
        intent="motion",
        summary=f"执行模板：{record.query_key}",
        target={"type": "template", "query_key": record.query_key, "name": record.query_key, "func_num": record.func_num},
        dispatch_id=bridge_result.dispatch_id,
        bridge_detail=bridge_result.detail,
    )
    log_service.append(
        "控制",
        "execute_template",
        "dry_run" if bridge_result.mode == "dry_run" else "success",
        bridge_result.message,
        extra={"query_key": record.query_key, "func_num": record.func_num, "dispatch_id": bridge_result.dispatch_id},
    )
    return {**result, "bridge": bridge_result.__dict__, "precheck": precheck}


@app.post("/api/control/flows/{flow_name}/start")
def post_start_flow(flow_name: str) -> dict:
    return _execute_flow(flow_name, mode="start")


@app.post("/api/control/flows/{flow_name}/step")
def post_step_flow(flow_name: str) -> dict:
    return _execute_flow(flow_name, mode="step")


@app.post("/api/control/flows/stop")
def post_stop_flow() -> dict:
    result = store.cancel_plan()
    log_service.append("流程", "stop_flow", "success", "Web API 停止当前流程/计划")
    return {"accepted": True, "dashboard": result}


@app.post("/api/control/flows/reset")
def post_reset_flow() -> dict:
    result = store.cancel_plan()
    log_service.append("流程", "reset_flow", "success", "Web API 重置流程状态")
    return {"accepted": True, "dashboard": result}


def _execute_flow(flow_name: str, *, mode: str) -> dict:
    flows = load_flows_json(resolve_runtime_data_file("flows.json"))
    table = load_query_table_json(resolve_runtime_data_file("query_table.json"))
    flow = flows.get(flow_name)
    if flow is None:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "code": "FLOW_NOT_FOUND", "message": f"流程不存在: {flow_name}", "detail": {"flow_name": flow_name}},
        )
    missing = [step for step in flow.steps if step not in table]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"error": True, "code": "FLOW_TEMPLATE_MISSING", "message": f"流程存在缺失模板: {', '.join(missing)}", "detail": {"missing": missing}},
        )
    selected_steps = list(flow.steps[:1] if mode == "step" else flow.steps)
    synthetic_plan = {
        "plan_id": f"flow-{flow.name}",
        "intent": "flow",
        "summary": f"{'单步执行' if mode == 'step' else '执行流程'}：{flow.name}",
        "target": {"flow_name": flow.name, "mode": mode, "steps": selected_steps},
    }
    precheck = precheck_service.run_l1(store.snapshot(), synthetic_plan)
    if precheck["status"] == "fail":
        raise HTTPException(
            status_code=409,
            detail={"error": True, "code": "PRECHECK_FAILED", "message": "安全预检未通过，流程未执行。", "detail": precheck},
        )

    flow_payload = {"name": flow.name, "steps": selected_steps, "step_delay_ms": flow.step_delay_ms}
    bridge_result = control_bridge.submit_flow(flow_payload, mode=mode)
    if not bridge_result.accepted:
        raise HTTPException(
            status_code=502,
            detail={"error": True, "code": "BRIDGE_REJECTED", "message": bridge_result.message, "detail": bridge_result.detail or {}},
        )
    result = store.start_direct_execution(
        source_text=f"{'单步执行' if mode == 'step' else '执行流程'} {flow.name}",
        intent="flow",
        summary=f"{'单步执行' if mode == 'step' else '执行流程'}：{flow.name}",
        target={"type": "flow", "flow_name": flow.name, "name": flow.name, "mode": mode, "steps": selected_steps},
        dispatch_id=bridge_result.dispatch_id,
        bridge_detail=bridge_result.detail,
    )
    log_service.append(
        "流程",
        "execute_flow_step" if mode == "step" else "execute_flow",
        "dry_run" if bridge_result.mode == "dry_run" else "success",
        bridge_result.message,
        extra={"flow_name": flow.name, "mode": mode, "steps": selected_steps, "dispatch_id": bridge_result.dispatch_id},
    )
    return {**result, "bridge": bridge_result.__dict__, "precheck": precheck}


@app.post("/api/system/pause")
def post_toggle_pause() -> dict:
    result = store.toggle_pause()
    log_service.append("控制", "pause_toggle", "success", "Web API 切换暂停状态")
    return result


@app.post("/api/system/emergency-stop")
def post_emergency_stop() -> dict:
    result = store.emergency_stop()
    log_service.append("控制", "emergency_stop", "success", "Web API 触发模拟急停")
    return result


@app.post("/api/system/reset-mock")
def post_reset_mock() -> dict:
    control_bridge.reset_mock_controller()
    result = store.reset()
    log_service.append("系统", "reset_mock", "success", "Web API 重置 mock 状态")
    return result


@app.get("/api/logs/recent")
def get_recent_logs(limit: int = 100) -> dict:
    safe_limit = max(1, min(limit, 500))
    return {"entries": log_service.recent(safe_limit), "log_path": str(log_service.path)}


@app.get("/api/templates")
def get_templates() -> dict:
    table = load_query_table_json(resolve_runtime_data_file("query_table.json"))
    return _templates_payload(table)


@app.put("/api/templates/{query_key}")
def put_template(query_key: str, payload: TemplateRecordInput) -> dict:
    try:
        table = load_query_table_json(resolve_writable_runtime_data_file("query_table.json"))
        record = _template_record_from_input(payload)
        validation_error = _validate_template_record(record)
        if validation_error:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "code": "TEMPLATE_INVALID", "message": validation_error, "detail": record.to_dict()},
            )
        if query_key != record.query_key:
            table.pop(query_key, None)
        table[record.query_key] = record
        save_query_table_json(resolve_writable_runtime_data_file("query_table.json"), table)
        log_service.append("后台", "save_template", "success", record.query_key)
        return _templates_payload(table)
    except QueryTableError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "code": "QUERY_TABLE_ERROR", "message": str(exc), "detail": {"query_key": query_key}},
        ) from exc


@app.delete("/api/templates/{query_key}")
def delete_template(query_key: str) -> dict:
    table = load_query_table_json(resolve_writable_runtime_data_file("query_table.json"))
    if query_key not in table:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "code": "TEMPLATE_NOT_FOUND", "message": f"模板不存在: {query_key}", "detail": {"query_key": query_key}},
        )
    del table[query_key]
    save_query_table_json(resolve_writable_runtime_data_file("query_table.json"), table)
    log_service.append("后台", "delete_template", "success", query_key)
    return _templates_payload(table)


def _templates_payload(table: dict[str, QueryRecord]) -> dict:
    records = []
    for record in sorted(table.values(), key=lambda item: item.query_key):
        records.append(
            {
                "query_key": record.query_key,
                "func_num": record.func_num,
                "function_name": record.function_name,
                "keywords": record.keywords,
                "description": record.description,
                "safety_level": record.safety_level,
                "params": record.params,
                "summary": record.summary_text(),
            }
        )
    return {"records": records, "count": len(records)}


def _template_payload(record: QueryRecord) -> dict:
    return {
        "query_key": record.query_key,
        "func_num": record.func_num,
        "function_name": record.function_name,
        "keywords": record.keywords,
        "description": record.description,
        "safety_level": record.safety_level,
        "params": record.params,
        "summary": record.summary_text(),
    }


def _template_record_from_input(payload: TemplateRecordInput) -> QueryRecord:
    return QueryRecord(
        query_key=payload.query_key.strip(),
        func_num=int(payload.func_num),
        params=dict(payload.params),
        keywords=payload.keywords.strip(),
        description=payload.description.strip(),
        safety_level=int(payload.safety_level),
    )


def _validate_template_record(record: QueryRecord) -> str | None:
    if not record.query_key:
        return "模板名称不能为空。"
    if record.func_num not in (11, 104, 106, 107, 108, 109, 110, 120):
        return "当前仅支持 Func11 / Func104 / Func106 / Func107 / Func108 / Func109 / Func110 / Func120。"
    if not (1 <= record.safety_level <= 5):
        return "安全等级必须在 1 到 5 之间。"
    if record.func_num in (106, 107, 108, 11):
        for key in ("spd_pct", "acc_pct", "dec_pct"):
            if key in record.params and not (0 <= float(record.params[key]) <= 100):
                return f"{key} 必须在 0 到 100 之间。"
    if record.func_num == 106 and not (0 <= record.int_param("axis_no") <= 5):
        return "Func106 的轴号只能是 0 到 5。"
    if record.func_num == 107 and not (6 <= record.int_param("axis_no") <= 11):
        return "Func107 的轴号只能是 6 到 11。"
    if record.func_num == 109 and record.float_param("delay_sec") <= 0:
        return "Func109 的 delay_sec 必须大于 0。"
    if record.func_num == 110 and record.float_param("delay_sec") <= 0:
        return "Func110 的 delay_sec 必须大于 0。"
    if record.func_num == 120:
        if not (0 <= record.int_param("io_no") <= 11):
            return "Func120 的 io_no 必须在 0 到 11 之间。"
        if record.int_param("io_action") not in (0, 1):
            return "Func120 的 io_action 只能是 0 或 1。"
    return None


@app.get("/api/flows")
def get_flows() -> dict:
    flows = load_flows_json(resolve_runtime_data_file("flows.json"))
    return _flows_payload(flows)


@app.put("/api/flows/{name}")
def put_flow(name: str, payload: FlowRecordInput) -> dict:
    flows = load_flows_json(resolve_writable_runtime_data_file("flows.json"))
    flow = FlowDefinition(
        name=payload.name.strip(),
        steps=tuple(step.strip() for step in payload.steps if step.strip()),
        step_delay_ms=max(0, int(payload.step_delay_ms)),
    )
    if not flow.name:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "code": "FLOW_INVALID", "message": "流程名称不能为空。", "detail": payload.dict()},
        )
    if name != flow.name:
        flows.pop(name, None)
    flows[flow.name] = flow
    save_flows_json(resolve_writable_runtime_data_file("flows.json"), flows)
    log_service.append("后台", "save_flow", "success", flow.name)
    return _flows_payload(flows)


@app.delete("/api/flows/{name}")
def delete_flow(name: str) -> dict:
    flows = load_flows_json(resolve_writable_runtime_data_file("flows.json"))
    if name not in flows:
        raise HTTPException(
            status_code=404,
            detail={"error": True, "code": "FLOW_NOT_FOUND", "message": f"流程不存在: {name}", "detail": {"name": name}},
        )
    del flows[name]
    save_flows_json(resolve_writable_runtime_data_file("flows.json"), flows)
    log_service.append("后台", "delete_flow", "success", name)
    return _flows_payload(flows)


def _flows_payload(flows: dict[str, FlowDefinition]) -> dict:
    return {
        "flows": [
            {"name": flow.name, "steps": list(flow.steps), "step_delay_ms": flow.step_delay_ms}
            for flow in sorted(flows.values(), key=lambda item: item.name)
        ]
    }


@app.get("/api/system/config")
def get_system_config() -> dict:
    config = load_system_config(resolve_runtime_data_file("system_config.json"))
    return config.to_dict()


@app.put("/api/system/config")
def put_system_config(payload: SystemConfigInput) -> dict:
    config = AxisRangeConfig.from_dict(payload.config)
    validation_error = validate_system_config(config)
    if validation_error:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "code": "SYSTEM_CONFIG_INVALID", "message": validation_error, "detail": config.to_dict()},
        )
    save_system_config(resolve_writable_runtime_data_file("system_config.json"), config)
    log_service.append("后台", "save_system_config", "success", "Web API 保存系统参数")
    return config.to_dict()


@app.get("/api/avoidance-config")
def get_avoidance_config() -> dict:
    config = load_avoidance_config(resolve_runtime_data_file("avoidance_rules.json"))
    return config.to_dict()


@app.put("/api/avoidance-config")
def put_avoidance_config(payload: AvoidanceConfigInput) -> dict:
    safe_points: dict[str, SafePoint] = {}
    for key, item in payload.safe_points.items():
        point = SafePoint(
            name=item.name.strip() or key,
            x=item.x,
            y=item.y,
            z=item.z,
            rx=item.rx,
            ry=item.ry,
            rz=item.rz,
            speed_percent=item.speed_percent,
            acc_percent=item.acc_percent,
            description=item.description,
        )
        validation_error = validate_safe_point(point)
        if validation_error:
            raise HTTPException(
                status_code=400,
                detail={"error": True, "code": "SAFE_POINT_INVALID", "message": validation_error, "detail": point.to_dict()},
            )
        safe_points[point.name] = point
    config = AvoidanceConfig(
        mode=payload.mode,
        rx_threshold=payload.rx_threshold,
        ry_threshold=payload.ry_threshold,
        rz_threshold=payload.rz_threshold,
        low_z_threshold=payload.low_z_threshold,
        xy_move_threshold=payload.xy_move_threshold,
        safe_points=safe_points,
        rules=payload.rules,
    )
    save_avoidance_config(resolve_writable_runtime_data_file("avoidance_rules.json"), config)
    log_service.append("后台", "save_avoidance_config", "success", "Web API 保存安全中间点")
    return config.to_dict()


@app.get("/api/voice/status")
def get_voice_status() -> dict:
    return voice_service.status()


@app.get("/api/voice/devices")
def get_voice_devices() -> dict:
    return voice_service.devices()


@app.post("/api/voice/start")
def post_voice_start() -> dict:
    result = voice_service.start()
    log_service.append("语音", "start", "success", "Web API 请求启动语音识别")
    return result


@app.post("/api/voice/stop")
def post_voice_stop() -> dict:
    result = voice_service.stop()
    log_service.append("语音", "stop", "success", "Web API 请求停止语音识别")
    return result


@app.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(store.websocket_message())
            voice_event = voice_service.consume_event()
            if voice_event:
                await websocket.send_json({"type": "voice_input_complete", "payload": voice_event, "timestamp": utc_now()})
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


@app.get("/")
def get_web_index() -> FileResponse:
    index_file = web_dist_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": True,
                "code": "WEB_DIST_NOT_FOUND",
                "message": "前端构建产物不存在，请先运行 npm run build。",
                "detail": {"path": str(index_file)},
            },
        )
    return FileResponse(index_file)


@app.get("/{path:path}")
def get_web_fallback(path: str) -> FileResponse:
    if path.startswith("api/") or path.startswith("ws/"):
        raise HTTPException(status_code=404, detail="Not Found")
    return get_web_index()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Robot Modbus Lite Web API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bridge-mode", choices=["dry_run", "service", "mock_controller"], default=os.environ.get("ROBOT_WEB_BRIDGE_MODE", "dry_run"))
    parser.add_argument("--controller-host", default=os.environ.get("ROBOT_WEB_CONTROLLER_HOST", "127.0.0.1"))
    args = parser.parse_args()

    configure_control_bridge(args.bridge_mode, controller_host=args.controller_host)
    _server_trace(f"uvicorn starting host={args.host} port={args.port}")
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        loop="asyncio",
        http="h11",
        ws="websockets",
        lifespan="off",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    _server_trace("uvicorn server.run begin")
    server.run()
    _server_trace("uvicorn server.run returned")


if __name__ == "__main__":
    main()
