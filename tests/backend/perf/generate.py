"""Deterministic Vault generator for the M4 10k-note benchmark.

Each generated note (~1-2 KB) contains: Chinese + English title and body,
a handful of tags/properties, and wikilinks (some resolved, some broken).
Generation is seed-driven and byte-stable so runs are comparable.
"""

from __future__ import annotations

import random
from pathlib import Path

_EN_POOL = [
    "alpha beta gamma delta epsilon zeta eta theta iota kappa",
    "lambda mu nu xi omicron pi rho sigma tau upsilon",
    "phi chi psi omega hello world searchable english term",
    "quick brown fox jumps over lazy dog notebook index fts",
    "unicode61 tokenizer bm25 ranking sqlite derived vault",
    "watcher incremental rebuild transaction rollback isolation",
    "frontmatter properties tags casefold snippet plaintext window",
    "backlinks outgoing wikilink embed heading block alias broken",
    "note taking markdown local first privacy offline desktop",
    "performance benchmark latency percentile tracemalloc memory",
]
_ZH_WORDS = [
    "你好世界", "机器学习", "知识管理", "工作笔记", "项目计划", "会议纪要",
    "读书心得", "旅行日记", "食谱收藏", "代码片段", "技术方案", "产品设计",
    "用户研究", "数据分析", "本地优先", "全文检索", "中文分词", "搜索目标",
    "重要标签", "未完成任务",
]
_TAGS = ["工作", "学习", "生活", "灵感", "项目", "归档", "阅读", "旅行", "健康", "重要"]


def _frontmatter(rand: random.Random, tags: list[str]) -> str:
    lines = ["---", "title: 标题示例"]
    lines.append("tags: " + ", ".join(tags))
    lines.append("related:\n  - 项目A\n  - 项目B")
    lines.append(f"count: {rand.randint(1, 99)}")
    lines.append("enabled: true")
    lines.append("---")
    return "\n".join(lines)


def _body(rand: random.Random, target_hint: str | None) -> str:
    parts: list[str] = []
    zh = " ".join(rand.sample(_ZH_WORDS, rand.randint(2, 5)))
    en = rand.choice(_EN_POOL)
    parts.append(f"# {zh} {en.split()[0]}")
    parts.append(f"{zh} 正文混合 {en} 与中文短语用于{target_hint or '检索'}。")
    for _ in range(rand.randint(2, 4)):
        sub_zh = rand.choice(_ZH_WORDS)
        sub_en = " ".join(rand.sample(en.split(), min(4, len(en.split()))))
        parts.append(f"{sub_zh} — {sub_en}，Emoji 😀 混合保留。")
    return "\n\n".join(parts)


def generate_vault(
    root: Path,
    *,
    count: int = 10_000,
    seed: int = 20260906,
    wikilink_probability: float = 0.6,
    broken_probability: float = 0.15,
) -> list[str]:
    """Write ``count`` markdown notes under ``root`` and return their paths.

    Deterministic for a fixed seed; note i links mostly to notes close in the
    sequence so resolution is realistic.  Some links deliberately target
    notes that never exist (broken) — M3/M4 must not fail on them.
    """
    rand = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    shard = max(1, count // 20)
    for i in range(count):
        shard_dir = root / f"shard-{i % shard:03d}"
        shard_dir.mkdir(exist_ok=True)
        mixed_zh = "".join(rand.sample(_ZH_WORDS, 2))
        path = f"shard-{i % shard:03d}/note-{i:05d}-{mixed_zh}.md"
        tags = rand.sample(_TAGS, rand.randint(0, 3))
        fm = _frontmatter(rand, tags)

        links: list[str] = []
        if rand.random() < wikilink_probability:
            for _ in range(rand.randint(1, 3)):
                if rand.random() < broken_probability:
                    links.append(f"[[Missing Note {rand.randint(0, 999_999)}]]")
                else:
                    target = rand.randint(max(0, i - 20), min(count - 1, i + 20))
                    links.append(f"[[note-{target:05d}]]")
        body = _body(rand, "全库搜索")
        link_block = ("\n\n" + "\n".join(links)) if links else ""
        data = (fm + "\n" + body + link_block + "\n").encode("utf-8")
        (root / path).write_bytes(data)
        paths.append(path)
    return paths


__all__ = ["generate_vault"]
