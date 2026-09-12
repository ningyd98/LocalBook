"""Local-only configuration endpoints. No note content is sent by connection tests."""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...ai.adapters.omlx_client import DiscoveryError
from ...ai.adapters.openai_compatible import OpenAICompatibleAdapter
from ...ai.capabilities import resolve_chat_model
from ...ai.service import AIStatusService
from ...config import ProviderKind, ProviderProfile, _normalize_profile_id
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


class ProviderPayload(Strict):
    """One provider profile on the wire (PLAN-PROVIDERS §4).

    Strict and bounded: the server owns ``builtin``/``source``, and an omitted
    ``api_key`` means "keep the stored secret for this profile".
    """

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)
    kind: ProviderKind = "openai-compatible"
    base_url: str | None = Field(default=None, max_length=4096)
    api_key: str | None = Field(default=None, max_length=4096)
    chat_model: str = Field(default="auto", min_length=1, max_length=256)
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_output_tokens: int = Field(default=1200, ge=64, le=8192)
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=0.5, gt=0, le=10)
    max_models_response_bytes: int = Field(default=1_000_000, gt=0)

    @field_validator("id")
    @classmethod
    def profile_id(cls, value: str) -> str:
        normalized = _normalize_profile_id(value)
        if not normalized:
            raise ValueError("Profile id must be 1-64 chars of a-z, 0-9, '.', '_' or '-'")
        return normalized

    @field_validator("chat_model")
    @classmethod
    def model_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model must not be blank")
        return value


class ProviderUpsert(Strict):
    expected_revision: int = Field(ge=1)
    provider: ProviderPayload
    # New/edit-then-activate is the common case; "save without switching" stays
    # available for preparing a profile for later.
    activate: bool = True


class ProviderActivate(Strict):
    expected_revision: int = Field(ge=1)
    profile_id: str = Field(min_length=1, max_length=64)


class ProviderDelete(Strict):
    expected_revision: int = Field(ge=1)
    profile_id: str = Field(min_length=1, max_length=64)


class RerankerProbe(Strict):
    """Probe one reranker endpoint without writing anything (M14 §七)."""

    provider: Literal["openai_compatible", "openai"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=4096)
    model: str = Field(min_length=1, max_length=256)
    api_key: str | None = None


class ProviderTest(Strict):
    provider: ProviderPayload
    # Optional: when omitted the active profile is the donor of stored secrets.
    expected_revision: int | None = Field(default=None, ge=1)


class SettingsUpdate(Strict):
    expected_revision: int = Field(ge=1)
    ai: AIConfiguration


