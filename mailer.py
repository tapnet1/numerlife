"""Plain SMTP delivery. Settings come from .env (read at send time):

    NUMER_SMTP_HOST, NUMER_SMTP_PORT (465 = SSL, 587 = STARTTLS), NUMER_SMTP_USER,
    NUMER_SMTP_PASSWORD, NUMER_MAIL_FROM ("numer.life <reports@numer.life>")

Moving to Google Workspace later is a settings change (smtp-relay.gmail.com or smtp.gmail.com).
Errors are reduced to a short, secret-free description before they are stored or shown.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

TIMEOUT = 20


def settings() -> dict:
    return {
        "host": os.environ.get("NUMER_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("NUMER_SMTP_PORT") or 465),
        "user": os.environ.get("NUMER_SMTP_USER", "").strip(),
        "password": os.environ.get("NUMER_SMTP_PASSWORD", ""),
        "sender": (os.environ.get("NUMER_MAIL_FROM") or os.environ.get("NUMER_SMTP_USER") or "").strip(),
    }


def configured() -> bool:
    current = settings()
    return bool(current["host"] and parseaddr(current["sender"])[1])


def build(to: str, subject: str, text: str, html: str, headers: dict | None = None) -> EmailMessage:
    sender = settings()["sender"]
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    domain = parseaddr(sender)[1].rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domain)
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def connect() -> smtplib.SMTP:
    current = settings()
    context = ssl.create_default_context()
    if current["port"] == 465:
        server = smtplib.SMTP_SSL(current["host"], current["port"], timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(current["host"], current["port"], timeout=TIMEOUT)
        server.starttls(context=context)
    if current["user"]:
        server.login(current["user"], current["password"])
    return server


def describe_error(exc: BaseException) -> str:
    """A short description that never contains credentials or message content."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP sign-in failed ({exc.smtp_code}): check NUMER_SMTP_USER / NUMER_SMTP_PASSWORD"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "recipient address refused"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"SMTP error {exc.smtp_code}"
    if isinstance(exc, (TimeoutError, OSError)):
        return f"could not reach the mail server ({type(exc).__name__})"
    return type(exc).__name__
