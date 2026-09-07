"""AI status service (Phase 0).

Per-request, read-only probe of the oMLX endpoint. No caching, no background
watcher, no startup dependency: AI failure never blocks the API. All HTTP is
confined to the adapter; the service depends on the ModelDiscoveryClient
protocol, and tests inject ``httpx.MockTransport``-backed clients.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from .adapters.omlx_client import (
    DiscoveryError,
    ModelDiscoveryClient,
    OMLXDiscoveryConfig,
    OMLXModelDiscoveryClient,
)
from .matching import matches_qwen35_4b
from .schemas import AICapabilities, AIStatus, AIStatusResponse

logger = logging.getLogger("localnote.ai")

_DEFAULT_CONNECT_TIMEOUT = 0.5
_DEFAULT_REQUEST_TIMEOUT = 2.0
_DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
_DEFAULT_QWEN_PATTERN = r"qwen3\.?5[-_ ]?4b"

# Outcome → user-facing message. Safe by construction: no stack traces, no
# secrets, no response bodies.
_MESSAGES: dict[str, str] = {
    "not_configured": "AI endpoint is not configured",
    "connection_refused": "oMLX endpoint is unreachable",
    "timeout": "oMLX endpoint did not respond before timeout",
    "http_error": "oMLX endpoint returned an HTTP error",
    "invalid_response": "oMLX endpoint returned an invalid model list response",
    "unknown": "Unexpected error while probing the oMLX endpoint",
}


def _public_endpoint(base_url: str) -> str:
    """Normalize an endpoint for display/logging: strip credentials and query."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if ":" in host:
        netloc = f"[{host}]"
    else:
        netloc = host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _now() -> datetime:
    return datetime.now(UTC)


class AIStatusService:
    """Builds an :class:`AIStatusResponse` for one probe."""

    def __init__(
        self,
        *,
        base_url: str | None,
        connect_timeout_seconds: float = _DEFAULT_CONNECT_TIMEOUT,
        request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT,
        max_models_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        qwen_match_pattern: str = _DEFAULT_QWEN_PATTERN,
        transport=None,
        client_factory: Callable[[], ModelDiscoveryClient] | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip() or None
        self._connect_timeout = connect_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._max_bytes = max_models_response_bytes
        self._pattern = qwen_match_pattern
        if client_factory is not None:
            self._client_factory = client_factory
        else:
            self._client_factory = self._default_client_factory(transport)

    def _default_client_factory(self, transport=None) -> Callable[[], ModelDiscoveryClient]:
        def build() -> ModelDiscoveryClient:
            # Only called when self._base_url is not None.
            config = OMLXDiscoveryConfig(
                base_url=self._base_url or "",
                connect_timeout_seconds=self._connect_timeout,
                request_timeout_seconds=self._request_timeout,
                max_response_bytes=self._max_bytes,
            )
            return OMLXModelDiscoveryClient(config, transport=transport)

        return build

    @classmethod
    def from_ai_settings(cls, ai, transport=None) -> AIStatusService:
        """Build from ``server.config.AISettings`` (duck-typed to avoid cycles)."""
        return cls(
            base_url=ai.base_url,
            connect_timeout_seconds=ai.connect_timeout_seconds,
            request_timeout_seconds=ai.request_timeout_seconds,
            max_models_response_bytes=ai.max_models_response_bytes,
            qwen_match_pattern=ai.qwen_match_pattern,
            transport=transport,
        )

    async def check(self) -> AIStatusResponse:
        checked_at = _now()
        if self._base_url is None:
            return AIStatusResponse(
                status=AIStatus.NOT_CONFIGURED,
                endpoint=None,
                error_code="not_configured",
                message=_MESSAGES["not_configured"],
                checked_at=checked_at,
            )

        endpoint = _public_endpoint(self._base_url)
        try:
            models = await self._client_factory().list_models()
        except DiscoveryError as exc:
            logger.info(
                "ai_status outcome=offline error_code=%s endpoint=%s",
                exc.code,
                endpoint,
            )
            return AIStatusResponse(
                status=AIStatus.OFFLINE,
                endpoint=endpoint,
                error_code=exc.code,
                message=_MESSAGES.get(exc.code, _MESSAGES["unknown"]),
                checked_at=checked_at,
            )
        except Exception:  # defensive: never let a probe crash the route
            logger.exception("ai_status unexpected failure endpoint=%s", endpoint)
            return AIStatusResponse(
                status=AIStatus.OFFLINE,
                endpoint=endpoint,
                error_code="unknown",
                message=_MESSAGES["unknown"],
                checked_at=checked_at,
            )

        qwen_model = next(
            (model.id for model in models if matches_qwen35_4b(model.id, self._pattern)),
            None,
        )
        capabilities = _aggregate_capabilities(models, qwen_model)

        if qwen_model is None:
            logger.info(
                "ai_status outcome=connected no_matching_model=True endpoint=%s models=%d",
                endpoint,
                len(models),
            )
            return AIStatusResponse(
                status=AIStatus.CONNECTED,
                endpoint=endpoint,
                qwen_model=None,
                models=models,
                capabilities=capabilities,
                error_code="no_matching_model",
                message=("oMLX endpoint reachable, but no Qwen3.5-4B model was discovered"),
                checked_at=checked_at,
            )

        logger.info(
            "ai_status outcome=connected endpoint=%s qwen_model=%s models=%d",
            endpoint,
            qwen_model,
            len(models),
        )
        return AIStatusResponse(
            status=AIStatus.CONNECTED,
            endpoint=endpoint,
            qwen_model=qwen_model,
            models=models,
            capabilities=capabilities,
            error_code=None,
            message=None,
            checked_at=checked_at,
        )


def _aggregate_capabilities(models: list, qwen_model: str | None) -> AICapabilities:
    """Aggregate capabilities across discovered models.

    ``chat`` is core: a discovered Qwen3.5-4B implies chat. Otherwise chat is
    only claimed when at least one model explicitly reports it. embedding and
    rerank are optional and only claimed from explicit (whitelisted) metadata.
    """
    chat = bool(qwen_model) or any(m.capabilities.chat for m in models)
    embedding = any(m.capabilities.embedding for m in models)
    rerank = any(m.capabilities.rerank for m in models)
    return AICapabilities(chat=chat, embedding=embedding, rerank=rerank)
