"""Tests for error classification helpers (classify_llm_error, describe_exception)."""

from __future__ import annotations

import pytest

from tender_agent.errors import (
    ErrorInfo,
    LLMErrorKind,
    classify_llm_error,
    describe_exception,
)

# ── helpers to build fake exceptions ────────────────────────────────────────


class _FakeAPIError(Exception):
    """Generic fake exception with a configurable status_code."""

    def __init__(self, message: str = "", *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeAPITimeoutError(Exception):
    """Mimics an APITimeoutError class name."""

    pass


class _FakeAPIConnectionError(Exception):
    """Mimics an APIConnectionError class name."""

    pass


# ── classify_llm_error tests ─────────────────────────────────────────────────


def test_classify_rate_limit() -> None:
    exc = _FakeAPIError("too many requests", status_code=429)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.RATE_LIMIT
    assert info.message != ""


def test_classify_quota_exceeded_via_message() -> None:
    exc = _FakeAPIError("insufficient_quota – you exceeded your current quota", status_code=429)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.QUOTA_EXCEEDED
    assert info.message != ""


def test_classify_quota_exceeded_billing() -> None:
    exc = _FakeAPIError("billing limit reached", status_code=429)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.QUOTA_EXCEEDED


def test_classify_auth_401() -> None:
    exc = _FakeAPIError("unauthorized", status_code=401)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.AUTH
    assert info.message != ""


def test_classify_auth_403() -> None:
    exc = _FakeAPIError("forbidden", status_code=403)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.AUTH


def test_classify_bad_request_400() -> None:
    exc = _FakeAPIError("invalid request", status_code=400)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.BAD_REQUEST
    assert info.message != ""


def test_classify_server_error_503() -> None:
    exc = _FakeAPIError("service unavailable", status_code=503)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.SERVER_ERROR
    assert info.message != ""


def test_classify_server_error_500() -> None:
    exc = _FakeAPIError("internal server error", status_code=500)
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.SERVER_ERROR


def test_classify_timeout_by_class_name() -> None:
    exc = _FakeAPITimeoutError("timed out")
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.TIMEOUT
    assert info.message != ""


def test_classify_timeout_by_message_content() -> None:
    exc = RuntimeError("connection timeout occurred")
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.TIMEOUT


def test_classify_connection_by_class_name() -> None:
    exc = _FakeAPIConnectionError("connection refused")
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.CONNECTION
    assert info.message != ""


def test_classify_unknown_exception() -> None:
    exc = ValueError("totally unexpected error")
    info = classify_llm_error(exc)
    assert info.kind == LLMErrorKind.UNKNOWN
    assert "ValueError" in info.message


def test_all_messages_non_empty() -> None:
    """Every classify_llm_error path must return a non-empty message."""
    test_cases: list[Exception] = [
        _FakeAPIError("rate limit", status_code=429),
        _FakeAPIError("insufficient_quota", status_code=429),
        _FakeAPIError("auth", status_code=401),
        _FakeAPIError("auth", status_code=403),
        _FakeAPIError("bad", status_code=400),
        _FakeAPIError("server", status_code=500),
        _FakeAPITimeoutError("timeout"),
        _FakeAPIConnectionError("conn"),
        ValueError("unknown"),
    ]
    for exc in test_cases:
        info = classify_llm_error(exc)
        assert isinstance(info.message, str)
        assert len(info.message) > 0, f"Empty message for {type(exc).__name__}"


def test_error_info_is_frozen() -> None:
    """ErrorInfo is a frozen dataclass — should not allow mutation."""
    info = ErrorInfo(kind="test", message="msg")
    with pytest.raises((AttributeError, TypeError)):
        info.kind = "other"  # type: ignore[misc]


# ── describe_exception tests ─────────────────────────────────────────────────


class ProzorroError(Exception):
    """Local dummy matching the name the function checks."""


class EmailSendError(Exception):
    """Local dummy matching the name the function checks."""


class RecipientsError(Exception):
    """Local dummy matching the name the function checks."""


class FiltersError(Exception):
    """Local dummy matching the name the function checks."""


def test_describe_prozorro_error() -> None:
    info = describe_exception(ProzorroError("api error"))
    assert info.kind == "prozorro_error"
    assert info.message != ""


def test_describe_email_send_error() -> None:
    info = describe_exception(EmailSendError("smtp error"))
    assert info.kind == "email_error"
    assert info.message != ""


def test_describe_recipients_error() -> None:
    info = describe_exception(RecipientsError("bad address"))
    assert info.kind == "config_error"


def test_describe_filters_error() -> None:
    info = describe_exception(FiltersError("bad yaml"))
    assert info.kind == "config_error"


def test_describe_unknown_exception() -> None:
    info = describe_exception(ValueError("oops"))
    assert info.kind == "unknown"
    assert "ValueError" in info.message
    assert "oops" in info.message
