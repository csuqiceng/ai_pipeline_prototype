from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from robot_modbus_lite.models import QueryRecord


REQUIRED_PARAM_KEYS: dict[int, tuple[str, ...]] = {
    104: ("estop_ctrl", "pause_ctrl", "cancel_ctrl", "reset_ctrl"),
    106: ("axis_no", "pos_val", "spd_pct", "acc_pct", "dec_pct", "fuzzy_pos", "fuzzy_spd", "fuzzy_acc", "fuzzy_dec", "stop_cmd"),
    107: ("axis_no", "pos_val", "spd_pct", "acc_pct", "dec_pct", "fuzzy_pos", "fuzzy_spd", "fuzzy_acc", "fuzzy_dec", "stop_cmd"),
    8: (
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
    ),
    102: (
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
    ),
    108: (
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
    ),
    112: (
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
    ),
    109: ("delay_sec",),
    110: ("delay_sec",),
    120: ("io_no", "io_action"),
}


@dataclass(frozen=True)
class CommandDraft:
    draft_id: str
    func_id: int
    intent: str
    params: dict[str, float | int | str]
    param_sources: dict[str, str]
    raw_text: str
    confidence: float
    precheck_result: dict[str, Any] | None = None
    confirmed: bool = False


def draft_to_query_record(draft: CommandDraft) -> QueryRecord:
    missing = _missing_required_params(draft.func_id, draft.params)
    if missing:
        raise ValueError(f"missing required params for Func{draft.func_id}: {', '.join(missing)}")
    params = copy.deepcopy(draft.params)
    if int(draft.func_id) in {8, 102, 108, 112} and "position_increment" in params:
        params["fuzzy_pos"] = int(float(params.get("position_increment", 0) or 0))
    return QueryRecord(
        query_key=f"agent:{draft.draft_id}",
        func_num=int(draft.func_id),
        params=params,
        description=_summary_from_draft(draft),
    )


def _missing_required_params(func_id: int, params: dict[str, Any]) -> list[str]:
    required = REQUIRED_PARAM_KEYS.get(int(func_id))
    if required is None:
        raise ValueError(f"unsupported func_id for CommandDraft: {func_id}")
    return [key for key in required if key not in params]


def _summary_from_draft(draft: CommandDraft) -> str:
    return f"Agent draft {draft.intent}: {draft.raw_text}".strip()
