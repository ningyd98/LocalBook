"""LocalNote Server configuration (Phase 0).

pydantic-settings model with ``LOCALNOTE_`` prefix and ``__`` nested
delimiter, e.g. ``LOCALNOTE_AI__BASE_URL``.

Phase 0 expresses ``server`` / ``vault`` / ``ai`` / ``scheduler``; M3 added
the in-memory ``index`` limits; M4 extends the ``index`` section with the
SQLite derived-database / FTS knobs (``db_filename``, ``fts_tokenizer``,
``journal_mode``, ``synchronous``, ``busy_timeout_ms``); M5 added ``graph``
and M7 added ``policy`` / ``history`` / ``recovery``; M8 expands ``scheduler``
(enabled by default, cron/timezone validation, Level-2 tag-only whitelist),
adds ``history`` retention knobs and ``index.auto_rebuild``.

A missing Vault root means "not configured" and never blocks startup.
An AI ``base_url`` of ``None`` (or empty after trimming) means
``not_configured``; the default value points at the local oMLX server, so with
oMLX down the status probe degrades to ``offline`` instead.

PLAN-PROVIDERS adds the multi-provider library: ``ai.profiles`` keeps one
:class:`ProviderProfile` per configured supplier (endpoint, credential, model
and generation knobs) and ``ai.active_profile_id`` names the applied one. The
flat ``ai.base_url``/``api_key``/``chat_model``/timeout fields remain the
*applied* configuration every consumer reads, so an installation that never
adds a profile behaves exactly as before.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "LOCALNOTE_"
ENV_NESTED_DELIMITER = "__"

# Attachment upload split point (PLAN-ATTACHMENTS v1.1 §5.1/§10.5): files at or
# below this size use the JSON ``content_base64`` channel, larger files use the
# streaming multipart endpoint.  This is a protocol constant, not a user
# setting; ``vault.max_file_bytes`` remains the absolute hard limit.
ATTACHMENT_JSON_MAX_BYTES = 10 * 1024 * 1024

# Shell-friendly flat aliases for the most common knobs. Documented nested
# variables always win when both are present.
_FLAT_ALIASES: dict[str, tuple[str, str]] = {
    "LOCALNOTE_HOST": ("server", "host"),
    "LOCALNOTE_PORT": ("server", "port"),
    "LOCALNOTE_VAULT_ROOT": ("vault", "root"),
    "LOCALNOTE_VAULT_WATCHER_ENABLED": ("vault", "watcher_enabled"),
    "LOCALNOTE_VAULT_WATCHER_DEBOUNCE_MS": ("vault", "watcher_debounce_ms"),
    "LOCALNOTE_VAULT_MAX_FILE_BYTES": ("vault", "max_file_bytes"),
    "LOCALNOTE_OMLX_BASE_URL": ("ai", "base_url"),
}


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3780
    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    # Extra Host names the /api/v1/settings routes accept beyond the built-in
    # loopback set (127.0.0.1 / localhost / ::1). Only needed when the app is
    # reached through a reverse proxy that preserves the public Host header
    # (e.g. nginx -> frp -> 127.0.0.1:5173); the loopback peer check still
    # applies. Opt-in: the default keeps settings local-only.
    settings_trusted_hosts: list[str] = []

    @field_validator("settings_trusted_hosts", mode="before")
    @classmethod
    def _split_trusted_hosts(cls, value: object) -> object:
        """Accept a JSON list or a comma-separated string (env-friendly)."""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return value
            return [part.strip() for part in text.split(",") if part.strip()]
        return value


class VaultSettings(BaseModel):
    """Runtime options for the local Vault core.

    ``root`` is intentionally optional: settings construction is side-effect
    free and a missing root means that Vault endpoints are disabled.  The
    service, not this model, validates existence, directory-ness and
    permissions.
    """

    root: Path | None = None
    watcher_enabled: bool = True
    watcher_debounce_ms: int = Field(default=200, ge=0)
    max_file_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    # How long a soft-deleted file or folder stays in ``.localnote/trash``
    # before the next trash operation removes it for good.
    trash_retention_days: int = Field(default=30, ge=1, le=3650)
    @field_validator("root", mode="before")
    @classmethod
    def _blank_root_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


DEFAULT_PROFILE_ID = "default"
# Provider kinds are a closed server-side set (PLAN-PROVIDERS D8); display
# presets live in the web UI, not in the persisted contract.
PROVIDER_KINDS = ("omlx", "openai", "openai-compatible", "custom")
ProviderKind = Literal["omlx", "openai", "openai-compatible", "custom"]
# Bounded provider library (PLAN-PROVIDERS §2): enough for local + several cloud
# routes without letting the settings file grow unbounded.
MAX_AI_PROFILES = 30
_PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def _normalize_profile_id(value: str) -> str:
    """Lowercase/normalize a user-supplied profile id; ``""`` when unusable."""
    text = "-".join(value.strip().lower().split())
    return text if _PROFILE_ID_PATTERN.fullmatch(text) else ""


def _default_profile_name(base_url: str | None, chat_model: str) -> str:
    """Human label for the synthesized flat-field profile."""
    if base_url:
        from urllib.parse import urlsplit

        host = urlsplit(base_url).netloc or base_url
        return host
    return chat_model if chat_model and chat_model != "auto" else "Local AI"


class ProviderProfile(BaseModel):
    """One saved AI provider route (endpoint + credential + model defaults).

    Mirrors DSH's ``llm-pi-ai.providers`` entry shape: everything a request
    needs lives on the profile so switching is a pointer move, never a merge of
    half-updated global fields.
    """

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)
    kind: ProviderKind = "openai-compatible"
    base_url: str | None = Field(default="http://127.0.0.1:8000/v1", max_length=4096)
    api_key: str | None = Field(default=None, max_length=4096)
    chat_model: str = Field(default="auto", min_length=1, max_length=256)
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_output_tokens: int = Field(default=1200, ge=64, le=8192)
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=0.5, gt=0, le=10)
    max_models_response_bytes: int = Field(default=1_000_000, gt=0)
    # Server-owned: never accepted from a client payload.
    builtin: bool = False
    source: Literal["settings", "env", "default"] = "settings"
    from_env: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _id_is_slug(cls, value: object) -> str:
        normalized = _normalize_profile_id(value) if isinstance(value, str) else ""
        if not normalized:
            raise ValueError("Profile id must be 1-64 chars of a-z, 0-9, '.', '_' or '-'")
        return normalized

    @field_validator("name", "chat_model", mode="before")
    @classmethod
    def _trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_url", mode="before")
    @classmethod
    def _blank_base_url_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip("/") or None
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_api_key_is_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("chat_model")
    @classmethod
    def _model_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Model must not be blank")
        return value

    @property
    def display_name(self) -> str:
        return self.name or self.id


class AISettings(BaseModel):
    """Bounded, additive settings for read-only AI workflows.

    Since PLAN-PROVIDERS the flat fields below are the **currently applied
    configuration** (what every AI consumer reads), while ``profiles`` holds the
    saved multi-provider library and ``active_profile_id`` names the entry the
    flat fields were projected from.  The two views are kept consistent by
    :meth:`effective_profile` / :meth:`with_profile`; ``Runtime`` owns revision,
    persistence and rollback.
    """

    enabled: bool = True
    # The applied provider kind; kept for the status/probe contract. It follows
    # the active profile's ``kind`` (PLAN-PROVIDERS D6). ``omlx`` stays the
    # default so an untouched installation behaves exactly as before.
    provider: ProviderKind = "omlx"
    base_url: str | None = "http://127.0.0.1:8000/v1"
    # Optional bearer token for OpenAI-compatible endpoints that require
    # authentication. Empty/None means "send no Authorization header". The
    # value is never returned by the API (settings snapshots expose only
    # ``api_key_set``); it is stored in the instance settings file.
    api_key: str | None = None
    chat_model: str = "auto"
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_context_notes: int = Field(default=8, ge=1, le=50)
    max_context_chars_per_note: int = Field(default=12000, ge=500, le=100000)
    max_context_chars_total: int = Field(default=60000, ge=1000, le=300000)
    # Local models stream slowly: a 4B MLX model needs several seconds for one
    # completion, so the generation timeout must not be a connection-style
    # budget. Status discovery keeps its own short timeouts below.
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=0.5, gt=0, le=10)
    max_output_tokens: int = Field(default=1200, ge=64, le=8192)
    max_models_response_bytes: int = Field(default=1_000_000, gt=0)
    qwen_match_pattern: str = r"qwen3\.?5[-_ ]?4b"
    # Shell proxy support is opt-in. httpx's default (`trust_env=True`) reads
    # ``HTTP_PROXY``/``NO_PROXY`` from the environment and *raises* while parsing
    # a malformed one (e.g. a ``NO_PROXY`` containing an IPv6 literal), which
    # would break every local model call for a user who never asked LocalNote to
    # use a proxy at all. Set ``LOCALNOTE_AI__USE_ENV_PROXY=1`` to opt back in
    # when the endpoint really is reached through a corporate proxy.
    use_env_proxy: bool = False
    # Saved provider library. Empty for the single-provider legacy layout —
    # nothing is written to disk until the user actually adds a profile.
    profiles: list[ProviderProfile] = Field(default_factory=list, max_length=MAX_AI_PROFILES)
    active_profile_id: str = Field(default=DEFAULT_PROFILE_ID, min_length=1, max_length=64)

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_api_key_is_none(cls, value: object) -> object:
        """Blank/whitespace keys mean "no authentication" (never "")."""
        if value is None:
            return None
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed or None
        return value

    @field_validator("active_profile_id", mode="before")
    @classmethod
    def _blank_active_profile(cls, value: object) -> str:
        normalized = _normalize_profile_id(value) if isinstance(value, str) else ""
        return normalized or DEFAULT_PROFILE_ID

    @model_validator(mode="wrap")
    @classmethod
    def _consistent_profile_pointers(cls, data: Any, handler):
        """Keep ``active_profile_id`` resolvable, ids unique, flat fields applied.

        Projection lives here (not only in :meth:`effective_profile`) so every
        construction path — environment variables, the saved settings file, a
        partial update — leaves the flat fields equal to the applied profile.
        An empty library stays empty: the single-provider layout is only
        materialized when something actually writes (PLAN-PROVIDERS D3).
        """
        result = handler(data)
        if not isinstance(result, cls) or not result.profiles:
            return result
        seen: set[str] = set()
        for profile in result.profiles:
            if profile.id in seen:
                raise ValueError(f"Duplicate AI profile id: {profile.id}")
            seen.add(profile.id)
        if result.active_profile_id not in seen:
            result.active_profile_id = result.profiles[0].id
        supplied = (
            {key for key in data if key in cls.model_fields} if isinstance(data, dict) else set()
        )
        before = {field: getattr(result, field) for field in supplied}
        active = next(p for p in result.profiles if p.id == result.active_profile_id)
        result._project(active)
        # A flat field the caller supplied wins over the stored profile — unless
        # the caller also picked the profile, in which case the saved pointer is
        # authoritative (loading a saved state). This is what lets a partial
        # update edit the applied entry instead of silently reverting it.
        if "active_profile_id" in supplied:
            return result
        for field, value in before.items():
            setattr(result, field, value)
        return result

    def effective_profile(self) -> ProviderProfile:
        """The applied profile, projected so flat fields stay the source of truth.

        Read-only with respect to the library: with no saved profile it returns
        (and applies) the synthesized flat-field route without materializing an
        entry, so probing status or reading a snapshot can never turn a
        single-provider installation into a multi-provider one (D3).
        """
        if not self.profiles:
            profile = self.synthesized_profiles()[0]
        else:
            profile = next(
                (item for item in self.profiles if item.id == self.active_profile_id),
                # Defensive: the validator keeps this unreachable, but a read
                # path must never raise on a hand-assembled instance.
                self.profiles[0],
            )
        self._project(profile)
        return profile

    def with_flat_overrides(self, values: dict[str, Any]) -> AISettings:
        """Apply a validated flat update on top of the current applied route.

        The profile library is *not* rebuilt from a full field dump here: only
        the caller's own keys are honored, so an omitted tuning knob keeps the
        applied value and the profile view cannot silently reset it
        (PLAN-PROVIDERS D2). ``api_key`` keeps its documented semantics — absent
        or ``None`` preserves the stored secret, ``""`` clears it.
        """
        updates = dict(values)
        if "api_key" in updates:
            if updates["api_key"] is None:
                updates.pop("api_key")
            elif isinstance(updates["api_key"], str) and not updates["api_key"].strip():
                # Same normalization as the ``api_key`` validator: an explicit
                # blank clears the secret and is never persisted as "".
                updates["api_key"] = None
        flat = self.effective_profile().model_dump()
        flat.update(updates)
        flat["provider"] = flat.pop("kind")
        merged = self.model_copy(update=flat)
        merged.active_profile_id = self.active_profile_id
        if not self.profiles:
            return merged
        active = next(p for p in merged.profiles if p.id == merged.active_profile_id)
        return merged.with_profile(
            active.model_copy(update={k: v for k, v in flat.items() if k != "provider"}),
            activate=True,
        )

    def synthesized_profiles(self) -> list[ProviderProfile]:
        """Provider library as the UI must see it: never empty for a live install."""
        if self.profiles:
            return list(self.profiles)
        return [
            ProviderProfile(
                id=DEFAULT_PROFILE_ID,
                name=_default_profile_name(self.base_url, self.chat_model),
                kind=self.provider,
                base_url=self.base_url,
                api_key=self.api_key,
                chat_model=self.chat_model,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
                request_timeout_seconds=self.request_timeout_seconds,
                connect_timeout_seconds=self.connect_timeout_seconds,
                max_models_response_bytes=self.max_models_response_bytes,
                builtin=True,
                source="default",
            )
        ]

    def ensure_active_profile(self) -> AISettings:
        """Materialize the flat-field profile when no profiles are saved."""
        if self.profiles:
            return self
        self.profiles = [self.synthesized_profiles()[0].model_copy(update={"source": "settings"})]
        self.active_profile_id = self.profiles[0].id
        return self

    def with_profile(self, profile: ProviderProfile, *, activate: bool = True) -> AISettings:
        """Return a copy with ``profile`` upserted, optionally applied.

        Pure: neither ``self`` nor the applied flat fields of ``self`` change, so
        a failed transition leaves the running configuration untouched.
        """
        updated = self.model_copy(deep=True).ensure_active_profile()
        updated.profiles = [item for item in updated.profiles if item.id != profile.id]
        updated.profiles.append(profile)
        if activate:
            updated.active_profile_id = profile.id
            updated._project(profile)
        return updated

    def without_profile(self, profile_id: str) -> AISettings:
        """Return a copy without ``profile_id``; the applied entry stays applied."""
        updated = self.model_copy(deep=True).ensure_active_profile()
        updated.profiles = [item for item in updated.profiles if item.id != profile_id]
        if profile_id == updated.active_profile_id and updated.profiles:
            updated.active_profile_id = updated.profiles[0].id
        return updated

    def _project(self, profile: ProviderProfile) -> None:
        """Mirror a profile onto the flat fields (flat fields stay the truth)."""
        self.provider = profile.kind
        self.base_url = profile.base_url
        self.api_key = profile.api_key
        self.chat_model = profile.chat_model
        self.temperature = profile.temperature
        self.max_output_tokens = profile.max_output_tokens
        self.request_timeout_seconds = profile.request_timeout_seconds
        self.connect_timeout_seconds = profile.connect_timeout_seconds
        self.max_models_response_bytes = profile.max_models_response_bytes


_CRON_FIELD_BOUNDS: tuple[
    tuple[int, int],
    tuple[int, int],
    tuple[int, int],
    tuple[int, int],
    tuple[int, int],
] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),   # day of week (0 and 7 both mean Sunday)
)
_CRON_FIELD_NAMES = ("minute", "hour", "day", "month", "weekday")
_SCHEDULER_LEVEL2_ALLOWED = {"add_tags", "remove_tags"}


def _valid_cron(value: str) -> str:
    """Strict bounded cron subset: 5 fields of int / ``*`` / ``*/step``.

    Anything else (``?``, ranges, lists, names, arbitrary Python-like
    expressions) is rejected as a configuration error so the schedule can
    never diverge between validation and the backend triggers.
    """
    text = str(value).strip()
    fields = text.split()
    if len(fields) != 5:
        raise ValueError(
            "cron expression must have exactly 5 fields (minute hour day month weekday)"
        )
    for index, field in enumerate(fields):
        low, high = _CRON_FIELD_BOUNDS[index]
        token = field
        if token.startswith("*/"):
            step = token[2:]
            if not step.isdigit() or int(step) < 1:
                raise ValueError(f"cron {_CRON_FIELD_NAMES[index]} step must be a positive integer")
            continue
        if token == "*":
            continue
        if not token.isdigit():
            raise ValueError(
                f"cron {_CRON_FIELD_NAMES[index]} only allows integers, '*', or '*/step'"
            )
        number = int(token)
        if not low <= number <= high:
            raise ValueError(
                f"cron {_CRON_FIELD_NAMES[index]} out of range {low}..{high}"
            )
    return text


def _valid_timezone(value: str) -> str:
    """Validate a timezone name without guessing; never silently fall back."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = str(value).strip()
    if not name:
        raise ValueError("timezone must not be empty; set a valid IANA zone or 'UTC'")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        message = (
            f"unknown IANA timezone {name!r}; "
            "set a valid zone name such as 'UTC' or 'Asia/Shanghai'"
        )
        raise ValueError(message) from exc
    return name


