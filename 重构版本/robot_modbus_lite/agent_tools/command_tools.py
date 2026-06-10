from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from robot_modbus_lite.agent.address_resolver import AddressResolver
from robot_modbus_lite.agent.atomic_template import AtomicTemplateAgent
from robot_modbus_lite.agent.command_understanding import CommandUnderstandingAgent
from robot_modbus_lite.agent.drafts import (
    REQUIRED_PARAM_KEYS,
    CommandDraft,
    draft_to_query_record as _draft_to_query_record,
)
from robot_modbus_lite.atomic_memory import AtomicMemory
from robot_modbus_lite.agent.parameter_completion import (
    ControllerSnapshot,
    ParameterCompletionAgent,
    ParameterCompletionError,
)
from robot_modbus_lite.agent_tools.tool_result import ToolResult


_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def normalize_chinese_numbers(text: str) -> str:
    raw = str(text or "")

    def replace_match(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        sign = match.group("sign") or ""
        value = _parse_chinese_number(match.group("number"))
        if value is None:
            return match.group(0)
        signed = -value if sign == "负" else value
        return f"{prefix}{_format_number(signed)}"

    pattern = re.compile(
        r"(?P<prefix>RX|RY|RZ|rx|ry|rz|[XYZxyz]|加速度|减速度|速度|加速|减速)\s*"
        r"(?P<sign>负)?(?P<number>[零〇一二两三四五六七八九十百千万点]+)"
    )
    normalized = pattern.sub(replace_match, raw)
    return re.sub(r"(加速度|减速度|速度|加速|减速)\s+(-?\d+(?:\.\d+)?)", r"\1\2", normalized)


def parse_command_params(text: str, *, agent: CommandUnderstandingAgent | None = None) -> ToolResult:
    raw_text = str(text or "")
    normalized_text = normalize_chinese_numbers(raw_text)
    parse_text = _normalize_speed_percent(normalized_text)
    understanding = (agent or CommandUnderstandingAgent()).understand(parse_text)
    intent = str(getattr(understanding, "intent", "") or "")
    if intent == "unknown":
        return ToolResult.failure(
            state="unknown_intent",
            message=str(getattr(understanding, "clarification", "") or "未识别为控制指令。"),
            code="UNKNOWN_INTENT",
            data={
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "needs_model": bool(getattr(understanding, "needs_model", False)),
            },
        )
    return ToolResult.success(
        state="command_params_parsed",
        message="已解析命令参数。",
        data={
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "intent": intent,
            "func_id": getattr(understanding, "func_id", None),
            "params": dict(getattr(understanding, "extracted_params", {}) or {}),
            "confidence": float(getattr(understanding, "confidence", 0.0) or 0.0),
            "needs_model": bool(getattr(understanding, "needs_model", False)),
        },
    )


def lookup_command_schema(command_name: str | int) -> ToolResult:
    func_id = _func_id_from_command_name(command_name)
    if func_id is None or func_id not in REQUIRED_PARAM_KEYS:
        return ToolResult.failure(
            state="command_schema_not_found",
            message=f"未找到命令 schema：{command_name}",
            code="COMMAND_SCHEMA_NOT_FOUND",
            data={"command_name": str(command_name), "generates_command": False},
        )
    required = tuple(REQUIRED_PARAM_KEYS[int(func_id)])
    return ToolResult.success(
        state="command_schema_loaded",
        message=f"已加载 Func{func_id} 命令 schema。",
        data={
            "schema": {
                "func_id": int(func_id),
                "required_params": list(required),
                "optional_params": [],
                "generates_command": False,
            }
        },
    )


def validate_required_params(func_id: int, params: dict[str, Any]) -> ToolResult:
    try:
        required = tuple(REQUIRED_PARAM_KEYS[int(func_id)])
    except (KeyError, TypeError, ValueError):
        return ToolResult.failure(
            state="command_schema_not_found",
            message=f"不支持 Func{func_id} 的必填参数校验。",
            code="COMMAND_SCHEMA_NOT_FOUND",
            data={"func_id": func_id},
        )
    source = dict(params or {})
    missing = [key for key in required if key not in source]
    if missing:
        return ToolResult.failure(
            state="missing_params",
            message="参数不完整，不能执行。",
            code="MISSING_REQUIRED_PARAMS",
            data={"func_id": int(func_id), "missing_fields": missing, "params": source},
            fields=missing,
        )
    return ToolResult.success(
        state="required_params_valid",
        message="必填参数已完整。",
        data={"func_id": int(func_id), "params": source},
    )


def check_param_bounds(params: dict[str, Any], *, bounds: dict[str, Any] | None = None) -> ToolResult:
    source = dict(params or {})
    config = dict(bounds or {})
    checks = {
        "target_x": _range_tuple(config.get("x")),
        "target_y": _range_tuple(config.get("y")),
        "target_z": _range_tuple(config.get("z")),
        "spd_pct": (None, _float_or_none(config.get("safe_speed_max"))),
        "acc_pct": (None, _float_or_none(config.get("safe_acc_max"))),
        "dec_pct": (None, _float_or_none(config.get("safe_dec_max"))),
    }
    violations: list[dict[str, Any]] = []
    for field, limits in checks.items():
        if field not in source or limits is None:
            continue
        value = _float_or_none(source.get(field))
        if value is None:
            continue
        low, high = limits
        if low is not None and value < low or high is not None and value > high:
            violations.append({"field": field, "value": value, "min": low, "max": high})
    if violations:
        return ToolResult.failure(
            state="param_bounds_failed",
            message="参数边界检查未通过。",
            code="PARAM_BOUNDS_FAILED",
            data={"params": source, "bounds": config, "violations": violations},
            fields=[item["field"] for item in violations],
        )
    return ToolResult.success(
        state="param_bounds_passed",
        message="参数边界检查通过。",
        data={"params": source, "bounds": config},
    )


def resolve_command_address(name: str, *, resolver: AddressResolver | None = None) -> ToolResult:
    clean = str(name or "").strip()
    if not clean:
        return ToolResult.failure(
            state="command_address_not_found",
            message="缺少地址名称。",
            code="COMMAND_ADDRESS_NOT_FOUND",
        )
    source = resolver or AddressResolver()
    if not hasattr(source, clean):
        return ToolResult.failure(
            state="command_address_not_found",
            message=f"未找到协议地址：{clean}",
            code="COMMAND_ADDRESS_NOT_FOUND",
            data={"name": clean},
        )
    value = getattr(source, clean)
    return ToolResult.success(
        state="command_address_resolved",
        message=f"已解析协议地址 {clean}。",
        data={"name": clean, "value": value, "generates_command": False},
    )


def build_system_action_draft(text: str, *, agent: CommandUnderstandingAgent | None = None) -> ToolResult:
    raw_text = str(text or "")
    normalized_text = normalize_chinese_numbers(raw_text)
    understanding = (agent or CommandUnderstandingAgent()).understand(normalized_text)
    intent = str(getattr(understanding, "intent", "") or "")
    if not (intent.startswith("sys_") or intent == "alarm_reset"):
        return ToolResult.failure(
            state="system_action_not_matched",
            message="未识别为系统控制动作。",
            code="SYSTEM_ACTION_NOT_MATCHED",
            data={"raw_text": raw_text, "normalized_text": normalized_text, "generates_command": False},
        )
    return ToolResult.success(
        state="system_action_draft_built",
        message="已生成系统动作草案，等待本地门禁处理。",
        data={
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "intent": intent,
            "func_id": int(getattr(understanding, "func_id", 104) or 104),
            "confidence": float(getattr(understanding, "confidence", 0.0) or 0.0),
            "requires_execution_gate": True,
            "generates_command": False,
        },
    )


def parse_command_intent(text: str, *, agent: CommandUnderstandingAgent | None = None) -> ToolResult:
    raw_text = str(text or "")
    normalized_text = normalize_chinese_numbers(raw_text)
    parse_text = _normalize_speed_percent(normalized_text)
    understanding = (agent or CommandUnderstandingAgent()).understand(parse_text)
    intent = str(getattr(understanding, "intent", "") or "")
    if intent == "unknown":
        return ToolResult.failure(
            state="unknown_intent",
            message=str(getattr(understanding, "clarification", "") or "未识别为控制类意图。"),
            code="UNKNOWN_INTENT",
            data={
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "generates_command": False,
                "needs_model": bool(getattr(understanding, "needs_model", False)),
            },
        )
    return ToolResult.success(
        state="command_intent_parsed",
        message="已解析命令意图。",
        data={
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "intent": intent,
            "func_id": getattr(understanding, "func_id", None),
            "confidence": float(getattr(understanding, "confidence", 0.0) or 0.0),
            "needs_model": bool(getattr(understanding, "needs_model", False)),
            "bypass_completion": bool(getattr(understanding, "bypass_completion", False)),
            "generates_command": False,
        },
    )


def build_command_draft(
    text: str,
    *,
    snapshot_provider: Callable[[], ControllerSnapshot] | None = None,
    understanding_agent: CommandUnderstandingAgent | None = None,
    completion_agent: ParameterCompletionAgent | None = None,
) -> ToolResult:
    raw_text = str(text or "")
    normalized_text = normalize_chinese_numbers(raw_text)
    parse_text = _normalize_speed_percent(normalized_text)
    understanding = (understanding_agent or CommandUnderstandingAgent()).understand(parse_text)
    intent = str(getattr(understanding, "intent", "") or "")
    if intent == "unknown":
        return ToolResult.failure(
            state="unknown_intent",
            message=str(getattr(understanding, "clarification", "") or "未识别为控制指令，未生成命令草案。"),
            code="UNKNOWN_INTENT",
            data={
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "generates_command": False,
                "needs_model": bool(getattr(understanding, "needs_model", False)),
            },
        )
    agent = completion_agent or ParameterCompletionAgent(snapshot_provider or _missing_snapshot)
    try:
        draft = agent.complete(understanding)
    except ParameterCompletionError as exc:
        return ToolResult.failure(
            state="command_draft_needs_clarification",
            message=str(exc),
            code="COMMAND_DRAFT_INCOMPLETE",
            data={
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "intent": intent,
                "func_id": getattr(understanding, "func_id", None),
                "params": dict(getattr(understanding, "extracted_params", {}) or {}),
                "generates_command": False,
            },
        )
    return ToolResult.success(
        state="command_draft_built",
        message="已生成命令草案，等待后续门禁和确认。",
        data={
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "draft": asdict(draft),
            "generates_command": False,
        },
    )


def apply_atomic_template(text: str, *, memory: AtomicMemory) -> ToolResult:
    raw_text = str(text or "")
    answer = AtomicTemplateAgent(memory=memory).apply(raw_text)
    if answer is None:
        return ToolResult.failure(
            state="atomic_template_not_matched",
            message="未匹配到可用原子模板。",
            code="ATOMIC_TEMPLATE_NOT_MATCHED",
            data={"raw_text": raw_text, "generates_command": False},
        )
    record = answer.get("record")
    if record is None:
        return ToolResult.failure(
            state="atomic_template_invalid",
            message="原子模板未返回可用记录。",
            code="ATOMIC_TEMPLATE_INVALID",
            data={"raw_text": raw_text, "generates_command": False},
        )
    return ToolResult.success(
        state="atomic_template_applied",
        message=str(answer.get("text", "") or "已生成原子模板草案，等待门禁和确认。"),
        data={
            "raw_text": raw_text,
            "action_type": str(answer.get("action_type", "") or "atomic_template"),
            "target": str(answer.get("target", "") or ""),
            "query_record": _query_record_to_data(record),
            "requires_confirmation": bool(answer.get("requires_confirmation", True)),
            "risk_level": str(answer.get("risk_level", "") or ""),
            "generates_command": False,
        },
    )


def draft_to_query_record(draft: CommandDraft | dict[str, Any]) -> ToolResult:
    command_draft = _command_draft_from_value(draft)
    if not command_draft.confirmed:
        return ToolResult.failure(
            state="command_draft_not_confirmed",
            message="命令草案尚未确认，不能转换为执行记录。",
            code="COMMAND_DRAFT_NOT_CONFIRMED",
            data={"draft_id": command_draft.draft_id, "func_id": command_draft.func_id},
        )
    try:
        record = _draft_to_query_record(command_draft)
    except ValueError as exc:
        return ToolResult.failure(
            state="command_draft_invalid",
            message=str(exc),
            code="COMMAND_DRAFT_INVALID",
            data={"draft_id": command_draft.draft_id, "func_id": command_draft.func_id},
        )
    return ToolResult.success(
        state="query_record_built",
        message="已将确认后的命令草案转换为执行记录。",
        data={
            "draft_id": command_draft.draft_id,
            "query_record": {
                "query_key": str(record.query_key),
                "func_num": int(record.func_num),
                "params": dict(record.params),
                "description": str(record.description),
            },
        },
    )


def _query_record_to_data(record: Any) -> dict[str, Any]:
    return {
        "query_key": str(getattr(record, "query_key", "") or ""),
        "func_num": int(getattr(record, "func_num", 0) or 0),
        "params": dict(getattr(record, "params", {}) or {}),
        "description": str(getattr(record, "description", "") or ""),
        "safety_level": int(getattr(record, "safety_level", 0) or 0),
    }


def _command_draft_from_value(value: CommandDraft | dict[str, Any]) -> CommandDraft:
    if isinstance(value, CommandDraft):
        return value
    payload = dict(value or {})
    return CommandDraft(
        draft_id=str(payload.get("draft_id", "") or ""),
        func_id=int(payload.get("func_id", 0) or 0),
        intent=str(payload.get("intent", "") or ""),
        params=dict(payload.get("params", {}) or {}),
        param_sources=dict(payload.get("param_sources", {}) or {}),
        raw_text=str(payload.get("raw_text", "") or ""),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        precheck_result=payload.get("precheck_result"),
        confirmed=bool(payload.get("confirmed", False)),
    )


def _normalize_speed_percent(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        suffix = match.group("suffix") or ""
        if suffix == "%":
            return match.group(0)
        return f"{match.group('label')}{match.group('value')}%"

    return re.sub(
        r"(?P<label>加速度|减速度|速度|加速|减速)\s*(?P<value>-?\d+(?:\.\d+)?)(?P<suffix>%?)",
        replace,
        text,
    )


def _parse_chinese_number(text: str) -> float | None:
    clean = str(text or "")
    if not clean:
        return None
    if "点" in clean:
        integer_text, decimal_text = clean.split("点", 1)
        integer = _parse_chinese_integer(integer_text) if integer_text else 0
        if integer is None:
            return None
        digits: list[str] = []
        for char in decimal_text:
            if char not in _CN_DIGITS:
                return None
            digits.append(str(_CN_DIGITS[char]))
        return float(f"{integer}.{''.join(digits)}")
    integer = _parse_chinese_integer(clean)
    return None if integer is None else float(integer)


def _parse_chinese_integer(text: str) -> int | None:
    clean = str(text or "")
    if not clean:
        return 0
    total = 0
    section = 0
    number = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for char in clean:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
            continue
        if char in units:
            unit = units[char]
            section += (number or 1) * unit
            number = 0
            continue
        if char == "万":
            total += (section + number) * 10000
            section = 0
            number = 0
            continue
        return None
    return total + section + number


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _func_id_from_command_name(command_name: str | int) -> int | None:
    try:
        return int(command_name)
    except (TypeError, ValueError):
        pass
    clean = str(command_name or "").strip().lower()
    aliases = {
        "estop": 104,
        "pause": 104,
        "resume": 104,
        "alarm_reset": 104,
        "jog_positive": 106,
        "jog_negative": 107,
        "move": 108,
        "move_linear": 108,
        "absolute_motion": 108,
        "continuous_path": 112,
        "delay": 109,
        "delay_blocking": 109,
        "io": 120,
    }
    return aliases.get(clean)


def _range_tuple(value: Any) -> tuple[float | None, float | None] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    return _float_or_none(value[0]), _float_or_none(value[1])


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _missing_snapshot() -> ControllerSnapshot:
    return ControllerSnapshot(read_ok=False)
