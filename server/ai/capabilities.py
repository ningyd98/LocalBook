"""Capability normalization and deterministic model resolution."""

from __future__ import annotations

import hashlib
import threading
import time

from .errors import AIError, AIErrorCode
from .matching import matches_qwen35_4b
from .schemas import DiscoveredModel

_MODEL_CATALOG_TTL_SECONDS = 30.0
_MODEL_CATALOG_MAX_ENTRIES = 128
_model_catalog_lock = threading.Lock()
_model_catalog_cache: dict[
    tuple[str, str, int | None], tuple[float, tuple[DiscoveredModel, ...]]
] = {}


def aggregate_capabilities(models: list[DiscoveredModel]):
    from .schemas import AICapabilities

    return AICapabilities(
        chat=any(m.capabilities.chat or matches_qwen35_4b(m.id) for m in models),
        embedding=any(m.capabilities.embedding for m in models),
        rerank=any(m.capabilities.rerank for m in models),
    )


def resolve_chat_model(
    models: list[DiscoveredModel], requested: str, pattern: str = r"qwen3\.?5[-_ ]?4b"
) -> str:
    chat = [m for m in models if m.capabilities.chat or matches_qwen35_4b(m.id, pattern)]
    if requested != "auto":
        # An explicit discovered ID is usable when the provider omits optional
        # capability metadata. Still reject models declared embedding/rerank-only.
        candidates = [m for m in models if m.capabilities.chat or not (m.capabilities.embedding or m.capabilities.rerank)]
        if not any(m.id == requested for m in candidates):
            raise AIError(
                AIErrorCode.MODEL_NOT_FOUND, "Requested model is unavailable", status_code=400
            )
        return requested
    if not chat:
        raise AIError(AIErrorCode.MODEL_NOT_FOUND, "No chat model is available")
    q = next((m.id for m in chat if matches_qwen35_4b(m.id, pattern)), None)
    return q or chat[0].id


def _model_catalog_key(adapter) -> tuple[str, str, int | None] | None:
    """Build a credential-safe cache key for one provider route."""
    base_url = str(getattr(adapter, "base_url", "") or "").strip()
    if not base_url:
        return None
    api_key = str(getattr(adapter, "api_key", "") or "").encode("utf-8")
    credential_fingerprint = hashlib.sha256(api_key).hexdigest()
    transport = getattr(adapter, "transport", None)
    return (
        base_url,
        credential_fingerprint,
        id(transport) if transport is not None else None,
    )


async def resolve_chat_model_for_request(
    adapter,
    requested: str,
    pattern: str = r"qwen3\.?5[-_ ]?4b",
) -> str:
    """Resolve a configured model without a discovery request on every answer.

    A selected model ID can go directly to a configured HTTP provider. Other
    adapters retain discovery and capability validation, since they may not
    support sending an undiscovered ID. ``auto`` still discovers and applies
    the existing capability rules, reusing that provider's catalog briefly.
    """
    selected = str(requested or "auto").strip() or "auto"
    key = _model_catalog_key(adapter)
    if selected != "auto" and key is not None:
        return selected
    now = time.monotonic()
    if key is not None:
        with _model_catalog_lock:
            cached = _model_catalog_cache.get(key)
            if cached is not None and cached[0] > now:
                return resolve_chat_model(list(cached[1]), selected, pattern)
            if cached is not None:
                _model_catalog_cache.pop(key, None)

    models = await adapter.list_models()
    if key is not None:
        expires_at = time.monotonic() + _MODEL_CATALOG_TTL_SECONDS
        with _model_catalog_lock:
            expired = [
                cache_key
                for cache_key, (expiry, _) in _model_catalog_cache.items()
                if expiry <= now
            ]
            for cache_key in expired:
                _model_catalog_cache.pop(cache_key, None)
            if len(_model_catalog_cache) >= _MODEL_CATALOG_MAX_ENTRIES:
                oldest = min(
                    _model_catalog_cache,
                    key=lambda cache_key: _model_catalog_cache[cache_key][0],
                )
                _model_catalog_cache.pop(oldest, None)
            _model_catalog_cache[key] = (expires_at, tuple(models))
    return resolve_chat_model(models, selected, pattern)
