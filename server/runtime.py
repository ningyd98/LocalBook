"""Instance configuration and exclusive, rollback-safe workspace transitions."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import MAX_AI_PROFILES, AISettings, ProviderProfile, RagSettings, Settings
from .vault.lifecycle import VaultLifecycle

log = logging.getLogger("localnote.runtime")
SESSION_HEADER = "X-LocalNote-Vault-Session"

# Flat AI settings a provider profile carries (PLAN-PROVIDERS D2). Kept in one
# place so persistence, projection and the wire view cannot drift apart. The
# profile's ``kind`` is the applied provider discriminator on ``AISettings``.
_PROFILE_FIELDS = (
    "kind",
    "base_url",
    "api_key",
    "chat_model",
    "temperature",
    "max_output_tokens",
    "request_timeout_seconds",
    "connect_timeout_seconds",
    "max_models_response_bytes",
)
_PROFILE_SAVED_FIELDS = ("id", "name", *_PROFILE_FIELDS)


def _profile_patch(ai: AISettings) -> dict[str, Any]:
    """The profile fields of a flat AI configuration, ready for ``model_copy``."""
    return {
        field: getattr(ai, "provider" if field == "kind" else field)
        for field in _PROFILE_FIELDS
    }


class SettingsError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def validate_endpoint(value: str | None) -> str | None:
    text = (value or "").strip().rstrip("/")
    if not text:
        return None
    try:
        url = urlsplit(text)
        valid = (url.scheme in {"http", "https"} and url.hostname and
                 not url.username and not url.password and not url.query and not url.fragment)
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise SettingsError("invalid_ai_endpoint", "Use an HTTP(S) service URL without credentials or query parameters.", 422)
    return text


class ConfigRepository:
    def __init__(self, path: Path | None):
        self.path = path

    @classmethod
    def for_settings(cls, settings: Settings, *, isolated: bool = False):
        override = os.environ.get("LOCALNOTE_SETTINGS_FILE")
        path = Path(override).expanduser() if override else (
            None if isolated else Path.home() / ".config/localnote/instances" /
            f"{settings.server.host}-{settings.server.port}" / "settings.json"
        )
        return cls(path)

    def load(self, settings: Settings) -> tuple[Settings, int]:
        if self.path is None or not self.path.exists():
            return settings, 1
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ai = AISettings.model_validate({**settings.ai.model_dump(), **data["ai"]})
            ai.base_url = validate_endpoint(ai.base_url)
            vault = settings.vault.model_copy(update={"root": Path(data["vault_root"]) if data.get("vault_root") else None})
            return settings.model_copy(update={"ai": ai, "vault": vault}), max(1, int(data["revision"]))
        except SettingsError:
            raise
        except Exception as exc:
            raise SettingsError("settings_unreadable", "Saved configuration cannot be read. Restore the configuration file before starting.", 503) from exc

    def save(self, settings: Settings, revision: int):
        if self.path is None:
            return  # Explicit settings injection has an isolated in-memory repository.
        ai: dict[str, Any] = {
            k: getattr(settings.ai, k) for k in ("enabled", "base_url", "api_key", "chat_model")
        }
        # The provider library is written only once the user has one: a
        # single-provider installation keeps the exact legacy file shape
        # (PLAN-PROVIDERS D3), so downgrades and hand-edits stay simple.
        if settings.ai.profiles:
            ai["active_profile_id"] = settings.ai.active_profile_id
            ai["profiles"] = [
                profile.model_dump(include=set(_PROFILE_SAVED_FIELDS))
                for profile in settings.ai.profiles
            ]
        data = {"revision": revision, "vault_root": str(settings.vault.root) if settings.vault.root else None,
                "ai": ai}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                # replace is the commit point. A directory fsync unsupported by
                # the filesystem must not report a rollback after committing.
                log.warning("Settings saved; directory durability sync unavailable")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class Runtime:
    def __init__(self, app: Any, settings: Settings, repository: ConfigRepository, revision: int):
        self.app, self.settings, self.repository, self.revision = app, settings, repository, revision
        self.session_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.changing = False
        self.in_flight = 0
        self.lifecycle: VaultLifecycle | None = None
        self.scheduler: Any = None
        self.agent: Any = None

    def _services(self, settings: Settings, lifecycle: VaultLifecycle):
        from .api.dependencies import (
            _agent_service_from,
            _history_from_index,
            build_scheduler_service,
        )
        history = _history_from_index(lifecycle.index_service)
        vault = lifecycle.service
        agent = _agent_service_from(settings, vault, lifecycle.index_service, history) if vault else None
        scheduler = build_scheduler_service(settings, vault=vault, index=lifecycle.index_service, agent_service=agent, history=history)
        return agent, scheduler

    def _publish(self):
        state = self.app.state
        state.settings = self.settings
        for name, value in {
            "vault_lifecycle": self.lifecycle,
            "vault_service": self.lifecycle.service if self.lifecycle else None,
            "index_service": self.lifecycle.index_service if self.lifecycle else None,
            "rag_stack": self.lifecycle.rag_stack if self.lifecycle else None,
            "agent_job_service": self.agent, "scheduler_service": self.scheduler,
        }.items():
            if value is not None:
                setattr(state, name, value)
            elif hasattr(state, name):
                delattr(state, name)

    def startup(self):
        self.lifecycle = VaultLifecycle(
            self.settings.vault,
            index=self.settings.index,
            rag=self.settings.rag,
            note_text_cap=self.settings.index.note_text_cap,
        )
        self.lifecycle.startup()
        self.agent, self.scheduler = self._services(self.settings, self.lifecycle)
        self._publish()
        self.scheduler.scan_recovery(mark=True)
        try:
            self.scheduler.start()
        except Exception:
            log.exception("scheduler startup degraded")
        if self.scheduler.network_warning():
            log.warning("API is listening outside loopback without authentication or HTTPS")

    def shutdown(self):
        if self.scheduler:
            self.scheduler.stop(wait=True, timeout=3)
        if self.lifecycle:
            self.lifecycle.shutdown()

    def _profile_views(self) -> list[dict[str, Any]]:
        """Wire view of the provider library: never carries a key, only its presence."""
        views: list[dict[str, Any]] = []
        for profile in self.settings.ai.synthesized_profiles():
            views.append({
                "id": profile.id,
                "name": profile.display_name,
                "kind": profile.kind,
                "base_url": profile.base_url,
                "chat_model": profile.chat_model,
                "temperature": profile.temperature,
                "max_output_tokens": profile.max_output_tokens,
                "request_timeout_seconds": profile.request_timeout_seconds,
                "connect_timeout_seconds": profile.connect_timeout_seconds,
                "max_models_response_bytes": profile.max_models_response_bytes,
                "api_key_set": bool(profile.api_key),
                "builtin": profile.builtin,
                "source": profile.source,
                "from_env": profile.from_env,
                "is_active": profile.id == self.settings.ai.active_profile_id,
            })
        return views

    def snapshot(self):
        with self.lock:
            applied = self.settings.ai.effective_profile()
            return {"revision": self.revision, "vault_session_id": self.session_id,
                    "changing": self.changing, "version": self.app.version,
                    "vault": {"root": str(self.settings.vault.root) if self.settings.vault.root else None,
                              "status": "ready" if self.lifecycle and self.lifecycle.service else
                              "not_configured" if not self.settings.vault.root else "unavailable"},
                    # The API key is never echoed back; only whether one is stored.
                    "ai": {**{k: getattr(self.settings.ai, k)
                              for k in ("enabled", "base_url", "chat_model")},
                           "active_profile_id": applied.id,
                           "profiles": self._profile_views(),
                           "api_key_set": bool(self.settings.ai.api_key)},
                    "rag": self.settings.rag.snapshot_view()}

    @contextmanager
    def request(self, session: str | None, *, write: bool):
        with self.lock:
            if self.changing:
                raise SettingsError("workspace_switching", "The workspace is switching. Please retry shortly.", 503)
            if write and not session:
                raise SettingsError("vault_session_required", "Refresh the workspace before making changes.", 428)
            if session and session != self.session_id:
                raise SettingsError("vault_session_changed", "The vault changed in another page. Your local draft has been preserved.")
            self.in_flight += 1
        try:
            yield
        finally:
            with self.lock:
                self.in_flight -= 1

    @contextmanager
    def transition(self, revision: int, session: str | None = None):
        with self.lock:
            if self.changing or self.in_flight:
                raise SettingsError("runtime_busy", "A request or task is running. Please retry when it finishes.")
            if revision != self.revision:
                raise SettingsError("settings_conflict", "Settings changed in another page. Reload the current settings.")
            if session is not None and session != self.session_id:
                raise SettingsError("vault_session_changed", "The active vault has changed. Reload settings.")
            if self.scheduler and not self.scheduler.pause_for_reconfigure():
                raise SettingsError("runtime_busy", "A background task is still running. Please retry when it finishes.")
            self.changing = True
        try:
            yield
        finally:
            with self.lock:
                self.changing = False
                if self.scheduler:
                    self.scheduler.resume_after_reconfigure()

    def apply_ai(self, values: dict[str, Any], revision: int):
        values["base_url"] = validate_endpoint(values.get("base_url"))
        # Omitted/None api_key means "keep the stored key"; "" clears it.
        # The settings UI never receives the stored value, so it must be able
        # to submit a partial update without wiping the key.
        values = {k: v for k, v in values.items() if not (k == "api_key" and v is None)}
        # Only the caller's own keys are honored: the applied route supplies
        # everything else, so a partial update edits the applied entry instead
        # of resetting its tuning. An edit on top of an empty library stays a
        # flat edit — no profile is materialized — which keeps the legacy
        # single-provider settings file shape intact (PLAN-PROVIDERS D2/D3).
        return self._commit_ai(self.settings.ai.with_flat_overrides(values), revision)

    def _commit_ai(self, ai: AISettings, revision: int):
        """Apply one validated AI configuration, or keep the previous one.

        Every AI mutation shares this path: ``transition`` guards revision and
        excludes concurrent Vault switches, then the Agent/scheduler pair is
        rebuilt and only committed after the settings file is durable.
        """
        with self.transition(revision):
            updated = self.settings.model_copy(update={"ai": ai})
            if self.lifecycle is None:
                raise SettingsError("runtime_unavailable", "Application startup is not complete.", 503)
            old = self.scheduler
            scheduler = None
            try:
                agent, scheduler = self._services(updated, self.lifecycle)
                scheduler.pause_for_reconfigure()
                scheduler.start()
                self.repository.save(updated, self.revision + 1)
            except Exception as exc:
                if scheduler:
                    scheduler.stop()
                raise SettingsError("settings_apply_failed", "Configuration could not be applied. Previous settings are retained.", 500) from exc
            self.settings, self.agent, self.scheduler = updated, agent, scheduler
            self.revision += 1
            self._publish()
            try:
                old.stop()
            except Exception:
                log.exception("Previous scheduler cleanup failed after configuration commit")
        return self.snapshot()

    def apply_rag(self, values: dict[str, Any], revision: int):
        """Apply validated RAG settings and rebuild the derived RAG stack.

        Only the RAG layer is reconfigured: the Vault, its watcher, the M4
        derived index and every note stay exactly as they are.
        """
        # ``model_dump()`` (not the UI snapshot, which masks the secret) so an
        # omitted key keeps the stored one and "" clears it.
        merged = self.settings.rag.model_dump()
        allowed = set(merged)
        merged.update(
            {
                key: value
                for key, value in values.items()
                # Only declared RagSettings fields apply. ``None`` always means
                # "keep what is stored" — including the API key, so a partial
                # edit can never blank a stored secret by omission; submitting
                # the empty string is the explicit way to clear it.
                if key in allowed and value is not None
            }
        )
        try:
            updated = RagSettings(**merged)
        except Exception as exc:
            log.info("rag settings rejected: %s", exc)
            raise SettingsError(
                "invalid_rag_settings",
                "Those RAG settings are not valid. Previous settings are retained.",
                422,
            ) from exc
        with self.transition(revision):
            updated_settings = self.settings.model_copy(update={"rag": updated})
            if self.lifecycle is None:
                raise SettingsError("runtime_unavailable", "Application startup is not complete.", 503)
            try:
                self.lifecycle.reconfigure_rag(updated)
                self.repository.save(updated_settings, self.revision + 1)
            except Exception as exc:
                raise SettingsError(
                    "settings_apply_failed",
                    "RAG configuration could not be applied. Previous settings are retained.",
                    500,
                ) from exc
            self.settings = updated_settings
            self.revision += 1
            self._publish()
        return self.snapshot()

    def _find_profile(self, profile_id: str) -> ProviderProfile:
        for profile in self.settings.ai.synthesized_profiles():
            if profile.id == profile_id:
                return profile
        raise SettingsError(
            "ai_profile_not_found",
            "That AI provider profile no longer exists. Reload the settings.",
            404,
        )

    def activate_profile(self, profile_id: str, revision: int):
        """Switch the applied provider route; a no-op switch keeps the revision."""
        with self.lock:
            profile = self._find_profile(profile_id)
            already_applied = profile.id == self.settings.ai.active_profile_id
        if already_applied:
            # Idempotent no-op: still validated against the revision so a stale
            # page can never believe it switched, but nothing is persisted and
            # the revision does not move.
            with self.transition(revision):
                return self.snapshot()
        return self._commit_ai(self.settings.ai.with_profile(profile, activate=True), revision)

    def upsert_profile(self, profile: ProviderProfile, revision: int, *, activate: bool = True):
        """Create or replace one provider profile, optionally applying it."""
        ai = self.settings.ai
        existing = {item.id for item in ai.synthesized_profiles()}
        # A brand-new id needs room; replacing an existing one never grows the list.
        if ai.profiles and profile.id not in existing and len(ai.profiles) >= MAX_AI_PROFILES:
            raise SettingsError(
                "ai_profiles_full",
                f"At most {MAX_AI_PROFILES} AI provider profiles are supported.",
                422,
            )
        return self._commit_ai(ai.with_profile(profile, activate=activate), revision)

    def delete_profile(self, profile_id: str, revision: int):
        """Remove a provider profile. The applied route and the last one are protected."""
        with self.lock:
            profile = self._find_profile(profile_id)
            active_id = self.settings.ai.active_profile_id
            total = len(self.settings.ai.synthesized_profiles())
            if profile.id == active_id:
                raise SettingsError(
                    "ai_profile_active",
                    "Switch to another provider before deleting the active profile.",
                    409,
                )
            if total <= 1:
                raise SettingsError(
                    "ai_profile_last",
                    "The last AI provider profile cannot be deleted.",
                    409,
                )
            ai = self.settings.ai.without_profile(profile.id)
        return self._commit_ai(ai, revision)

    def switch_vault(self, root: str, revision: int, session: str):
        target = Path(root).expanduser()
        if not target.is_absolute() or not target.is_dir() or target.is_symlink():
            raise SettingsError("invalid_vault_root", "Choose an existing absolute directory, not a symbolic link.", 422)
        target = target.resolve()
        with self.transition(revision, session):
            if self.settings.vault.root and target == self.settings.vault.root.resolve():
                return {**self.snapshot(), "changing": False}
            if self.repository.path and self.repository.path.resolve().is_relative_to(target):
                raise SettingsError("invalid_vault_root", "Configuration must remain outside the vault.", 422)
            updated = self.settings.model_copy(update={"vault": self.settings.vault.model_copy(update={"root": target})})
            candidate = VaultLifecycle(
                updated.vault,
                index=updated.index,
                rag=updated.rag,
                note_text_cap=updated.index.note_text_cap,
            )
            scheduler = None
            try:
                candidate.startup(start_watcher=False)
                if not candidate.service or not candidate.index_service or candidate.index_service.build_state != "ready":
                    raise SettingsError("vault_prepare_failed", "The new vault or its index could not be initialized.", 422)
                agent, scheduler = self._services(updated, candidate)
                scheduler.scan_recovery(mark=True)
                scheduler.pause_for_reconfigure()
                candidate.service.start()
                scheduler.start()
                self.repository.save(updated, self.revision + 1)
            except Exception as exc:
                if scheduler:
                    scheduler.stop()
                candidate.shutdown()
                if isinstance(exc, SettingsError):
                    raise
                raise SettingsError("vault_switch_failed", "Switch failed. The previous vault and configuration are retained.", 500) from exc
            previous, previous_scheduler = self.lifecycle, self.scheduler
            self.settings, self.lifecycle, self.agent, self.scheduler = updated, candidate, agent, scheduler
            self.session_id = uuid.uuid4().hex
            self.revision += 1
            self._publish()
            try:
                if previous_scheduler:
                    previous_scheduler.stop()
                if previous:
                    previous.shutdown()
            except Exception:
                log.exception("Previous workspace cleanup failed after configuration commit")
        return self.snapshot()
