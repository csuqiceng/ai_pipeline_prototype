from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator, model_validator

from robot_modbus_lite.agent_tools.tool_result import ToolResult


class _BaseToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class ToolResultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    state: str = Field(..., min_length=1)
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class CommandDraftPayload(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    draft_id: str = Field(..., min_length=1)
    func_id: int = Field(..., gt=0)
    intent: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    param_sources: dict[str, Any] = Field(default_factory=dict)
    raw_text: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    precheck_result: Any | None = None
    confirmed: bool = False

    @model_validator(mode="after")
    def _validate_params_for_func_id(self) -> "CommandDraftPayload":
        invalid: list[str] = []
        if int(self.func_id) in {8, 102, 108, 112}:
            for field in ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz"):
                try:
                    float(self.params.get(field))
                except (TypeError, ValueError):
                    invalid.append(field)
            for field in ("spd_pct", "acc_pct", "dec_pct"):
                if field not in self.params:
                    continue
                try:
                    percent = float(self.params.get(field))
                except (TypeError, ValueError):
                    invalid.append(field)
                    continue
                if percent <= 0.0 or percent > 100.0:
                    invalid.append(field)
            if "position_increment" in self.params:
                try:
                    position_increment = int(float(self.params.get("position_increment")))
                except (TypeError, ValueError):
                    invalid.append("position_increment")
                else:
                    if position_increment not in {0, 1}:
                        invalid.append("position_increment")
            if "move_type" in self.params:
                try:
                    move_type = int(float(self.params.get("move_type")))
                except (TypeError, ValueError):
                    invalid.append("move_type")
                else:
                    if move_type not in {0, 1, 2}:
                        invalid.append("move_type")
        if int(self.func_id) in {106, 107}:
            try:
                axis_no = int(float(self.params.get("axis_no")))
            except (TypeError, ValueError):
                invalid.append("axis_no")
            else:
                if axis_no < 1 or axis_no > 10:
                    invalid.append("axis_no")
            try:
                float(self.params.get("pos_val"))
            except (TypeError, ValueError):
                invalid.append("pos_val")
            for field in ("spd_pct", "acc_pct", "dec_pct"):
                if field not in self.params:
                    continue
                try:
                    percent = float(self.params.get(field))
                except (TypeError, ValueError):
                    invalid.append(field)
                    continue
                if percent <= 0.0 or percent > 100.0:
                    invalid.append(field)
        if int(self.func_id) in {109, 110}:
            try:
                delay_sec = float(self.params.get("delay_sec"))
            except (TypeError, ValueError):
                invalid.append("delay_sec")
            else:
                if delay_sec <= 0.0:
                    invalid.append("delay_sec")
        if int(self.func_id) == 120:
            try:
                io_no = int(float(self.params.get("io_no")))
            except (TypeError, ValueError):
                invalid.append("io_no")
            else:
                if io_no < 0 or io_no > 11:
                    invalid.append("io_no")
            try:
                io_action = int(float(self.params.get("io_action")))
            except (TypeError, ValueError):
                invalid.append("io_action")
            else:
                if io_action not in {0, 1}:
                    invalid.append("io_action")
        if invalid:
            raise ValueError(f"command draft params invalid for func_id {self.func_id}: {','.join(invalid)}")
        return self


class ConfirmedCommandDraftPayload(CommandDraftPayload):
    confirmed: Literal[True]


class PositionAliasArgsModel(_BaseToolArgs):
    name: str | None = None
    position_name: str | None = None
    pose: Any | None = None
    created_by: str | None = None
    spd: int | None = None
    move_type: int | None = None

    @model_validator(mode="after")
    def _validate_name(self) -> "PositionAliasArgsModel":
        clean_name = str(self.name or self.position_name or "").strip()
        if not clean_name:
            raise ValueError("position alias name is required")
        return self

    @field_validator("pose")
    @classmethod
    def _validate_pose(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            seq = list(value)
        except TypeError as exc:
            raise ValueError("pose must be a sequence of 6 numeric values") from exc
        if len(seq) != 6:
            raise ValueError("pose must contain exactly 6 numeric values")
        try:
            return tuple(float(item) for item in seq)
        except (TypeError, ValueError) as exc:
            raise ValueError("pose must contain numeric values") from exc

    @field_validator("spd")
    @classmethod
    def _validate_spd(cls, value: int | None) -> int | None:
        if value is None:
            return None
        percent = int(value)
        if percent <= 0 or percent > 100:
            raise ValueError("position alias speed must be > 0 and <= 100")
        return percent

    @field_validator("move_type")
    @classmethod
    def _validate_move_type(cls, value: int | None) -> int | None:
        if value is None:
            return None
        move_type = int(value)
        if move_type not in {0, 1, 2}:
            raise ValueError("position alias move_type must be 0, 1, or 2")
        return move_type


class ParamBoundsArgsModel(_BaseToolArgs):
    params: dict[str, Any] = Field(default_factory=dict)
    bounds: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _validate_motion_percent_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        invalid: list[str] = []
        for field in ("spd_pct", "acc_pct", "dec_pct"):
            if field not in value:
                continue
            try:
                percent = float(value[field])
            except (TypeError, ValueError):
                invalid.append(field)
                continue
            if percent <= 0.0 or percent > 100.0:
                invalid.append(field)
        if invalid:
            raise ValueError(f"motion percent params must be > 0 and <= 100: {','.join(invalid)}")
        return value

    @field_validator("bounds")
    @classmethod
    def _validate_bounds_schema_version(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value and not str(value.get("schema_version", "") or "").strip():
            raise ValueError("bounds schema_version is required")
        return value


class SaveFlowStepPayload(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    step_id: int = Field(..., gt=0)
    action: str = Field(..., min_length=1)
    func_id: int = Field(..., gt=0)
    params: dict[str, Any] = Field(default_factory=dict)


class SaveFlowDraftPayload(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    flow_name: str = Field(..., min_length=1)
    expanded_steps: list[SaveFlowStepPayload] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _validate_steps_for_save(self) -> "SaveFlowDraftPayload":
        invalid: list[str] = []
        for index, step in enumerate(self.expanded_steps):
            prefix = f"expanded_steps.{index}.params"
            params = dict(step.params or {})
            if int(step.func_id) in {106, 107}:
                invalid.append(f"expanded_steps.{index}.func_id")
                continue
            if int(step.func_id) in {8, 102, 108, 112}:
                for field in ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz"):
                    try:
                        float(params.get(field))
                    except (TypeError, ValueError):
                        invalid.append(f"{prefix}.{field}")
                for field in ("spd_pct", "acc_pct", "dec_pct"):
                    if field not in params:
                        continue
                    try:
                        percent = float(params.get(field))
                    except (TypeError, ValueError):
                        invalid.append(f"{prefix}.{field}")
                        continue
                    if percent <= 0.0 or percent > 100.0:
                        invalid.append(f"{prefix}.{field}")
                if "move_type" in params:
                    try:
                        move_type = int(float(params.get("move_type")))
                    except (TypeError, ValueError):
                        invalid.append(f"{prefix}.move_type")
                    else:
                        if move_type not in {0, 1, 2}:
                            invalid.append(f"{prefix}.move_type")
                if "position_increment" in params:
                    try:
                        position_increment = int(float(params.get("position_increment")))
                    except (TypeError, ValueError):
                        invalid.append(f"{prefix}.position_increment")
                    else:
                        if position_increment not in {0, 1}:
                            invalid.append(f"{prefix}.position_increment")
            if int(step.func_id) in {109, 110}:
                try:
                    delay_sec = float(params.get("delay_sec"))
                except (TypeError, ValueError):
                    invalid.append(f"{prefix}.delay_sec")
                else:
                    if delay_sec <= 0.0:
                        invalid.append(f"{prefix}.delay_sec")
            if int(step.func_id) == 120:
                try:
                    io_no = int(float(params.get("io_no")))
                except (TypeError, ValueError):
                    invalid.append(f"{prefix}.io_no")
                else:
                    if io_no < 0 or io_no > 11:
                        invalid.append(f"{prefix}.io_no")
                try:
                    io_action = int(float(params.get("io_action")))
                except (TypeError, ValueError):
                    invalid.append(f"{prefix}.io_action")
                else:
                    if io_action not in {0, 1}:
                        invalid.append(f"{prefix}.io_action")
        if invalid:
            raise ValueError(f"flow draft save params invalid: {','.join(invalid)}")
        return self


class MemoryCandidateArgsModel(_BaseToolArgs):
    kind: str = Field(...)
    key: str = Field(...)
    value: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("kind", "key")
    @classmethod
    def _validate_identity_field(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("memory candidate identity field is required")
        return value


class DraftIdArgsModel(_BaseToolArgs):
    draft_id: str = Field(...)

    @field_validator("draft_id")
    @classmethod
    def _validate_draft_id(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("draft_id is required")
        return value


class MemoryIdArgsModel(_BaseToolArgs):
    memory_id: str = Field(...)
    reviewer: str | None = None
    reason: str | None = None
    context: dict[str, Any] | None = None

    @field_validator("memory_id")
    @classmethod
    def _validate_memory_id(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("memory_id is required")
        return value


class ActiveMemoryArgsModel(_BaseToolArgs):
    kind: str = Field(...)
    key: str | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("active memory kind is required")
        return value


class FeedbackVoteArgsModel(_BaseToolArgs):
    interaction_id: str = Field(...)
    target_type: Literal["interaction", "answer", "memory"]
    target_id: str = Field(...)
    vote: Literal["up", "down"]
    note: str | None = None

    @field_validator("interaction_id", "target_id")
    @classmethod
    def _validate_identity_field(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("feedback identity field is required")
        return value


class NonBlankTextArgsModel(_BaseToolArgs):
    text: str = Field(...)

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("text is required")
        return value


class CommandSchemaArgsModel(_BaseToolArgs):
    command_name: str | None = None
    func_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_lookup_key(self) -> "CommandSchemaArgsModel":
        if self.func_id is None and not str(self.command_name or "").strip():
            raise ValueError("command schema lookup key is required")
        return self


class RequiredParamsArgsModel(_BaseToolArgs):
    func_id: int = Field(..., gt=0)
    params: dict[str, Any] = Field(default_factory=dict)


class CommandAddressArgsModel(_BaseToolArgs):
    name: str | None = None
    address_name: str | None = None

    @model_validator(mode="after")
    def _validate_address_name(self) -> "CommandAddressArgsModel":
        if not str(self.name or self.address_name or "").strip():
            raise ValueError("command address name is required")
        return self


class PrepareRegisteredFlowArgsModel(_BaseToolArgs):
    flow_name: str | None = None
    name: str | None = None
    mode: str | None = None

    @model_validator(mode="after")
    def _validate_flow_name(self) -> "PrepareRegisteredFlowArgsModel":
        if not str(self.flow_name or self.name or "").strip():
            raise ValueError("registered flow name is required")
        return self


TextArgs = create_model("TextArgs", __base__=_BaseToolArgs, text=(str, Field(...)))
EmptyArgs = create_model("EmptyArgs", __base__=_BaseToolArgs)
DraftArgs = create_model(
    "DraftArgs",
    __base__=_BaseToolArgs,
    draft=(dict[str, Any], Field(default_factory=dict)),
)
SaveFlowDraftArgs = create_model(
    "SaveFlowDraftArgs",
    __base__=_BaseToolArgs,
    draft=(SaveFlowDraftPayload, Field(...)),
)
CommandDraftArgs = create_model(
    "CommandDraftArgs",
    __base__=_BaseToolArgs,
    draft=(CommandDraftPayload, Field(...)),
)
ConfirmedCommandDraftArgs = create_model(
    "ConfirmedCommandDraftArgs",
    __base__=_BaseToolArgs,
    draft=(ConfirmedCommandDraftPayload, Field(...)),
)
SafetyPrecheckArgs = create_model(
    "SafetyPrecheckArgs",
    __base__=_BaseToolArgs,
    draft=(CommandDraftPayload, Field(...)),
    snapshot=(dict[str, Any] | None, None),
    start_pose=(Any | None, None),
)
StartFlowDraftArgs = create_model(
    "StartFlowDraftArgs",
    __base__=_BaseToolArgs,
    text=(str | None, None),
    flow_name=(str | None, None),
)
SetFlowNameArgs = create_model(
    "SetFlowNameArgs",
    __base__=_BaseToolArgs,
    draft=(dict[str, Any], Field(default_factory=dict)),
    flow_name=(str, Field(...)),
)
AppendFlowStepArgs = create_model(
    "AppendFlowStepArgs",
    __base__=_BaseToolArgs,
    step_text=(str, Field(...)),
    draft=(dict[str, Any] | None, None),
)
FlowTextArgs = create_model(
    "FlowTextArgs",
    __base__=_BaseToolArgs,
    text=(str, Field(...)),
    draft=(dict[str, Any] | None, None),
)
RegisteredFlowArgs = create_model(
    "RegisteredFlowArgs",
    __base__=_BaseToolArgs,
    flow_name=(str | None, None),
    name=(str | None, None),
    mode=(str | None, None),
)
QueryFlowDraftArgs = create_model(
    "QueryFlowDraftArgs",
    __base__=_BaseToolArgs,
    draft=(dict[str, Any] | None, None),
)
StatusSnapshotArgs = create_model(
    "StatusSnapshotArgs",
    __base__=_BaseToolArgs,
    snapshot=(dict[str, Any] | None, None),
)
AxisStatusArgs = create_model(
    "AxisStatusArgs",
    __base__=_BaseToolArgs,
    snapshot=(dict[str, Any] | None, None),
    axis=(int | None, None),
)
SavedPositionArgs = create_model(
    "SavedPositionArgs",
    __base__=_BaseToolArgs,
    text=(str, Field(...)),
    lookup=(Any | None, None),
)
QueryMemoryCandidatesArgs = create_model(
    "QueryMemoryCandidatesArgs",
    __base__=_BaseToolArgs,
    kind=(str | None, None),
)
QueryMemoryReviewArgs = create_model(
    "QueryMemoryReviewArgs",
    __base__=_BaseToolArgs,
    status=(Literal["candidate", "active", "disabled", "rolled_back"] | None, None),
    kind=(str | None, None),
    include_audit=(bool | None, True),
)
_TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "lookup_command_schema": CommandSchemaArgsModel,
    "parse_command_intent": NonBlankTextArgsModel,
    "parse_command_params": NonBlankTextArgsModel,
    "validate_required_params": RequiredParamsArgsModel,
    "check_param_bounds": ParamBoundsArgsModel,
    "resolve_command_address": CommandAddressArgsModel,
    "build_system_action_draft": NonBlankTextArgsModel,
    "build_command_draft": NonBlankTextArgsModel,
    "apply_atomic_template": NonBlankTextArgsModel,
    "draft_to_query_record": ConfirmedCommandDraftArgs,
    "run_safety_precheck": SafetyPrecheckArgs,
    "create_pending_confirm": CommandDraftArgs,
    "query_pending_confirm": DraftIdArgsModel,
    "confirm_pending_plan": DraftIdArgsModel,
    "cancel_pending_plan": DraftIdArgsModel,
    "expire_pending_plan": DraftIdArgsModel,
    "split_compound_command": NonBlankTextArgsModel,
    "plan_compound_command": NonBlankTextArgsModel,
    "start_flow_draft": StartFlowDraftArgs,
    "set_flow_name": SetFlowNameArgs,
    "append_flow_step": AppendFlowStepArgs,
    "answer_flow_clarification": FlowTextArgs,
    "edit_flow_draft_params": FlowTextArgs,
    "save_flow_draft": SaveFlowDraftArgs,
    "query_registered_flow": RegisteredFlowArgs,
    "prepare_registered_flow_execution": PrepareRegisteredFlowArgsModel,
    "set_flow_draft": DraftArgs,
    "query_current_flow_draft": QueryFlowDraftArgs,
    "cancel_flow_draft": EmptyArgs,
    "query_dashboard_section": TextArgs,
    "get_axis_status": AxisStatusArgs,
    "get_alarm": StatusSnapshotArgs,
    "get_execution_progress": StatusSnapshotArgs,
    "query_saved_position": SavedPositionArgs,
    "explain_text": TextArgs,
    "create_memory_candidate": MemoryCandidateArgsModel,
    "query_memory_candidates": QueryMemoryCandidatesArgs,
    "query_memory_review": QueryMemoryReviewArgs,
    "approve_memory_candidate": MemoryIdArgsModel,
    "disable_memory": MemoryIdArgsModel,
    "rollback_memory": MemoryIdArgsModel,
    "lookup_active_memory": ActiveMemoryArgsModel,
    "record_memory_applied": MemoryIdArgsModel,
    "record_feedback_vote": FeedbackVoteArgsModel,
    "save_position_alias": PositionAliasArgsModel,
    "delete_position_alias": PositionAliasArgsModel,
}


def tool_input_schema(tool_name: str) -> dict[str, Any]:
    model = _TOOL_ARG_MODELS.get(str(tool_name or ""))
    if model is None:
        return {}
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def tool_output_schema(tool_name: str) -> dict[str, Any]:
    _ = str(tool_name or "")
    schema = ToolResultOutput.model_json_schema()
    schema.pop("title", None)
    return schema


def validate_tool_args(tool_name: str, args: dict[str, Any] | None) -> ToolResult:
    name = str(tool_name or "")
    model = _TOOL_ARG_MODELS.get(name)
    if model is None:
        return ToolResult.failure(
            state="tool_schema_not_found",
            message=f"未找到工具参数 schema：{name}",
            code="TOOL_SCHEMA_NOT_FOUND",
            data={"tool_name": name},
        )
    try:
        validated = model.model_validate(dict(args or {}))
    except ValidationError as exc:
        fields = _validation_fields(exc)
        return ToolResult.failure(
            state="tool_args_invalid",
            message="工具参数不符合 schema。",
            code="TOOL_ARGS_INVALID",
            data={"tool_name": name},
            fields=fields,
            validation_errors=exc.errors(),
        )
    return ToolResult.success(
        state="tool_args_valid",
        data={"tool_name": name, "args": validated.model_dump(exclude_none=True)},
    )


def validate_tool_result(tool_name: str, result: Any) -> ToolResult:
    name = str(tool_name or "")
    if not isinstance(result, ToolResult):
        return _tool_output_invalid(name, "工具返回值不是 ToolResult。", fields=[])
    try:
        ToolResultOutput.model_validate(result.to_dict())
    except ValidationError as exc:
        return _tool_output_invalid(name, "工具返回值不符合输出 schema。", fields=_validation_fields(exc), validation_errors=exc.errors())
    return result


def _tool_output_invalid(
    tool_name: str,
    message: str,
    *,
    fields: list[str],
    validation_errors: list[dict[str, Any]] | None = None,
) -> ToolResult:
    return ToolResult.failure(
        state="tool_output_invalid",
        message=message,
        code="TOOL_OUTPUT_INVALID",
        data={"tool_name": str(tool_name or "")},
        fields=fields,
        validation_errors=list(validation_errors or []),
    )


def _validation_fields(exc: ValidationError) -> list[str]:
    fields: list[str] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        if not loc:
            message = str(error.get("msg", "") or "")
            if "position alias name" in message:
                field = "name"
                if field not in fields:
                    fields.append(field)
            elif "bounds schema_version" in message:
                field = "bounds.schema_version"
                if field not in fields:
                    fields.append(field)
            elif "registered flow name" in message:
                field = "flow_name"
                if field not in fields:
                    fields.append(field)
            elif "command schema lookup key" in message:
                field = "command_name"
                if field not in fields:
                    fields.append(field)
            elif "command address name" in message:
                field = "name"
                if field not in fields:
                    fields.append(field)
            continue
        message = str(error.get("msg", "") or "")
        if "command draft params invalid" in message:
            invalid = message.rsplit(":", 1)[-1]
            prefix = ".".join(str(part) for part in loc)
            for item in invalid.split(","):
                clean_item = item.strip()
                field = f"{prefix}.params.{clean_item}" if prefix else f"params.{clean_item}"
                if clean_item and field not in fields:
                    fields.append(field)
            continue
        if "flow draft save params invalid" in message:
            invalid = message.rsplit(":", 1)[-1]
            prefix = ".".join(str(part) for part in loc)
            for item in invalid.split(","):
                clean_item = item.strip()
                field = f"{prefix}.{clean_item}" if prefix else clean_item
                if clean_item and field not in fields:
                    fields.append(field)
            continue
        if loc == ("params",) and "motion percent params" in message:
            invalid = message.rsplit(":", 1)[-1]
            for item in invalid.split(","):
                field = f"params.{item.strip()}"
                if item.strip() and field not in fields:
                    fields.append(field)
            continue
        if loc == ("bounds",) and "bounds schema_version" in message:
            field = "bounds.schema_version"
            if field not in fields:
                fields.append(field)
            continue
        field = ".".join(str(part) for part in loc)
        if field not in fields:
            fields.append(field)
    return fields
