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


def test_send_attaches_pdf(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recipients: Recipients
) -> None:
    """Lines 59-61: PDF attachment branch."""
    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)
    pdf = b"%PDF-1.4 fake"
    EmailSender(settings).send("Тема", "body", recipients, pdf_attachment=pdf, pdf_filename="x.pdf")

    smtp = FakeSMTP.last
    assert smtp is not None
    assert smtp.sent is not None
    _from, _to, raw_msg = smtp.sent
    assert b"x.pdf" in raw_msg


def test_send_wraps_os_errors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recipients: Recipients
) -> None:
    """Lines 75-76: OSError is wrapped into EmailSendError."""

    class BrokenSMTP(FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise OSError("connection refused")

    monkeypatch.setattr(sender_module.smtplib, "SMTP", BrokenSMTP)
    with pytest.raises(sender_module.EmailSendError, match="Network error"):
        EmailSender(settings).send("Тема", "body", recipients)


class FakeSMTP_SSL:
    """Simulates smtplib.SMTP_SSL."""

    last: FakeSMTP_SSL | None = None

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.login_args: tuple[str, str] | None = None
        self.sent: tuple[str, list[str], bytes] | None = None
        self.quit_called = False
        FakeSMTP_SSL.last = self

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: bytes) -> None:
        self.sent = (from_addr, to_addrs, msg)

    def quit(self) -> None:
        self.quit_called = True


def test_send_ssl_connect(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recipients: Recipients
) -> None:
    """Line 90: smtp_security='ssl' uses SMTP_SSL."""
    monkeypatch.setattr(sender_module.smtplib, "SMTP_SSL", FakeSMTP_SSL)
    ssl_settings = settings.model_copy(update={"smtp_security": "ssl"})
    EmailSender(ssl_settings).send("Тема", "body", recipients)

    smtp = FakeSMTP_SSL.last
    assert smtp is not None
    assert smtp.sent is not None
    assert smtp.quit_called is True


def test_send_with_security_none_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    recipients: Recipients,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L3: SMTP_SECURITY=none emits a WARNING about cleartext transmission."""
    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)
    none_settings = settings.model_copy(update={"smtp_security": "none"})
    with caplog.at_level("WARNING", logger="tender_agent.emailer.sender"):
        EmailSender(none_settings).send("Тема", "body", recipients)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("cleartext" in str(r.msg) or "cleartext" in r.getMessage() for r in warnings)


def test_send_with_starttls_logs_no_insecure_warning(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    recipients: Recipients,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L3: the cleartext warning is NOT emitted for secure transports."""
    monkeypatch.setattr(sender_module.smtplib, "SMTP", FakeSMTP)
    with caplog.at_level("WARNING", logger="tender_agent.emailer.sender"):
        EmailSender(settings).send("Тема", "body", recipients)
    assert not any("cleartext" in r.getMessage() for r in caplog.records)
