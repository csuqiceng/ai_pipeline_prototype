from __future__ import annotations

from .models import ParsedCommand, QueryRecord


class CommandParseError(ValueError):
    pass


ALIASES = {
    # 归零 / 回零
    "回零": "归零",
    "回到归零": "归零",
    "回到零位": "归零",
    "移动到归零": "归零",
    "回到原点": "归零",
    "回原点": "归零",
    "回home": "归零",
    "回home位": "归零",
    # 位置 A-E / X
    "移动到位置a": "位置A",
    "移动到位置b": "位置B",
    "移动到位置c": "位置C",
    "移动到位置d": "位置D",
    "移动到位置e": "位置E",
    "移动到位置x": "位置X",
    "到位置a": "位置A",
    "到位置b": "位置B",
    "到位置c": "位置C",
    "到位置d": "位置D",
    "到位置e": "位置E",
    "到位置x": "位置X",
    "去位置a": "位置A",
    "去位置b": "位置B",
    "去位置c": "位置C",
    "去位置d": "位置D",
    "去位置e": "位置E",
    "去位置x": "位置X",
    "位置a": "位置A",
    "位置b": "位置B",
    "位置c": "位置C",
    "位置d": "位置D",
    "位置e": "位置E",
    "位置x": "位置X",
    "a点": "位置A",
    "b点": "位置B",
    "c点": "位置C",
    "d点": "位置D",
    "e点": "位置E",
    "x点": "位置X",
    # 抓取 / 放下
    "抓取": "抓取",
    "抓一下": "抓取",
    "夹紧": "抓取",
    "夹住": "抓取",
    "合爪": "抓取",
    "闭合": "抓取",
    "放下": "放下",
    "松开": "放下",
    "释放": "放下",
    "松爪": "放下",
    "打开": "放下",
    "开爪": "放下",
    # 延时 / 等待
    "延时": "延时",
    "等待": "延时",
    "等一下": "延时",
    "暂停一下": "延时",
    # Home
    "home": "home",
    # 通用动作前缀（去前缀后再匹配）
    "移动到": "",
    "去": "",
    "到": "",
    "把": "",
    "将": "",
}


_ACTION_PREFIXES = ("把", "将", "帮我", "请", "麻烦", "要")


def parse_command(text: str, table: dict[str, QueryRecord]) -> ParsedCommand:
    normalized = text.strip()
    if not normalized:
        raise CommandParseError("输入为空，无法解析。")

    # Strip common action prefixes before matching
    stripped = normalized
    for prefix in _ACTION_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()

    lowered = stripped.lower()
    if lowered in ALIASES:
        query_key = ALIASES[lowered]
        if query_key:
            _ensure_exists(query_key, table)
            return ParsedCommand(raw_text=text, query_key=query_key)

    compact = stripped.replace(" ", "").lower()
    if compact in ALIASES:
        query_key = ALIASES[compact]
        if query_key:
            _ensure_exists(query_key, table)
            return ParsedCommand(raw_text=text, query_key=query_key)

    for key in table:
        if key.lower() in lowered or key.lower() in compact:
            return ParsedCommand(raw_text=text, query_key=key)

    raise CommandParseError(f"未识别到可执行查询键: {text}")


def _ensure_exists(query_key: str, table: dict[str, QueryRecord]) -> None:
    if query_key not in table:
        raise CommandParseError(f"查询表中不存在键: {query_key}")
