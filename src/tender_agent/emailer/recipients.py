"""Recipient list loading and validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel

from tender_agent.logging import get_logger

log = get_logger(__name__)


class RecipientsError(Exception):
    """Raised when the recipients file cannot be loaded or validated."""


class Recipients(BaseModel):
    """Validated email recipient lists."""

    to: list[str]
    cc: list[str] = []
    bcc: list[str] = []


def _validate_addresses(field: str, addresses: list[str]) -> None:
    """Validate each address in *addresses*; raise RecipientsError on failure."""
    for addr in addresses:
        try:
            validate_email(addr, check_deliverability=False)
        except EmailNotValidError as exc:
            raise RecipientsError(
                f"Invalid email address {addr!r} in field '{field}': {exc}"
            ) from exc


def load_recipients(path: Path) -> Recipients:
    """Load and validate a YAML recipients file.

    Args:
        path: Path to a YAML file with ``to``, ``cc``, and ``bcc`` keys.

    Returns:
        A validated :class:`Recipients` instance.

    Raises:
        RecipientsError: If the file is missing, contains invalid YAML,
            has an empty ``to`` list, or any address is invalid.
    """
    if not path.exists():
        raise RecipientsError(f"Recipients file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RecipientsError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RecipientsError(
            f"Recipients file {path} must contain a YAML mapping, got {type(raw).__name__}"
        )

    def _as_list(key: str) -> list[str]:
        val = raw.get(key)
        if val is None:
            return []
        if not isinstance(val, list):
            raise RecipientsError(
                f"Field '{key}' in {path} must be a list, got {type(val).__name__}"
            )
        return [str(item) for item in val]

    to = _as_list("to")
    cc = _as_list("cc")
    bcc = _as_list("bcc")

    if not to:
        raise RecipientsError(
            f"Field 'to' in {path} is empty or absent — at least one recipient required"
        )

    _validate_addresses("to", to)
    _validate_addresses("cc", cc)
    _validate_addresses("bcc", bcc)

    log.info("recipients loaded", to=len(to), cc=len(cc), bcc=len(bcc))
    return Recipients(to=to, cc=cc, bcc=bcc)
