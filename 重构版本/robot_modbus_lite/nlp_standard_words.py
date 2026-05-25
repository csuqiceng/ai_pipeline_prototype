from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StandardWord:
    standard: str
    pinyin: str
    category: str
    homophones: tuple[str, ...] = ()
    sichuan_variants: tuple[str, ...] = ()
    func_id: int | None = None


DEFAULT_WORD_ITEMS = [
    ("执行", "zhixing", "flow", ("直行", "执形"), (), None),
    ("保存", "baocun", "flow", ("保村", "宝存", "报存"), (), None),
    ("删除", "shanchu", "flow", ("山除", "善除"), (), None),
    ("流程", "liucheng", "flow", (), (), None),
    ("步骤", "buzhou", "flow", (), (), None),
    ("循环", "xunhuan", "flow", (), (), None),
    ("单步", "danbu", "flow", (), (), None),
    ("位置", "weizhi", "position", (), (), 108),
    ("示教", "shijiao", "position", (), (), None),
    ("移动", "yidong", "motion", (), (), 108),
    ("上升", "shangsheng", "motion", (), ("上头走", "往上头走"), 107),
    ("下降", "xiajiang", "motion", (), ("下切", "往下头走"), 107),
    ("左移", "zuoyi", "motion", (), (), 107),
    ("右移", "youyi", "motion", (), (), 107),
    ("前进", "qianjin", "motion", (), (), 107),
    ("后退", "houtui", "motion", (), (), 107),
    ("关节", "guanjie", "motion", (), (), 106),
    ("虚拟轴", "xunizhou", "motion", (), (), 107),
    ("回零", "huiling", "system", (), (), 104),
    ("复位", "fuwei", "system", (), (), 104),
    ("暂停", "zanting", "system", (), (), 104),
    ("继续", "jixu", "system", (), (), 110),
    ("停止", "tingzhi", "system", (), (), 104),
    ("急停", "jiting", "emergency", (), (), 104),
    ("报警", "baojing", "alarm", (), (), None),
    ("确认", "queren", "confirm", (), (), None),
    ("取消", "quxiao", "confirm", (), (), None),
    ("速度", "sudu", "param", (), (), None),
    ("加速度", "jiasudu", "param", (), (), None),
    ("减速度", "jiansudu", "param", (), (), None),
    ("慢速", "mansu", "param", (), (), None),
    ("快速", "kuaisu", "param", (), (), None),
    ("毫米", "haomi", "unit", (), (), None),
    ("厘米", "limi", "unit", (), (), None),
    ("度", "du", "unit", (), (), None),
    ("秒", "miao", "unit", (), (), None),
    ("打开", "dakai", "io", (), (), 120),
    ("关闭", "guanbi", "io", (), (), 120),
    ("IO", "io", "io", (), (), 120),
    ("看板", "kanban", "query", (), (), None),
    ("状态", "zhuangtai", "query", (), (), None),
    ("边界", "bianjie", "query", (), (), None),
    ("极限", "jixian", "query", (), (), None),
    ("通讯", "tongxun", "query", (), (), None),
    ("故障", "guzhang", "query", (), (), None),
    ("演练", "yanlian", "flow", (), (), None),
    ("锁定", "suoding", "flow", (), (), None),
    ("工程师", "gongchengshi", "permission", (), (), None),
    ("操作员", "caozuoyuan", "permission", (), (), None),
    ("系统", "xitong", "system", (), (), None),
    ("等待", "dengdai", "flow", (), (), 110),
    ("延时", "yanshi", "flow", (), (), 110),
]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "nlp_standard_words.json"


def _word_from_item(item: tuple[str, str, str, tuple[str, ...], tuple[str, ...], int | None]) -> StandardWord:
    return StandardWord(*item)


def _word_from_dict(payload: dict[str, Any]) -> StandardWord:
    return StandardWord(
        standard=str(payload.get("standard", "")).strip(),
        pinyin=str(payload.get("pinyin", "")).strip(),
        category=str(payload.get("category", "")).strip(),
        homophones=tuple(str(item).strip() for item in payload.get("homophones", ()) if str(item).strip()),
        sichuan_variants=tuple(str(item).strip() for item in payload.get("sichuan_variants", ()) if str(item).strip()),
        func_id=(int(payload["func_id"]) if payload.get("func_id") not in (None, "") else None),
    )


def default_standard_words() -> dict[str, StandardWord]:
    return {item[0]: _word_from_item(item) for item in DEFAULT_WORD_ITEMS}


def load_standard_words(path: str | Path | None = None) -> dict[str, StandardWord]:
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not source.exists():
        return default_standard_words()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        raw_words = payload.get("words", payload)
        if isinstance(raw_words, dict):
            raw_words = raw_words.values()
        words: dict[str, StandardWord] = {}
        for item in raw_words:
            if not isinstance(item, dict):
                continue
            word = _word_from_dict(item)
            if word.standard and word.pinyin and word.category:
                words[word.standard] = word
        return words or default_standard_words()
    except Exception:
        return default_standard_words()


def save_standard_words_config(path: str | Path, words: dict[str, StandardWord] | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "description": "现场自然语言标准词配置，可扩展 homophones 和 sichuan_variants。",
        "words": [
            {
                "standard": word.standard,
                "pinyin": word.pinyin,
                "category": word.category,
                "homophones": list(word.homophones),
                "sichuan_variants": list(word.sichuan_variants),
                "func_id": word.func_id,
            }
            for word in (words or default_standard_words()).values()
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


STANDARD_WORDS = load_standard_words()


def get_words_by_category(category: str) -> list[StandardWord]:
    return [word for word in STANDARD_WORDS.values() if word.category == category]


def get_words_by_func(func_id: int) -> list[StandardWord]:
    return [word for word in STANDARD_WORDS.values() if word.func_id == func_id]
