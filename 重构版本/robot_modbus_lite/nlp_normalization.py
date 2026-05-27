from __future__ import annotations

from dataclasses import dataclass, field

from .nlp_standard_words import STANDARD_WORDS, StandardWord


DEFAULT_SICHUAN_PHONETIC_VARIANTS = {
    "灰零": "回零",
    "夫位": "复位",
    "副位": "复位",
    "兰警": "报警",
    "兰紧": "报警",
    "确冷": "确认",
    "取笑": "取消",
}

DEFAULT_COLLOQUIAL_UNITS = {
    "公分": "厘米",
    "个厘米": "厘米",
    "厘米米": "厘米",
}


@dataclass(frozen=True)
class NormalizationStep:
    kind: str
    source: str
    target: str


@dataclass(frozen=True)
class NormalizationResult:
    original: str
    text: str
    steps: tuple[NormalizationStep, ...] = field(default_factory=tuple)


class NlpNormalizer:
    def __init__(
        self,
        *,
        enable_pinyin: bool = True,
        require_pinyin: bool = True,
        standard_words: dict[str, StandardWord] | None = None,
    ):
        self.enable_pinyin = enable_pinyin
        self.require_pinyin = require_pinyin
        self.standard_words = standard_words or STANDARD_WORDS
        self.homophones = {
            variant: word.standard
            for word in self.standard_words.values()
            for variant in word.homophones
        }
        self.dialect = {
            variant: word.standard
            for word in self.standard_words.values()
            for variant in word.sichuan_variants
        }
        self.dialect_phonetic = dict(DEFAULT_SICHUAN_PHONETIC_VARIANTS)
        self.units = dict(DEFAULT_COLLOQUIAL_UNITS)

    def normalize(self, text: str) -> NormalizationResult:
        original = str(text or "")
        current = original
        steps: list[NormalizationStep] = []

        for source, target in sorted(self.dialect.items(), key=lambda item: len(item[0]), reverse=True):
            if source in current:
                current = current.replace(source, target)
                steps.append(NormalizationStep("dialect", source, target))

        for source, target in sorted(self.dialect_phonetic.items(), key=lambda item: len(item[0]), reverse=True):
            if source in current:
                current = current.replace(source, target)
                steps.append(NormalizationStep("dialect_phonetic", source, target))

        for source, target in sorted(self.homophones.items(), key=lambda item: len(item[0]), reverse=True):
            if source in current:
                current = current.replace(source, target)
                steps.append(NormalizationStep("homophone", source, target))

        for source, target in sorted(self.units.items(), key=lambda item: len(item[0]), reverse=True):
            if source in current:
                current = current.replace(source, target)
                steps.append(NormalizationStep("unit", source, target))

        if self.enable_pinyin:
            current, pinyin_steps = self._pinyin_normalize(current)
            steps.extend(pinyin_steps)

        return NormalizationResult(original=original, text=current, steps=tuple(steps))

    def _pinyin_normalize(self, text: str) -> tuple[str, list[NormalizationStep]]:
        try:
            from pypinyin import Style, pinyin  # type: ignore
        except Exception as exc:
            if self.require_pinyin:
                raise RuntimeError("pypinyin is required for pinyin normalization") from exc
            return text, []
        pinyin_index = {word.pinyin: word.standard for word in self.standard_words.values()}
        compact = "".join(item[0] for item in pinyin(text, style=Style.NORMAL))
        if compact in pinyin_index and pinyin_index[compact] != text:
            return pinyin_index[compact], [NormalizationStep("pinyin", text, pinyin_index[compact])]
        return text, []
