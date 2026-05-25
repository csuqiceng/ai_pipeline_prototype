"""Rule parser for V2.0 secondary atomic natural-language commands."""

from __future__ import annotations

import re

from .atomic_models import AtomicElements, AtomicResolved


WAKE_WORDS = ("小正", "小郑", "校正")


class AtomicParser:
    """Parse deterministic atomic commands after the wake word."""

    def classify(self, text: str) -> AtomicResolved:
        raw = (text or "").strip()
        command_text = self._strip_wake_word(raw)
        if command_text is None:
            if re.fullmatch(r"(急停|紧急停止)", raw):
                return AtomicResolved(
                    kind="warning",
                    action_type="warning",
                    target=None,
                    reason="应急指令需使用标准格式：急停 授权码 急停。",
                    params={"command_text": raw},
                    risk_level="high",
                    requires_confirmation=False,
                )
            return AtomicResolved(
                kind="chat",
                action_type="unknown",
                target=None,
                reason="缺少唤醒词，未进入原子控制层。",
                params={"command_text": raw},
                requires_confirmation=False,
            )
        return AtomicResolved(
            kind="unsupported",
            action_type="atomic",
            target=None,
            reason="已进入原子控制层。",
            params={"command_text": command_text},
        )

    def parse(self, text: str) -> AtomicElements:
        raw = (text or "").strip()
        command_text = self._strip_wake_word(raw)
        if command_text is None:
            return AtomicElements(raw_text=raw, command_text=raw)
        compact = self._compact(command_text)
        spd_pct = self._extract_speed_percent(compact)
        acc_pct = self._extract_percent(compact, ("加速度", "加速"))
        dec_pct = self._extract_percent(compact, ("减速度", "减速"))

        memory = self._parse_memory(raw, compact, spd_pct)
        if memory is not None:
            return memory
        position = self._parse_position(raw, compact)
        if position is not None:
            return position
        history = self._parse_history(raw, compact)
        if history is not None:
            return history
        delay = self._parse_delay(raw, compact)
        if delay is not None:
            return delay
        io = self._parse_io(raw, compact)
        if io is not None:
            return io
        joint = self._parse_joint(raw, compact, spd_pct, acc_pct, dec_pct)
        if joint is not None:
            return joint
        virtual = self._parse_virtual(raw, compact, spd_pct, acc_pct, dec_pct)
        if virtual is not None:
            return virtual
        cartesian = self._parse_cartesian(raw, compact, spd_pct, acc_pct, dec_pct)
        if cartesian is not None:
            return cartesian
        return AtomicElements(raw_text=raw, command_text=command_text)

    def _parse_memory(self, raw: str, compact: str, spd_pct: float | None) -> AtomicElements | None:
        if re.fullmatch(r"(加速|快一点|速度快一点)", compact):
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="speed_up")
        if re.fullmatch(r"(减速|慢一点|速度慢一点)", compact):
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="speed_down")
        if compact in {"慢速", "低速"}:
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="speed", spd_pct=5.0)
        if compact in {"正常速度", "默认速度"}:
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="speed", spd_pct=50.0)
        if compact in {"全速", "高速"}:
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="speed", spd_pct=100.0)
        if spd_pct is not None and re.fullmatch(r"(速度|速)?\d+(?:\.\d+)?%", compact):
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="speed", spd_pct=spd_pct)
        step_match = re.search(r"步长(-?\d+(?:\.\d+)?)(毫米|mm|度|°)", compact, flags=re.IGNORECASE)
        if step_match:
            unit = step_match.group(2).lower()
            name = "step_deg" if unit in {"度", "°"} else "step_mm"
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name=name, step=float(step_match.group(1)))
        if "专家模式" in compact:
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="confirm_mode", target=0, fuzzy_pos=0)
        if "熟练模式" in compact:
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="confirm_mode", target=1, fuzzy_pos=0)
        if "新手模式" in compact:
            return AtomicElements(raw_text=raw, command_text=compact, family="memory", name="confirm_mode", target=2, fuzzy_pos=0)
        return None

    def _parse_position(self, raw: str, compact: str) -> AtomicElements | None:
        save_match = re.search(r"保存当前位置为位置([A-Za-z0-9]+)", compact)
        if save_match:
            return AtomicElements(raw_text=raw, command_text=compact, family="position", name=f"save:{save_match.group(1)}")
        move_match = re.search(r"(?:移动到|到|去|走到)位置([A-Za-z0-9]+)", compact)
        if move_match:
            return AtomicElements(raw_text=raw, command_text=compact, family="position", name=f"move:{move_match.group(1)}", fuzzy_pos=0)
        query_match = re.search(r"位置([A-Za-z0-9]+)(?:的)?坐标(?:是多少|多少|呢)?", compact)
        if query_match:
            return AtomicElements(raw_text=raw, command_text=compact, family="position", name=f"query:{query_match.group(1)}")
        delete_match = re.search(r"删除位置([A-Za-z0-9]+)", compact)
        if delete_match:
            return AtomicElements(raw_text=raw, command_text=compact, family="position", name=f"delete:{delete_match.group(1)}")
        return None

    def _parse_history(self, raw: str, compact: str) -> AtomicElements | None:
        if compact in {"再走一次", "重复一次", "再执行一次", "再走一遍", "再来一次"}:
            return AtomicElements(raw_text=raw, command_text=compact, family="history", name="repeat")
        if compact in {"返回", "退回", "回到上一步", "返回上一步"}:
            return AtomicElements(raw_text=raw, command_text=compact, family="history", name="back")
        continue_match = re.search(r"继续(前进|后退|左移|右移|上升|下降)(-?\d+(?:\.\d+)?)(?:毫米|mm)?", compact, flags=re.IGNORECASE)
        if continue_match:
            direction_text = continue_match.group(1)
            direction_map = {"前进": (6, 1), "后退": (6, -1), "左移": (7, 1), "右移": (7, -1), "上升": (8, 1), "下降": (8, -1)}
            axis_no, direction = direction_map[direction_text]
            return AtomicElements(raw_text=raw, command_text=compact, family="virtual", axis_no=axis_no, direction=direction, step=float(continue_match.group(2)), fuzzy_pos=1)
        simple_continue = re.search(r"继续(前进|后退|左移|右移|上升|下降)$", compact)
        if simple_continue:
            direction_text = simple_continue.group(1)
            direction_map = {"前进": (6, 1), "后退": (6, -1), "左移": (7, 1), "右移": (7, -1), "上升": (8, 1), "下降": (8, -1)}
            axis_no, direction = direction_map[direction_text]
            return AtomicElements(raw_text=raw, command_text=compact, family="virtual", axis_no=axis_no, direction=direction, fuzzy_pos=1)
        continue_last_match = re.fullmatch(
            r"(?:继续|继续走|继续移动|沿上次方向继续|按上次方向继续)(-?\d+(?:\.\d+)?)(?:毫米|mm|度|°)?",
            compact,
            flags=re.IGNORECASE,
        )
        if continue_last_match:
            return AtomicElements(
                raw_text=raw,
                command_text=compact,
                family="history",
                name="continue",
                step=abs(float(continue_last_match.group(1))),
            )
        if compact in {"继续", "继续走", "继续移动", "沿上次方向继续", "按上次方向继续"}:
            return AtomicElements(raw_text=raw, command_text=compact, family="history", name="continue")
        return None

    def _parse_delay(self, raw: str, compact: str) -> AtomicElements | None:
        match = re.search(r"(等待|延时|暂停)(\d+(?:\.\d+)?)(秒|s|毫秒|ms)", compact, flags=re.IGNORECASE)
        if not match:
            return None
        value = float(match.group(2))
        if match.group(3).lower() in {"毫秒", "ms"}:
            value /= 1000.0
        return AtomicElements(raw_text=raw, command_text=compact, family="delay", delay_sec=value)

    def _parse_io(self, raw: str, compact: str) -> AtomicElements | None:
        match = re.search(r"(?:io|IO|输出|y|Y)(\d+)(开|打开|关|关闭|on|off)", compact, flags=re.IGNORECASE)
        if not match:
            return None
        action_text = match.group(2).lower()
        action = 0 if action_text in {"关", "关闭", "off"} else 1
        return AtomicElements(raw_text=raw, command_text=compact, family="io", io_no=int(match.group(1)), io_action=action)

    def _parse_joint(
        self,
        raw: str,
        compact: str,
        spd_pct: float | None,
        acc_pct: float | None,
        dec_pct: float | None,
    ) -> AtomicElements | None:
        match = re.search(r"J([1-6])", compact, flags=re.IGNORECASE)
        if not match:
            return None
        axis_no = int(match.group(1)) - 1
        target_match = re.search(r"(?:转到|到|绝对)(-?\d+(?:\.\d+)?)(?:度|°)?", compact)
        if target_match:
            return AtomicElements(
                raw_text=raw,
                command_text=compact,
                family="joint",
                axis_no=axis_no,
                target=float(target_match.group(1)),
                spd_pct=spd_pct,
                acc_pct=acc_pct,
                dec_pct=dec_pct,
                fuzzy_pos=0,
            )
        direction = -1 if any(word in compact for word in ("反转", "负转", "逆时针", "回退")) else 1
        step = self._extract_distance(compact, ("度", "°"))
        return AtomicElements(
            raw_text=raw,
            command_text=compact,
            family="joint",
            axis_no=axis_no,
            direction=direction,
            step=step,
            spd_pct=spd_pct,
            acc_pct=acc_pct,
            dec_pct=dec_pct,
            fuzzy_pos=1,
        )

    def _parse_virtual(
        self,
        raw: str,
        compact: str,
        spd_pct: float | None,
        acc_pct: float | None,
        dec_pct: float | None,
    ) -> AtomicElements | None:
        virtual_map = (
            (6, 1, ("前进", "向前")),
            (6, -1, ("后退", "向后")),
            (7, 1, ("左移", "向左")),
            (7, -1, ("右移", "向右")),
            (8, 1, ("上升", "升高", "向上")),
            (8, -1, ("下降", "降低", "向下")),
            (9, 1, ("RX正转", "绕X正转")),
            (9, -1, ("RX反转", "绕X反转")),
            (10, 1, ("RY正转", "绕Y正转")),
            (10, -1, ("RY反转", "绕Y反转")),
            (11, 1, ("RZ正转", "绕Z正转")),
            (11, -1, ("RZ反转", "绕Z反转")),
        )
        for axis_no, direction, keywords in virtual_map:
            if any(keyword.lower() in compact.lower() for keyword in keywords):
                unit = ("度", "°") if axis_no >= 9 else ("毫米", "mm")
                return AtomicElements(
                    raw_text=raw,
                    command_text=compact,
                    family="virtual",
                    axis_no=axis_no,
                    direction=direction,
                    step=self._extract_distance(compact, unit),
                    spd_pct=spd_pct,
                    acc_pct=acc_pct,
                    dec_pct=dec_pct,
                    fuzzy_pos=1,
                )
        return None

    def _parse_cartesian(
        self,
        raw: str,
        compact: str,
        spd_pct: float | None,
        acc_pct: float | None,
        dec_pct: float | None,
    ) -> AtomicElements | None:
        values: dict[str, float] = {}
        for key in ("x", "y", "z", "rx", "ry", "rz"):
            match = re.search(rf"{key.upper()}(-?\d+(?:\.\d+)?)", compact, flags=re.IGNORECASE)
            if match:
                values[key] = float(match.group(1))
        if not values:
            return None
        return AtomicElements(
            raw_text=raw,
            command_text=compact,
            family="cartesian",
            x=values.get("x"),
            y=values.get("y"),
            z=values.get("z"),
            rx=values.get("rx"),
            ry=values.get("ry"),
            rz=values.get("rz"),
            spd_pct=spd_pct,
            acc_pct=acc_pct,
            dec_pct=dec_pct,
            fuzzy_pos=0,
        )

    @staticmethod
    def _strip_wake_word(text: str) -> str | None:
        compact = text.strip()
        for wake_word in WAKE_WORDS:
            if compact.startswith(wake_word):
                return compact[len(wake_word):].lstrip(" ，,。:：") or ""
        return None

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @staticmethod
    def _extract_speed_percent(text: str) -> float | None:
        patterns = (
            r"(?<!加)(?<!减)速度(-?\d+(?:\.\d+)?)%",
            r"(-?\d+(?:\.\d+)?)%(?<!加)(?<!减)速度",
            r"(-?\d+(?:\.\d+)?)%速",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return None

    @staticmethod
    def _extract_percent(text: str, labels: tuple[str, ...]) -> float | None:
        for label in labels:
            match = re.search(rf"{label}(-?\d+(?:\.\d+)?)%", text)
            if match:
                return float(match.group(1))
        for label in labels:
            match = re.search(rf"(-?\d+(?:\.\d+)?)%{label}", text)
            if match:
                return float(match.group(1))
        match = re.search(r"(-?\d+(?:\.\d+)?)%", text)
        if match and any(label in text for label in labels):
            return float(match.group(1))
        return None

    @staticmethod
    def _extract_distance(text: str, units: tuple[str, ...]) -> float | None:
        pattern_units = "|".join(re.escape(unit) for unit in units)
        match = re.search(rf"(-?\d+(?:\.\d+)?)(?:{pattern_units})", text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
        return None
