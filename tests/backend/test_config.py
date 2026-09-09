"""Settings defaults and overrides (PLAN 8.1 config matrix + M1 vault knobs
+ M4 index SQLite knobs)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from server.config import IndexSettings, Settings, VaultSettings


def test_index_defaults_are_backward_compatible() -> None:
    settings = Settings()
    index = settings.index
    assert index.note_text_cap == 1_000_000
    # M4 SQLite/FTS knobs (PLAN-M4 §5.7): defaults keep M3 behaviour.
    assert index.db_filename == "index.db"
    assert index.fts_tokenizer == "unicode61"
    assert index.journal_mode == "WAL"
    assert index.synchronous == "NORMAL"
    assert index.busy_timeout_ms == 5000


def test_index_nested_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALNOTE_INDEX__NOTE_TEXT_CAP", "250000")
    monkeypatch.setenv("LOCALNOTE_INDEX__DB_FILENAME", "custom.db")
    monkeypatch.setenv("LOCALNOTE_INDEX__FTS_TOKENIZER", "trigram")
    monkeypatch.setenv("LOCALNOTE_INDEX__JOURNAL_MODE", "delete")
    monkeypatch.setenv("LOCALNOTE_INDEX__SYNCHRONOUS", "full")
    monkeypatch.setenv("LOCALNOTE_INDEX__BUSY_TIMEOUT_MS", "250")

    index = Settings().index
    assert index.note_text_cap == 250000
    assert index.db_filename == "custom.db"
    assert index.fts_tokenizer == "trigram"
    assert index.journal_mode == "DELETE"
    assert index.synchronous == "FULL"
    assert index.busy_timeout_ms == 250


def test_index_invalid_values_rejected() -> None:
    with pytest.raises(ValidationError):
        IndexSettings(journal_mode="bogus")
    with pytest.raises(ValidationError):
        IndexSettings(synchronous="sometimes")
    with pytest.raises(ValidationError):
        IndexSettings(db_filename="")
    with pytest.raises(ValidationError):
        IndexSettings(busy_timeout_ms=0)
    with pytest.raises(ValidationError):
        IndexSettings(fts_tokenizer="   ")  # blank not allowed


def test_defaults_with_empty_environment() -> None:
    settings = Settings()
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 3780
    assert settings.server.cors_origins == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    assert settings.vault.root is None
    assert settings.ai.base_url == "http://127.0.0.1:8000/v1"
    assert settings.ai.request_timeout_seconds == 60.0
    assert settings.ai.connect_timeout_seconds == 0.5
    assert settings.scheduler.enabled is True  # M8: visible-but-stoppable default
    assert settings.scheduler.daily_cron == "0 23 * * *"
    assert settings.scheduler.weekly_cron == "0 20 * * 0"
    assert settings.scheduler.level2_auto_enabled is False
    assert settings.scheduler.level2_auto_actions == []
    assert settings.history.retention_days == 30
    assert settings.history.max_scheduler_runs == 1000


def test_vault_defaults_are_inert_and_do_not_touch_filesystem() -> None:
    settings = Settings()
    vault = settings.vault
    assert vault.root is None
    assert vault.watcher_enabled is True
    assert vault.watcher_debounce_ms == 200
    assert vault.max_file_bytes == 50 * 1024 * 1024
    # Construction must never scan, create or probe any directory.
    assert VaultSettings(root=None).root is None


def test_vault_settings_single_validation() -> None:
    vault = VaultSettings(
        root="/tmp/x",
        watcher_enabled=False,
        watcher_debounce_ms=1200,
        max_file_bytes=1024,
    )
    assert str(vault.root) == "/tmp/x"
    assert vault.watcher_enabled is False
    assert vault.watcher_debounce_ms == 1200
    assert vault.max_file_bytes == 1024


def test_vault_invalid_runtime_values_rejected() -> None:
    with pytest.raises(ValidationError):
        VaultSettings(watcher_debounce_ms=-1)
    with pytest.raises(ValidationError):
        VaultSettings(max_file_bytes=0)
    with pytest.raises(ValidationError):
        VaultSettings(max_file_bytes=-5)


def test_vault_nested_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALNOTE_VAULT__ROOT", "/tmp/nested-vault")
    monkeypatch.setenv("LOCALNOTE_VAULT__WATCHER_ENABLED", "false")
    monkeypatch.setenv("LOCALNOTE_VAULT__WATCHER_DEBOUNCE_MS", "350")
    monkeypatch.setenv("LOCALNOTE_VAULT__MAX_FILE_BYTES", "1048576")

    settings = Settings()
    assert str(settings.vault.root) == "/tmp/nested-vault"
    assert settings.vault.watcher_enabled is False
    assert settings.vault.watcher_debounce_ms == 350
    assert settings.vault.max_file_bytes == 1048576


def test_vault_flat_aliases_and_nested_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALNOTE_VAULT_ROOT", "/tmp/flat")
    monkeypatch.setenv("LOCALNOTE_VAULT_WATCHER_ENABLED", "false")
    flat = Settings()
    assert flat.vault.root == Path("/tmp/flat")
    assert flat.vault.watcher_enabled is False

    # Nested always wins over the flat alias.
    monkeypatch.setenv("LOCALNOTE_VAULT__ROOT", "/tmp/nested-wins")
    settings = Settings()
    assert str(settings.vault.root) == "/tmp/nested-wins"
    assert settings.vault.watcher_enabled is False


def test_blank_vault_root_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALNOTE_VAULT__ROOT", "   ")
    assert Settings().vault.root is None


def test_nested_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALNOTE_SERVER__HOST", "0.0.0.0")
    monkeypatch.setenv("LOCALNOTE_SERVER__PORT", "3790")
    monkeypatch.setenv(
        "LOCALNOTE_SERVER__CORS_ORIGINS", '["http://localhost:3000"]'
    )
    monkeypatch.setenv("LOCALNOTE_VAULT__ROOT", "/tmp/fake-vault")
    monkeypatch.setenv("LOCALNOTE_AI__BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("LOCALNOTE_AI__REQUEST_TIMEOUT_SECONDS", "0.7")
    monkeypatch.setenv("LOCALNOTE_SCHEDULER__ENABLED", "true")

    settings = Settings()
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 3790
    assert settings.server.cors_origins == ["http://localhost:3000"]
    assert str(settings.vault.root) == "/tmp/fake-vault"
    assert settings.ai.base_url == "http://127.0.0.1:9999/v1"
    assert settings.ai.request_timeout_seconds == 0.7
    assert settings.scheduler.enabled is True


def test_flat_aliases_apply_when_nested_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALNOTE_PORT", "3810")
    monkeypatch.setenv("LOCALNOTE_HOST", "127.0.0.2")
    monkeypatch.setenv("LOCALNOTE_OMLX_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("LOCALNOTE_VAULT_ROOT", "/tmp/flat-vault")

    settings = Settings()
    assert settings.server.port == 3810
    assert settings.server.host == "127.0.0.2"
    assert settings.ai.base_url == "http://127.0.0.1:9000/v1"
    assert str(settings.vault.root) == "/tmp/flat-vault"


def test_nested_variable_wins_over_flat_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALNOTE_PORT", "3810")
    monkeypatch.setenv("LOCALNOTE_SERVER__PORT", "3820")

    settings = Settings()
    assert settings.server.port == 3820


def test_empty_ai_base_url_is_allowed_but_treated_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Empty string is legal config; the service normalizes it to None
    # ("not_configured") before probing. See server/ai/service.py.
    monkeypatch.setenv("LOCALNOTE_AI__BASE_URL", "")
    settings = Settings()
    assert settings.ai.base_url == ""


def test_invalid_port_value_raises_clear_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALNOTE_SERVER__PORT", "not-a-port")
    with pytest.raises(ValidationError):
        Settings()
