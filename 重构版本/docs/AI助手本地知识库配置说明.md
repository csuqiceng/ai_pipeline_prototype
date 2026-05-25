# AI助手本地知识库配置说明

更新时间：2026-05-26

## 目的

`data/assistant_knowledge_base.json` 是 AI 助手的本地知识库，用于回答身份、功能、使用方式、安全边界、位置/流程/报警等说明类问题。

DeepSeek 问答会先检索这个知识库，只把命中的 3-5 条资料注入 prompt。没有 DeepSeek、DeepSeek 额度不足或网络异常时，高置信问题也可以由本地知识库直接回答。

## 配置文件

路径：

```text
data/assistant_knowledge_base.json
```

结构：

```json
{
  "version": "1.0",
  "description": "AI 助手本地知识库。",
  "entries": [
    {
      "id": "system_identity",
      "category": "identity",
      "keywords": ["你是谁", "你是什么", "介绍一下"],
      "content": "我是机械手自然语言交互系统的问答助手。",
      "priority": "high",
      "source": "system_default",
      "answer_style": "identity"
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `id` | 条目唯一标识，建议英文小写加下划线 |
| `category` | 分类，例如 `identity`、`usage`、`safety`、`position`、`flow`、`alarm` |
| `keywords` | 触发关键词/常见问法，越贴近现场说法越好 |
| `content` | 注入 DeepSeek 或本地直接回答的知识内容 |
| `priority` | `high`、`normal`、`low`，高优先级更容易排在前面 |
| `source` | 来源，例如 `system_default`、`manual_v1.1`、`site_maintenance` |
| `answer_style` | 回答风格提示，例如 `identity`、`usage`、`safety` |

## 维护规则

- 静态说明写进知识库，例如“系统能做什么”“AI 是否会直接控制机械手”。
- 动态数据不要写进知识库，例如“当前加载了 20 个模板”“当前流程草案是什么”。这些由代码实时注入。
- 一条知识只表达一个主题，避免把身份、报警、流程混在同一条。
- 新增现场说法时优先追加 `keywords`，不要改动已有 `id`。
- 涉及安全边界的条目建议设置 `priority: "high"`。

## DeepSeek 使用边界

DeepSeek 只允许基于本地知识库和当前动态上下文回答问题或生成草案，不直接控制机械手。所有实际执行仍必须经过本地白名单、安全预检和人工确认。
