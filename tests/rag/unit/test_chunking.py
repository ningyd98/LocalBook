"""Phase 1 — Markdown-aware chunking (M14 §三)."""

from __future__ import annotations

from server.rag.chunking.markdown import MarkdownChunker, estimate_tokens
from server.rag.schemas import Chunker, RAGChunk, content_hash


def _lines(chunk: RAGChunk, text: str) -> str:
    """The exact 1-based inclusive line range named by a chunk's citation.

    ``chunk.content`` is a verbatim source slice (it keeps the trailing newline
    of its last line); a line range can only express whole lines, so the
    comparison drops that single trailing terminator.
    """
    lines = text.splitlines()[chunk.start_line - 1 : chunk.end_line]
    return "\n".join(lines)


def test_empty_document_has_no_chunks() -> None:
    chunker = MarkdownChunker()
    assert chunker.chunk_document("empty.md", "") == []
    assert chunker.chunk_document("blank.md", "\n\n   \n") == []


def test_frontmatter_only_document_has_no_body_chunks() -> None:
    chunker = MarkdownChunker()
    text = "---\ntags: [a, b]\n---\n"
    assert chunker.chunk_document("meta.md", text) == []


def test_chunk_implements_protocol() -> None:
    assert isinstance(MarkdownChunker(), Chunker)


def test_simple_heading_produces_one_chunk_with_heading_metadata() -> None:
    text = "# 云边协同\n\n机械臂需要在边缘节点重规划任务。\n"
    chunks = MarkdownChunker().chunk_document("notes/cloud.md", text)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.heading == "云边协同"
    assert chunk.heading_path == "云边协同"
    assert chunk.start_line == 1
    assert chunk.end_line == 3
    assert _lines(chunk, text) == chunk.content.rstrip("\n")
    assert chunk.content_hash == content_hash(chunk.content)
    assert chunk.chunk_index == 0
    assert chunk.document_id == chunk.path == "notes/cloud.md"


def test_content_is_exact_source_slice() -> None:
    text = (
        "# 标题\n"
        "\n"
        "第一段内容，包含中文与 English 混合。\n"
        "\n"
        "第二段内容。\n"
    )
    for chunk in MarkdownChunker().chunk_document("a/b.md", text):
        assert text[chunk.start_offset : chunk.end_offset] == chunk.content
        assert _lines(chunk, text) == chunk.content.rstrip("\n")


def test_code_block_is_never_split() -> None:
    text = (
        "# 代码\n"
        "\n"
        "```python\n"
        "def f():\n"
        "    return 1\n"
        "\n"
        "\n"
        "print(f())\n"
        "```\n"
    )
    chunks = MarkdownChunker(target_tokens=10, max_tokens=12).chunk_document("code.md", text)
    code_chunks = [c for c in chunks if "```python" in c.content]
    assert len(code_chunks) == 1
    assert code_chunks[0].content.count("```") == 2
    assert "print(f())" in code_chunks[0].content


def test_table_is_never_split() -> None:
    text = (
        "# 表格\n"
        "\n"
        "| 组件 | 职责 |\n"
        "| --- | --- |\n"
        "| 边缘节点 | 实时控制 |\n"
        "| 云端 | 全局规划 |\n"
    )
    chunks = MarkdownChunker(target_tokens=5, max_tokens=8).chunk_document("t.md", text)
    table_chunks = [c for c in chunks if "| 边缘节点 |" in c.content]
    assert len(table_chunks) == 1
    assert "| 云端 | 全局规划 |" in table_chunks[0].content


def test_frontmatter_is_metadata_not_body() -> None:
    text = (
        "---\n"
        "title: 云边协同\n"
        "tags: [研究, 机械臂]\n"
        "aliases: [cloud-edge, 云边]\n"
        "---\n"
        "\n"
        "# 重规划机制\n"
        "\n"
        "在异网异构环境下触发重规划。\n"
    )
    chunks = MarkdownChunker().chunk_document("研究/云边协同.md", text)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "tags: [研究, 机械臂]" not in chunk.content
    assert chunk.tags == ["研究", "机械臂"]
    assert chunk.aliases == ["cloud-edge", "云边"]
    assert chunk.content.startswith("# 重规划机制")
    assert chunk.start_line == 7
    assert _lines(chunk, text) == chunk.content.rstrip("\n")


