"""Layered write policy for system configuration parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ParamLayer = Literal["readonly", "optimizable", "forbidden"]
ParamActor = Literal["engineer", "ai_optimizer", "system"]

PARAM_LAYER_LABELS: dict[ParamLayer, str] = {
    "readonly": "只读区",
    "optimizable": "可优化区",
    "forbidden": "禁写区",
}

READONLY_FIELDS = frozenset(
    {
        "x",
        "y",
        "z",
        "joint_limits",
        "motion_timeout_sec",
        "operator_confirm_timeout_sec",
        "operator_dashboard_refresh_ms",
        "operator_view_refresh_ms",
        "controller_realtime_poll_ms",
        "dashboard_stale_after_ms",
    }
)

OPTIMIZABLE_FIELDS = frozenset(
    {
        "safe_r_min",
        "safe_r_max",
        "safe_z_min",
        "safe_z_max",
        "safe_speed_max",
        "safe_acc_max",
        "safe_dec_max",
        "l3_min_step_delay_ms",
        "l3_cumulative_error_limit_mm",
        "l3_forbidden_boxes",
    }
)

FORBIDDEN_FIELDS = frozenset(
    {
        "emergency_codes",
        "echo_retry_interval_sec",
        "echo_retry_count",
        "echo_write_rounds",
        "echo_compare_epsilon",
        "operator_tts_enabled",
        "broadcast_dedupe_window_sec",
        "tts_retry_delay_sec",
        "tts_max_failures",
    }
)


@dataclass(frozen=True)
class ParamPatchValidation:
    ok: bool
    denied_fields: list[str]
    message: str


def classify_system_config_field(field_name: str) -> ParamLayer:
    field = str(field_name or "").strip()
    if field in READONLY_FIELDS:
        return "readonly"
    if field in OPTIMIZABLE_FIELDS:
        return "optimizable"
    if field in FORBIDDEN_FIELDS:
        return "forbidden"
    return "forbidden"


def fields_by_layer() -> dict[ParamLayer, list[str]]:
    return {
        "readonly": sorted(READONLY_FIELDS),
        "optimizable": sorted(OPTIMIZABLE_FIELDS),
        "forbidden": sorted(FORBIDDEN_FIELDS),
    }


def validate_param_patch(patch: dict[str, object], *, actor: ParamActor) -> ParamPatchValidation:
    denied: list[str] = []
    for field in patch:
        layer = classify_system_config_field(field)
        if not _actor_can_write_layer(actor, layer):
            denied.append(str(field))
    if not denied:
        return ParamPatchValidation(ok=True, denied_fields=[], message="参数写入权限检查通过。")
    details = "；".join(f"{field} 属于 {PARAM_LAYER_LABELS[classify_system_config_field(field)]}" for field in denied)
    return ParamPatchValidation(
        ok=False,
        denied_fields=denied,
        message=f"参数写入权限不足：{details}。",
    )


def export_param_layer_markdown() -> str:
    lines = [
        "# 系统参数分层读写清单",
        "",
        "| 分层 | 字段 | AI优化器 | 工程师 | 系统内部 |",
        "|---|---|---|---|---|",
    ]
    for layer, fields in fields_by_layer().items():
        for field in fields:
            lines.append(
                "| {layer_label} | `{field}` | {ai} | {engineer} | {system} |".format(
                    layer_label=PARAM_LAYER_LABELS[layer],
                    field=field,
                    ai=_write_label(_actor_can_write_layer("ai_optimizer", layer)),
                    engineer=_write_label(_actor_can_write_layer("engineer", layer)),
                    system=_write_label(_actor_can_write_layer("system", layer)),
                )
            )
    lines.append("")
    return "\n".join(lines)


def _actor_can_write_layer(actor: ParamActor, layer: ParamLayer) -> bool:
    if actor == "system":
        return True
    if actor == "engineer":
        return layer in {"readonly", "optimizable"}
    if actor == "ai_optimizer":
        return layer == "optimizable"
    return False


def _write_label(allowed: bool) -> str:
    return "可写" if allowed else "禁写"
