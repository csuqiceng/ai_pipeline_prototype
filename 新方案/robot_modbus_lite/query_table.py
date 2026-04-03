from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .models import QueryRecord


class QueryTableError(ValueError):
    pass


def load_query_table(csv_path: str | Path) -> dict[str, QueryRecord]:
    path = Path(csv_path)
    if path.suffix.lower() == ".json":
        return load_query_table_json(path)
    return load_query_table_csv(path)


def load_query_table_csv(path: str | Path) -> dict[str, QueryRecord]:
    path = Path(path)
    if not path.exists():
        raise QueryTableError(f"未找到地址表文件: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = _find_effective_header(lines)
    rows = csv.reader(lines[header_index:], delimiter="\t")

    try:
        next(rows)
    except StopIteration as exc:
        raise QueryTableError("有效数据表为空。") from exc

    table: dict[str, QueryRecord] = {}
    for row in rows:
        cleaned = [item.strip() for item in row]
        if not any(cleaned):
            continue
        if len(cleaned) < 9:
            raise QueryTableError(f"有效数据行字段不足: {cleaned}")

        query_key = cleaned[0]
        function_id = _parse_int_prefix(cleaned[1])
        registers = tuple(_parse_numeric(value) for value in cleaned[2:9])
        if len(registers) != 7:
            raise QueryTableError(f"寄存器数量非法: {cleaned}")

        table[query_key] = QueryRecord(
            query_key=query_key,
            function_id=function_id,
            registers=registers,  # type: ignore[arg-type]
        )

    if not table:
        raise QueryTableError("未解析到任何有效数据记录。")
    return table


def load_query_table_json(path: str | Path) -> dict[str, QueryRecord]:
    path = Path(path)
    if not path.exists():
        raise QueryTableError(f"未找到 JSON 查询表文件: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise QueryTableError("JSON 查询表缺少 records 列表。")

    table: dict[str, QueryRecord] = {}
    for item in records:
        if not isinstance(item, dict):
            raise QueryTableError(f"非法记录: {item!r}")
        query_key = str(item.get("query_key", "")).strip()
        if not query_key:
            raise QueryTableError("JSON 记录缺少 query_key。")
        function_id = int(item.get("function_id"))
        registers_raw = item.get("registers")
        if not isinstance(registers_raw, list) or len(registers_raw) != 7:
            raise QueryTableError(f"JSON 记录寄存器数量非法: {item!r}")
        registers = tuple(float(value) for value in registers_raw)
        table[query_key] = QueryRecord(
            query_key=query_key,
            function_id=function_id,
            registers=registers,  # type: ignore[arg-type]
            function_name=str(item.get("function_name", "movabs")),
            data_format=str(item.get("data_format", "IEE")),
        )

    if not table:
        raise QueryTableError("JSON 查询表中没有有效记录。")
    return table


def save_query_table_json(path: str | Path, table: dict[str, QueryRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "records": [
            {
                "query_key": record.query_key,
                "function_id": record.function_id,
                "function_name": record.function_name,
                "data_format": record.data_format,
                "registers": list(record.registers),
            }
            for record in sorted(table.values(), key=lambda item: item.query_key)
        ]
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def bootstrap_query_table_json(json_path: str | Path, csv_path: str | Path) -> Path:
    json_file = Path(json_path)
    if json_file.exists():
        return json_file
    table = load_query_table_csv(csv_path)
    save_query_table_json(json_file, table)
    return json_file


def _find_effective_header(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if line.strip().startswith("AI解析数据表"):
            return index
    raise QueryTableError("未找到“AI解析数据表”表头。")


def _parse_int_prefix(value: str) -> int:
    match = re.match(r"\s*(-?\d+)", value)
    if not match:
        raise QueryTableError(f"函数序号无法解析: {value!r}")
    return int(match.group(1))


def _parse_numeric(value: str) -> float:
    normalized = value.strip().replace("%", "")
    if normalized in {"", "无"}:
        return 0.0
    return float(normalized)