class SchedulerSettings(BaseModel):
    """Local single-process scheduler knobs (M8, PLAN-M8 §5.2/§5.6).

    Defaults are the M8 safety posture: ``enabled=True`` (a visible scheduler
    that can be stopped), Daily 23:00 / Weekly Sunday 20:00 in ``timezone``,
    ``index_consistency`` off, Level-2 automatic execution off, one instance
    per task, coalesce on and bounded misfire grace.  Every cron/interval/time
    bound is validated here so a malformed schedule fails configuration
    loudly instead of being guessed at runtime.
    """

    enabled: bool = True
    timezone: str = "UTC"
    daily_cron: str = "0 23 * * *"
    weekly_cron: str = "0 20 * * 0"
    index_check_enabled: bool = False
    index_check_interval_hours: int = Field(default=24, ge=1, le=168)
    level2_auto_enabled: bool = False
    level2_auto_actions: list[str] = Field(default_factory=list, max_length=10)
    job_timeout_seconds: float = Field(default=300, gt=0, le=3600)
    misfire_grace_seconds: int = Field(default=3600, ge=0, le=86_400)
    max_instances: int = Field(default=1, ge=1, le=1)
    coalesce: bool = True
    stale_run_after_seconds: int = Field(default=3600, ge=60, le=604_800)

    @field_validator("daily_cron", "weekly_cron")
    @classmethod
    def _cron_fields(cls, value: str) -> str:
        return _valid_cron(value)

    @field_validator("timezone")
    @classmethod
    def _timezone_fields(cls, value: str) -> str:
        return _valid_timezone(value)

    @field_validator("level2_auto_actions")
    @classmethod
    def _level2_only_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            tag_action = value.strip().lower()
            if tag_action not in _SCHEDULER_LEVEL2_ALLOWED:
                raise ValueError(
                    "scheduler.level2_auto_actions only supports tag actions "
                    f"({sorted(_SCHEDULER_LEVEL2_ALLOWED)}), got {value!r}"
                )
            if tag_action not in normalized:
                normalized.append(tag_action)
        return normalized

    @model_validator(mode="after")
    def _level2_open_requires_actions(self) -> SchedulerSettings:
        if self.level2_auto_enabled and not self.level2_auto_actions:
            raise ValueError(
                "scheduler.level2_auto_enabled=True requires a non-empty "
                "level2_auto_actions whitelist (tag actions only)"
            )
        return self


