from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import QueryRecord


SUPPORTED_FUNC_NUMS = {104, 106, 107, 108}


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
    rows = csv.reader(lines, delimiter="\t")
    table: dict[str, QueryRecord] = {}
    for row in rows:
        cleaned = [item.strip() for item in row]
        if len(cleaned) < 10 or cleaned[0] in {"", "query_key"}:
            continue
        query_key = cleaned[0]
        func_num = int(float(cleaned[1]))
        if func_num not in SUPPORTED_FUNC_NUMS:
            continue
        params = _default_params_for_func(func_num)
        values = [float(item or 0) for item in cleaned[2:]]
        if func_num == 104:
            params["stop_mode"] = values[0] if values else 0
        elif func_num in (106, 107):
            keys = [
                "axis_no",
                "pos_val",
                "spd",
                "acc_v",
                "dec_v",
                "fuzzy_pos",
                "fuzzy_spd",
                "fuzzy_acc",
                "fuzzy_dec",
                "stop_cmd",
            ]
            for key, value in zip(keys, values):
                params[key] = value
        elif func_num == 108:
            keys = [
                "target_x",
                "target_y",
                "target_z",
                "target_rx",
                "target_ry",
                "target_rz",
                "spd",
                "acc_v",
                "dec_v",
                "stop_cmd",
                "fuzzy_pos",
                "fuzzy_spd",
                "fuzzy_acc",
                "fuzzy_dec",
                "move_type",
            ]
            for key, value in zip(keys, values):
                params[key] = value
        table[query_key] = QueryRecord(query_key=query_key, func_num=func_num, params=params)

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
        func_num = int(item.get("func_num"))
        if func_num not in SUPPORTED_FUNC_NUMS:
            raise QueryTableError(f"仅支持 Func104/106/107/108，实际={func_num}")
        params_raw = item.get("params")
        if not isinstance(params_raw, dict):
            raise QueryTableError(f"JSON 记录缺少 params: {item!r}")
        params = _default_params_for_func(func_num)
        for key in params:
            if key in params_raw:
                params[key] = params_raw[key]
        table[query_key] = QueryRecord(
            query_key=query_key,
            func_num=func_num,
            params=params,
            keywords=str(item.get("keywords", "")),
            description=str(item.get("description", "")),
            safety_level=int(item.get("safety_level", 5)),
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
                "func_num": record.func_num,
                "keywords": record.keywords,
                "description": record.description,
                "safety_level": record.safety_level,
                "params": record.params,
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


def _default_params_for_func(func_num: int) -> dict[str, float | int]:
    if func_num == 104:
        return {"stop_mode": 0}
    if func_num in (106, 107):
        return {
            "axis_no": 0,
            "pos_val": 0.0,
            "spd": 300.0,
            "acc_v": 60.0,
            "dec_v": 60.0,
            "fuzzy_pos": 0,
            "fuzzy_spd": 0,
            "fuzzy_acc": 0,
            "fuzzy_dec": 0,
            "stop_cmd": 0,
        }
    if func_num == 108:
        return {
            "target_x": 0.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "target_rx": 0.0,
            "target_ry": 0.0,
            "target_rz": 0.0,
            "spd": 300.0,
            "acc_v": 400.0,
            "dec_v": 400.0,
            "stop_cmd": 0,
            "fuzzy_pos": 0,
            "fuzzy_spd": 0,
            "fuzzy_acc": 0,
            "fuzzy_dec": 0,
            "move_type": 0,
        }
    raise QueryTableError(f"不支持的函数号: {func_num}")