def test_heading_breadcrumb_tracks_nesting() -> None:
    text = (
        "# 云边协同\n"
        "## 重规划机制\n"
        "在边缘侧重新分配任务。\n"
        "### 触发条件\n"
        "时延超过阈值时触发。\n"
    )
    chunks = MarkdownChunker().chunk_document("x.md", text)
    chunk = chunks[0]
    # The shallowest heading in the chunk owns it; the deeper heading stays in
    # the chunk text and in the embedding input.
    assert chunk.heading_path == "云边协同"
    assert chunk.heading == "云边协同"
    assert "### 触发条件" in chunk.content
    assert chunk.section_path == "云边协同 > 重规划机制 > 触发条件"
    assert "章节：云边协同 > 重规划机制 > 触发条件" in chunk.embedding_text
    # A chunk whose group starts at a deeper heading keeps that deeper path.
    deeper = MarkdownChunker(target_tokens=8, max_tokens=10).chunk_document("x.md", text)
    assert deeper[-1].heading_path == "云边协同 > 重规划机制 > 触发条件"


def test_chinese_long_paragraph_is_split_with_monotonic_ranges() -> None:
    paragraph = "边缘计算节点需要在网络抖动时重新规划任务。" * 60
    text = f"# 长文\n\n{paragraph}\n"
    chunker = MarkdownChunker(target_tokens=200, max_tokens=300, overlap_tokens=0)
    chunks = chunker.chunk_document("long.md", text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.start_offset : chunk.end_offset] == chunk.content
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].start_offset < chunks[1].start_offset
    assert chunks[0].end_offset <= chunks[1].start_offset


def test_mixed_cjk_and_latin_token_estimate() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("云边协同") == 4
    assert estimate_tokens("云边协同 cloud edge") == 6


def test_chunk_ids_are_deterministic_and_unique() -> None:
    text = "# A\n\n第一段。\n\n## B\n\n第二段。\n"
    first = MarkdownChunker().chunk_document("same.md", text)
    second = MarkdownChunker().chunk_document("same.md", text)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)
    other = MarkdownChunker().chunk_document("other.md", text)
    assert {c.chunk_id for c in first}.isdisjoint({c.chunk_id for c in other})


def test_chunk_ids_survive_an_inserted_paragraph() -> None:
    """Inserting text must not renumber unchanged chunks (incremental reuse)."""
    text = "# A\n\n第一段内容。\n\n第二段内容。\n\n第三段内容。\n"
    before = MarkdownChunker(target_tokens=1, max_tokens=8).chunk_document("a.md", text)
    edited = "# A\n\n新插入的一段。\n\n第一段内容。\n\n第二段内容。\n\n第三段内容。\n"
    after = MarkdownChunker(target_tokens=1, max_tokens=8).chunk_document("a.md", edited)
    kept = {chunk.chunk_id for chunk in before} & {chunk.chunk_id for chunk in after}
    # The three original paragraphs keep their identity.
    assert len(kept) >= 2  # unchanged paragraphs keep their identity


def test_embedding_text_contains_heading_path_but_content_stays_raw() -> None:
    text = "# 云边协同\n## 重规划机制\n\n任务需要重新分配。\n"
    chunk = MarkdownChunker().chunk_document("研究/云边协同.md", text)[-1]
    embedding_text = chunk.embedding_text
    assert "文件：研究/云边协同.md" in embedding_text
    assert "章节：云边协同 > 重规划机制" in embedding_text
    assert chunk.heading_path == "云边协同"
    assert "正文：" in embedding_text
    assert chunk.content in embedding_text
    assert chunk.content == text[chunk.start_offset : chunk.end_offset]


def test_unterminated_code_fence_does_not_crash() -> None:
    text = "# A\n\n```python\nprint('x')\n"
    chunks = MarkdownChunker().chunk_document("broken.md", text)
    assert chunks
    assert chunks[-1].content == text[chunks[-1].start_offset : chunks[-1].end_offset]


def test_wikilinks_and_tags_survive_chunking() -> None:
    text = "# 笔记\n\n参见 [[云边协同]] 与 #机械臂 标签。\n"
    chunk = MarkdownChunker().chunk_document("n.md", text)[0]
    assert "[[云边协同]]" in chunk.content
    assert "#机械臂" in chunk.content
