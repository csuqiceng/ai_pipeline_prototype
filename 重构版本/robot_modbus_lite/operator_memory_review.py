from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .agent_tools.tool_result import ToolResult


ToolCaller = Callable[..., ToolResult]


@dataclass(frozen=True)
class MemoryReviewCommandResult:
    handled: bool
    category: str = ""
    result: ToolResult | None = None


@dataclass(frozen=True)
class MemoryReviewRow:
    memory_id: str
    kind: str
    key: str
    value_text: str
    status: str
    source: str = ""
    confidence: float | None = None
    audit_count: int = 0
    last_event: str = ""
    detail_text: str = ""


@dataclass(frozen=True)
class MemoryReviewView:
    ok: bool
    message: str
    rows: tuple[MemoryReviewRow, ...] = ()
    detail_text: str = ""
    status_options: tuple[str, ...] = ("candidate", "active", "disabled", "rolled_back")
    kind_options: tuple[str, ...] = ("asr_alias", "flow_preference", "user_preference", "position_alias", "command_alias")


class OperatorMemoryReviewCommands:
    def __init__(self, call_tool: ToolCaller) -> None:
        self._call_tool = call_tool

    def handle(self, text: str) -> MemoryReviewCommandResult:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return MemoryReviewCommandResult(False)
        status = self._status_from_text(compact)
        if status:
            kind = memory_kind_from_text(text)
            kwargs: dict[str, Any] = {"status": status, "include_audit": True}
            if kind:
                kwargs["kind"] = kind
            return MemoryReviewCommandResult(
                True,
                "经验审核",
                self._call_tool("query_memory_review", **kwargs),
            )
        if any(keyword in compact for keyword in ("批准全部待审核经验", "通过全部待审核经验", "批准所有候选经验", "通过所有候选经验")):
            return MemoryReviewCommandResult(True, "经验批量审核", self._approve_all_candidates())
        if any(keyword in compact for keyword in ("停用全部生效经验", "禁用全部生效经验", "停用所有生效经验", "禁用所有生效经验")):
            return MemoryReviewCommandResult(True, "经验批量停用", self._batch_update_active("disable_memory", "disabled"))
        if any(keyword in compact for keyword in ("回滚全部生效经验", "撤回全部生效经验", "回滚所有生效经验", "撤回所有生效经验")):
            return MemoryReviewCommandResult(True, "经验批量回滚", self._batch_update_active("rollback_memory", "rolled_back"))
        memory_id = memory_id_from_text(text)
        if memory_id and any(keyword in compact for keyword in ("批准经验", "通过经验", "审核通过经验", "批准记忆", "通过记忆")):
            return MemoryReviewCommandResult(
                True,
                "经验审核",
                self._call_tool("approve_memory_candidate", memory_id=memory_id, reviewer="operator-ui"),
            )
        if memory_id and any(keyword in compact for keyword in ("回滚经验", "撤回经验", "停用经验", "回滚记忆", "撤回记忆")):
            return MemoryReviewCommandResult(
                True,
                "经验回滚",
                self._call_tool(
                    "rollback_memory",
                    memory_id=memory_id,
                    reviewer="operator-ui",
                    reason="operator command",
                ),
            )
        return MemoryReviewCommandResult(False)

    def _approve_all_candidates(self) -> ToolResult:
        listed = self._call_tool("query_memory_review", status="candidate", include_audit=False)
        if not getattr(listed, "ok", False):
            return listed
        data = getattr(listed, "data", {}) if isinstance(getattr(listed, "data", {}), dict) else {}
        memories = list(data.get("memories", []) or [])
        approved: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            memory_id = str(memory.get("memory_id", "") or "")
            if not memory_id:
                continue
            result = self._call_tool("approve_memory_candidate", memory_id=memory_id, reviewer="operator-ui")
            if getattr(result, "ok", False):
                result_data = getattr(result, "data", {}) if isinstance(getattr(result, "data", {}), dict) else {}
                approved.append(dict(result_data.get("memory", {}) or {"memory_id": memory_id}))
            else:
                failures.append({"memory_id": memory_id, "message": str(getattr(result, "message", "") or "")})
        if failures:
            return ToolResult.failure(
                state="memory_batch_approve_partial_failed",
                message=f"已批准 {len(approved)} 条候选经验，{len(failures)} 条失败。",
                code="MEMORY_BATCH_APPROVE_PARTIAL_FAILED",
                data={"approved": approved, "failures": failures},
            )
        return ToolResult.success(
            state="memory_batch_approved",
            message=f"已批准 {len(approved)} 条候选经验。",
            data={"approved": approved, "count": len(approved)},
        )

    def _batch_update_active(self, tool_name: str, target_status: str) -> ToolResult:
        listed = self._call_tool("query_memory_review", status="active", include_audit=False)
        if not getattr(listed, "ok", False):
            return listed
        data = getattr(listed, "data", {}) if isinstance(getattr(listed, "data", {}), dict) else {}
        memories = list(data.get("memories", []) or [])
        changed: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            memory_id = str(memory.get("memory_id", "") or "")
            if not memory_id:
                continue
            result = self._call_tool(
                tool_name,
                memory_id=memory_id,
                reviewer="operator-ui",
                reason="operator batch command",
            )
            if getattr(result, "ok", False):
                result_data = getattr(result, "data", {}) if isinstance(getattr(result, "data", {}), dict) else {}
                changed.append(dict(result_data.get("memory", {}) or {"memory_id": memory_id, "status": target_status}))
            else:
                failures.append({"memory_id": memory_id, "message": str(getattr(result, "message", "") or "")})
        verb = "停用" if target_status == "disabled" else "回滚"
        if failures:
            return ToolResult.failure(
                state=f"memory_batch_{target_status}_partial_failed",
                message=f"已{verb} {len(changed)} 条生效经验，{len(failures)} 条失败。",
                code=f"MEMORY_BATCH_{target_status.upper()}_PARTIAL_FAILED",
                data={"changed": changed, "failures": failures},
            )
        return ToolResult.success(
            state=f"memory_batch_{target_status}",
            message=f"已{verb} {len(changed)} 条生效经验。",
            data={"changed": changed, "count": len(changed), "status": target_status},
        )

    @staticmethod
    def _status_from_text(compact: str) -> str:
        if any(keyword in compact for keyword in ("查看待审核经验", "查看候选经验", "经验审核列表", "待审核记忆")):
            return "candidate"
        if any(keyword in compact for keyword in ("查看生效经验", "查看已生效经验", "查看active经验", "生效记忆")):
            return "active"
        if any(keyword in compact for keyword in ("查看已回滚经验", "查看回滚经验", "回滚记忆列表")):
            return "rolled_back"
        if any(keyword in compact for keyword in ("查看停用经验", "查看已停用经验", "停用记忆列表")):
            return "disabled"
        return ""


