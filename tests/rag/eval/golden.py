"""Small fixed Golden Dataset for RAG evaluation (M14 §二十).

Purpose: make retrieval quality *measurable and regression-testable*. Every
change to chunking, the fusion weights, the embedding provider or a reranker can
be scored against these questions and compared with the previous run instead of
being judged by "it returned something".

The dataset is deliberately small (15 documents, 14 questions) and lives in code
so it is reviewed together with the tests that consume it. It covers:

- Chinese and English, and a mixed-language question;
- a **semantic-only** query whose words do not appear in the target note;
- a **lexical-only** query whose distinctive term the vector path ranks low;
- a **queried in English, answered in a Chinese note** case;
- confusable notes (two notes about neighbouring topics) that a weak retriever
  would mix up;
- wikilink content so link/graph retrievers can be added later.

Ranking metrics use document-level recall (the note the answer lives in) because
that is what a citation points at, plus MRR over the first relevant document.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

DOCUMENTS: dict[str, str] = {
    "Thesis/cloud-edge.md": """---
tags: [研究, 云边协同]
aliases: [cloud-edge, 云边]
---

# 云边协同机械臂系统

## 快慢双系统

云端负责全局规划，边缘节点负责实时控制，两者构成快慢双系统：
慢系统周期性下发任务图，快系统在本地以毫秒级周期执行并反馈。

## 重规划机制

当网络时延超过阈值或任务失败率升高时，边缘节点触发重规划，
在本地重新分配剩余任务，不再等待云端确认。

参见 [[Obsidian插件兼容]] 与 [[RAG设计]]。
""",
    "Thesis/edge-scheduling.md": """# 边缘任务调度

## 异网异构环境

在异网异构环境下，带宽与算力波动剧烈，调度器需要动态调整分片策略。
这与云边协同的侧重点不同：本文关注调度算法本身，而不是系统分层。
""",
    "Thesis/cloud-only-planning.md": """# 云端全局规划

云端拥有完整视图，适合做长周期、计算密集的全局规划与仿真。
本文不讨论边缘侧的实时控制。
""",
    "AI/RAG设计.md": """---
tags: [AI, RAG]
aliases: [检索增强]
---

# 本地 RAG 设计

## 混合检索

词法检索与向量检索各自有短板，本项目用 RRF 融合两者的排名，
而不是把 BM25 分数和余弦相似度直接相加。

## 引用校验

模型只能引用检索结果里存在的 source id，否则引用会被删除。
""",
    "AI/embedding-notes.md": """# 向量与重排笔记

## 向量数据库选型

Qdrant 与 Milvus 都需要独立服务；本地优先应用更适合把向量存在 SQLite 里。
维度与模型必须与索引记录一致，改模型要重新嵌入。

## 重排

交叉编码器重排能提升前几条的精度，但会引入额外延迟，因此是可选项。
""",
    "AI/agent-ideas.md": """# 本地 AI Agent 思路

## 受控写入

Agent 不应直接修改笔记：先由模型生成结构化动作，经策略判定与差异审阅后，
再由程序写盘，并且每一步都有历史记录可以撤销。
""",
    "AI/local-model-ops.md": """# 本地模型部署笔记

本地 4B 量级模型推理一次约几秒，长上下文会显著变慢；
提示词越短、schema 越强，输出越稳定。
""",
    "LocalBook设计.md": """# LocalBook 设计

## 插件兼容

Markdown 是唯一事实源，核心能力（Vault、索引、检索）不依赖 Web UI，
因此可以复用到 Obsidian 兼容宿主里。

## 派生数据

SQLite 索引与向量索引都是派生数据，删除后可以完整重建。
""",
    "Obsidian插件兼容.md": """# Obsidian 插件兼容

## Plugin Runtime

插件运行需要宿主暴露 Vault 抽象与 metadata 抽象；
我们不实现插件运行时，只保证核心能力可以复用。
""",
    "Gardening/tomato.md": """# 番茄种植笔记

阳台番茄需要每天六小时以上直射光，结果期注意补钾肥。
与软件笔记无关，仅用于检验检索是否会误命中。
""",
    "Gardening/compost.md": """# 堆肥记录

厨余与干草按三比一混合，每两周翻堆一次，温度保持在五十度左右。
""",
    "Notes/wiki-links.md": """# 链接笔记

这里引用 [[云边协同机械臂系统]]，并嵌入 ![[Gardening/tomato.md]]。
链接解析与标题层级有关。
""",
    "Notes/meeting-2026.md": """# 2026 年会议记录

讨论了本地 AI 助手的范围：先做只读检索，再考虑受控写入。
""",
    "English/vector-search.md": """# Vector Search Notes

## Embeddings

An embedding model maps text to a dense vector. Storing normalised vectors
makes cosine similarity a plain dot product, which keeps the scan cheap.

## Hybrid ranking

Rank fusion is preferred over score normalisation because BM25 and cosine
similarity are not comparable scales.
""",
    "English/obsidian-plugins.md": """# Obsidian Plugin Compatibility

