"""M5 derived/rebuild guarantee (PLAN-M5 §1.2/§9.1 rows 10–11; task M5-11).

The graph is a *derived, query-time* projection: deleting ``.localnote`` and
rebuilding reproduces identical graph DTOs, Markdown/attachment hashes never
change, and graph requests never write to the Vault or SQLite.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

from server.graph.service import GraphService
from server.index.service import DerivedIndexService
from server.vault.service import VaultService


def _file_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".localnote" not in path.parts:
            result[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def _graph_digest(graph: GraphService) -> dict[str, object]:
    """Deterministic graph DTOs (timestamps excluded — they vary per call)."""
    global_payload = graph.global_graph(limit=2000).model_dump(mode="json")
    local_payload = graph.local_graph("notes/a.md", depth=2).model_dump(mode="json")
    tag_payload = graph.tag_graph("工作").model_dump(mode="json")
    for payload in (global_payload, local_payload, tag_payload):
        payload.pop("generated_at", None)
    return {
        "global": global_payload,
        "local_a": local_payload,
        "tag_work": tag_payload,
    }


def _rows(index: DerivedIndexService) -> tuple[int, int, int, int, int]:
    with index._lock:
        db = index._db
        assert db is not None
        return tuple(
            int(db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"])
            for table in ("notes", "tags", "links", "backlinks", "properties")
        )


def test_graph_requests_never_write_note_bytes_or_sqlite(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    graph = GraphService(index)
    hashes_before = _file_hashes(vault_fixture_copy)
    rows_before = _rows(index)

    for _ in range(3):
        graph.global_graph(limit=2000)
        graph.local_graph("combo.md", depth=2)
        graph.tag_graph("工作")
        graph.global_graph(tag="alpha", include_broken=False)

    assert _rows(index) == rows_before
    assert _file_hashes(vault_fixture_copy) == hashes_before


def test_delete_localnote_rebuild_in_process_is_identical(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    graph = GraphService(index)
    hashes_before = _file_hashes(vault_fixture_copy)
    digest_before = _graph_digest(graph)

    shutil.rmtree(vault_fixture_copy / ".localnote")
    result = index.rebuild()
    assert result.ready is True

    digest_after = _graph_digest(graph)
    assert digest_after == digest_before
    assert _file_hashes(vault_fixture_copy) == hashes_before
    index.close()


def test_restart_rebuild_reproduces_graph(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    hashes_before = _file_hashes(vault_fixture_copy)

    first_service = vault_service_factory(vault_fixture_copy)
    first_index = index_service_factory(first_service)
    digest_before = _graph_digest(GraphService(first_index))
    first_index.close()

    shutil.rmtree(vault_fixture_copy / ".localnote")

    second_service = vault_service_factory(vault_fixture_copy)
    second_index = index_service_factory(second_service)
    digest_after = _graph_digest(GraphService(second_index))
    assert digest_after == digest_before
    assert _file_hashes(vault_fixture_copy) == hashes_before
    second_index.close()
