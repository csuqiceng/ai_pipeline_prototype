from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


PoseLookup = Callable[[str], Any]


class PositionQueryAgent:
    def __init__(self, *, lookup: PoseLookup) -> None:
        self._lookup = lookup

    def answer(self, text: str) -> dict[str, object] | None:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return None
        if any(word in compact for word in ("移动到", "走到", "去", "保存", "删除")):
            return None
        match = re.search(r"位置([A-Za-z0-9_\-\u4e00-\u9fff]+)(?:的)?(?:坐标|参数)(?:是多少|多少|呢)?", compact)
        bare_query = False
        if not match:
            match = re.fullmatch(r"位置([A-Za-z0-9_\-\u4e00-\u9fff]+)", compact)
            bare_query = match is not None
        if not match:
            return None
        name = str(match.group(1)).strip()
        pose = self._lookup(name)
        if pose is None:
            if bare_query:
                return None
            return {
                "kind": "position_query_answer",
                "position_name": name,
                "text": f"位置{name}不存在。没有触发机械手动作。",
                "generates_command": False,
            }
        values = _pose6(pose)
        text_value = (
            f"位置{name}坐标：X={values[0]} Y={values[1]} Z={values[2]} "
            f"RX={values[3]} RY={values[4]} RZ={values[5]}。没有触发机械手动作。"
        )
        return {
            "kind": "position_query_answer",
            "position_name": name,
            "pose": values,
            "text": text_value,
            "generates_command": False,
        }


def _pose6(value: Any) -> tuple[float, float, float, float, float, float]:
    seq = tuple(value)
    if len(seq) < 6:
        raise ValueError("position pose requires 6 values")
    return tuple(float(item) for item in seq[:6])  # type: ignore[return-value]
