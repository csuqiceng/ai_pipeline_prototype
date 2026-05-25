"""Configurable local knowledge base for assistant chat answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "assistant_knowledge_base.json"

PRIORITY_SCORE = {
    "high": 30,
    "normal": 10,
    "low": 0,
}


@dataclass(frozen=True)
class KnowledgeEntry:
    entry_id: str
    category: str
    keywords: tuple[str, ...]
    content: str
    priority: str = "normal"
    source: str = "system"
    answer_style: str = "plain"


@dataclass(frozen=True)
class KnowledgeMatch:
    entry: KnowledgeEntry
    score: int
    matched_keywords: tuple[str, ...]

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id

    @property
    def category(self) -> str:
        return self.entry.category

    @property
    def content(self) -> str:
        return self.entry.content


class AssistantKnowledgeBase:
    def __init__(self, entries: Iterable[KnowledgeEntry] = ()) -> None:
        self.entries = tuple(entries)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AssistantKnowledgeBase":
        source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        if not source.exists():
            return cls(default_entries())
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            return cls(default_entries())
        raw_entries = payload.get("entries", payload) if isinstance(payload, dict) else payload
        if isinstance(raw_entries, dict):
            raw_entries = raw_entries.values()
        entries: list[KnowledgeEntry] = []
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                entry = _entry_from_dict(item)
                if entry is not None:
                    entries.append(entry)
        return cls(entries or default_entries())

    def search(self, text: str, *, limit: int = 5, categories: Iterable[str] | None = None) -> list[KnowledgeMatch]:
        compact = _compact(text)
        if not compact:
            return []
        allowed = {str(item).strip() for item in categories or () if str(item).strip()}
        matches: list[KnowledgeMatch] = []
        for entry in self.entries:
            if allowed and entry.category not in allowed:
                continue
            matched = tuple(keyword for keyword in entry.keywords if _compact(keyword) and _compact(keyword) in compact)
            if not matched:
                continue
            score = PRIORITY_SCORE.get(entry.priority, PRIORITY_SCORE["normal"])
            score += sum(len(_compact(keyword)) for keyword in matched)
            score += 8 * len(matched)
            matches.append(KnowledgeMatch(entry=entry, score=score, matched_keywords=matched))
        matches.sort(key=lambda item: (-item.score, item.entry.entry_id))
        return matches[: max(1, int(limit))]

    def best_answer(self, text: str, *, min_score: int = 20) -> str:
        matches = self.search(text, limit=1)
        if not matches or matches[0].score < int(min_score):
            return ""
        return matches[0].content.strip()

    def prompt_context(self, text: str, *, limit: int = 5) -> str:
        matches = self.search(text, limit=limit)
        if not matches:
            return ""
        lines = ["本地知识库命中资料："]
        for match in matches:
            entry = match.entry
            keywords = "、".join(match.matched_keywords)
            lines.append(
                f"- [{entry.category}/{entry.entry_id}/source={entry.source}/style={entry.answer_style}] "
                f"命中词：{keywords}。{entry.content}"
            )
        return "\n".join(lines)


def default_entries() -> tuple[KnowledgeEntry, ...]:
    return (
        KnowledgeEntry(
            "system_identity",
            "identity",
            ("你是谁", "你是什么", "介绍一下", "问答助手"),
            "我是机械手自然语言交互系统的问答助手，可以帮助理解系统能力、查询状态和位置资料，并协助创建流程草案。",
            "high",
            "system_default",
            "identity",
        ),
        KnowledgeEntry(
            "system_capabilities",
            "usage",
            ("能做什么", "有什么功能", "功能", "能力", "怎么用", "帮助"),
            "当前系统支持自然语言理解、状态/看板查询、位置示教与查询、流程草案创建、多轮澄清、安全预检、确认后执行和报警建议。",
            "high",
            "system_default",
            "usage",
        ),
        KnowledgeEntry(
            "safety_boundary",
            "safety",
            ("AI控制", "DeepSeek会操作吗", "能不能直接控制", "安全边界", "直接执行"),
            "DeepSeek 和问答助手只负责理解问题、检索资料或生成草案，不直接控制机械手。所有执行都必须经过本地白名单、安全预检和人工确认。",
            "high",
            "system_default",
            "safety",
        ),
        KnowledgeEntry(
            "position_query",
            "position",
            ("位置参数", "位置坐标", "位置A", "home位", "坐标是什么", "参数是什么"),
            "查询位置参数时可以问“位置A的参数是什么样的”或“home位坐标是多少”。系统会优先从位置库和当前草案中回答。",
            "normal",
            "system_default",
            "usage",
        ),
        KnowledgeEntry(
            "template_query",
            "template",
            ("有哪些模板", "模板命令", "默认命令", "有哪些指令", "命令列表"),
            "可以询问当前加载的模板命令，例如“有哪些默认命令”“位置A的命令怎么说”。系统会结合模板表和当前配置回答。",
            "normal",
            "system_default",
            "usage",
        ),
        KnowledgeEntry(
            "flow_draft_usage",
            "flow",
            ("流程草案", "创建流程", "保存并执行", "这个流程", "流程是什么样"),
            "复杂口语流程会先生成草案，不会自动保存或执行。用户可查看草案步骤，再说“确认保存”或“保存并执行”。",
            "normal",
            "system_default",
            "usage",
        ),
        KnowledgeEntry(
            "alarm_usage",
            "alarm",
            ("报警怎么办", "报警处理", "报警建议", "报警怎么恢复"),
            "报警时应先确认现场安全，再查看报警码、报警详情和建议；排除原因后再执行报警确认或复位。",
            "normal",
            "system_default",
            "safety",
        ),
        KnowledgeEntry(
            "tts_usage",
            "voice",
            ("语音播报", "TTS", "为什么不播报", "播报开关"),
            "语音播报可以在用户页开关中启用或关闭。未登录时系统不会播报，避免登录前产生无关语音提示。",
            "normal",
            "system_default",
            "usage",
        ),
    )


def save_default_knowledge_base(path: str | Path, entries: Iterable[KnowledgeEntry] | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "description": "AI 助手本地知识库。用于闲聊、功能说明、使用方式和安全边界问答，可由现场维护。",
        "entries": [
            {
                "id": entry.entry_id,
                "category": entry.category,
                "keywords": list(entry.keywords),
                "content": entry.content,
                "priority": entry.priority,
                "source": entry.source,
                "answer_style": entry.answer_style,
            }
            for entry in (entries or default_entries())
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _entry_from_dict(payload: dict[str, Any]) -> KnowledgeEntry | None:
    entry_id = str(payload.get("id") or payload.get("entry_id") or "").strip()
    category = str(payload.get("category") or "").strip()
    content = str(payload.get("content") or "").strip()
    keywords = tuple(str(item).strip() for item in payload.get("keywords", ()) if str(item).strip())
    if not entry_id or not category or not content or not keywords:
        return None
    return KnowledgeEntry(
        entry_id=entry_id,
        category=category,
        keywords=keywords,
        content=content,
        priority=str(payload.get("priority") or "normal").strip() or "normal",
        source=str(payload.get("source") or "system").strip() or "system",
        answer_style=str(payload.get("answer_style") or "plain").strip() or "plain",
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()
