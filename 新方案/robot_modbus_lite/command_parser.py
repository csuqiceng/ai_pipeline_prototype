from __future__ import annotations

from .models import ParsedCommand, QueryRecord


class CommandParseError(ValueError):
    pass


ALIASES = {
    "回零": "归零",
    "回到归零": "归零",
    "回到零位": "归零",
    "移动到归零": "归零",
    "移动到位置a": "位置A",
    "移动到位置b": "位置B",
    "移动到位置c": "位置C",
    "移动到位置d": "位置D",
    "移动到位置e": "位置E",
    "移动到位置x": "位置X",
    "位置a": "位置A",
    "位置b": "位置B",
    "位置c": "位置C",
    "位置d": "位置D",
    "位置e": "位置E",
    "位置x": "位置X",
    "抓取": "抓取",
    "抓一下": "抓取",
    "夹紧": "抓取",
    "放下": "放下",
    "松开": "放下",
    "释放": "放下",
    "延时": "延时",
    "home": "home",
}


def parse_command(text: str, table: dict[str, QueryRecord]) -> ParsedCommand:
    normalized = text.strip()
    if not normalized:
        raise CommandParseError("输入为空，无法解析。")

    lowered = normalized.lower()
    if lowered in ALIASES:
        query_key = ALIASES[lowered]
        _ensure_exists(query_key, table)
        return ParsedCommand(raw_text=text, query_key=query_key)

    compact = normalized.replace(" ", "").lower()
    if compact in ALIASES:
        query_key = ALIASES[compact]
        _ensure_exists(query_key, table)
        return ParsedCommand(raw_text=text, query_key=query_key)

    for key in table:
        if key.lower() in lowered or key.lower() in compact:
            return ParsedCommand(raw_text=text, query_key=key)

    raise CommandParseError(f"未识别到可执行查询键: {text}")


def _ensure_exists(query_key: str, table: dict[str, QueryRecord]) -> None:
    if query_key not in table:
        raise CommandParseError(f"查询表中不存在键: {query_key}")
