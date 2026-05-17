"""Error classification helpers: translate raw exceptions into human-readable messages."""

from __future__ import annotations

from dataclasses import dataclass


class LLMErrorKind:
    """String constants for the kinds of LLM API errors we classify."""

    RATE_LIMIT = "rate_limit"
    QUOTA_EXCEEDED = "quota_exceeded"
    AUTH = "auth"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    BAD_REQUEST = "bad_request"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorInfo:
    """Classified error with a human-readable English message."""

    kind: str
    message: str  # human-readable English sentence


def classify_llm_error(exc: BaseException) -> ErrorInfo:
    """Classify a raw LLM API exception into a structured :class:`ErrorInfo`.

    Uses duck-typing (``status_code`` attribute, class name, str(exc)) to
    avoid importing provider-specific exception types.
    """
    status_code: int | None = getattr(exc, "status_code", None)
    exc_str = str(exc).lower()
    class_name = type(exc).__name__

    # 429 — rate limit or quota exceeded
    if status_code == 429:
        if any(
            phrase in exc_str
            for phrase in ("insufficient_quota", "billing", "exceeded your current quota")
        ):
            return ErrorInfo(
                kind=LLMErrorKind.QUOTA_EXCEEDED,
                message=(
                    "The AI model API account has no remaining budget or quota. "
                    "Classification will fall back to the backup model."
                ),
            )
        return ErrorInfo(
            kind=LLMErrorKind.RATE_LIMIT,
            message=(
                "The AI model API is temporarily rate-limited — too many requests. "
                "Will retry with the backup model."
            ),
        )

    # 401 / 403 — authentication / authorization
    if status_code in {401, 403}:
        return ErrorInfo(
            kind=LLMErrorKind.AUTH,
            message=(
                "The AI model API key was rejected — check that it is correct and has not expired."
            ),
        )

    # 400 — bad request
    if status_code == 400:
        return ErrorInfo(
            kind=LLMErrorKind.BAD_REQUEST,
            message="The AI model API rejected the request as invalid.",
        )

    # 5xx — server error
    if status_code is not None and status_code >= 500:
        return ErrorInfo(
            kind=LLMErrorKind.SERVER_ERROR,
            message="The AI model API returned a server error.",
        )

    # Timeout — by class name or message content
    if (
        "Timeout" in class_name
        or "APITimeout" in class_name
        or (status_code is None and "timeout" in exc_str)
    ):
        return ErrorInfo(
            kind=LLMErrorKind.TIMEOUT,
            message="The AI model API request timed out.",
        )

    # Connection errors — by class name
    if "Connection" in class_name or "Connect" in class_name:
        return ErrorInfo(
            kind=LLMErrorKind.CONNECTION,
            message="Could not connect to the AI model API — check your internet connection.",
        )

    # Fallback
    return ErrorInfo(
        kind=LLMErrorKind.UNKNOWN,
        message=f"Unexpected error from the AI model API: {type(exc).__name__}.",
    )


def describe_exception(exc: BaseException) -> ErrorInfo:
    """Classify a pipeline-level exception into a structured :class:`ErrorInfo`.

    Uses class-name string matching to avoid circular imports from domain
    exception types defined in other modules.
    """
    class_name = type(exc).__name__

    if class_name == "ProzorroError":
        return ErrorInfo(
            kind="prozorro_error",
            message="Failed to communicate with the PROZORRO API after retries.",
        )

    if class_name == "EmailSendError":
        return ErrorInfo(
            kind="email_error",
            message="Failed to send the email report via SMTP.",
        )

    if class_name == "RecipientsError":
        return ErrorInfo(
            kind="config_error",
            message="The recipient list is missing or contains invalid email addresses.",
        )

    if class_name == "FiltersError":
        return ErrorInfo(
            kind="config_error",
            message="The prefilter configuration file is missing or invalid.",
        )

    return ErrorInfo(
        kind="unknown",
        message=f"An unexpected error stopped the run: {type(exc).__name__}: {exc}",
    )