_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
_SYNCHRONOUS_MODES = {"OFF", "NORMAL", "FULL", "EXTRA"}


class IndexSettings(BaseModel):
    """Derived-index knobs (M3 in-memory limits + M4 SQLite/FTS switches).

    M3: ``note_text_cap`` bounds how many characters of each note body are
    retained for keyword search so a large note cannot bloat the index.
    M4 (PLAN-M4 §5.3/§5.7): ``db_filename`` is the derived database file name
    inside ``.localnote``; ``fts_tokenizer`` selects the FTS5 tokenizer
    (default ``unicode61`` — Chinese short queries degrade to the substring
    path, see PLAN-M4 §5.4); ``journal_mode``/``synchronous``/``busy_timeout_ms``
    control SQLite PRAGMAs.  All defaults are backward compatible and every
    field is overridable through ``LOCALNOTE_INDEX__*`` environment variables.
    """

    note_text_cap: int = Field(default=1_000_000, gt=0)
    db_filename: str = Field(default="index.db", min_length=1)
    fts_tokenizer: str = "unicode61"
    journal_mode: str = "WAL"
    synchronous: str = "NORMAL"
    busy_timeout_ms: int = Field(default=5000, gt=0)
    # M8 index-consistency: ``auto_rebuild`` permits the *scheduler* job to
    # rebuild the derived index when it detects a mismatch.  Defaults off;
    # rebuild writes derived rows only, never Markdown bytes.
    auto_rebuild: bool = False

    @field_validator("journal_mode")
    @classmethod
    def _journal_mode_valid(cls, value: object) -> str:
        normalized = str(value).upper()
        if normalized not in _JOURNAL_MODES:
            raise ValueError(f"journal_mode must be one of {sorted(_JOURNAL_MODES)}")
        return normalized

    @field_validator("synchronous")
    @classmethod
    def _synchronous_valid(cls, value: object) -> str:
        normalized = str(value).upper()
        if normalized not in _SYNCHRONOUS_MODES:
            raise ValueError(f"synchronous must be one of {sorted(_SYNCHRONOUS_MODES)}")
        return normalized

    @field_validator("fts_tokenizer")
    @classmethod
    def _tokenizer_not_empty(cls, value: object) -> str:
        tokenizer = str(value).strip()
        if not tokenizer:
            raise ValueError("fts_tokenizer must not be empty")
        return tokenizer


