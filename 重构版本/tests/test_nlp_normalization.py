import json

from robot_modbus_lite.nlp_normalization import NlpNormalizer
from robot_modbus_lite.nlp_standard_words import (
    STANDARD_WORDS,
    get_words_by_category,
    load_standard_words,
)


def test_standard_word_catalog_has_required_core_words():
    assert len(STANDARD_WORDS) >= 50
    for word in ["执行", "保存", "删除", "流程", "位置", "急停", "复位", "上升", "下降"]:
        assert word in STANDARD_WORDS
    assert get_words_by_category("flow")


def test_homophone_normalization_maps_common_errors():
    normalizer = NlpNormalizer()

    result = normalizer.normalize("小正，保村位置A")

    assert result.text == "小正，保存位置A"
    assert any(step.kind == "homophone" for step in result.steps)


def test_dialect_normalization_maps_sichuan_phrase():
    normalizer = NlpNormalizer()

    result = normalizer.normalize("小正，往上头走十毫米")

    assert "上升" in result.text or "向上" in result.text
    assert any(step.kind == "dialect" for step in result.steps)


def test_pinyin_normalization_is_safe_when_dependency_missing():
    normalizer = NlpNormalizer(enable_pinyin=True)

    result = normalizer.normalize("小正，zhixing流程A")

    assert result.text
    assert result.original == "小正，zhixing流程A"


def test_pinyin_normalization_can_require_dependency():
    normalizer = NlpNormalizer(enable_pinyin=True, require_pinyin=True)

    try:
        import pypinyin  # type: ignore  # noqa: F401
    except Exception:
        try:
            normalizer.normalize("小正，执行流程A")
        except RuntimeError as exc:
            assert "pypinyin" in str(exc)
        else:
            raise AssertionError("missing pypinyin should raise when require_pinyin=True")


def test_sichuan_phonetic_confusion_maps_common_words_without_pypinyin():
    normalizer = NlpNormalizer()

    result = normalizer.normalize("小正，灰零")

    assert result.text == "小正，回零"
    assert any(step.kind == "dialect_phonetic" for step in result.steps)


def test_colloquial_unit_normalization_maps_field_terms():
    normalizer = NlpNormalizer()

    result = normalizer.normalize("小正，前进一公分")

    assert result.text == "小正，前进一厘米"
    assert any(step.kind == "unit" for step in result.steps)


def test_standard_words_can_load_from_json_config(tmp_path):
    path = tmp_path / "nlp_standard_words.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "words": [
                    {
                        "standard": "夹取",
                        "pinyin": "jiaqu",
                        "category": "motion",
                        "homophones": ["加取"],
                        "sichuan_variants": ["夹一哈"],
                        "func_id": 120,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    words = load_standard_words(path)

    assert words["夹取"].homophones == ("加取",)
    assert words["夹取"].sichuan_variants == ("夹一哈",)
    assert words["夹取"].func_id == 120


def test_normalizer_uses_config_loaded_words(tmp_path):
    path = tmp_path / "nlp_standard_words.json"
    path.write_text(
        json.dumps(
            {
                "words": [
                    {
                        "standard": "夹取",
                        "pinyin": "jiaqu",
                        "category": "motion",
                        "homophones": ["加取"],
                        "sichuan_variants": ["夹一哈"],
                        "func_id": 120,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    normalizer = NlpNormalizer(standard_words=load_standard_words(path))

    result = normalizer.normalize("小正，加取工件")

    assert result.text == "小正，夹取工件"
    assert any(step.kind == "homophone" for step in result.steps)


def test_standard_words_fallback_to_defaults_when_config_is_invalid(tmp_path):
    path = tmp_path / "nlp_standard_words.json"
    path.write_text("{bad json", encoding="utf-8")

    words = load_standard_words(path)

    assert "急停" in words
    assert len(words) >= 50