A plugin host needs a stable vault abstraction and a metadata API; the plugin
runtime itself is out of scope for the first version.
""",
}


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldenQuestion:
    """One graded question."""

    question: str
    expected_paths: list[str]
    # ``semantic_only``: the answer's words do not appear in the target note —
    # lexical retrieval cannot find it, vector retrieval can.
    semantic_only: bool = False
    # ``lexical_only``: an exact term that the vector path may rank lower.
    lexical_only: bool = False
    notes: str = ""


GOLDEN: tuple[GoldenQuestion, ...] = (
    GoldenQuestion(
        question="哪篇笔记讨论了快慢双系统？",
        expected_paths=["Thesis/cloud-edge.md"],
        lexical_only=True,
        notes="Exact term 快慢双系统 appears only in the cloud-edge note.",
    ),
    GoldenQuestion(
        question="我的笔记里有没有讨论 Obsidian 插件兼容？",
        expected_paths=["LocalBook设计.md", "Obsidian插件兼容.md", "English/obsidian-plugins.md"],
        notes="Three notes discuss plugin compatibility at different levels.",
    ),
    GoldenQuestion(
        question="我对 Obsidian 插件运行时做过哪些设计？",
        expected_paths=["Obsidian插件兼容.md", "LocalBook设计.md", "English/obsidian-plugins.md"],
    ),
    GoldenQuestion(
        question="总结我关于本地 AI Agent 的思路。",
        expected_paths=["AI/agent-ideas.md"],
    ),
    GoldenQuestion(
        question="我的哪些笔记讨论过 embedding、rerank 或向量数据库？",
        expected_paths=["AI/embedding-notes.md", "English/vector-search.md"],
    ),
    GoldenQuestion(
        question="云边协同为什么需要重规划？",
        expected_paths=["Thesis/cloud-edge.md"],
        lexical_only=True,
    ),
    GoldenQuestion(
        question="检索为什么要用 RRF 融合而不是分数相加？",
        expected_paths=["AI/RAG设计.md", "English/vector-search.md"],
    ),
    GoldenQuestion(
        question="如何防止模型编造不存在的引用？",
        expected_paths=["AI/RAG设计.md"],
        semantic_only=True,
        notes="The target note says 引用校验/编造 source id without using 防止/编造 words.",
    ),
    GoldenQuestion(
        question="Which note explains why a plugin host needs a vault abstraction?",
        expected_paths=["English/obsidian-plugins.md", "Obsidian插件兼容.md", "LocalBook设计.md"],
        notes="English query answering against English and Chinese notes.",
    ),
    GoldenQuestion(
        question="为什么向量索引要能重建？",
        expected_paths=["LocalBook设计.md"],
        semantic_only=True,
    ),
    GoldenQuestion(
        question="本地模型推理速度对提示词长度有什么要求？",
        expected_paths=["AI/local-model-ops.md"],
    ),
    GoldenQuestion(
        question="为什么要让 Agent 先生成结构化动作再写入？",
        expected_paths=["AI/agent-ideas.md"],
        semantic_only=True,
    ),
    GoldenQuestion(
        question="番茄种植需要多少光照？",
        expected_paths=["Gardening/tomato.md"],
        lexical_only=True,
        notes="Confusable-topic control: gardening must not be crowded out.",
    ),
    GoldenQuestion(
        question="哪篇笔记记录了异网异构下的调度问题？",
        expected_paths=["Thesis/edge-scheduling.md"],
        lexical_only=True,
    ),
)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class QuestionOutcome:
    question: str
    retrieved_paths: list[str] = field(default_factory=list)
    expected_paths: list[str] = field(default_factory=list)
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    reciprocal_rank: float = 0.0
    mode: str = "hybrid"

    @property
    def hit(self) -> bool:
        return self.reciprocal_rank > 0.0


@dataclass(slots=True)
class EvaluationReport:
    """Aggregate metrics for one retrieval mode."""

    mode: str
    outcomes: list[QuestionOutcome]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def recall_at_5(self) -> float:
        return self._mean("recall_at_5")

    @property
    def recall_at_10(self) -> float:
        return self._mean("recall_at_10")

    @property
    def mrr(self) -> float:
        return self._mean("reciprocal_rank")

    @property
    def misses(self) -> list[str]:
        return [outcome.question for outcome in self.outcomes if not outcome.hit]

    def _mean(self, attribute: str) -> float:
        if not self.outcomes:
            return 0.0
        return sum(getattr(item, attribute) for item in self.outcomes) / len(self.outcomes)

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "questions": self.total,
            "recall@5": round(self.recall_at_5, 4),
            "recall@10": round(self.recall_at_10, 4),
            "mrr": round(self.mrr, 4),
            "misses": self.misses,
        }

    def format(self) -> str:
        return (
            f"mode={self.mode} n={self.total} "
            f"recall@5={self.recall_at_5:.3f} "
            f"recall@10={self.recall_at_10:.3f} "
            f"mrr={self.mrr:.3f}"
            + (f" misses={len(self.misses)}" if self.misses else "")
        )


def score(
    question: GoldenQuestion,
    retrieved_paths: list[str],
    *,
    mode: str = "hybrid",
) -> QuestionOutcome:
    """Compute one question's recall/MRR from the ranked document list."""
    expected = list(question.expected_paths)
    unique: list[str] = []
    for path in retrieved_paths:
        if path not in unique:
            unique.append(path)
    outcome = QuestionOutcome(
        question=question.question,
        retrieved_paths=unique,
        expected_paths=expected,
        mode=mode,
    )
    if not expected:
        return outcome
    for path in unique[:5]:
        if path in expected:
            outcome.recall_at_5 = 1.0
    for path in unique[:10]:
        if path in expected:
            outcome.recall_at_10 = 1.0
    for rank, path in enumerate(unique[:10], start=1):
        if path in expected:
            outcome.reciprocal_rank = 1.0 / rank
            break
    return outcome


__all__ = [
    "DOCUMENTS",
    "GOLDEN",
    "EvaluationReport",
    "GoldenQuestion",
    "QuestionOutcome",
    "score",
]
