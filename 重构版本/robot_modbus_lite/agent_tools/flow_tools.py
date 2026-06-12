from __future__ import annotations

import re
from typing import Any

from robot_modbus_lite.agent_tools import command_tools
from robot_modbus_lite.agent_tools.tool_result import ToolResult
from robot_modbus_lite.execution_plan_service import ExecutionPlanService
from robot_modbus_lite.flow_registry import FlowEntry, FlowStep
from robot_modbus_lite.models import FlowDefinition


def parse_existing_flow_draft(parse_func: Any, text: str) -> ToolResult | None:
    if not callable(parse_func):
        return None
    plan = parse_func(str(text or ""))
    if str(getattr(plan, "source", "") or "") != "flow_draft":
        return None
    actions = tuple(getattr(plan, "actions", ()) or ())
    if not actions:
        return None
    first_type = str(getattr(actions[0], "action_type", "") or "")
    if first_type not in {"flow_draft", "clarification", "unknown"}:
        return None
    if first_type == "unknown" and str(getattr(actions[0], "source", "") or "") != "flow_draft":
        return None
    draft = getattr(plan, "flow_draft", {}) or {}
    draft = dict(draft) if isinstance(draft, dict) else {}
    return ToolResult.success(
        state="flow_draft_plan",
        message=str(getattr(plan, "reason", "") or "已生成流程草案。"),
        data={
            "intent": "create_flow",
            "draft": draft,
            "flow_name": str(draft.get("flow_name", "") or ""),
            "missing_fields": [],
            "plan": plan,
        },
    )


def start_flow_draft(flow_name: str | None = None) -> ToolResult:
    clean_name = str(flow_name or "").strip()
    draft = {"flow_name": clean_name, "expanded_steps": []}
    if not clean_name:
        return ToolResult.failure(
            state="flow_draft_needs_name",
            message="请问新流程的名称是什么？",
            code="FLOW_NAME_MISSING",
            data={
                "intent": "create_flow",
                "draft": draft,
                "flow_name": "",
                "missing_fields": ["flow_name"],
            },
            fields=["flow_name"],
        )
    return ToolResult.success(
        state="flow_draft_updated",
        message=f"已创建流程草案“{clean_name}”，可以继续添加步骤。",
        data={
            "intent": "create_flow",
            "draft": draft,
            "flow_name": clean_name,
            "missing_fields": [],
        },
    )


def set_flow_name(draft: dict[str, Any] | None, flow_name: str) -> ToolResult:
    clean_name = str(flow_name or "").strip()
    current = dict(draft or {})
    if not clean_name:
        current.setdefault("expanded_steps", [])
        return ToolResult.failure(
            state="flow_draft_needs_name",
            message="请提供流程名称。",
            code="FLOW_NAME_MISSING",
            data={
                "intent": "create_flow",
                "draft": current,
                "flow_name": "",
                "missing_fields": ["flow_name"],
            },
            fields=["flow_name"],
        )
    current["flow_name"] = clean_name
    current.setdefault("expanded_steps", [])
    return ToolResult.success(
        state="flow_draft_updated",
        message=f"好的，流程草案名称已设为“{clean_name}”。请继续添加动作步骤。",
        data={
            "intent": "create_flow",
            "draft": current,
            "flow_name": clean_name,
            "missing_fields": [],
        },
    )


def append_flow_step(service: ExecutionPlanService, *, step_text: str, draft: dict[str, Any] | None = None) -> ToolResult:
    current = _ensure_service_draft(service, draft)
    steps = list(current.get("expanded_steps") or current.get("steps") or [])
    actions = _split_spoken_step_actions(step_text)
    if not actions:
        actions = [_normalize_step_action(step_text)]
    for action in actions:
        step_id = len(steps) + 1
        steps.append(
            {
                "step_id": step_id,
                "action": action,
                "description": action,
                "func_id": _step_func_id(action),
                "params": _initial_step_params(action),
            }
        )
    current["expanded_steps"] = steps
    return set_flow_draft(service, current)


