"""Tests for Settings: secret handling (H3) and URL validation (M1)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from tender_agent.settings import Settings


def _make_settings(**overrides: object) -> Settings:
    """Build hermetic Settings without reading a .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# ── H3: live secrets are SecretStr, not plain str ────────────────────────────


def test_api_keys_are_secret_str() -> None:
    """H3: provider API keys are stored as SecretStr."""
    settings = _make_settings(openai_api_key="sk-supersecret")
    assert isinstance(settings.openai_api_key, SecretStr)
    assert settings.openai_api_key.get_secret_value() == "sk-supersecret"


def test_smtp_password_is_secret_str() -> None:
    """H3: the SMTP password is stored as SecretStr."""
    settings = _make_settings(smtp_password="hunter2")
    assert isinstance(settings.smtp_password, SecretStr)
    assert settings.smtp_password.get_secret_value() == "hunter2"


def test_secrets_are_masked_in_repr() -> None:
    """H3: secrets do not leak through repr()/str() of the Settings object."""
    settings = _make_settings(
        openai_api_key="sk-supersecret",
        smtp_password="hunter2",
    )
    rendered = repr(settings) + str(settings)
    assert "sk-supersecret" not in rendered
    assert "hunter2" not in rendered


def test_unset_api_keys_default_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured provider key stays None."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _make_settings()
    assert settings.anthropic_api_key is None


# ── M1: PROZORRO_API_BASE scheme validation ──────────────────────────────────


def test_prozorro_api_base_accepts_https() -> None:
    """M1: an https base URL is accepted."""
    settings = _make_settings(prozorro_api_base="https://example.com/api")
    assert settings.prozorro_api_base == "https://example.com/api"


def test_prozorro_api_base_accepts_http() -> None:
    """M1: a plain http base URL is accepted."""
    settings = _make_settings(prozorro_api_base="http://localhost:8080/api")
    assert settings.prozorro_api_base == "http://localhost:8080/api"


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/api",
        "gopher://example.com",
        "not-a-url",
    ],
)
def test_prozorro_api_base_rejects_non_http_scheme(bad_url: str) -> None:
    """M1: non-http(s) schemes are rejected with a clear error."""
    with pytest.raises(ValidationError, match="must be an http"):
        _make_settings(prozorro_api_base=bad_url)
