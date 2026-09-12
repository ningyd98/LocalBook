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
from .capabilities import resolve_chat_model
from .errors import AIError
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
    "auth_error": "oMLX endpoint rejected the API key (HTTP 401/403)",
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
        api_key: str | None = None,
        connect_timeout_seconds: float = _DEFAULT_CONNECT_TIMEOUT,
        request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT,
        max_models_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        qwen_match_pattern: str = _DEFAULT_QWEN_PATTERN,
        chat_model: str = "auto",
        trust_env: bool = False,
        profile_id: str | None = None,
        profile_name: str | None = None,
        profile_kind: str | None = None,
        transport=None,
        client_factory: Callable[[], ModelDiscoveryClient] | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip() or None
        self._api_key = (api_key or "").strip() or None
        self._connect_timeout = connect_timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._max_bytes = max_models_response_bytes
        self._pattern = qwen_match_pattern
        self._chat_model = chat_model
        self._profile = {
            "active_profile_id": profile_id,
            "active_profile_name": profile_name,
            "active_profile_kind": profile_kind,
        }
        self._trust_env = bool(trust_env)
        if client_factory is not None:
            self._client_factory = client_factory
        else:
            self._client_factory = self._default_client_factory(transport)

    def _default_client_factory(self, transport=None) -> Callable[[], ModelDiscoveryClient]:
        def build() -> ModelDiscoveryClient:
            # Only called when self._base_url is not None.
            config = OMLXDiscoveryConfig(
                base_url=self._base_url or "",
                api_key=self._api_key,
                connect_timeout_seconds=self._connect_timeout,
                request_timeout_seconds=self._request_timeout,
                max_response_bytes=self._max_bytes,
                trust_env=self._trust_env,
            )
            return OMLXModelDiscoveryClient(config, transport=transport)

        return build

    @classmethod
    def from_ai_settings(cls, ai, transport=None) -> AIStatusService:
        """Build from ``server.config.AISettings`` (duck-typed to avoid cycles).

        The applied provider profile is attached when the object provides one
        (PLAN-PROVIDERS); plain settings objects keep the legacy behaviour.
        """
        profile = None
        get_profile = getattr(ai, "effective_profile", None)
        if callable(get_profile):
            try:
                profile = get_profile()
            except Exception:  # a probe must never fail on profile metadata
                profile = None
        return cls(
            base_url=ai.base_url,
            api_key=getattr(ai, "api_key", None),
            connect_timeout_seconds=ai.connect_timeout_seconds,
            request_timeout_seconds=ai.request_timeout_seconds,
            max_models_response_bytes=ai.max_models_response_bytes,
            qwen_match_pattern=ai.qwen_match_pattern,
            chat_model=ai.chat_model,
            trust_env=bool(getattr(ai, "use_env_proxy", False)),
            profile_id=getattr(profile, "id", None),
            profile_name=getattr(profile, "display_name", None),
            profile_kind=getattr(profile, "kind", None),
            transport=transport,
        )

    async def check(self) -> AIStatusResponse:
        checked_at = _now()
        if self._base_url is None:
            return AIStatusResponse(
                **self._profile,
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
                **self._profile,
                status=AIStatus.OFFLINE,
                endpoint=endpoint,
                error_code=exc.code,
                message=_MESSAGES.get(exc.code, _MESSAGES["unknown"]),
                checked_at=checked_at,
            )
        except Exception:  # defensive: never let a probe crash the route
            logger.exception("ai_status unexpected failure endpoint=%s", endpoint)
            return AIStatusResponse(
                **self._profile,
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

        try:
            selected_model = resolve_chat_model(models, self._chat_model, self._pattern)
        except AIError:
            selected_model = None

        if selected_model is None:
            logger.info(
                "ai_status outcome=connected no_matching_model=True endpoint=%s models=%d",
                endpoint,
                len(models),
            )
            return AIStatusResponse(
                **self._profile,
                status=AIStatus.CONNECTED,
                endpoint=endpoint,
                qwen_model=None,
                models=models,
                capabilities=capabilities,
                error_code="no_matching_model",
                message="AI service is reachable, but the configured chat model is unavailable",
                checked_at=checked_at,
            )

        logger.info(
            "ai_status outcome=connected endpoint=%s qwen_model=%s models=%d",
            endpoint,
            qwen_model,
            len(models),
        )
        return AIStatusResponse(
            **self._profile,
            status=AIStatus.CONNECTED,
            endpoint=endpoint,
            qwen_model=qwen_model,
            selected_model=selected_model,
            models=models,
            capabilities=capabilities,
            error_code=None,
            message=None,
            checked_at=checked_at,
        )

    async def discover(self) -> tuple[list, str | None]:
        """Fetch the model list and resolve the configured chat model.

        Raises :class:`DiscoveryError` for any probe failure (including
        ``auth_error``) so callers can map it to their own error shape. Unlike
        :meth:`check` this never swallows failures into an offline status: the
        settings UI needs the concrete reason to explain a 401.
        """
        models = await self._client_factory().list_models()
        try:
            selected = resolve_chat_model(models, self._chat_model, self._pattern)
        except AIError:
            selected = None
        return models, selected


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
