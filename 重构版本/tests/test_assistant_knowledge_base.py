import json
from pathlib import Path

from robot_modbus_lite.assistant_knowledge_base import AssistantKnowledgeBase, KnowledgeEntry


def test_assistant_knowledge_base_retrieves_relevant_entries_by_alias(tmp_path: Path):
    path = tmp_path / "assistant_knowledge_base.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "entries": [
                    {
                        "id": "identity",
                        "category": "identity",
                        "keywords": ["你是谁", "能做什么", "功能"],
                        "content": "我是机械手自然语言交互系统的问答助手。",
                        "priority": "high",
                        "source": "test",
                    },
                    {
                        "id": "alarm",
                        "category": "alarm",
                        "keywords": ["报警", "报警怎么办"],
                        "content": "报警时请先确认现场安全，再查看报警建议。",
                        "priority": "normal",
                        "source": "test",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    base = AssistantKnowledgeBase.load(path)
    results = base.search("你是谁，能做什么")

    assert [item.entry_id for item in results] == ["identity"]
    assert results[0].score > 0


def test_assistant_knowledge_base_prioritizes_more_specific_and_high_priority_entries():
    base = AssistantKnowledgeBase(
        entries=(
            KnowledgeEntry(
                entry_id="generic",
                category="usage",
                keywords=("模板",),
                content="可以查询模板。",
                priority="normal",
                source="test",
            ),
            KnowledgeEntry(
                entry_id="specific",
                category="usage",
                keywords=("有哪些模板", "模板命令"),
                content="可以列出当前模板命令示例。",
                priority="high",
                source="test",
            ),
        )
    )

    results = base.search("有哪些模板命令")

    assert [item.entry_id for item in results] == ["specific", "generic"]


def test_assistant_knowledge_base_formats_prompt_context_with_sources():
    base = AssistantKnowledgeBase(
        entries=(
            KnowledgeEntry(
                entry_id="safety",
                category="safety",
                keywords=("AI控制",),
                content="AI 不直接控制机械手。",
                priority="high",
                source="系统内置",
            ),
        )
    )

    context = base.prompt_context("DeepSeek会直接AI控制吗")

    assert "本地知识库命中资料" in context
    assert "safety" in context
    assert "系统内置" in context
    assert "AI 不直接控制机械手。" in context


def test_assistant_knowledge_base_best_answer_requires_min_score():
    base = AssistantKnowledgeBase(
        entries=(
            KnowledgeEntry(
                entry_id="alarm",
                category="alarm",
                keywords=("报警",),
                content="报警处理说明。",
                priority="low",
                source="test",
            ),
        )
    )

    assert base.best_answer("报警灯亮了", min_score=20) == ""
    assert base.best_answer("报警灯亮了", min_score=1) == "报警处理说明。"


def test_assistant_knowledge_base_empty_context_when_no_match():
    base = AssistantKnowledgeBase(entries=())

    assert base.best_answer("完全无关", min_score=1) == ""
    assert base.prompt_context("完全无关") == ""


def test_assistant_knowledge_base_category_filter_limits_results():
    base = AssistantKnowledgeBase(
        entries=(
            KnowledgeEntry("usage", "usage", ("帮助",), "帮助说明。", "high", "test"),
            KnowledgeEntry("alarm", "alarm", ("帮助",), "报警帮助。", "high", "test"),
        )
    )

    results = base.search("帮助", categories=("alarm",))

    assert [item.entry_id for item in results] == ["alarm"]


def test_assistant_knowledge_base_default_entries_include_json_topics():
    base = AssistantKnowledgeBase()
    entries = {item.entry_id for item in __import__("robot_modbus_lite.assistant_knowledge_base", fromlist=["default_entries"]).default_entries()}

    assert "template_query" in entries
    assert "tts_usage" in entries
    assert "agent_architecture_boundary" in entries
    assert "l1_safety_gate" in entries
    assert "l2_motion_preview" in entries
    assert "confirmation_gate" in entries
    assert "execution_result_context" in entries


def test_assistant_knowledge_base_answers_agent_boundary_and_l2_locally():
    base = AssistantKnowledgeBase.load()

    agent_answer = base.best_answer("现在所有都走agent吗", min_score=40)
    l2_answer = base.best_answer("L2是什么", min_score=40)

    assert "统一 Agent 总入口" in agent_answer
    assert "DeepSeek 只做受限兜底解释" in agent_answer
    assert "运动规划预演" in l2_answer
    assert "人工确认" in l2_answer
