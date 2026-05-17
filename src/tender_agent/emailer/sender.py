"""SMTP email sender with TLS/SSL/plain support."""

from __future__ import annotations

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from tender_agent.emailer.recipients import Recipients
from tender_agent.logging import get_logger
from tender_agent.settings import Settings

log = get_logger(__name__)


class EmailSendError(Exception):
    """Raised when the email could not be delivered."""


class EmailSender:
    """Sends email reports via SMTP."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(
        self,
        subject: str,
        body_text: str,
        recipients: Recipients,
        pdf_attachment: bytes | None = None,
        pdf_filename: str = "report.pdf",
    ) -> None:
        """Build and dispatch an email with a plain-text body and optional PDF attachment.

        Args:
            subject: Email subject line (UTF-8 encoded).
            body_text: Plain-text body of the email.
            recipients: Recipient lists (to/cc/bcc).
            pdf_attachment: Raw PDF bytes to attach, or None for no attachment.
            pdf_filename: Filename shown in the attachment.

        Raises:
            EmailSendError: If the connection or send fails.
        """
        s = self._settings
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = formataddr(("ШІ-Тендерник", s.sender_address))
        msg["To"] = ", ".join(recipients.to)
        if recipients.cc:
            msg["Cc"] = ", ".join(recipients.cc)

        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        if pdf_attachment is not None:
            part = MIMEApplication(pdf_attachment, _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
            msg.attach(part)

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
            has_pdf=pdf_attachment is not None,
        )

    def _connect(self) -> smtplib.SMTP:
        """Open and return an SMTP connection per the configured security mode."""
        s = self._settings
        log.info("smtp_connecting", host=s.smtp_host, port=s.smtp_port, security=s.smtp_security)
        if s.smtp_security == "ssl":
            return smtplib.SMTP_SSL(s.smtp_host, s.smtp_port)
        conn = smtplib.SMTP(s.smtp_host, s.smtp_port)
        if s.smtp_security == "starttls":
            conn.starttls()
        return conn
