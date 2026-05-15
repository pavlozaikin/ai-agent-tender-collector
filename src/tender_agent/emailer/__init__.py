"""Email composition and delivery for the tender agent report."""

from tender_agent.emailer.recipients import (
    Recipients,
    RecipientsError,
    load_recipients,
)
from tender_agent.emailer.report import RenderedReport, render_report
from tender_agent.emailer.sender import EmailSender, EmailSendError

__all__ = [
    "EmailSendError",
    "EmailSender",
    "Recipients",
    "RecipientsError",
    "RenderedReport",
    "load_recipients",
    "render_report",
]
