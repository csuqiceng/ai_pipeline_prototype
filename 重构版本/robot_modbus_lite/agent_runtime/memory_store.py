from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
FORBIDDEN_MEMORY_KINDS = frozenset(
    {
        "register_address",
        "modbus_register",
        "safety_boundary",
        "safety_limit",
        "permission_matrix",
        "permission_rule",
        "execution_mode",
        "confirm_mode",
        "confirmation_mode",
        "confirmation_bypass",
        "skip_confirmation",
        "controller_protocol",
        "protocol_constant",
        "controller_default",
        "controller_parameter_default",
    }
)
FORBIDDEN_MEMORY_KEYWORDS = (
    "register",
    "modbus",
    "address",
    "寄存器",
    "地址",
    "安全边界",
    "安全限位",
    "权限矩阵",
    "确认模式",
    "专家模式",
    "新手模式",
    "自动执行",
    "直接执行",
    "跳过确认",
    "免确认",
    "协议常量",
    "控制器默认",
)


class ForbiddenMemoryCandidateError(ValueError):
    def __init__(self, *, reason: str, kind: str, key: str) -> None:
        self.reason = str(reason or "forbidden")
        self.kind = str(kind or "")
        self.key = str(key or "")
        super().__init__(f"memory candidate is forbidden: {self.reason}")


def default_agent_memory_path(runtime_root: str | Path) -> Path:
    return Path(runtime_root) / "data" / "agent_memory.sqlite3"


class AgentMemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def create_candidate(
        self,
        *,
        kind: str,
        key: str,
        value: dict[str, Any],
        source: str = "",
        confidence: float | None = None,
    ) -> dict[str, Any]:
        self._reject_forbidden_candidate(kind=kind, key=key, value=value)
        now = self._now()
        memory_id = self._new_id("mem")
        payload = {
            "memory_id": memory_id,
            "kind": str(kind),
            "key": str(key),
            "value": dict(value),
            "status": "candidate",
            "source": str(source or ""),
            "confidence": confidence,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """
                insert into memory_items
                (memory_id, kind, key, value_json, status, source, confidence, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["memory_id"],
                    payload["kind"],
                    payload["key"],
                    self._json(payload["value"]),
                    payload["status"],
                    payload["source"],
                    payload["confidence"],
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
            self._insert_audit(conn, event="candidate_created", memory_id=memory_id, payload=payload)
        return payload

    @staticmethod
    def _reject_forbidden_candidate(*, kind: str, key: str, value: dict[str, Any]) -> None:
        clean_kind = str(kind or "").strip()
        clean_key = str(key or "").strip()
        normalized_kind = clean_kind.lower()
        if normalized_kind in FORBIDDEN_MEMORY_KINDS:
            raise ForbiddenMemoryCandidateError(reason=normalized_kind, kind=clean_kind, key=clean_key)
        haystack = " ".join(
            (
                normalized_kind,
                clean_key.lower(),
                json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True).lower(),
            )
        )
        for keyword in FORBIDDEN_MEMORY_KEYWORDS:
            if keyword.lower() in haystack:
                raise ForbiddenMemoryCandidateError(reason=str(keyword), kind=clean_kind, key=clean_key)

    def approve_memory(self, memory_id: str, *, reviewer: str = "") -> dict[str, Any]:
        memory = self._get_memory(memory_id)
        self._reject_forbidden_candidate(
            kind=str(memory.get("kind", "") or ""),
            key=str(memory.get("key", "") or ""),
            value=dict(memory.get("value", {}) or {}),
        )
        return self._update_memory_status(
            memory_id,
            status="active",
            event="memory_approved",
            payload={"reviewer": str(reviewer or "")},
        )

    def disable_memory(self, memory_id: str, *, reviewer: str = "", reason: str = "") -> dict[str, Any]:
        return self._update_memory_status(
            memory_id,
            status="disabled",
            event="memory_disabled",
            payload={"reviewer": str(reviewer or ""), "reason": str(reason or "")},
        )

    def rollback_memory(self, memory_id: str, *, reviewer: str = "", reason: str = "") -> dict[str, Any]:
        return self._update_memory_status(
            memory_id,
            status="rolled_back",
            event="memory_rolled_back",
            payload={"reviewer": str(reviewer or ""), "reason": str(reason or "")},
        )

    def list_memories(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "select * from memory_items"
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(str(kind))
        if clauses:
            query = f"{query} where {' and '.join(clauses)}"
        query = f"{query} order by created_at asc"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def lookup_active(self, *, kind: str, key: str | None = None) -> list[dict[str, Any]]:
        query = "select * from memory_items where status = ? and kind = ?"
        params: list[Any] = ["active", str(kind)]
        if key is not None:
            query = f"{query} and key = ?"
            params.append(str(key))
        query = f"{query} order by updated_at desc"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def lookup_memories(self, *, kind: str, key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "select * from memory_items where kind = ? and key = ? order by updated_at desc",
                (str(kind), str(key)),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def _get_memory(self, memory_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("select * from memory_items where memory_id = ?", (str(memory_id),)).fetchone()
        if row is None:
            raise KeyError(f"memory not found: {memory_id}")
        return self._memory_from_row(row)

    def record_feedback_vote(
        self,
        *,
        interaction_id: str,
        target_type: str,
        target_id: str,
        vote: str,
        note: str = "",
    ) -> dict[str, Any]:
        now = self._now()
        vote_payload = {
            "vote_id": self._new_id("vote"),
            "interaction_id": str(interaction_id),
            "target_type": str(target_type),
            "target_id": str(target_id),
            "vote": str(vote),
            "note": str(note or ""),
            "created_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """
                insert into feedback_votes
                (vote_id, interaction_id, target_type, target_id, vote, note, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vote_payload["vote_id"],
                    vote_payload["interaction_id"],
                    vote_payload["target_type"],
                    vote_payload["target_id"],
                    vote_payload["vote"],
                    vote_payload["note"],
                    vote_payload["created_at"],
                ),
            )
        return vote_payload

    def list_feedback_votes(self, *, interaction_id: str | None = None) -> list[dict[str, Any]]:
        query = "select * from feedback_votes"
        params: list[Any] = []
        if interaction_id is not None:
            query = f"{query} where interaction_id = ?"
            params.append(str(interaction_id))
        query = f"{query} order by created_at asc"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def record_memory_applied(self, memory_id: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"context": dict(context or {})}
        with self._connect() as conn:
            return self._insert_audit(conn, event="memory_applied", memory_id=memory_id, payload=payload)

    def list_audit_events(self, *, memory_id: str | None = None) -> list[dict[str, Any]]:
        query = "select * from memory_audit"
        params: list[Any] = []
        if memory_id is not None:
            query = f"{query} where memory_id = ?"
            params.append(str(memory_id))
        query = f"{query} order by created_at asc"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._audit_from_row(row) for row in rows]

    def _update_memory_status(
        self,
        memory_id: str,
        *,
        status: str,
        event: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                "update memory_items set status = ?, updated_at = ? where memory_id = ?",
                (status, now, str(memory_id)),
            )
            self._insert_audit(conn, event=event, memory_id=memory_id, payload=payload)
            row = conn.execute("select * from memory_items where memory_id = ?", (str(memory_id),)).fetchone()
        if row is None:
            raise KeyError(f"memory not found: {memory_id}")
        return self._memory_from_row(row)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists memory_items (
                    memory_id text primary key,
                    kind text not null,
                    key text not null,
                    value_json text not null,
                    status text not null,
                    source text not null default '',
                    confidence real,
                    created_at real not null,
                    updated_at real not null
                );

                create index if not exists idx_memory_items_lookup
                    on memory_items(status, kind, key);

                create table if not exists feedback_votes (
                    vote_id text primary key,
                    interaction_id text not null,
                    target_type text not null,
                    target_id text not null,
                    vote text not null,
                    note text not null default '',
                    created_at real not null
                );

                create index if not exists idx_feedback_votes_interaction
                    on feedback_votes(interaction_id);

                create table if not exists memory_audit (
                    audit_id text primary key,
                    event text not null,
                    memory_id text not null,
                    payload_json text not null,
                    created_at real not null
                );

                create index if not exists idx_memory_audit_memory
                    on memory_audit(memory_id);
                """
            )
            version = int(conn.execute("pragma user_version").fetchone()[0] or 0)
            if version < SCHEMA_VERSION:
                conn.execute(f"pragma user_version = {SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_audit(
        self,
        conn: sqlite3.Connection,
        *,
        event: str,
        memory_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        audit = {
            "audit_id": self._new_id("audit"),
            "event": str(event),
            "memory_id": str(memory_id),
            "payload": dict(payload),
            "created_at": self._now(),
        }
        conn.execute(
            """
            insert into memory_audit (audit_id, event, memory_id, payload_json, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (
                audit["audit_id"],
                audit["event"],
                audit["memory_id"],
                self._json(audit["payload"]),
                audit["created_at"],
            ),
        )
        return audit

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "memory_id": row["memory_id"],
            "kind": row["kind"],
            "key": row["key"],
            "value": json.loads(row["value_json"]),
            "status": row["status"],
            "source": row["source"],
            "confidence": row["confidence"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _audit_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "audit_id": row["audit_id"],
            "event": row["event"],
            "memory_id": row["memory_id"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> float:
        return time.time()
