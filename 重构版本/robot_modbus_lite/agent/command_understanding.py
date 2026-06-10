"""Deterministic command understanding for the restricted Agent path."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from robot_modbus_lite.agent.address_resolver import AddressResolver
from robot_modbus_lite.atomic_parser import AtomicParser
from robot_modbus_lite.nlp_normalization import NlpNormalizer
from robot_modbus_lite.voice_nlp_adapter import SYSTEM_ACTION_ALIASES


SYSTEM_FUNC_ID = 104
JOINT_JOG_FUNC_ID = 106
VIRTUAL_JOG_FUNC_ID = 107
MOVE_LINEAR_FUNC_ID = 108
CONTINUOUS_PATH_FUNC_ID = 112
EMERGENCY_INTENTS = {"sys_estop", "sys_pause", "sys_resume", "sys_cancel", "alarm_reset"}
CHINESE_NUMBER_CHARS = "负零〇一二两三四五六七八九十百千万点."
CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
AXIS_ASR_ALIASES = (
    ("RX", ("R X", "r x", "阿尔艾克斯", "绕X")),
    ("RY", ("R Y", "r y", "阿尔歪", "绕Y")),
    ("RZ", ("R Z", "r z", "阿尔Z", "绕Z")),
    ("X", ("艾克斯", "叉", "差", "X轴", "x轴")),
    ("Y", ("歪", "外", "Y轴", "y轴")),
    ("Z", ("zed", "Z轴", "z轴")),
)


@dataclass(frozen=True)
class CommandUnderstandingResult:
    raw_text: str
    intent: str
    func_id: int | None
    normalized_text: str = ""
    extracted_params: dict[str, float | int | str] = field(default_factory=dict)
    confidence: float = 0.0
    needs_model: bool = False
    clarification: str = ""
    bypass_completion: bool = False


class CommandUnderstandingAgent:
    """Parse control-oriented text into an Agent intent without executing it."""

    def __init__(
        self,
        parser: AtomicParser | None = None,
        address_resolver: AddressResolver | None = None,
        normalizer: NlpNormalizer | None = None,
    ) -> None:
        self._parser = parser or AtomicParser()
        self._address_resolver = address_resolver or AddressResolver()
        self._normalizer = normalizer or NlpNormalizer(enable_pinyin=False)

    def understand(self, text: str) -> CommandUnderstandingResult:
        raw = (text or "").strip()
        raw_compact = re.sub(r"\s+", "", raw)
        normalized = self._normalize_text_for_understanding(raw)
        compact = re.sub(r"\s+", "", normalized)
        if not compact:
            return self._unknown(raw, "请补充具体指令。", needs_model=False, normalized_text=normalized)

        system_intent = SYSTEM_ACTION_ALIASES.get(raw_compact) or SYSTEM_ACTION_ALIASES.get(compact)
        if system_intent in EMERGENCY_INTENTS:
            return CommandUnderstandingResult(
                raw_text=raw,
                intent=system_intent,
                func_id=SYSTEM_FUNC_ID,
                normalized_text=normalized,
                confidence=1.0,
                needs_model=False,
                bypass_completion=True,
            )

        if self._looks_like_alarm_query(compact):
            return CommandUnderstandingResult(
                raw_text=raw,
                intent="alarm_query",
                func_id=None,
                normalized_text=normalized,
                confidence=0.95,
                needs_model=False,
                bypass_completion=True,
            )

        if self._looks_like_status_query(compact):
            return CommandUnderstandingResult(
                raw_text=raw,
                intent="status_query",
                func_id=None,
                normalized_text=normalized,
                confidence=0.95,
                needs_model=False,
                bypass_completion=True,
            )

        if self._looks_like_unsupported_compound(compact):
            return self._unknown(
                raw,
                "复合指令暂不由受限Agent一次性执行，请拆成单条指令分别确认。",
                needs_model=False,
                normalized_text=normalized,
            )

        delay_params = self._parse_delay_params(compact)
        if delay_params:
            intent = "delay_parallel" if any(word in compact for word in ("并行", "同时")) else "delay_blocking"
            return CommandUnderstandingResult(
                raw_text=raw,
                intent=intent,
                func_id=110 if intent == "delay_parallel" else 109,
                normalized_text=normalized,
                extracted_params=delay_params,
                confidence=0.95,
                needs_model=False,
            )

        io_params = self._parse_io_params(compact)
        if io_params:
            return CommandUnderstandingResult(
                raw_text=raw,
                intent="io",
                func_id=120,
                normalized_text=normalized,
                extracted_params=io_params,
                confidence=0.95,
                needs_model=False,
            )

        elements = self._parser.parse(normalized)
        if elements.family == "delay" and getattr(elements, "delay_sec", None) is not None:
            intent = "delay_parallel" if any(word in compact for word in ("并行", "同时")) else "delay_blocking"
            return CommandUnderstandingResult(
                raw_text=raw,
                intent=intent,
                func_id=110 if intent == "delay_parallel" else 109,
                normalized_text=normalized,
                extracted_params={"delay_sec": float(elements.delay_sec)},
                confidence=0.95,
                needs_model=False,
            )
        if elements.family == "io" and getattr(elements, "io_no", None) is not None and getattr(elements, "io_action", None) is not None:
            return CommandUnderstandingResult(
                raw_text=raw,
                intent="io",
                func_id=120,
                normalized_text=normalized,
                extracted_params={"io_no": int(elements.io_no), "io_action": int(elements.io_action)},
                confidence=0.95,
                needs_model=False,
            )
        if elements.family in {"joint", "virtual"} and getattr(elements, "axis_no", None) is not None:
            jog_params = self._params_from_jog(elements)
            if jog_params:
                is_joint = elements.family == "joint"
                return CommandUnderstandingResult(
                    raw_text=raw,
                    intent="joint_jog" if is_joint else "virtual_jog",
                    func_id=JOINT_JOG_FUNC_ID if is_joint else VIRTUAL_JOG_FUNC_ID,
                    normalized_text=normalized,
                    extracted_params=jog_params,
                    confidence=0.95,
                    needs_model=False,
                )
        params = self._params_from_cartesian(elements) if elements.family == "cartesian" else self._parse_cartesian_params(compact)
        incremental_params = self._parse_incremental_params(compact)
        if incremental_params:
            params.update(incremental_params)
        if params and any(key.startswith("delta_") for key in params):
            params["position_increment"] = 1
        elif params and any(key in params for key in ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")):
            params["position_increment"] = 0
        if params:
            is_continuous_path = any(word in compact for word in ("规划路径", "规避", "绕行"))
            confidence = 0.95 if self._has_full_pose(params) else 0.88
            return CommandUnderstandingResult(
                raw_text=raw,
                intent="continuous_path" if is_continuous_path else "move_linear",
                func_id=self._address_resolver.continuous_path_func
                if is_continuous_path
                else self._address_resolver.absolute_motion_func,
                normalized_text=normalized,
                extracted_params=params,
                confidence=confidence,
                needs_model=False,
            )

        if self._looks_like_control_text(compact):
            return self._unknown(raw, "请补充明确的坐标、方向或参数。", needs_model=True, normalized_text=normalized)
        return self._unknown(raw, "未识别为控制指令。", needs_model=False, normalized_text=normalized)

    @staticmethod
    def _params_from_cartesian(elements: Any) -> dict[str, float | int | str]:
        mapping = (
            ("x", "target_x"),
            ("y", "target_y"),
            ("z", "target_z"),
            ("rx", "target_rx"),
            ("ry", "target_ry"),
            ("rz", "target_rz"),
            ("spd_pct", "spd_pct"),
            ("acc_pct", "acc_pct"),
            ("dec_pct", "dec_pct"),
        )
        params: dict[str, float | int | str] = {}
        for source_key, target_key in mapping:
            value = getattr(elements, source_key)
            if value is not None:
                params[target_key] = float(value)
        return params

    @staticmethod
    def _params_from_jog(elements: Any) -> dict[str, float | int | str]:
        axis_no = getattr(elements, "axis_no", None)
        if axis_no is None:
            return {}
        target = getattr(elements, "target", None)
        step = getattr(elements, "step", None)
        direction = int(getattr(elements, "direction", 1) or 1)
        if target is not None:
            pos_val = float(target)
            fuzzy_pos = 0
        elif step is not None:
            pos_val = direction * abs(float(step))
            fuzzy_pos = int(getattr(elements, "fuzzy_pos", 1) or 1)
        else:
            return {}
        params: dict[str, float | int | str] = {
            "axis_no": int(axis_no),
            "pos_val": float(pos_val),
            "fuzzy_pos": int(fuzzy_pos),
            "fuzzy_spd": 0 if getattr(elements, "spd_pct", None) is not None else 1,
        }
        for source_key in ("spd_pct", "acc_pct", "dec_pct"):
            value = getattr(elements, source_key, None)
            if value is not None:
                params[source_key] = float(value)
        return params

    @staticmethod
    def _parse_cartesian_params(compact: str) -> dict[str, float | int | str]:
        params: dict[str, float | int | str] = {}
        for source_key, target_key in (
            ("x", "target_x"),
            ("y", "target_y"),
            ("z", "target_z"),
            ("rx", "target_rx"),
            ("ry", "target_ry"),
            ("rz", "target_rz"),
        ):
            match = re.search(rf"{source_key.upper()}(-?\d+(?:\.\d+)?)", compact, flags=re.IGNORECASE)
            if match:
                params[target_key] = float(match.group(1))

        speed_match = re.search(r"(?<!加)(?<!减)速度(-?\d+(?:\.\d+)?)(?:%)?", compact)
        if speed_match:
            params["spd_pct"] = float(speed_match.group(1))
        acc_match = re.search(r"(?:加速度|加速)(-?\d+(?:\.\d+)?)(?:%)?", compact)
        if acc_match:
            params["acc_pct"] = float(acc_match.group(1))
        dec_match = re.search(r"(?:减速度|减速)(-?\d+(?:\.\d+)?)(?:%)?", compact)
        if dec_match:
            params["dec_pct"] = float(dec_match.group(1))
        return params

    @classmethod
    def _parse_incremental_params(cls, compact: str) -> dict[str, float | int | str]:
        params: dict[str, float | int | str] = {}
        for aliases, delta_key, sign in (
            (("向左", "左移"), "delta_x", 1.0),
            (("向右", "右移"), "delta_x", -1.0),
            (("向前", "前进"), "delta_y", 1.0),
            (("向后", "后退"), "delta_y", -1.0),
            (("升高", "上升", "向上"), "delta_z", 1.0),
            (("下降", "降低", "向下"), "delta_z", -1.0),
        ):
            for alias in aliases:
                match = re.search(rf"{re.escape(alias)}(?:移动)?(-?\d+(?:\.\d+)?)(?:毫米|mm)?", compact, flags=re.IGNORECASE)
                if not match:
                    continue
                params[delta_key] = sign * float(match.group(1))
                return cls._parse_cartesian_params(compact) | params
        return params

    @staticmethod
    def _parse_delay_params(compact: str) -> dict[str, float | int | str]:
        match = re.search(r"(?:等待|延时|暂停)(\d+(?:\.\d+)?)(秒|s|毫秒|ms)", compact, flags=re.IGNORECASE)
        if not match:
            return {}
        value = float(match.group(1))
        if match.group(2).lower() in {"毫秒", "ms"}:
            value /= 1000.0
        return {"delay_sec": value}

    @staticmethod
    def _parse_io_params(compact: str) -> dict[str, float | int | str]:
        match = re.search(r"(?:io|IO|输出|y|Y)(\d+)(开|打开|关|关闭|on|off)", compact, flags=re.IGNORECASE)
        if not match:
            return {}
        action_text = match.group(2).lower()
        return {"io_no": int(match.group(1)), "io_action": 0 if action_text in {"关", "关闭", "off"} else 1}

    @classmethod
    def _normalize_parameter_numbers(cls, text: str) -> str:
        normalized = cls._normalize_asr_parameter_aliases(text or "")
        number_pattern = rf"-?[{CHINESE_NUMBER_CHARS}]+"

        def replace_axis(match: re.Match[str]) -> str:
            label = match.group(1)
            value = cls._chinese_number_to_float(match.group(2))
            if value is None:
                return match.group(0)
            return f"{label}{cls._format_number(value)}"

        normalized = re.sub(
            rf"\b(RX|RY|RZ|X|Y|Z)\s*({number_pattern})",
            replace_axis,
            normalized,
            flags=re.IGNORECASE,
        )

        def replace_labeled_number(match: re.Match[str]) -> str:
            label = match.group(1)
            value = cls._chinese_number_to_float(match.group(2))
            unit = match.group(3) or ""
            if value is None:
                return match.group(0)
            return f"{label}{cls._format_number(value)}{unit}"

        normalized = re.sub(
            rf"(速度|加速度|加速|减速度|减速|等待|延时|暂停|移动|升高|下降|降低|前进|后退|左移|右移|向左|向右|向前|向后|向上|向下)\s*({number_pattern})(%|秒|s|毫秒|ms|毫米|mm|度|°)?",
            replace_labeled_number,
            normalized,
            flags=re.IGNORECASE,
        )
        return normalized

    def _normalize_text_for_understanding(self, text: str) -> str:
        try:
            normalized = self._normalizer.normalize(text).text
        except Exception:
            normalized = text
        return self._normalize_parameter_numbers(normalized)

    @staticmethod
    def _normalize_asr_parameter_aliases(text: str) -> str:
        normalized = text or ""
        value_pattern = rf"-?(?:\d+(?:\.\d+)?|[{CHINESE_NUMBER_CHARS}]+)"
        for axis, aliases in AXIS_ASR_ALIASES:
            for alias in aliases:
                pattern = rf"(?<![A-Za-z0-9]){re.escape(alias)}\s*({value_pattern})"
                normalized = re.sub(pattern, rf"{axis}\1", normalized, flags=re.IGNORECASE)
        return normalized

    @staticmethod
    def _format_number(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else str(value)

    @classmethod
    def _chinese_number_to_float(cls, value: str) -> float | None:
        token = (value or "").strip()
        if not token:
            return None
        negative = token.startswith(("负", "-"))
        token = token.lstrip("负-")
        if not token:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            parsed = float(token)
            return -parsed if negative else parsed
        if any(char not in CHINESE_NUMBER_CHARS for char in token):
            return None
        if token.count("点") > 1 or token.count(".") > 1:
            return None

        if "点" in token or "." in token:
            head, tail = re.split(r"[点.]", token, maxsplit=1)
            head_value = cls._chinese_integer_to_int(head) if head else 0
            if head_value is None or not tail:
                return None
            digits = []
            for char in tail:
                if char not in CHINESE_DIGITS:
                    return None
                digits.append(str(CHINESE_DIGITS[char]))
            parsed = float(f"{head_value}.{''.join(digits)}")
            return -parsed if negative else parsed

        integer = cls._chinese_integer_to_int(token)
        if integer is None:
            return None
        parsed = float(integer)
        return -parsed if negative else parsed

    @staticmethod
    def _chinese_integer_to_int(token: str) -> int | None:
        if not token:
            return 0
        total = 0
        section = 0
        number = 0
        has_digit = False
        unit_seen = False
        for char in token:
            if char in CHINESE_DIGITS:
                number = CHINESE_DIGITS[char]
                has_digit = True
                continue
            if char in {"十", "百", "千"}:
                unit_seen = True
                unit = {"十": 10, "百": 100, "千": 1000}[char]
                if number == 0:
                    number = 1
                section += number * unit
                number = 0
                continue
            if char == "万":
                unit_seen = True
                section += number
                total += section * 10000
                section = 0
                number = 0
                continue
            return None
        if not has_digit and not unit_seen:
            return None
        return total + section + number

    @staticmethod
    def _has_full_pose(params: dict[str, Any]) -> bool:
        return all(
            key in params
            for key in ("target_x", "target_y", "target_z", "target_rx", "target_ry", "target_rz")
        )

    @staticmethod
    def _looks_like_alarm_query(compact: str) -> bool:
        return "报警" in compact and any(word in compact for word in ("什么", "查询", "说明", "原因", "当前", "状态"))

    @staticmethod
    def _looks_like_status_query(compact: str) -> bool:
        if any(phrase in compact for phrase in ("为什么不能动", "为何不能动", "怎么不能动", "不能动了吗")):
            return True
        if any(phrase in compact for phrase in ("运动完成了吗", "执行完成了吗", "完成了吗", "结束了吗")):
            return True
        return any(word in compact for word in ("当前状态", "系统状态", "设备状态", "运行状态", "现在状态"))

    @staticmethod
    def _looks_like_control_text(compact: str) -> bool:
        return any(
            word in compact
            for word in (
                "走",
                "去",
                "移动",
                "往",
                "到",
                "前进",
                "后退",
                "左移",
                "右移",
                "上升",
                "下降",
                "升高",
                "降低",
                "向左",
                "向右",
                "向前",
                "向后",
                "向上",
                "向下",
            )
        )

    @classmethod
    def _looks_like_unsupported_compound(cls, compact: str) -> bool:
        if not any(word in compact for word in ("然后", "再", "接着", "并且")):
            return False
        actionable = 0
        if cls._parse_cartesian_params(compact) or cls._parse_incremental_params(compact):
            actionable += 1
        if cls._parse_delay_params(compact):
            actionable += 1
        if cls._parse_io_params(compact):
            actionable += 1
        return actionable >= 2

    @staticmethod
    def _unknown(
        raw: str,
        clarification: str,
        *,
        needs_model: bool,
        normalized_text: str | None = None,
    ) -> CommandUnderstandingResult:
        return CommandUnderstandingResult(
            raw_text=raw,
            intent="unknown",
            func_id=None,
            normalized_text=str(normalized_text if normalized_text is not None else raw),
            confidence=0.3 if needs_model else 0.0,
            needs_model=needs_model,
            clarification=clarification,
            bypass_completion=True,
        )
