"""RAG test fixtures: a throwaway Vault, an in-memory-ish SQLite store and fakes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from server.index.db import IndexDatabase
from server.rag.chunking.markdown import MarkdownChunker
from server.rag.embeddings.base import HashEmbeddingProvider, MockEmbeddingProvider
from server.rag.embeddings.runner import EmbeddingRunner
from server.rag.index_service import RagIndexService
from server.rag.vector.sqlite import SqliteVectorStore
from server.vault.service import VaultService

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
VAULT_FIXTURES_DIR = FIXTURES_DIR / "vault"


@pytest.fixture
def rag_vault(tmp_path: Path) -> VaultService:
    """A writable Vault used only by RAG tests (never a real user Vault)."""
    root = tmp_path / "vault"
    root.mkdir(parents=True, exist_ok=True)
    service = VaultService(root, watcher_enabled=False)
    service.initialize(start_watcher=False)
    return service


@pytest.fixture
def index_db(tmp_path: Path) -> Iterator[IndexDatabase]:
    """A fresh derived index database (the RAG tables live beside M4's)."""
    derived = tmp_path / "vault" / ".localnote"
    derived.mkdir(parents=True, exist_ok=True)
    db = IndexDatabase(derived / "index.db")
    db.open()
    yield db
    db.close()


@pytest.fixture
def store(index_db: IndexDatabase) -> Iterator[SqliteVectorStore]:
    store = SqliteVectorStore(index_db)
    store.ensure_ready()
    yield store
    store.close()


@pytest.fixture
def chunker() -> MarkdownChunker:
    return MarkdownChunker(target_tokens=120, max_tokens=200, overlap_tokens=20)


def make_index_service(
    vault: VaultService,
    store: SqliteVectorStore,
    *,
    provider=None,
    chunker: MarkdownChunker | None = None,
    embed_batch_size: int = 4,
) -> RagIndexService:
    """Build a RagIndexService with a deterministic provider by default."""
    resolved = provider or MockEmbeddingProvider(dimension=32)
    return RagIndexService(
        vault,
        store,
        chunker=chunker,
        embedding_provider=resolved,
        embedding_runner=EmbeddingRunner(resolved),
        embed_batch_size=embed_batch_size,
    )


def write_note(vault: VaultService, path: str, body: str) -> str:
    """Create or replace a note through the Vault façade; returns its sha256."""
    data = body.encode("utf-8")
    existing = None
    try:
        _, existing = vault.read_bytes(path)
    except Exception:
        existing = None
    if existing is None:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent:
            _ensure_dirs(vault, parent)
        vault.create_bytes(path, data)
    else:
        vault.write_bytes(path, data, expected_sha256=existing)
    _, digest = vault.read_bytes(path)
    return digest


def _ensure_dirs(vault: VaultService, parent: str) -> None:
    parts = parent.split("/")
    for index in range(1, len(parts) + 1):
        segment = "/".join(parts[:index])
        try:
            vault.create_directory(segment)
        except Exception:
            continue


@pytest.fixture
def hash_provider() -> HashEmbeddingProvider:
    return HashEmbeddingProvider(dimension=64)


__all__ = [
    "FIXTURES_DIR",
    "VAULT_FIXTURES_DIR",
    "make_index_service",
    "write_note",
]
