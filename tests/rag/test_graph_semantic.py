from __future__ import annotations

import struct

from server.graph.service import GraphService, note_id
from server.index.schemas import GraphNoteRow, GraphSnapshot
from server.rag.schemas import RAGIndexState


class _Provider:
    model = "test-model"
    dimension = 2
    is_degraded = False


class _AutoDimensionProvider(_Provider):
    dimension = 0


class _Index:
    def assert_ready(self):
        pass

    def graph_snapshot(self):
        return GraphSnapshot(
            notes=tuple(
                GraphNoteRow(path=path, title=path, basename=path)
                for path in ("a.md", "b.md", "c.md")
            )
        )

    def note_fingerprints(self, paths):
        return {p: f"sha-{p}" for p in paths if p in {"a.md", "b.md", "c.md"}}


class _Store:
    def __init__(self, vectors, provider_name="_Provider", compatible=True, status="ready"):
        self.vectors = vectors
        self.provider_name = provider_name
        self.compatible = compatible
        self.status = status

    def state(self):
        return RAGIndexState(
            embedding_provider=self.provider_name,
            embedding_model="test-model",
            embedding_dimension=2,
            embedding_version="v1",
            chunk_count=3,
            status=self.status,
        )

    def chunk_count(self):
        return 3

    def embedded_count(self):
        return 3

    @property
    def generation(self):
        return 1

    def semantic_embeddings_compatible(self, **kwargs):
        return self.compatible

    def semantic_note_vectors(self, paths, **kwargs):
        return [
            dict(
                path=path,
                sha256=f"sha-{path}",
                chunk_count=1,
                embedded_count=1,
                dimension=2,
                model="test-model",
                embedding_version="v1",
                vector=struct.pack("<ff", *self.vectors[path]),
            )
            for path in paths
            if path in self.vectors
        ]


def test_semantic_projection_emits_stable_undirected_edges_for_mutual_neighbors():
    service = GraphService(
        _Index(),
        vector_store=_Store(
            {
                "a.md": (1.0, 0.0),
                "b.md": (0.99, 0.01),
                "c.md": (0.0, 1.0),
            }
        ),
        embedding_provider=_Provider(),
        embedding_version="v1",
    )

    edges, status, covered = service._semantic_projection({"a.md", "b.md"}, include=True)

    assert status == "ready"
    assert covered == 2
    assert len(edges) == 1
    edge = edges[0]
    assert edge.id == "semantic:%61%2E%6D%64#%62%2E%6D%64"
    assert edge.source == note_id("a.md")
    assert edge.target == note_id("b.md")
    assert edge.directed is False
    assert edge.score is not None and edge.score >= 0.84


def test_semantic_projection_rejects_outdated_document_fingerprints():
    store = _Store({"a.md": (1.0, 0.0), "b.md": (0.99, 0.01)})
    original = store.semantic_note_vectors
    store.semantic_note_vectors = lambda paths, **kwargs: [
        (row | {"sha256": "old"}) if row["path"] == "b.md" else row
        for row in original(paths, **kwargs)
    ]
    edges, status, covered = GraphService(
        _Index(), vector_store=store, embedding_provider=_Provider(), embedding_version="v1"
    )._semantic_projection({"a.md", "b.md"}, include=True)

    assert edges == []  # The sole valid note has no candidate partner.
    assert status == "ready"
    assert covered == 1


def test_semantic_projection_reports_limited_without_reading_vectors():
    store = _Store({})
    edges, status, covered = GraphService(
        _Index(), vector_store=store, embedding_provider=_Provider(), embedding_version="v1"
    )._semantic_projection({str(i) for i in range(501)}, include=True)
    assert edges == []
    assert status == "limited"
    assert covered == 0


def test_semantic_projection_rejects_mixed_embedding_versions():
    store = _Store({"a.md": (1.0, 0.0), "b.md": (0.99, 0.01)}, compatible=False)
    edges, status, covered = GraphService(
        _Index(), vector_store=store, embedding_provider=_Provider(), embedding_version="v1"
    )._semantic_projection({"a.md", "b.md"}, include=True)
    assert edges == []
    assert status == "outdated"
    assert covered == 0


def test_semantic_projection_keeps_complete_notes_when_one_embedding_is_pending():
    service = GraphService(
        _Index(),
        vector_store=_Store(
            {"a.md": (1.0, 0.0), "b.md": (0.99, 0.01)}, status="pending"
        ),
        embedding_provider=_Provider(),
        embedding_version="v1",
    )

    edges, status, covered = service._semantic_projection(
        {"a.md", "b.md", "c.md"}, include=True
    )

    assert status == "ready"
    assert covered == 2
    assert len(edges) == 1


def test_zero_dimension_provider_uses_ready_v1_state_and_partial_coverage():
    service = GraphService(
        _Index(),
        vector_store=_Store(
            {"a.md": (1.0, 0.0), "b.md": (0.99, 0.01)},
            provider_name="_AutoDimensionProvider",
        ),
        embedding_provider=_AutoDimensionProvider(),
        embedding_version="v1",
    )

    edges, status, covered = service._semantic_projection({"a.md", "b.md", "c.md"}, include=True)

    assert status == "ready"
    assert covered == 2
    assert len(edges) == 1


def test_global_pagination_keeps_full_scope_semantic_edge_total():
    service = GraphService(
        _Index(),
        vector_store=_Store({"a.md": (1.0, 0.0), "b.md": (0.99, 0.01)}),
        embedding_provider=_Provider(),
        embedding_version="v1",
    )

    first = service.global_graph(limit=1, offset=0)
    second = service.global_graph(limit=1, offset=1)

    assert first.page.total_nodes == second.page.total_nodes == 3
    assert first.page.total_edges == second.page.total_edges == 1
    assert first.semantic_covered_nodes == second.semantic_covered_nodes == 2
    assert first.edges == second.edges == []  # endpoints are on different pages
