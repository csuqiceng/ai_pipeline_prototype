from __future__ import annotations

from typing import Any

from robot_modbus_lite.agent_runtime.memory_store import AgentMemoryStore, ForbiddenMemoryCandidateError

from .tool_result import ToolResult


def create_memory_candidate(
    store: AgentMemoryStore,
    *,
    kind: str,
    key: str,
    value: dict[str, Any],
    source: str = "",
    confidence: float | None = None,
) -> ToolResult:
    try:
        memory = store.create_candidate(
            kind=kind,
            key=key,
            value=value,
            source=source,
            confidence=confidence,
        )
    except ForbiddenMemoryCandidateError as exc:
        return ToolResult.failure(
            state="forbidden_memory_candidate",
            message="该候选经验属于不可学习区，已拒绝写入。",
            code="FORBIDDEN_MEMORY_CANDIDATE",
            data={"kind": exc.kind, "key": exc.key, "reason": exc.reason},
        )
    return ToolResult.success(
        state="memory_candidate_created",
        message="已创建候选经验，等待审核后才能生效。",
        data={"memory": memory},
    )


def query_memory_candidates(store: AgentMemoryStore, *, kind: str | None = None) -> ToolResult:
    memories = store.list_memories(status="candidate", kind=kind)
    return ToolResult.success(
        state="memory_candidates_listed",
        message=f"找到 {len(memories)} 条候选经验。",
        data={"memories": memories},
    )


def query_memory_review(
    store: AgentMemoryStore,
    *,
    status: str | None = None,
    kind: str | None = None,
    include_audit: bool = True,
) -> ToolResult:
    memories = store.list_memories(status=status, kind=kind)
    if include_audit:
        memories = [
            {
                **memory,
                "audit_events": store.list_audit_events(memory_id=str(memory.get("memory_id", ""))),
            }
            for memory in memories
        ]
    return ToolResult.success(
        state="memory_review_listed",
        message=f"找到 {len(memories)} 条经验记录。",
        data={
            "memories": memories,
            "count": len(memories),
            "status": status,
            "kind": kind,
            "include_audit": include_audit,
        },
    )


def approve_memory_candidate(
    store: AgentMemoryStore,
    memory_id: str,
    *,
    reviewer: str = "",
) -> ToolResult:
    try:
        memory = store.approve_memory(memory_id, reviewer=reviewer)
    except ForbiddenMemoryCandidateError as exc:
        return ToolResult.failure(
            state="forbidden_memory_candidate",
            message="该候选经验属于不可学习区，已拒绝生效。",
            code="FORBIDDEN_MEMORY_CANDIDATE",
            data={"kind": exc.kind, "key": exc.key, "reason": exc.reason},
        )
    return ToolResult.success(
        state="memory_approved",
        message="候选经验已审核通过并生效。",
        data={"memory": memory},
    )


def disable_memory(
    store: AgentMemoryStore,
    memory_id: str,
    *,
    reviewer: str = "",
    reason: str = "",
) -> ToolResult:
    memory = store.disable_memory(memory_id, reviewer=reviewer, reason=reason)
    return ToolResult.success(
        state="memory_disabled",
        message="经验已停用。",
        data={"memory": memory},
    )


def rollback_memory(
    store: AgentMemoryStore,
    memory_id: str,
    *,
    reviewer: str = "",
    reason: str = "",
) -> ToolResult:
    memory = store.rollback_memory(memory_id, reviewer=reviewer, reason=reason)
    return ToolResult.success(
        state="memory_rolled_back",
        message="经验已回滚并写入审计日志。",
        data={"memory": memory},
    )


def lookup_active_memory(
    store: AgentMemoryStore,
    *,
    kind: str,
    key: str | None = None,
) -> ToolResult:
    memories = store.lookup_active(kind=kind, key=key)
    return ToolResult.success(
        state="active_memory_found" if memories else "active_memory_empty",
        message=f"找到 {len(memories)} 条生效经验。",
        data={"memories": memories},
    )


