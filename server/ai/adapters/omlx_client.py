"""Model discovery client for oMLX's OpenAI-compatible ``GET /v1/models``.

This module is the **only** place LocalNote talks to oMLX over HTTP in
Phase 0. ``httpx`` is confined here; services and routes depend on the small
:class:`ModelDiscoveryClient` protocol instead.

Test policy: every test injects an ``httpx.MockTransport`` or a fake client.
Real oMLX network calls never happen under test.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..schemas import AICapabilities, DiscoveredModel

logger = logging.getLogger("localnote.ai.omlx")

# Capability keys read from optional per-model metadata. Anything else in the
# entry is ignored; non-boolean values are treated as False (safe whitelist).
_CAPABILITY_WHITELIST = ("chat", "embedding", "rerank")


class DiscoveryError(Exception):
    """Raised when the oMLX model-list probe cannot produce a model list.

    ``code`` is one of the AIStatusResponse ``error_code`` literals:
    connection_refused | timeout | http_error | auth_error | invalid_response
    | unknown.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OMLXDiscoveryConfig:
    """Endpoint + safety limits for a discovery probe."""

    base_url: str
    api_key: str | None = None
    connect_timeout_seconds: float = 0.5
    request_timeout_seconds: float = 2.0
    max_response_bytes: int = 1_000_000
    # Opt-in shell proxies (see ``AISettings.use_env_proxy``): httpx's default
    # would raise while parsing a malformed ``NO_PROXY`` and turn every probe
    # into a confusing failure.
    trust_env: bool = False


class ModelDiscoveryClient(Protocol):
    """Minimal client surface the AI status service depends on."""

    async def list_models(self) -> list[DiscoveredModel]: ...


class OMLXModelDiscoveryClient:
    """Discovers models via ``GET {base_url}/models`` with bounded IO."""

    def __init__(
        self,
        config: OMLXDiscoveryConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._timeout = httpx.Timeout(
            config.request_timeout_seconds,
            connect=config.connect_timeout_seconds,
        )

    @property
    def models_url(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/models"

    @property
    def _headers(self) -> dict[str, str]:
        """Bearer auth only when a key is configured (never an empty header)."""
        key = (self._config.api_key or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def list_models(self) -> list[DiscoveredModel]:
        timeout = self._timeout
        transport = self._transport
        headers = self._headers
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=self._config.trust_env,
        ) as client:
            try:
                async with client.stream("GET", self.models_url, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise DiscoveryError(
                            "auth_error",
                            "oMLX endpoint rejected the credentials",
                        )
                    if response.status_code >= 400:
                        raise DiscoveryError(
                            "http_error",
                            f"oMLX endpoint returned HTTP {response.status_code}",
                        )
                    body = await self._read_bounded(response)
            except DiscoveryError:
                # Our own classification: re-raise untouched.
                raise
            except httpx.ConnectError as exc:
                raise DiscoveryError("connection_refused", "oMLX endpoint is unreachable") from exc
            except httpx.TimeoutException as exc:
                raise DiscoveryError(
                    "timeout", "oMLX endpoint did not respond before timeout"
                ) from exc
            except httpx.HTTPError as exc:
                raise DiscoveryError(
                    "unknown", "Unexpected HTTP client error while probing oMLX"
                ) from exc

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DiscoveryError(
                "invalid_response",
                "oMLX endpoint returned an invalid model list response",
            ) from exc
        return _parse_models_payload(payload)

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._config.max_response_bytes:
                raise DiscoveryError(
                    "invalid_response",
                    "oMLX model list response exceeded the size limit",
                )
            chunks.append(chunk)
        return b"".join(chunks)


def _parse_models_payload(payload: Any) -> list[DiscoveredModel]:
    """Strict, safe parse of an OpenAI-style ``{data: [...]}`` payload.

    Only ``data`` as a list of objects with a non-empty string ``id`` is
    accepted; anything else raises ``DiscoveryError(invalid_response)``.
    Optional metadata (``owned_by``, ``capabilities``) is read leniently —
    malformed optional fields degrade to safe defaults instead of failing.
    """
    if not isinstance(payload, dict):
        raise DiscoveryError("invalid_response", "oMLX model list response was not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise DiscoveryError(
            "invalid_response", "oMLX model list response has no valid 'data' list"
        )

    models: list[DiscoveredModel] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise DiscoveryError("invalid_response", "oMLX model list entry is not an object")
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise DiscoveryError("invalid_response", "oMLX model list entry has no valid string id")
        owned_by = entry.get("owned_by")
        if not isinstance(owned_by, str):
            owned_by = None
        models.append(
            DiscoveredModel(
                id=model_id,
                owned_by=owned_by,
                capabilities=_parse_capabilities(entry.get("capabilities")),
            )
        )
    return models


def _parse_capabilities(raw: Any) -> AICapabilities:
    caps = AICapabilities()
    if not isinstance(raw, dict):
        return caps
    for name in _CAPABILITY_WHITELIST:
        value = raw.get(name)
        if isinstance(value, bool):
            setattr(caps, name, value)
    return caps
