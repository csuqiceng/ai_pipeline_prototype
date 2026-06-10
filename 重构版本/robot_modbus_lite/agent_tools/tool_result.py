from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    state: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def success(
        cls,
        *,
        state: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(ok=True, state=str(state), message=str(message or ""), data=dict(data or {}), errors=[])

    @classmethod
    def failure(
        cls,
        *,
        state: str,
        message: str,
        code: str,
        data: dict[str, Any] | None = None,
        **error_fields: Any,
    ) -> "ToolResult":
        error = {"code": str(code), "message": str(message or "")}
        error.update(error_fields)
        return cls(
            ok=False,
            state=str(state),
            message=str(message or ""),
            data=dict(data or {}),
            errors=[error],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "state": str(self.state),
            "message": str(self.message),
            "data": dict(self.data),
            "errors": [dict(error) for error in self.errors],
        }

