"""Tests for the generic SMTP email sender."""

from __future__ import annotations

import smtplib

import pytest

from tender_agent.emailer import sender as sender_module
from tender_agent.emailer.recipients import Recipients
from tender_agent.emailer.sender import EmailSender, EmailSendError
from tender_agent.settings import Settings


class FakeSMTP:
    """Records SMTP interactions instead of opening a real connection."""

    last: FakeSMTP | None = None

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: tuple[str, list[str], bytes] | None = None
        self.quit_called = False
        self.fail_on_send = False
        FakeSMTP.last = self

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: bytes) -> None:
        if self.fail_on_send:
            raise smtplib.SMTPException("boom")
        self.sent = (from_addr, to_addrs, msg)

    def quit(self) -> None:
        self.quit_called = True


@pytest.fixture
def recipients() -> Recipients:
    return Recipients(to=["to@example.com"], cc=["cc@example.com"], bcc=["bcc@example.com"])


def test_send_starttls_flow(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recipients: Recipients
) -> None:
    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)
    EmailSender(settings).send("Тема", "<p>звіт</p>", recipients)

    smtp = FakeSMTP.last
    assert smtp is not None
    assert smtp.started_tls is True  # settings default security is starttls
    assert smtp.login_args == ("agent@example.com", "secret")
    assert smtp.quit_called is True

    assert smtp.sent is not None
    from_addr, to_addrs, msg = smtp.sent
    # Bcc must be in the envelope but NOT in the message headers.
    assert set(to_addrs) == {"to@example.com", "cc@example.com", "bcc@example.com"}
    assert b"bcc@example.com" not in msg
    assert b"cc@example.com" in msg


def test_send_wraps_smtp_errors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recipients: Recipients
) -> None:
    class FailingSMTP(FakeSMTP):
        def __init__(self, host: str, port: int) -> None:
            super().__init__(host, port)
            self.fail_on_send = True

    monkeypatch.setattr(sender_module.smtplib, "SMTP", FailingSMTP)
    with pytest.raises(EmailSendError, match="SMTP error"):
        EmailSender(settings).send("Тема", "<p>x</p>", recipients)


def test_send_skips_login_without_username(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recipients: Recipients
) -> None:
    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)
    no_auth = settings.model_copy(update={"smtp_username": "", "smtp_security": "none"})
    EmailSender(no_auth).send("Тема", "<p>x</p>", recipients)

    smtp = FakeSMTP.last
    assert smtp is not None
    assert smtp.login_args is None
    assert smtp.started_tls is False
