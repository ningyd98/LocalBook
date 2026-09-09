"""Local-only configuration endpoints. No note content is sent by connection tests."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...ai.adapters.omlx_client import DiscoveryError
from ...ai.adapters.openai_compatible import OpenAICompatibleAdapter
from ...ai.capabilities import resolve_chat_model
from ...ai.service import AIStatusService
from ...runtime import Runtime, SettingsError, validate_endpoint


def _is_loopback_peer(request: Request) -> bool:
    """True when the effective client is loopback (or an in-process ASGI client).

    Behind the configured reverse-proxy chain (nginx -> frp -> Vite) the TCP
    peer is the *public* client address while the last hop is loopback; the
    trusted host allow-list is what opts into that chain, so the right-most
    ``X-Forwarded-For`` hop is honoured here as the effective peer. TestClient's
    synthetic peer ``testclient`` is accepted only for in-process ASGI requests
    (``scope["app"]`` is never set on a real TCP connection).
    """
    peer = request.client.host if request.client else ""
    try:
        if ipaddress.ip_address(peer).is_loopback:
            return True
    except ValueError:
        if peer == "testclient" and "app" in request.scope:
            return True
    hops = [hop.strip() for hop in request.headers.get("x-forwarded-for", "").split(",")]
    for hop in reversed([h for h in hops if h]):
        try:
            return ipaddress.ip_address(hop).is_loopback
        except ValueError:
            continue
    return False


def _origin_allowed(origin: str | None, host: str, cors_origins: list[str]) -> bool:
    """Same-origin requests are allowed; cross-site Origins must be listed.

    ``request.base_url`` cannot be compared directly behind a TLS-terminating
    proxy (it reports ``http`` while the browser sends ``https``), so the
    comparison is on the normalized ``scheme://host`` of the request host.
    """
    if not origin:
        return True
    if origin in cors_origins:
        return True
    parsed = urlsplit(origin)
    return parsed.hostname == host and parsed.scheme in {"http", "https"}


def local_settings(request: Request) -> Runtime:
    runtime = request.app.state.runtime
    local = _is_loopback_peer(request)
    host = request.url.hostname or ""
    allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver"} | set(
        runtime.settings.server.settings_trusted_hosts
    )
    origin_ok = _origin_allowed(
        request.headers.get("origin"), host, runtime.settings.server.cors_origins
    )
    if not local or host not in allowed_hosts or not origin_ok:
        raise SettingsError("settings_local_only", "Settings can only be managed from a trusted local page.", 403)
    if request.method != "GET" and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise SettingsError("settings_json_required", "Configuration requests require JSON.", 415)
    return runtime


router = APIRouter(prefix="/api/v1/settings", tags=["settings"], dependencies=[Depends(local_settings)])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AIConfiguration(Strict):
    enabled: bool
    base_url: str | None = None
    # ``None`` keeps the stored key (the UI never receives it); "" clears it.
    api_key: str | None = Field(default=None, max_length=4096)
    chat_model: str = Field(default="auto", min_length=1, max_length=256)

    @field_validator("chat_model")
    @classmethod
    def model_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model must not be blank")
        return value


class SettingsUpdate(Strict):
    expected_revision: int = Field(ge=1)
    ai: AIConfiguration


class VaultSwitch(Strict):
    root: str = Field(min_length=1, max_length=4096)
    expected_revision: int = Field(ge=1)
    vault_session_id: str = Field(min_length=1, max_length=100)


def _ai_probe_settings(body: AIConfiguration, request: Request):
    """Effective AI settings for a probe: submitted values + stored secrets.

    ``api_key`` is optional in the request; when omitted the stored key is
    used so "test connection" works without re-typing the secret.
    """
    stored = request.app.state.runtime.settings.ai
    if body.api_key is None:
        return stored
    return stored.model_copy(update={"api_key": body.api_key})


@router.get("")
def get_settings(request: Request):
    return request.app.state.runtime.snapshot()


@router.patch("")
def update_settings(body: SettingsUpdate, request: Request):
    return request.app.state.runtime.apply_ai(body.ai.model_dump(), body.expected_revision)


@router.post("/vault/switch")
def switch_vault(body: VaultSwitch, request: Request):
    return request.app.state.runtime.switch_vault(body.root, body.expected_revision, body.vault_session_id)


@router.post("/ai/models")
async def list_ai_models(body: AIConfiguration, request: Request):
    """Discover the provider's model list for the settings UI dropdown."""
    endpoint = validate_endpoint(body.base_url)
    if not endpoint:
        return {"status": "not_configured", "models": [], "selected_model": None,
                "error_code": "not_configured", "message": "AI service URL is empty."}
    settings = _ai_probe_settings(body, request)
    service = AIStatusService(
        base_url=endpoint,
        api_key=settings.api_key,
        request_timeout_seconds=settings.request_timeout_seconds,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        max_models_response_bytes=settings.max_models_response_bytes,
        qwen_match_pattern=settings.qwen_match_pattern,
        chat_model=body.chat_model,
    )
    key_sent = bool(settings.api_key)
    try:
        models, selected = await service.discover()
    except DiscoveryError as exc:
        auth = exc.code == "auth_error"
        # ``key_sent`` lets the UI distinguish "no key configured" from
        # "a key was sent and the service rejected it" (wrong/expired key).
        return {"status": "offline", "models": [], "selected_model": None,
                "error_code": exc.code,
                "http_status": 401 if auth else None,
                "key_sent": key_sent,
                "message": exc.message}
    except Exception:  # defensive: never let discovery crash the route
        return {"status": "offline", "models": [], "selected_model": None,
                "error_code": "unknown", "key_sent": key_sent,
                "message": "AI model discovery failed."}
    return {"status": "connected", "models": [m.model_dump() for m in models],
            "selected_model": selected, "error_code": None, "key_sent": key_sent,
            "message": None}


@router.post("/ai/test")
async def test_ai(body: AIConfiguration, request: Request):
    endpoint = validate_endpoint(body.base_url)
    if not endpoint:
        return {"status": "not_configured", "models": [], "selected_model": None, "message": "AI service URL is empty."}
    settings = _ai_probe_settings(body, request)
    adapter = OpenAICompatibleAdapter(endpoint, api_key=settings.api_key,
                                     timeout_seconds=settings.request_timeout_seconds,
                                     connect_timeout_seconds=settings.connect_timeout_seconds,
                                     max_response_bytes=settings.max_models_response_bytes)
    models = []
    try:
        models = await adapter.list_models()
        selected = resolve_chat_model(models, body.chat_model, settings.qwen_match_pattern)
        return {"status": "connected", "models": [m.model_dump() for m in models], "selected_model": selected, "message": None}
    except Exception as exc:
        code = getattr(exc, "kind", None) or str(getattr(exc, "code", "unavailable"))
        return {"status": "offline", "models": [m.model_dump() for m in models], "selected_model": None, "message": "AI connection or model discovery failed.", "error_code": code, "http_status": getattr(exc, "http_status", None)}