class RagSettings(BaseModel):
    """Local-first RAG knobs (M14).

    Everything here is optional and safe by default: with no embedding endpoint
    configured the index uses the documented local fallback embedder and the
    vector path degrades, while the lexical (FTS) path keeps working. The chat
    model is *never* assumed to be able to embed — ``embedding_model`` is an
    independent setting, and a change to any of provider/model/dimension marks
    the stored vectors outdated instead of silently mixing two vector spaces.
    """

    enabled: bool = True

    # Embedding endpoint (independent from ai.*: chat and embedding models may
    # be entirely different servers).
    embedding_provider: str = "hash"
    embedding_base_url: str = ""
    embedding_model: str = "local-hash"
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    embedding_batch_size: int = Field(default=32, ge=1, le=512)
    embedding_dimension: int = Field(default=256, ge=0, le=8192)
    embedding_version: str = "v1"
    # Optional cosine floor for "nothing is relevant" (model dependent; 0.0
    # disables it). See server/rag/retrieval/vector.py::DEFAULT_MIN_SCORE.
    vector_min_score: float = Field(default=0.0, ge=-1.0, le=1.0)

    # Chunking
    chunk_target_tokens: int = Field(default=800, ge=50, le=8000)
    chunk_max_tokens: int = Field(default=1200, ge=50, le=16000)
    chunk_overlap_tokens: int = Field(default=100, ge=0, le=2000)

    # Retrieval
    fts_top_k: int = Field(default=30, ge=1, le=200)
    vector_top_k: int = Field(default=30, ge=1, le=200)
    fusion_top_k: int = Field(default=20, ge=1, le=200)
    rerank_top_k: int = Field(default=10, ge=1, le=200)
    context_top_k: int = Field(default=6, ge=1, le=50)
    rrf_k: int = Field(default=60, ge=1, le=1000)

    # Optional link/graph expansion (roadmap item ③, M14). Everything here is
    # neutral by default: with ``link_retrieval_enabled`` off the factory wires
    # no link retriever at all, so candidate generation, the RRF input lists and
    # the fused order stay byte-for-byte the pre-③ behaviour. The four weights
    # mirror ``server.rag.retrieval.link.LinkRetriever``'s constructor defaults
    # (1.0 / 0.8 / 0.6 / 0.4) and only rank *within* that retriever; the RRF
    # weight that scales its whole list is a constructor parameter deliberately
    # not surfaced here yet (see ``server.rag.factory``).
    link_retrieval_enabled: bool = False
    link_top_k: int = Field(default=20, ge=1, le=200)
    wikilink_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    backlink_weight: float = Field(default=0.8, ge=0.0, le=5.0)
    tag_weight: float = Field(default=0.6, ge=0.0, le=5.0)
    graph_weight: float = Field(default=0.4, ge=0.0, le=5.0)

    # Context budget
    context_max_tokens: int = Field(default=4000, ge=200, le=64000)
    context_max_tokens_per_document: int = Field(default=1800, ge=100, le=64000)
    context_merge_adjacent: bool = True
    context_merge_gap_lines: int = Field(default=5, ge=0, le=100)

    # Optional reranker (disabled by default: M14 adds no external dependency).
    # ``lexical`` runs locally; ``openai_compatible`` posts to
    # ``{reranker_base_url}/rerank`` (Jina/Cohere/vLLM style) and degrades to the
    # fused order whenever the endpoint is missing or failing.
    reranker_enabled: bool = False
    reranker_provider: str = "lexical"
    reranker_base_url: str = ""
    reranker_model: str = ""
    reranker_api_key: str = ""
    reranker_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    reranker_min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # Same opt-in as ``ai.use_env_proxy``: local embedding/rerank endpoints must
    # not break because of a malformed shell proxy variable.
    use_env_proxy: bool = False

    # Index maintenance
    index_on_startup: bool = False
    debounce_seconds: float = Field(default=1.5, ge=0.0, le=60)
    require_citation: bool = True
    include_retrieval_debug: bool = False

    @model_validator(mode="after")
    def _max_not_below_target(self) -> "RagSettings":
        """``chunk_max_tokens`` may never be smaller than the target.

        Enforced after the whole model is built (not per-field) so a partial
        settings patch that only raises the target is validated against the
        merged configuration instead of the request body alone.
        """
        if int(self.chunk_max_tokens) < int(self.chunk_target_tokens):
            raise ValueError("chunk_max_tokens must be >= chunk_target_tokens")
        return self

    def snapshot_view(self) -> dict[str, Any]:
        """Settings surfaced to the UI: identical shape, secret never echoed.

        ``embedding_api_key`` is replaced by ``embedding_api_key_set`` exactly
        like ``ai.api_key`` so the browser can never read a stored secret.
        """
        view = self.model_dump()
        view.pop("embedding_api_key", None)
        view.pop("reranker_api_key", None)
        view["embedding_api_key_set"] = bool(self.embedding_api_key)
        view["reranker_api_key_set"] = bool(self.reranker_api_key)
        return view

    @field_validator("reranker_provider")
    @classmethod
    def _known_reranker(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"lexical", "lexical_overlap", "local", "openai_compatible", "openai", "none"}:
            raise ValueError(
                "reranker_provider must be one of "
                "lexical/openai_compatible/none"
            )
        return normalized

    @field_validator("embedding_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"hash", "openai_compatible", "openai", "none"}:
            raise ValueError(
                "embedding_provider must be one of hash/openai_compatible/openai/none"
            )
        return normalized


class GraphSettings(BaseModel):
    """M5 read-only graph projection knobs (PLAN-M5 §5.3, defaults fixed).

    Defaults reproduce the plan contract — ``limit`` 500 (max 2000),
    ``depth`` 1 (max 3), broken edges included by default — and never change
    existing M1–M4 behaviour.  ``max_edges`` is an additive wire-safety cap
    for one response's edge list (edges are derived after node pagination);
    when it bites, ``page.truncated`` is set so the client can load more.
    Every field is overridable through ``LOCALNOTE_GRAPH__*``.
    """

    default_limit: int = Field(default=500, ge=1)
    max_limit: int = Field(default=2000, ge=1)
    default_depth: int = Field(default=1, ge=0)
    max_depth: int = Field(default=3, ge=1)
    default_include_broken: bool = True
    max_edges: int = Field(default=2000, ge=1)

    @model_validator(mode="after")
    def _consistent_bounds(self) -> GraphSettings:
        if self.max_limit < self.default_limit:
            raise ValueError("max_limit must be >= default_limit")
        if self.max_depth < self.default_depth:
            raise ValueError("max_depth must be >= default_depth")
        return self


_LEVEL2_ALLOWED = {"add_tags", "remove_tags"}


class PolicySettings(BaseModel):
    """M7 policy engine knobs (PLAN-M7 §8.1; default = closed Level 2).

    ``level2_auto_actions`` defaults to the empty list: automatic execution is
    switched off until a deployment explicitly opts into tag-only actions.
    When enabled, only ``add_tags``/``remove_tags`` may ever be listed here
    and the engine intersects the configured set with the hard allow-set.

    Level 2 auto can never be meaningful with a zero-char budget (every real
    tag bookkeeping change moves at least a few characters), so opening
    ``level2_auto_actions`` while ``level2_max_modified_chars`` stays 0 is a
    configuration error: it would silently deny every Level-2 tag action via
    ``character_budget`` while pretending Level 2 is open.  Deployments must
    set a positive budget (hard ceiling 2_000, see
    :data:`server.policies.rules.MAX_LEVEL2_MODIFIED_CHARS`).
    """

    enabled: bool = True
    max_actions: int = Field(default=20, ge=1, le=20)
    max_files: int = Field(default=10, ge=1, le=10)
    max_modified_chars: int = Field(default=20_000, ge=0, le=20_000)
    level2_max_files: int = Field(default=1, ge=1, le=10)
    level2_max_modified_chars: int = Field(default=0, ge=0, le=2_000)
    level2_auto_actions: list[str] = Field(default_factory=list, max_length=10)
    protected_path_prefixes: list[str] = Field(
        default_factory=lambda: [".localnote"], max_length=20
    )

    @field_validator("level2_auto_actions")
    @classmethod
    def _level2_only_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            tag_action = value.strip().lower()
            if tag_action not in _LEVEL2_ALLOWED:
                raise ValueError(
                    f"level2_auto_actions only supports tag actions, got {value!r}"
                )
            if tag_action not in normalized:
                normalized.append(tag_action)
        return normalized

    @model_validator(mode="after")
    def _level2_open_requires_budget(self) -> PolicySettings:
        if self.level2_auto_actions and self.level2_max_modified_chars <= 0:
            raise ValueError(
                "level2_max_modified_chars must be > 0 when "
                "level2_auto_actions is non-empty (Level 2 tag-only auto "
                "cannot run on a zero-char budget); set a positive value up "
                "to the 2000-char ceiling"
            )
        return self


class HistorySettings(BaseModel):
    """M7 History caps (PLAN-M7 §8.1) + M8 retention (PLAN-M8 §5.6).

    Retention only ever removes *derived* History (``ai_jobs``/journal and
    ``scheduler_runs``) that is terminal and older than ``retention_days``;
    active/``awaiting_confirmation``/``recovery_required`` records are kept,
    the newest run per task is kept, and Vault files are never touched.
    """

    page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=20, le=500)
    max_journal_bytes: int = Field(default=10_000_000, ge=100_000, le=50_000_000)
    max_diff_bytes: int = Field(default=200_000, ge=10_000, le=5_000_000)
    retention_days: int = Field(default=30, ge=1, le=3650)
    cleanup_enabled: bool = True
    cleanup_interval_hours: int = Field(default=24, ge=1, le=168)
    max_scheduler_runs: int = Field(default=1000, ge=100, le=100_000)

    @model_validator(mode="after")
    def _consistent_page_bounds(self) -> HistorySettings:
        if self.max_page_size < self.page_size:
            raise ValueError("max_page_size must be >= page_size")
        return self


