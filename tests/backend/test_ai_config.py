"""M6 config contract tests (PLAN-M6 §9.1.1): defaults, env parsing, bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.ai.service import AIStatusService
from server.config import AISettings, Settings


def test_m6_ai_settings_defaults_match_plan() -> None:
    ai = AISettings()
    assert ai.enabled is True
    assert ai.provider == "omlx"
    assert ai.base_url == "http://127.0.0.1:8000/v1"
    assert ai.chat_model == "auto"
    assert ai.temperature == 0.1
    assert ai.max_context_notes == 8
    assert ai.max_context_chars_per_note == 12000
    assert ai.max_context_chars_total == 60000
    assert ai.request_timeout_seconds == 2.0
    assert ai.connect_timeout_seconds == 0.5
    assert ai.max_output_tokens == 1200


def test_ai_settings_all_m6_fields_are_present() -> None:
    for field in (
        "enabled",
        "provider",
        "base_url",
        "chat_model",
        "temperature",
        "max_context_notes",
        "max_context_chars_per_note",
        "max_context_chars_total",
        "request_timeout_seconds",
        "connect_timeout_seconds",
        "max_output_tokens",
        "max_models_response_bytes",
        "qwen_match_pattern",
    ):
        assert field in AISettings.model_fields, field


def test_nested_env_overrides_ai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALNOTE_AI__CHAT_MODEL", "Qwen3.5-4B-Instruct")
    monkeypatch.setenv("LOCALNOTE_AI__TEMPERATURE", "0.3")
    monkeypatch.setenv("LOCALNOTE_AI__MAX_OUTPUT_TOKENS", "2048")
    monkeypatch.setenv("LOCALNOTE_AI__ENABLED", "false")
    settings = Settings()
    assert settings.ai.chat_model == "Qwen3.5-4B-Instruct"
    assert settings.ai.temperature == 0.3
    assert settings.ai.max_output_tokens == 2048
    assert settings.ai.enabled is False


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"temperature": 1.5}, "temperature"),
        ({"temperature": -0.1}, "temperature"),
        ({"max_output_tokens": 32}, "max_output_tokens"),
        ({"max_output_tokens": 20000}, "max_output_tokens"),
        ({"max_context_notes": 0}, "max_context_notes"),
        ({"max_context_notes": 100}, "max_context_notes"),
        ({"request_timeout_seconds": 0}, "request_timeout_seconds"),
        ({"request_timeout_seconds": 999}, "request_timeout_seconds"),
        ({"max_context_chars_per_note": 10}, "max_context_chars_per_note"),
        ({"max_context_chars_total": 50}, "max_context_chars_total"),
        # Audit S4: provider is locked to the wired oMLX route.
        ({"provider": "openai"}, "provider"),
    ],
)
def test_ai_settings_bounds_are_strict(patch: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError):
        AISettings(**patch)


def test_disabled_ai_flag_is_explicit() -> None:
    settings = Settings(ai=AISettings(enabled=False, base_url="http://127.0.0.1:8000/v1"))
    assert settings.ai.enabled is False


async def test_blank_base_url_degrades_to_not_configured_without_http() -> None:
    async def _must_not_be_called():  # pragma: no cover
        raise AssertionError("no HTTP request may happen for an empty base_url")

    service = AIStatusService(base_url="", client_factory=_must_not_be_called)  # type: ignore[arg-type]
    result = await service.check()
    assert result.status.value == "not_configured"
    assert result.error_code == "not_configured"