def answer_flow_clarification(
    service: ExecutionPlanService,
    text: str,
    *,
    draft: dict[str, Any] | None = None,
    snapshot_provider: Any = None,
) -> ToolResult:
    _ensure_service_draft(service, draft)
    clarification = service.current_clarification()
    if clarification is None:
        return ToolResult.failure(
            state="flow_draft_no_clarification",
            message="当前流程草案没有待补充的问题。",
            code="FLOW_DRAFT_NO_CLARIFICATION",
            data=_flow_draft_data(service.pending_flow_draft() or {}),
        )
    missing_field = str(getattr(clarification, "missing_field", "") or "")
    if missing_field in {"target_pose", "target", "pose"}:
        command = command_tools.build_command_draft(
            str(text or ""),
            snapshot_provider=snapshot_provider,
        )
        if command.ok:
            command_draft = dict(command.data.get("draft", {}) or {})
            params = dict(command_draft.get("params", {}) or {})
            required = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
            if all(key in params for key in required):
                fabricated = " ".join(f"{key} {params[key]}" for key in required)
                applied = service.apply_clarification_answer(fabricated)
                if applied.applied:
                    service.edit_step_params(int(getattr(clarification, "step_id", 0) or 0), _flow_param_subset(params))
                    return _flow_result_after_update(service, message=applied.message)
    normalized = command_tools.normalize_chinese_numbers(str(text or ""))
    applied = service.apply_clarification_answer(normalized)
    if not applied.applied:
        current = service.pending_flow_draft() or {}
        return ToolResult.failure(
            state="flow_draft_needs_clarification",
            message=applied.message,
            code="FLOW_DRAFT_CLARIFICATION_NOT_APPLIED",
            data=_flow_draft_data(current, missing_fields=[missing_field]) | {
                "accepted_answer_types": [str(item) for item in tuple(getattr(clarification, "accepted_answer_types", ()) or ())],
            },
            fields=[missing_field],
        )
    return _flow_result_after_update(service, message=applied.message)


def edit_flow_draft_params(
    service: ExecutionPlanService,
    *,
    text: str,
    draft: dict[str, Any] | None = None,
) -> ToolResult:
    _ensure_service_draft(service, draft)
    patch = _parse_flow_param_edit(text)
    if not patch:
        return ToolResult.failure(
            state="flow_draft_edit_not_matched",
            message="未识别到要修改的流程参数。",
            code="FLOW_DRAFT_EDIT_NOT_MATCHED",
            data=_flow_draft_data(service.pending_flow_draft() or {}),
        )
    current = service.pending_flow_draft() or {}
    steps = [dict(step) for step in list(current.get("expanded_steps") or current.get("steps") or []) if isinstance(step, dict)]
    if not steps:
        return ToolResult.failure(
            state="flow_draft_not_ready",
            message="当前流程草案没有可修改的步骤。",
            code="FLOW_STEPS_MISSING",
            data=_flow_draft_data(current, missing_fields=["steps"]),
            fields=["steps"],
        )
    step_id = _parse_step_reference(text) or int(steps[-1].get("step_id") or len(steps))
    try:
        service.edit_step_params(step_id, patch)
    except Exception as exc:
        return ToolResult.failure(
            state="flow_draft_edit_failed",
            message=str(exc),
            code="FLOW_DRAFT_EDIT_FAILED",
            data=_flow_draft_data(service.pending_flow_draft() or current),
        )
    labels = "、".join(_param_label(key) for key in patch)
    values = "、".join(f"{_param_label(key)}={value:g}%" for key, value in patch.items())
    return _flow_result_after_update(
        service,
        message=f"已将第{step_id}步的{labels}修改为{values}。",
    )


