"""Backend-only orchestration for the restricted Agent path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from robot_modbus_lite.agent.address_resolver import AddressResolver
from robot_modbus_lite.agent.command_understanding import (
    CommandUnderstandingAgent,
    CommandUnderstandingResult,
)
from robot_modbus_lite.agent.confirmation import ConfirmationAgent
from robot_modbus_lite.agent.drafts import CommandDraft
from robot_modbus_lite.agent.parameter_completion import (
    ControllerSnapshot,
    ParameterCompletionAgent,
    ParameterCompletionError,
)
from robot_modbus_lite.agent.safety_review import SafetyReviewAgent
from robot_modbus_lite.models import QueryRecord


@dataclass(frozen=True)
class RestrictedAgentResult:
    kind: str
    intent: str
    func_id: int | None = None
    message: str = ""
    understanding: CommandUnderstandingResult | None = None
    draft: CommandDraft | None = None
    precheck_result: dict[str, Any] | None = None
    confirmation_text: str = ""
    query_record: QueryRecord | None = None


class RestrictedAgentService:
    """Coordinates deterministic Agent modules without executing controller writes."""

    def __init__(
        self,
        *,
        controller_snapshot_provider: Callable[[], ControllerSnapshot],
        runtime_snapshot_provider: Callable[[], dict[str, Any]],
        safety_review_agent: SafetyReviewAgent,
        status_signature_provider: Callable[[], str],
        safety_signature_provider: Callable[[], str],
        clock: Callable[[], float],
        confirm_timeout_sec: float = 60.0,
        understanding_agent: CommandUnderstandingAgent | None = None,
        completion_agent: ParameterCompletionAgent | None = None,
        confirmation_agent: ConfirmationAgent | None = None,
        start_pose_provider: Callable[[], tuple[float, float, float, float, float, float] | None] | None = None,
        address_resolver: AddressResolver | None = None,
    ) -> None:
        self.address_resolver = address_resolver or AddressResolver()
        self.understanding_agent = understanding_agent or CommandUnderstandingAgent(address_resolver=self.address_resolver)
        self.completion_agent = completion_agent or ParameterCompletionAgent(
            controller_snapshot_provider,
            address_resolver=self.address_resolver,
        )
        self.safety_review_agent = safety_review_agent
        self.confirmation_agent = confirmation_agent or ConfirmationAgent(timeout_sec=confirm_timeout_sec)
        self._runtime_snapshot_provider = runtime_snapshot_provider
        self._status_signature_provider = status_signature_provider
        self._safety_signature_provider = safety_signature_provider
        self._clock = clock
        self._start_pose_provider = start_pose_provider

    def parse(self, text: str) -> RestrictedAgentResult:
        understanding = self.understanding_agent.understand(text)
        if understanding.bypass_completion and understanding.intent != "unknown":
            return RestrictedAgentResult(
                kind="bypass",
                intent=understanding.intent,
                func_id=understanding.func_id,
                message="规则旁路，不进入参数补全和确认。",
                understanding=understanding,
            )
        if understanding.intent == "unknown":
            return RestrictedAgentResult(
                kind="clarification",
                intent=understanding.intent,
                func_id=understanding.func_id,
                message=understanding.clarification or "请补充具体指令。",
                understanding=understanding,
            )

        try:
            draft = self.completion_agent.complete(understanding)
        except ParameterCompletionError as exc:
            return RestrictedAgentResult(
                kind="blocked",
                intent=understanding.intent,
                func_id=understanding.func_id,
                message=str(exc),
                understanding=understanding,
            )

        precheck = self.safety_review_agent.review(
            draft,
            snapshot=self._runtime_snapshot_provider(),
            start_pose=self._start_pose(),
        )
        reviewed_draft = replace(draft, precheck_result=dict(precheck))
        if not bool(precheck.get("valid")):
            return RestrictedAgentResult(
                kind="precheck_failed",
                intent=understanding.intent,
                func_id=understanding.func_id,
                message=str(precheck.get("summary") or "安全预检未通过。"),
                understanding=understanding,
                draft=reviewed_draft,
                precheck_result=precheck,
            )

        self.confirmation_agent.begin(
            reviewed_draft,
            now=self._clock(),
            status_signature=self._status_signature_provider(),
            safety_signature=self._safety_signature_provider(),
        )
        return RestrictedAgentResult(
            kind="waiting_confirmation",
            intent=understanding.intent,
            func_id=understanding.func_id,
            message="等待操作者确认。",
            understanding=understanding,
            draft=reviewed_draft,
            precheck_result=precheck,
            confirmation_text=self.confirmation_agent.render_confirmation_text(reviewed_draft),
        )

    def confirm(self, draft_id: str) -> QueryRecord:
        return self.confirmation_agent.confirm(
            draft_id,
            now=self._clock(),
            status_signature=self._status_signature_provider(),
            safety_signature=self._safety_signature_provider(),
        )

    def reject(self, draft_id: str) -> None:
        self.confirmation_agent.reject(draft_id)

    def _start_pose(self) -> tuple[float, float, float, float, float, float] | None:
        if self._start_pose_provider is None:
            return None
        return self._start_pose_provider()