def memory_id_from_text(text: str) -> str:
    match = re.search(r"(mem[_\-][A-Za-z0-9_\-]+)", text or "")
    return str(match.group(1) or "") if match else ""


def memory_kind_from_text(text: str) -> str:
    match = re.search(r"\b(asr_alias|flow_preference|user_preference|position_alias|command_alias)\b", text or "", re.IGNORECASE)
    return str(match.group(1) or "").lower() if match else ""


def memory_review_view(result: ToolResult) -> MemoryReviewView:
    message = str(getattr(result, "message", "") or "")
    data = getattr(result, "data", {}) if isinstance(getattr(result, "data", {}), dict) else {}
    rows: list[MemoryReviewRow] = []
    memories = data.get("memories")
    if isinstance(memories, list):
        for item in memories:
            if isinstance(item, dict):
                rows.append(_memory_review_row(item))
    else:
        memory = data.get("memory")
        if isinstance(memory, dict):
            rows.append(_memory_review_row(memory))
    detail_text = rows[0].detail_text if rows else ""
    return MemoryReviewView(
        ok=bool(getattr(result, "ok", False)),
        message=message,
        rows=tuple(rows),
        detail_text=detail_text,
    )


def memory_tool_result_text(result: ToolResult) -> str:
    view = memory_review_view(result)
    if view.rows:
        lines = [view.message or f"找到 {len(view.rows)} 条经验记录。"]
        for row in view.rows[:10]:
            lines.append(f"{row.memory_id} | {row.kind} | {row.key} -> {row.value_text} | {row.status}")
        if len(view.rows) > 10:
            lines.append(f"还有 {len(view.rows) - 10} 条未显示。")
        return "\n".join(line for line in lines if line)
    if view.message:
        return view.message
    if str(getattr(result, "state", "") or "") == "memory_review_listed":
        return "当前没有待审核经验。"
    return "操作完成。" if getattr(result, "ok", False) else "操作失败。"


def _memory_review_row(memory: dict[str, Any]) -> MemoryReviewRow:
    audit_events = list(memory.get("audit_events", []) or [])
    last_event = ""
    if audit_events:
        last = audit_events[-1]
        if isinstance(last, dict):
            last_event = str(last.get("event", "") or "")
    confidence_value = memory.get("confidence")
    try:
        confidence = None if confidence_value is None else float(confidence_value)
    except (TypeError, ValueError):
        confidence = None
    row = MemoryReviewRow(
        memory_id=str(memory.get("memory_id", "") or ""),
        kind=str(memory.get("kind", "") or ""),
        key=str(memory.get("key", "") or ""),
        value_text=str(memory.get("value", {}) or {}),
        status=str(memory.get("status", "") or ""),
        source=str(memory.get("source", "") or ""),
        confidence=confidence,
        audit_count=len(audit_events),
        last_event=last_event,
    )
    return MemoryReviewRow(
        **{**row.__dict__, "detail_text": _memory_detail_text(row, [dict(item) for item in audit_events if isinstance(item, dict)])}
    )


def _memory_detail_text(row: MemoryReviewRow, audit_events: list[dict[str, Any]]) -> str:
    lines = [
        f"memory_id: {row.memory_id}",
        f"kind: {row.kind}",
        f"key: {row.key}",
        f"value: {row.value_text}",
        f"status: {row.status}",
    ]
    if row.source:
        lines.append(f"source: {row.source}")
    if row.confidence is not None:
        lines.append(f"confidence: {row.confidence}")
    if audit_events:
        lines.append("audit:")
        for event in audit_events[-5:]:
            lines.append(f"- {event.get('event', '')} {event.get('created_at', '')}".rstrip())
    return "\n".join(lines)