class RecoverySettings(BaseModel):
    """M7 transaction/undo knobs (PLAN-M7 §8.1)."""

    enabled: bool = True
    max_undo_bytes: int = Field(default=10_000_000, ge=100_000, le=50_000_000)
    confirmation_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED_DELIMITER,
        extra="ignore",
    )

    server: ServerSettings = ServerSettings()
    vault: VaultSettings = VaultSettings()
    ai: AISettings = AISettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    index: IndexSettings = IndexSettings()
    rag: RagSettings = RagSettings()
    graph: GraphSettings = GraphSettings()
    policy: PolicySettings = PolicySettings()
    history: HistorySettings = HistorySettings()
    recovery: RecoverySettings = RecoverySettings()

    @model_validator(mode="before")
    @classmethod
    def _apply_flat_env_aliases(cls, values: Any) -> Any:
        """Apply flat ``LOCALNOTE_HOST``-style vars unless the nested var is set.

        pydantic-settings only surfaces env vars it knows about, so flat
        aliases are read from the process environment here.
        """
        if not isinstance(values, dict):
            return values
        for flat, (section, field) in _FLAT_ALIASES.items():
            nested = f"{ENV_PREFIX}{section.upper()}{ENV_NESTED_DELIMITER}{field.upper()}"
            if flat in os.environ and nested not in os.environ:
                section_values = values.setdefault(section, {})
                if isinstance(section_values, dict):
                    section_values[field] = os.environ[flat]
        return values