class RagConfiguration(BaseModel):
    """RAG settings accepted by ``PATCH /settings/rag`` (M14).

    Every field is optional so the UI can submit a partial edit; ``None`` keeps
    the stored value, and ``embedding_api_key=""`` clears the stored secret
    (mirroring the AI key contract).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    embedding_provider: Literal["hash", "openai_compatible", "openai", "none"] | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=0, le=8192)
    chunk_target_tokens: int | None = Field(default=None, ge=50, le=8000)
    chunk_max_tokens: int | None = Field(default=None, ge=50, le=16000)
    chunk_overlap_tokens: int | None = Field(default=None, ge=0, le=2000)
    fts_top_k: int | None = Field(default=None, ge=1, le=200)
    vector_top_k: int | None = Field(default=None, ge=1, le=200)
    context_top_k: int | None = Field(default=None, ge=1, le=50)
    reranker_enabled: bool | None = None
    reranker_provider: str | None = None
    # Present in ``RagSettings`` and already edited by the settings UI; without
    # it a save would silently drop the URL (extra="forbid" rejects unknown
    # keys, and a whitelisting client would omit it).
    reranker_base_url: str | None = None
    reranker_model: str | None = None
    index_on_startup: bool | None = None
    # Link/graph expansion (roadmap item ③). Same partial-update contract as
    # every other field: ``None`` keeps the stored value; the bounds mirror
    # ``RagSettings`` exactly so a rejected value is rejected identically.
    link_retrieval_enabled: bool | None = None
    link_top_k: int | None = Field(default=None, ge=1, le=200)
    wikilink_weight: float | None = Field(default=None, ge=0.0, le=5.0)
    backlink_weight: float | None = Field(default=None, ge=0.0, le=5.0)
    tag_weight: float | None = Field(default=None, ge=0.0, le=5.0)
    graph_weight: float | None = Field(default=None, ge=0.0, le=5.0)


class RagSettingsPatch(Strict):
    rag: RagConfiguration
    expected_revision: int = Field(ge=1)


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


@router.post("/rag/reranker/test")
async def test_reranker(body: RerankerProbe, request: Request):
    """Check that a rerank endpoint answers, reusing the stored key when omitted.

    Reranking is optional, so a failure is reported as a normal result object
    (``status: "offline"``) rather than an error the UI has to special-case.
    """
    from ...rag.rerank.openai_compatible import OpenAICompatibleReranker, RerankUnavailable

    stored = request.app.state.runtime.settings.rag
    api_key = body.api_key if body.api_key is not None else stored.reranker_api_key
    endpoint = validate_endpoint(body.base_url)
    if endpoint is None:
        raise SettingsError("invalid_endpoint", "Enter a valid http(s) endpoint.", 422)
    probe = OpenAICompatibleReranker(
        base_url=endpoint,
        model=body.model,
        api_key=api_key or None,
        timeout_seconds=20.0,
    )
    try:
        items = await asyncio.to_thread(
            probe.rerank,
            "云边协同 混合检索",
            [
                "云边协同机械臂的慢系统负责全局规划。",
                "阳台番茄需要充足光照。",
            ],
        )
    except RerankUnavailable as exc:
        return {"status": "offline", "model": body.model, "message": str(exc), "results": []}
    return {
        "status": "connected",
        "model": body.model,
        "message": f"reranked {len(items)} document(s)",
        "results": [{"index": item.index, "score": item.score} for item in items],
    }


@router.patch("/rag")
def update_rag(body: RagSettingsPatch, request: Request):
    """Persist RAG settings and rebuild only the derived RAG stack.

    The Vault, watcher and M4 index are untouched: changing an embedding model
    can never interrupt editing, and every stored vector is derived data.
    """
    values = body.rag.model_dump()
    if values.get("embedding_base_url") is not None:
        values["embedding_base_url"] = validate_endpoint(values["embedding_base_url"]) or ""
    # Same endpoint contract for the optional reranker: HTTP(S), no embedded
    # credentials, no query/fragment. An unusable value becomes "" (rerank off)
    # instead of being stored and failing later.
    if values.get("reranker_base_url") is not None:
        values["reranker_base_url"] = validate_endpoint(values["reranker_base_url"]) or ""
    return request.app.state.runtime.apply_rag(values, body.expected_revision)


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
    adapter = OpenAICompatibleAdapter(
        endpoint,
        api_key=settings.api_key,
        timeout_seconds=settings.request_timeout_seconds,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        max_response_bytes=settings.max_models_response_bytes,
        trust_env=bool(getattr(settings, "use_env_proxy", False)),
    )
    models = []
    try:
        models = await adapter.list_models()
        selected = resolve_chat_model(models, body.chat_model, settings.qwen_match_pattern)
        return {"status": "connected", "models": [m.model_dump() for m in models], "selected_model": selected, "message": None}
    except Exception as exc:
        code = getattr(exc, "kind", None) or str(getattr(exc, "code", "unavailable"))
        return {"status": "offline", "models": [m.model_dump() for m in models], "selected_model": None, "message": "AI connection or model discovery failed.", "error_code": code, "http_status": getattr(exc, "http_status", None)}


# --------------------------------------------------------------------------
# Provider profiles (PLAN-PROVIDERS): one saved route per AI supplier.
# --------------------------------------------------------------------------


def _stored_profile(request: Request, profile_id: str) -> ProviderProfile | None:
    for profile in request.app.state.runtime.settings.ai.synthesized_profiles():
        if profile.id == profile_id:
            return profile
    return None


def _platform_defaults(request: Request) -> dict[str, object]:
    """Context-window knobs stay global; a profile never carries them."""
    ai = request.app.state.runtime.settings.ai
    return {
        "max_models_response_bytes": ai.max_models_response_bytes,
        "request_timeout_seconds": ai.request_timeout_seconds,
        "connect_timeout_seconds": ai.connect_timeout_seconds,
    }


def _profile_from_payload(
    body: ProviderPayload, existing: ProviderProfile | None
) -> ProviderProfile:
    """Build a storable profile; submitted fields win, omitted ones are inherited.

    ``api_key`` follows ``PATCH /settings`` semantics: absent/``None`` = keep the
    profile's stored secret, empty string = clear it, any other value = replace.
    Editing an existing profile therefore never resets a tuning knob the client
    did not send.
    """
    values = body.model_dump()
    if existing is not None:
        for field in ("name", "kind", "base_url", "chat_model", "temperature",
                      "max_output_tokens", "request_timeout_seconds",
                      "connect_timeout_seconds", "max_models_response_bytes"):
            if field not in body.model_fields_set:
                values[field] = getattr(existing, field)
        values["api_key"] = existing.api_key if body.api_key is None else values["api_key"]
    elif body.api_key is None:
        values["api_key"] = None
    profile = ProviderProfile(**values)
    # Same endpoint contract as every other AI route: HTTP(S), no embedded
    # credentials, no query or fragment.
    profile.base_url = validate_endpoint(profile.base_url)
    profile.builtin = bool(existing.builtin) if existing else False
    return profile


@router.get("/ai/profiles")
def list_ai_profiles(request: Request):
    snapshot = request.app.state.runtime.snapshot()
    return {"revision": snapshot["revision"],
            "active_profile_id": snapshot["ai"]["active_profile_id"],
            "profiles": snapshot["ai"]["profiles"]}


@router.post("/ai/profiles")
def upsert_ai_profile(body: ProviderUpsert, request: Request):
    runtime = request.app.state.runtime
    existing = _stored_profile(request, body.provider.id)
    profile = _profile_from_payload(body.provider, existing)
    return runtime.upsert_profile(profile, body.expected_revision, activate=body.activate)


@router.post("/ai/profiles/activate")
def activate_ai_profile(body: ProviderActivate, request: Request):
    return request.app.state.runtime.activate_profile(body.profile_id, body.expected_revision)


@router.post("/ai/profiles/delete")
def delete_ai_profile(body: ProviderDelete, request: Request):
    return request.app.state.runtime.delete_profile(body.profile_id, body.expected_revision)


@router.post("/ai/profiles/test")
async def test_ai_profile(body: ProviderTest, request: Request):
    """Probe one profile without writing anything.

    Uses the submitted endpoint/model and, when the payload omits the key, the
    stored secret of that profile — or of the active profile while a new one is
    being created, the same "reuse the stored key" affordance as ``/ai/test``.
    """
    runtime = request.app.state.runtime
    existing = _stored_profile(request, body.provider.id)
    donor = existing if existing is not None else runtime.settings.ai.effective_profile()
    profile = _profile_from_payload(body.provider, donor)
    endpoint = validate_endpoint(profile.base_url)
    if not endpoint:
        return {"status": "not_configured", "models": [], "selected_model": None,
                "error_code": "not_configured", "message": "AI service URL is empty."}
    adapter = OpenAICompatibleAdapter(
        endpoint,
        api_key=profile.api_key,
        timeout_seconds=profile.request_timeout_seconds,
        connect_timeout_seconds=profile.connect_timeout_seconds,
        max_response_bytes=profile.max_models_response_bytes,
        trust_env=bool(getattr(runtime.settings.ai, "use_env_proxy", False)),
    )
    models: list = []
    key_sent = bool(profile.api_key)
    try:
        models = await adapter.list_models()
        selected = resolve_chat_model(
            models, profile.chat_model, runtime.settings.ai.qwen_match_pattern
        )
        return {"status": "connected", "models": [m.model_dump() for m in models],
                "selected_model": selected, "error_code": None, "message": None,
                "key_sent": key_sent}
    except Exception as exc:
        status = getattr(exc, "http_status", None)
        code = getattr(exc, "kind", None) or str(getattr(exc, "code", "unavailable"))
        auth = code == "auth_error" or status in {401, 403}
        return {"status": "offline", "models": [m.model_dump() for m in models],
                "selected_model": None, "error_code": "auth_error" if auth else code,
                "key_sent": key_sent, "http_status": 401 if auth else status,
                "message": "AI connection or model discovery failed."}