def save_flow_draft(service: Any, draft: dict[str, Any]) -> ToolResult:
    current = dict(draft or {})
    flow_name = str(current.get("flow_name") or current.get("name") or "").strip()
    steps = [dict(step) for step in list(current.get("expanded_steps") or current.get("steps") or []) if isinstance(step, dict)]
    if not flow_name:
        return ToolResult.failure(
            state="flow_draft_not_ready",
            message="流程名称为空，不能保存。",
            code="FLOW_NAME_MISSING",
            data=_flow_draft_data(current, missing_fields=["flow_name"]),
            fields=["flow_name"],
        )
    if not steps:
        return ToolResult.failure(
            state="flow_draft_not_ready",
            message=f"流程草案“{flow_name}”没有可保存的步骤。",
            code="FLOW_STEPS_MISSING",
            data=_flow_draft_data(current, missing_fields=["steps"]),
            fields=["steps"],
        )
    missing_fields = _missing_save_fields(steps)
    if missing_fields:
        return ToolResult.failure(
            state="flow_draft_not_ready",
            message=f"流程草案“{flow_name}”仍有步骤参数未补齐，不能保存。",
            code="FLOW_DRAFT_INCOMPLETE",
            data=_flow_draft_data(current, missing_fields=missing_fields),
            fields=missing_fields,
        )
    try:
        entry = _flow_entry_from_draft(flow_name, steps)
        if hasattr(service, "save_flow_entry") and callable(service.save_flow_entry):
            service.save_flow_entry(entry)
        elif hasattr(service, "save_flow") and callable(service.save_flow):
            service.save_flow(FlowDefinition(name=entry.name, steps=tuple(_legacy_step_label(step) for step in entry.steps), step_delay_ms=entry.step_delay_ms))
        else:
            raise RuntimeError("流程保存服务不可用。")
    except Exception as exc:
        return ToolResult.failure(
            state="flow_draft_save_failed",
            message=str(exc),
            code="FLOW_DRAFT_SAVE_FAILED",
            data=_flow_draft_data(current),
        )
    return ToolResult.success(
        state="flow_draft_saved",
        message=f"已保存流程草案：{flow_name}。",
        data=_flow_draft_data(current) | {
            "flow_name": flow_name,
            "saved": True,
            "entry": _flow_entry_to_data(entry),
        },
    )


def query_registered_flow(service: Any, flow_name: str | None = None) -> ToolResult:
    clean_name = str(flow_name or "").strip()
    if not clean_name:
        names = _list_flow_names(service)
        return ToolResult.success(
            state="registered_flow_list",
            message="已读取已登记流程列表。",
            data={
                "flow_names": names,
                "count": len(names),
                "generates_command": False,
            },
        )
    entry = _get_flow_entry(service, clean_name)
    if entry is None:
        return ToolResult.failure(
            state="registered_flow_not_found",
            message=f"未找到已登记流程：{clean_name}。",
            code="REGISTERED_FLOW_NOT_FOUND",
            data={
                "flow_name": clean_name,
                "flow_names": _list_flow_names(service),
                "generates_command": False,
            },
        )
    data = _flow_entry_to_data(entry)
    return ToolResult.success(
        state="registered_flow_loaded",
        message=f"已读取已登记流程：{clean_name}，共 {data['step_count']} 步。",
        data={
            "flow_name": clean_name,
            "step_count": data["step_count"],
            "entry": data,
            "generates_command": False,
        },
    )


def prepare_registered_flow_execution(service: Any, flow_name: str, *, mode: str = "start") -> ToolResult:
    clean_name = str(flow_name or "").strip()
    if not clean_name:
        return ToolResult.failure(
            state="registered_flow_name_missing",
            message="请提供要执行的流程名称。",
            code="REGISTERED_FLOW_NAME_MISSING",
            data={"generates_command": False},
            fields=["flow_name"],
        )
    entry = _get_flow_entry(service, clean_name)
    if entry is None:
        return ToolResult.failure(
            state="registered_flow_not_found",
            message=f"未找到已登记流程：{clean_name}。",
            code="REGISTERED_FLOW_NOT_FOUND",
            data={
                "flow_name": clean_name,
                "flow_names": _list_flow_names(service),
                "generates_command": False,
            },
        )
    clean_mode = str(mode or "start").strip() or "start"
    data = _flow_entry_to_data(entry)
    unsupported_steps = _unsupported_registered_flow_steps(entry)
    if unsupported_steps:
        return ToolResult.failure(
            state="registered_flow_unsupported_steps",
            message="当前阶段不允许执行包含 Func106/Func107 点动步骤的已登记流程，请先改为 Func108/Func112 等受支持动作。",
            code="REGISTERED_FLOW_UNSUPPORTED_STEPS",
            data={
                "flow_name": clean_name,
                "unsupported_steps": unsupported_steps,
                "entry": data,
                "generates_command": False,
            },
        )
    return ToolResult.success(
        state="registered_flow_execution_draft",
        message=f"已准备流程“{clean_name}”的执行草案，等待门禁和确认。",
        data={
            "flow_name": clean_name,
            "mode": clean_mode,
            "step_count": data["step_count"],
            "entry": data,
            "requires_execution_gate": True,
            "requires_confirmation": True,
            "generates_command": False,
        },
    )


