"""SMTP email sender with TLS/SSL/plain support."""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from tender_agent.emailer.recipients import Recipients
from tender_agent.logging import get_logger
from tender_agent.settings import Settings

log = get_logger(__name__)

_PLAIN_FALLBACK = (
    "Цей звіт доступний лише у форматі HTML.\n"
    "Будь ласка, відкрийте його у поштовому клієнті з підтримкою HTML."
)


class EmailSendError(Exception):
    """Raised when the email could not be delivered."""


class EmailSender:
    """Sends HTML email reports via SMTP."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, subject: str, html_body: str, recipients: Recipients) -> None:
        """Build and dispatch an HTML email.

        Args:
            subject: Email subject line (will be UTF-8 encoded).
            html_body: Full HTML body of the report.
            recipients: Recipient lists (to/cc/bcc).

        Raises:
            EmailSendError: If the connection or send fails.
        """
        s = self._settings
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr(("Tender Agent", s.sender_address))
        msg["To"] = ", ".join(recipients.to)
        if recipients.cc:
            msg["Cc"] = ", ".join(recipients.cc)

        msg.attach(MIMEText(_PLAIN_FALLBACK, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        envelope_recipients = recipients.to + recipients.cc + recipients.bcc

        try:
            smtp = self._connect()
            try:
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.sendmail(s.sender_address, envelope_recipients, msg.as_bytes())
            finally:
                smtp.quit()
        except smtplib.SMTPException as exc:
            raise EmailSendError(f"SMTP error while sending report: {exc}") from exc
        except OSError as exc:
            raise EmailSendError(f"Network error while sending report: {exc}") from exc

        log.info(
            "email sent",
            to_count=len(recipients.to),
            cc_count=len(recipients.cc),
            bcc_count=len(recipients.bcc),
        )

    def _connect(self) -> smtplib.SMTP:
        """Open and return an SMTP connection per the configured security mode."""
        s = self._settings
        if s.smtp_security == "ssl":
            return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port)
        conn = smtplib.SMTP(s.smtp_host, s.smtp_port)
        if s.smtp_security == "starttls":
            conn.starttls()
        return conn
