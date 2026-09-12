"""Phase 12 — similarity kernels and the optional numpy fast path (M14 §二十一).

The scan must work on every installation (no native dependency) and get faster
when numpy happens to be present. These tests pin both kernels, their identical
ranking behaviour, and the fallback path.
"""

from __future__ import annotations

import random
import struct
import time

import pytest

from server.rag.vector import kernels
from server.rag.vector.kernels import (
    NUMPY_AVAILABLE,
    dot_products,
    dot_products_numpy,
    dot_products_pure,
    kernel_info,
)


def _blob(count: int, dimension: int, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    return struct.pack(f"<{count * dimension}f", *[rng.random() for _ in range(count * dimension)])


def test_pure_kernel_scores_every_vector() -> None:
    blob = _blob(4, 3)
    vector = [1.0, 0.0, 0.0]
    scores = dot_products_pure(blob, vector, 3)
    assert [index for _, index in scores] == [0, 1, 2, 3]
    floats = struct.unpack("<12f", blob)
    assert scores[0][0] == pytest.approx(floats[0])


def test_pure_kernel_handles_empty_and_ragged_input() -> None:
    assert dot_products_pure(b"", [1.0], 1) == []
    assert dot_products_pure(b"\x00\x00\x00", [1.0], 1) == []  # not a whole vector
    # A trailing partial vector is ignored rather than misread.
    blob = _blob(2, 2) + b"\x00\x00"
    assert len(dot_products_pure(blob, [1.0, 1.0], 2)) == 2


def test_pure_kernel_rejects_zero_dimension() -> None:
    assert dot_products_pure(_blob(1, 2), [1.0, 1.0], 0) == []


def test_kernel_info_reports_the_active_kernel() -> None:
    info = kernel_info()
    assert info.available is True
    assert info.name in {"numpy", "python"}
    assert info.reason


def test_dot_products_matches_the_pure_kernel() -> None:
    blob = _blob(5, 4)
    vector = [0.5, -0.25, 1.0, 0.0]
    reference = dot_products_pure(blob, vector, 4)
    resolved = dot_products(blob, vector, 4)
    assert len(resolved) == len(reference)
    for (score, index), (ref_score, ref_index) in zip(resolved, reference, strict=True):
        assert index == ref_index
        assert score == pytest.approx(ref_score, abs=1e-5)


def test_numpy_kernel_is_optional_and_returns_none_when_absent(monkeypatch) -> None:
    """Simulated absence: the kernel must refuse instead of raising."""
    monkeypatch.setattr(kernels, "NUMPY_AVAILABLE", False)
    monkeypatch.setattr(kernels, "_np", None)
    assert dot_products_numpy(_blob(2, 2), [1.0, 1.0], 2) is None
    # ...and the resolver still answers through the pure kernel.
    assert len(dot_products(_blob(2, 2), [1.0, 1.0], 2)) == 2


def test_numpy_kernel_failure_falls_back_instead_of_breaking(monkeypatch) -> None:
    """A broken accelerator must degrade, never surface as a retrieval error."""

    class Exploding:
        @staticmethod
        def frombuffer(*_args, **_kwargs):
            raise RuntimeError("kernel exploded")

        @staticmethod
        def asarray(*_args, **_kwargs):
            raise RuntimeError("kernel exploded")

    monkeypatch.setattr(kernels, "NUMPY_AVAILABLE", True)
    monkeypatch.setattr(kernels, "_np", Exploding)
    assert dot_products_numpy(_blob(2, 2), [1.0, 1.0], 2) is None
    assert len(dot_products(_blob(2, 2), [1.0, 1.0], 2)) == 2


@pytest.mark.skipif(not NUMPY_AVAILABLE, reason="numpy is not installed here")
def test_numpy_kernel_agrees_with_the_pure_kernel() -> None:
    blob = _blob(64, 32, seed=7)
    vector = [((index % 7) - 3) / 5.0 for index in range(32)]
    pure = dot_products_pure(blob, vector, 32)
    accelerated = dot_products_numpy(blob, vector, 32)
    assert accelerated is not None
    assert [index for _, index in accelerated] == [index for _, index in pure]
    for (score, _), (ref_score, _) in zip(accelerated, pure, strict=True):
        # float32 accumulation differs from float64 in the last bits only.
        assert score == pytest.approx(ref_score, abs=1e-4)


@pytest.mark.skipif(not NUMPY_AVAILABLE, reason="numpy is not installed here")
def test_numpy_kernel_is_faster_than_pure_python() -> None:
    """Not a micro-benchmark gate: it only proves the fast path is a real win."""
    count, dimension = 4000, 128
    blob = _blob(count, dimension, seed=3)
    vector = [0.1] * dimension
    dot_products_pure(blob, vector, dimension)  # warm
    started = time.perf_counter()
    dot_products_pure(blob, vector, dimension)
    pure_ms = (time.perf_counter() - started) * 1000.0
    dot_products_numpy(blob, vector, dimension)  # warm
    started = time.perf_counter()
    dot_products_numpy(blob, vector, dimension)
    numpy_ms = (time.perf_counter() - started) * 1000.0
    print(f"\n[kernel] {count}x{dimension}: pure {pure_ms:.1f} ms, numpy {numpy_ms:.1f} ms")
    assert numpy_ms < pure_ms


def test_store_search_uses_the_resolved_kernel(store) -> None:
    """The store must expose which kernel serves its scans."""
    assert store.kernel() in {"numpy", "python"}
    assert store.kernel() == kernel_info().name
