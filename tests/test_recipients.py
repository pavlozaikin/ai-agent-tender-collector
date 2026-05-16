"""Tests for recipient list loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tender_agent.emailer.recipients import RecipientsError, load_recipients


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_valid_recipients(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "r.yaml",
        "to:\n  - a@example.com\n  - b@example.com\ncc:\n  - c@example.com\nbcc: []\n",
    )
    recipients = load_recipients(path)
    assert recipients.to == ["a@example.com", "b@example.com"]
    assert recipients.cc == ["c@example.com"]
    assert recipients.bcc == []


def test_missing_cc_and_bcc_default_to_empty(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.yaml", "to:\n  - a@example.com\n")
    recipients = load_recipients(path)
    assert recipients.cc == []
    assert recipients.bcc == []


def test_null_list_treated_as_empty(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.yaml", "to:\n  - a@example.com\ncc: null\n")
    assert load_recipients(path).cc == []


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RecipientsError, match="not found"):
        load_recipients(tmp_path / "absent.yaml")


def test_empty_to_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.yaml", "to: []\ncc:\n  - c@example.com\n")
    with pytest.raises(RecipientsError, match="'to'"):
        load_recipients(path)


def test_invalid_address_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.yaml", "to:\n  - not-an-email\n")
    with pytest.raises(RecipientsError, match="Invalid email"):
        load_recipients(path)


def test_non_mapping_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.yaml", "- a@example.com\n")
    with pytest.raises(RecipientsError, match="mapping"):
        load_recipients(path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    """Lines 57-58: yaml.YAMLError branch."""
    path = _write(tmp_path / "r.yaml", "to: [\n  unclosed bracket\n")
    with pytest.raises(RecipientsError, match="Invalid YAML"):
        load_recipients(path)


def test_non_list_field_raises(tmp_path: Path) -> None:
    """Line 70: non-list value for a recipient field."""
    path = _write(tmp_path / "r.yaml", "to:\n  - a@example.com\ncc: not-a-list\n")
    with pytest.raises(RecipientsError, match="must be a list"):
        load_recipients(path)