def _unsupported_registered_flow_steps(entry: FlowEntry) -> list[dict[str, Any]]:
    unsupported: list[dict[str, Any]] = []
    for index, step in enumerate(entry.steps, start=1):
        try:
            func_id = int(getattr(step, "func_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if func_id not in {106, 107}:
            continue
        unsupported.append(
            {
                "step_id": int(getattr(step, "step_id", index) or index),
                "func_id": func_id,
                "action": str(getattr(step, "action", "") or getattr(step, "description", "") or f"step_{index}"),
            }
        )
    return unsupported


def set_flow_draft(service: ExecutionPlanService, draft: dict[str, Any]) -> ToolResult:
    try:
        service.set_pending_flow_draft(dict(draft or {}))
    except Exception as exc:
        return ToolResult.failure(
            state="flow_draft_rejected",
            message=str(exc),
            code="FLOW_DRAFT_REJECTED",
            data={"draft": dict(draft or {})},
        )
    current = service.pending_flow_draft() or {}
    clarification = service.current_clarification()
    if clarification is not None:
        missing_fields = [str(getattr(clarification, "missing_field", "") or "")]
        answer_types = [str(item) for item in tuple(getattr(clarification, "accepted_answer_types", ()) or ())]
        return ToolResult.failure(
            state="flow_draft_needs_clarification",
            message=str(getattr(clarification, "question", "") or "请补充流程参数。"),
            code="FLOW_DRAFT_MISSING_PARAMS",
            data=_flow_draft_data(current, missing_fields=missing_fields) | {"accepted_answer_types": answer_types},
            fields=missing_fields,
        )
    return ToolResult.success(
        state="flow_draft_updated",
        message="已生成流程草案。",
        data=_flow_draft_data(current),
    )


def query_current_draft(service: ExecutionPlanService) -> ToolResult:
    current = service.pending_flow_draft()
    if current is None:
        return ToolResult.failure(
            state="flow_draft_not_found",
            message="当前没有流程草案。",
            code="FLOW_DRAFT_NOT_FOUND",
        )
    return ToolResult.success(
        state="flow_draft_loaded",
        message=_flow_draft_summary(current),
        data=_flow_draft_data(current),
    )


def query_flow_draft(draft: dict[str, Any] | None) -> ToolResult:
    current = dict(draft or {})
    if not current:
        return ToolResult.failure(
            state="flow_draft_not_found",
            message="当前没有流程草案。",
            code="FLOW_DRAFT_NOT_FOUND",
        )
    return ToolResult.success(
        state="flow_draft_loaded",
        message=_flow_draft_summary(current),
        data=_flow_draft_data(current),
    )


def cancel_flow_draft(service: ExecutionPlanService) -> ToolResult:
    had_pending = service.pending_plan is not None
    service.cancel_pending_plan()
    return ToolResult.success(
        state="flow_draft_cancelled",
        message="已取消当前流程草案。" if had_pending else "当前没有流程草案。",
        data={"had_pending": had_pending},
    )


def _ensure_service_draft(service: ExecutionPlanService, draft: dict[str, Any] | None) -> dict[str, Any]:
    current = service.pending_flow_draft()
    if current is not None:
        return dict(current)
    seed = dict(draft or {})
    if not seed:
        seed = {"flow_name": "未命名流程", "expanded_steps": []}
    service.set_pending_flow_draft(seed)
    return dict(service.pending_flow_draft() or seed)


def _flow_result_after_update(service: ExecutionPlanService, *, message: str) -> ToolResult:
    current = service.pending_flow_draft() or {}
    clarification = service.current_clarification()
    if clarification is not None:
        missing_fields = [str(getattr(clarification, "missing_field", "") or "")]
        return ToolResult.failure(
            state="flow_draft_needs_clarification",
            message=str(getattr(clarification, "question", "") or message or "请继续补充流程参数。"),
            code="FLOW_DRAFT_MISSING_PARAMS",
            data=_flow_draft_data(current, missing_fields=missing_fields) | {
                "accepted_answer_types": [str(item) for item in tuple(getattr(clarification, "accepted_answer_types", ()) or ())],
            },
            fields=missing_fields,
        )
    return ToolResult.success(
        state="flow_draft_updated",
        message=str(message or "已更新流程草案。"),
        data=_flow_draft_data(current),
    )


def _normalize_step_action(text: str) -> str:
    compact = "".join(str(text or "").split())
    compact = compact.strip("，。,.；;！!？?")
    replacements = (
        "添加第一步是",
        "添加第1步是",
        "添加一步是",
        "第一步是",
        "第1步是",
        "添加第一步",
        "添加第1步",
        "添加一步",
        "添加下一步",
        "添加一个",
    )
    for prefix in replacements:
        if compact.startswith(prefix):
            compact = compact[len(prefix):]
            break
    if compact in {"一个位置", "位置"}:
        return "移动到位置"
    return compact or str(text or "").strip()


def _split_spoken_step_actions(text: str) -> list[str]:
    import re

    raw = str(text or "")
    matches = list(re.finditer(r"(?:步骤|第)\s*(?:[一二三四五六七八九十]+|\d+)\s*(?:步)?[，,、：:是]?", raw))
    if len(matches) < 2:
        return []
    actions: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        chunk = raw[start:end].strip(" \t\r\n，。,.；;！!？?")
        action = _normalize_step_action(chunk)
        if action:
            actions.append(action)
    return actions


def _step_func_id(action: str) -> int:
    compact = "".join(str(action or "").split())
    if any(word in compact for word in ("等待", "延时", "暂停")):
        return 109
    if "IO" in compact.upper() or "输出" in compact:
        return 120
    parsed = _parse_inline_command_params(action)
    if parsed:
        return int(parsed.get("func_id", 108) or 108)
    return 108


def _initial_step_params(action: str) -> dict[str, Any]:
    parsed = _parse_inline_command_params(action)
    if parsed and int(parsed.get("func_id", 0) or 0) == 108:
        params = dict(parsed.get("params", {}) or {})
        if all(key in params for key in ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")):
            params.setdefault("spd_pct", 50.0)
            return params
    func_id = _step_func_id(action)
    if func_id == 108:
        return {"spd_pct": 50.0}
    if func_id == 109:
        delay_sec = _parse_inline_duration(action)
        if delay_sec is not None:
            return {"delay_sec": delay_sec}
        return {}
    if func_id == 120:
        return _parse_inline_io(action)
    return {}


def _parse_inline_command_params(action: str) -> dict[str, Any]:
    result = command_tools.parse_command_params(str(action or ""))
    if not result.ok:
        return {}
    data = dict(result.data or {})
    func_id = int(data.get("func_id", 0) or 0)
    params = dict(data.get("params", {}) or {})
    if func_id == 108 and params:
        return {"func_id": func_id, "params": params}
    return {}


def _parse_inline_duration(text: str) -> float | None:
    import re

    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(毫秒|ms|秒|s)", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    if value <= 0.0:
        return None
    unit = match.group(2).lower()
    if unit in {"毫秒", "ms"}:
        value /= 1000.0
    return value


def _parse_inline_io(text: str) -> dict[str, Any]:
    import re

    compact = re.sub(r"\s+", "", str(text or ""))
    params: dict[str, Any] = {}
    no_match = re.search(r"(?:IO|io|输出|Y|y)(\d+)", compact)
    if no_match:
        io_no = int(no_match.group(1))
        if 0 <= io_no <= 11:
            params["io_no"] = io_no
    lowered = compact.lower()
    if any(word in lowered for word in ("打开", "开启", "开", "on")):
        params["io_action"] = 1
    elif any(word in lowered for word in ("关闭", "关", "off")):
        params["io_action"] = 0
    return params


def _parse_flow_param_edit(text: str) -> dict[str, float]:
    compact = command_tools.normalize_chinese_numbers(re.sub(r"\s+", "", str(text or "")))
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*%?", compact)
    if not match:
        return {}
    value = float(match.group(1))
    if value <= 0.0 or value > 100.0:
        return {}
    if any(word in compact for word in ("减速度", "减速")):
        return {"dec_pct": value}
    if any(word in compact for word in ("加速度", "加速")):
        return {"acc_pct": value}
    if any(word in compact for word in ("速度", "速")) or any(word in compact for word in ("改成", "改为", "修改为", "调成", "设为")):
        return {"spd_pct": value}
    return {}


def _parse_step_reference(text: str) -> int | None:
    compact = re.sub(r"\s+", "", str(text or ""))
    digit = re.search(r"第(\d+)步", compact)
    if digit:
        return int(digit.group(1))
    cn = re.search(r"第([一二两三四五六七八九十]+)步", compact)
    if not cn:
        return None
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return mapping.get(cn.group(1))


def _param_label(key: str) -> str:
    return {
        "spd_pct": "速度",
        "acc_pct": "加速度",
        "dec_pct": "减速度",
    }.get(str(key), str(key))


def _flow_param_subset(params: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "target_x",
        "target_y",
        "target_z",
        "target_rx",
        "target_ry",
        "target_rz",
        "spd_pct",
        "acc_pct",
        "dec_pct",
        "stop_cmd",
        "fuzzy_pos",
        "fuzzy_spd",
        "fuzzy_acc",
        "fuzzy_dec",
        "move_type",
        "position_increment",
    }
    return {key: value for key, value in dict(params or {}).items() if key in allowed}


def _missing_save_fields(steps: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for step in steps:
        params = dict(step.get("params") or {})
        func_id = int(step.get("func_id") or step.get("func_num") or 0)
        if func_id == 108:
            required = ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
            if any(key not in params for key in required):
                if "target_pose" not in missing:
                    missing.append("target_pose")
        if func_id in {109, 110} and "delay_sec" not in params:
            if "delay_sec" not in missing:
                missing.append("delay_sec")
        if func_id == 120:
            if "io_no" not in params and "io_no" not in missing:
                missing.append("io_no")
            if "io_action" not in params and "io_action" not in missing:
                missing.append("io_action")
    return missing


def _flow_entry_from_draft(flow_name: str, steps: list[dict[str, Any]]) -> FlowEntry:
    return FlowEntry(
        name=flow_name,
        steps=[
            FlowStep(
                step_id=int(step.get("step_id") or index),
                action=str(step.get("action") or step.get("description") or f"step_{index}"),
                func_id=int(step.get("func_id") or step.get("func_num") or 0),
                params=dict(step.get("params") or {}),
                position_name=step.get("position_name") or step.get("target_label"),
                spd_pct=int(float(dict(step.get("params") or {}).get("spd_pct", step.get("spd_pct", 50)) or 50)),
                description=str(step.get("description") or step.get("action") or f"step_{index}"),
            )
            for index, step in enumerate(steps, start=1)
        ],
        step_delay_ms=1000,
    )


def _legacy_step_label(step: FlowStep) -> str:
    return str(step.params.get("query_key") or step.description or step.action)


def _flow_entry_to_data(entry: FlowEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "step_count": len(entry.steps),
        "steps": [
            {
                "step_id": step.step_id,
                "action": step.action,
                "func_id": step.func_id,
                "params": dict(step.params),
                "description": step.description,
            }
            for step in entry.steps
        ],
    }


def _get_flow_entry(service: Any, flow_name: str) -> FlowEntry | None:
    getter = getattr(service, "get_flow_entry", None)
    if callable(getter):
        return getter(flow_name)
    registry = getattr(service, "flow_registry", None)
    getter = getattr(registry, "get", None)
    if callable(getter):
        return getter(flow_name)
    return None


def _list_flow_names(service: Any) -> list[str]:
    lister = getattr(service, "list_flow_names", None)
    if callable(lister):
        return sorted(str(name) for name in list(lister()))
    registry = getattr(service, "flow_registry", None)
    list_all = getattr(registry, "list_all", None)
    if callable(list_all):
        return sorted(str(getattr(entry, "name", "") or "") for entry in list_all() if str(getattr(entry, "name", "") or ""))
    return []


def _flow_draft_summary(draft: dict[str, Any]) -> str:
    data = _flow_draft_data(draft)
    flow_name = str(data.get("flow_name", "") or "未命名流程")
    step_count = int(data.get("step_count", 0) or 0)
    steps = list(dict(data.get("draft", {}) or {}).get("expanded_steps") or dict(data.get("draft", {}) or {}).get("steps") or [])
    labels: list[str] = []
    for index, step in enumerate(steps[:3], start=1):
        if not isinstance(step, dict):
            continue
        label = str(step.get("action") or step.get("description") or f"第{index}步").strip()
        if label:
            labels.append(f"{index}. {label}")
    suffix = f" 步骤：{'；'.join(labels)}。" if labels else ""
    return f"当前流程草案：{flow_name}，共 {step_count} 步。{suffix}"


def _flow_draft_data(draft: dict[str, Any], *, missing_fields: list[str] | None = None) -> dict[str, Any]:
    steps = list(draft.get("expanded_steps") or draft.get("steps") or [])
    return {
        "flow_name": str(draft.get("flow_name", "") or ""),
        "step_count": len(steps),
        "draft": dict(draft),
        "missing_fields": list(missing_fields or []),
    }