def record_feedback_vote(
    store: AgentMemoryStore,
    *,
    interaction_id: str,
    target_type: str,
    target_id: str,
    vote: str,
    note: str = "",
) -> ToolResult:
    vote_payload = store.record_feedback_vote(
        interaction_id=interaction_id,
        target_type=target_type,
        target_id=target_id,
        vote=vote,
        note=note,
    )
    return ToolResult.success(
        state="feedback_vote_recorded",
        message="用户反馈已记录。",
        data={"vote": vote_payload},
    )


def record_memory_applied(
    store: AgentMemoryStore,
    memory_id: str,
    *,
    context: dict[str, Any] | None = None,
) -> ToolResult:
    audit = store.record_memory_applied(memory_id, context=context)
    return ToolResult.success(
        state="memory_applied_recorded",
        message="经验应用记录已写入审计日志。",
        data={"audit": audit},
    )


def save_position_alias(
    registry: Any,
    *,
    name: str,
    pose: Any,
    created_by: str = "operator",
    spd: int = 50,
    move_type: int = 0,
) -> ToolResult:
    clean_name = str(name or "").strip()
    if not clean_name:
        return ToolResult.failure(
            state="position_alias_name_missing",
            message="位置名称不能为空。",
            code="POSITION_ALIAS_NAME_MISSING",
            data={"generates_command": False},
            fields=["name"],
        )
    try:
        ok, message = registry.set_position(
            clean_name,
            _pose6(pose),
            created_by=created_by,
            spd=int(spd),
            move_type=int(move_type),
        )
    except Exception as exc:
        return ToolResult.failure(
            state="position_alias_save_failed",
            message=str(exc),
            code="POSITION_ALIAS_SAVE_FAILED",
            data={"position_name": clean_name, "generates_command": False},
        )
    if not ok:
        return ToolResult.failure(
            state="position_alias_save_failed",
            message=str(message),
            code="POSITION_ALIAS_SAVE_FAILED",
            data={"position_name": clean_name, "generates_command": False},
        )
    entry = registry.get(clean_name) if hasattr(registry, "get") else None
    return ToolResult.success(
        state="position_alias_saved",
        message=str(message or f"位置'{clean_name}'已保存。"),
        data={
            "position_name": clean_name,
            "position": _position_entry_to_data(entry, name=clean_name, pose=pose),
            "generates_command": False,
        },
    )


def delete_position_alias(registry: Any, *, name: str) -> ToolResult:
    clean_name = str(name or "").strip()
    if not clean_name:
        return ToolResult.failure(
            state="position_alias_name_missing",
            message="位置名称不能为空。",
            code="POSITION_ALIAS_NAME_MISSING",
            data={"generates_command": False},
            fields=["name"],
        )
    try:
        if hasattr(registry, "remove") and callable(registry.remove):
            ok, message = registry.remove(clean_name)
        else:
            registry.delete_position(clean_name)
            ok, message = True, f"位置'{clean_name}'已删除"
    except Exception as exc:
        return ToolResult.failure(
            state="position_alias_delete_failed",
            message=str(exc),
            code="POSITION_ALIAS_DELETE_FAILED",
            data={"position_name": clean_name, "generates_command": False},
        )
    if not ok:
        return ToolResult.failure(
            state="position_alias_delete_failed",
            message=str(message),
            code="POSITION_ALIAS_DELETE_FAILED",
            data={"position_name": clean_name, "generates_command": False},
        )
    return ToolResult.success(
        state="position_alias_deleted",
        message=str(message or f"位置'{clean_name}'已删除。"),
        data={"position_name": clean_name, "generates_command": False},
    )


def _pose6(value: Any) -> tuple[float, float, float, float, float, float]:
    seq = list(value or ())
    if len(seq) < 6:
        raise ValueError("position pose requires 6 numeric values")
    return tuple(float(item) for item in seq[:6])  # type: ignore[return-value]


def _position_entry_to_data(entry: Any, *, name: str, pose: Any) -> dict[str, Any]:
    if entry is not None and hasattr(entry, "to_dict"):
        return dict(entry.to_dict())
    return {"name": name, "pose": list(_pose6(pose))}
