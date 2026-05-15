"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SmtpSecurity = Literal["starttls", "ssl", "none"]


class Settings(BaseSettings):
    """Typed application settings.

    Every field maps to an environment variable of the same name in
    upper case (see `.env.example`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── LLM models (format: "provider:model-id") ────────────────────────────
    llm_classify_primary: str = "openai:gpt-5.4-mini"
    llm_classify_backup: str = "openai:gpt-5.4-nano"
    llm_report_primary: str = "openai:gpt-5.5"
    llm_report_backup: str = "openai:gpt-5.4-mini"

    # ── Provider API keys ───────────────────────────────────────────────────
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    perplexity_api_key: str | None = None

    # ── Email (generic SMTP) ────────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_security: SmtpSecurity = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # ── Schedule ────────────────────────────────────────────────────────────
    schedule_cron: str = "0 8 * * *"
    timezone: str = "Europe/Kyiv"
    send_when_empty: bool = False

    # ── Crawling ────────────────────────────────────────────────────────────
    prozorro_api_base: str = "https://public-api.prozorro.gov.ua/api/2.5"
    crawl_lookback_days: int = 3
    request_timeout_seconds: float = 30.0
    max_retries: int = 4

    # ── Paths & logging ─────────────────────────────────────────────────────
    data_dir: Path = Path("data")
    filters_path: Path = Path("config/filters.yaml")
    recipients_path: Path = Path("recipients.yaml")
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.data_dir / "tender_agent.db"

    @property
    def reports_dir(self) -> Path:
        """Directory where rendered HTML reports are stored."""
        return self.data_dir / "reports"

    @property
    def sender_address(self) -> str:
        """Effective From address (falls back to the SMTP username)."""
        return self.smtp_from or self.smtp_username


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
