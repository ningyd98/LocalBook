"""Vector similarity kernels for the brute-force scan (M14 §二十一).

The store keeps one padded float32 run per document and scans it when answering
a query. Two kernels implement that scan:

- :func:`dot_products_numpy` — used when ``numpy`` happens to be installed
  (a matrix-vector product, roughly two orders of magnitude faster);
- :func:`dot_products_pure` — the always-available fallback.

``numpy`` is deliberately **not** a declared dependency: LocalBook must install
and run on every platform with no native build step, and the pure-Python scan
already meets the retrieval budget at the target scale. The numpy path is a
gratuitous optimisation — it is probed once, is never required, and any failure
in it falls back to the pure kernel.

Both kernels return ``(score, index)`` lists in *unsorted* order so callers can
rank with their own tie-breaking rules and tests can assert the two agree.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger("localnote.rag.vector.kernels")

try:  # optional accelerator; never a hard requirement
    import numpy as _np  # type: ignore[import-not-found]

    NUMPY_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the installation
    _np = None  # type: ignore[assignment]
    NUMPY_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class KernelInfo:
    """Which kernel serves the scan, for ``/rag/index/status`` diagnostics."""

    name: str
    available: bool
    reason: str = ""


def kernel_info() -> KernelInfo:
    if NUMPY_AVAILABLE:
        return KernelInfo(name="numpy", available=True, reason="numpy is installed")
    return KernelInfo(
        name="python",
        available=True,
        reason="numpy is not installed; using the pure-Python kernel",
    )


def dot_products_pure(
    blob: bytes, vector: Sequence[float], dimension: int
) -> list[tuple[float, int]]:
    """Cosine scores of one document's stored run (pure Python).

    The run is unpacked once with :mod:`struct` and scored with a tight loop;
    this is the kernel every installation is guaranteed to have.
    """
    if dimension <= 0 or not blob:
        return []
    usable = len(blob) - (len(blob) % (4 * dimension))
    if usable <= 0:
        return []
    floats = struct.unpack(f"<{usable // 4}f", blob[:usable])
    query = [float(value) for value in vector]
    scores: list[tuple[float, int]] = []
    for index in range(usable // (4 * dimension)):
        offset = index * dimension
        score = 0.0
        for position in range(dimension):
            score += floats[offset + position] * query[position]
        scores.append((score, index))
    return scores


def dot_products_numpy(
    blob: bytes, vector: Sequence[float], dimension: int
) -> list[tuple[float, int]] | None:
    """Same contract as :func:`dot_products_pure`, or ``None`` when unavailable.

    Returning ``None`` (instead of raising) keeps the caller's fallback trivial:
    any missing capability or shape problem simply means "use the other kernel".
    """
    if not NUMPY_AVAILABLE or dimension <= 0 or not blob:
        return None
    usable = len(blob) - (len(blob) % (4 * dimension))
    if usable <= 0:
        return None
    try:
        matrix = _np.frombuffer(blob[:usable], dtype="<f4").reshape(-1, dimension)
        query = _np.asarray([float(value) for value in vector], dtype=_np.float32)
        scores = matrix @ query
    except Exception as exc:  # pragma: no cover - defensive, kernel mismatch
        logger.debug("numpy kernel unavailable (%s); falling back", exc)
        return None
    return [(float(score), index) for index, score in enumerate(scores)]


def dot_products(
    blob: bytes, vector: Sequence[float], dimension: int
) -> list[tuple[float, int]]:
    """Score one run with the best available kernel (numpy, else pure Python)."""
    accelerated = dot_products_numpy(blob, vector, dimension)
    if accelerated is not None:
        return accelerated
    return dot_products_pure(blob, vector, dimension)


__all__ = [
    "KernelInfo",
    "NUMPY_AVAILABLE",
    "dot_products",
    "dot_products_numpy",
    "dot_products_pure",
    "kernel_info",
]
