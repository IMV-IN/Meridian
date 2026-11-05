"""Outbound email for sign-in codes and invitations.

SMTP works with any provider (Gmail app password, Brevo, Amazon SES, Resend,
Postmark, Google Workspace relay). Without SMTP configured the console mailer
logs the message — fine for development, never for real users.
"""

from __future__ import annotations

import html as _html
import json
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from .config import ControlConfig

logger = logging.getLogger("meridian_control.mail")


class Mailer(Protocol):
    kind: str

    def send(self, to: str, subject: str, text: str, html: str) -> None: ...


class ConsoleMailer:
    kind = "console"

    def __init__(self, outbox: str = "") -> None:
        self._outbox = Path(outbox) if outbox else None
        if self._outbox:
            self._outbox.mkdir(parents=True, exist_ok=True)

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        logger.warning("DEV MAILER (no SMTP configured) → %s | %s", to, subject)
        if self._outbox:
            name = f"{int(time.time() * 1000)}-{to.replace('@', '_at_')}.json"
            (self._outbox / name).write_text(json.dumps({"to": to, "subject": subject, "text": text}))


class SmtpMailer:
    kind = "smtp"

    def __init__(self, config: ControlConfig) -> None:
        self._c = config
        if not config.mail_from:
            raise ValueError("MERIDIAN_CONTROL_MAIL_FROM is required when SMTP is configured")

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        msg = EmailMessage()
        msg["From"] = self._c.mail_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        ctx = ssl.create_default_context()
        if self._c.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(self._c.smtp_host, self._c.smtp_port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(self._c.smtp_host, self._c.smtp_port, timeout=15)
        try:
            if self._c.smtp_security == "starttls":
                server.starttls(context=ctx)
            if self._c.smtp_username:
                server.login(self._c.smtp_username, self._c.smtp_password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass


def make_mailer(config: ControlConfig) -> Mailer:
    if config.smtp_host:
        return SmtpMailer(config)
    return ConsoleMailer(config.dev_mail_outbox)


_WRAP = (
    '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
    'max-width:480px;margin:24px auto;color:#121816">{body}</div>'
)
_MUTED = 'style="font-size:13px;color:#5d625f"'
_BUTTON = (
    'style="display:inline-block;background:#10211f;color:#e4f6c9;'
    'padding:10px 16px;border-radius:8px;text-decoration:none"'
)


def code_email(code: str, minutes: int) -> tuple[str, str, str]:
    subject = f"{code} is your Meridian sign-in code"
    text = (
        f"Your Meridian sign-in code is {code}\n\n"
        f"It expires in {minutes} minutes. If you didn't try to sign in, ignore this email."
    )
    body = (
        '<p style="font-size:15px">Your Meridian sign-in code:</p>'
        '<p style="font-size:32px;font-weight:600;letter-spacing:6px;margin:16px 0;'
        f'font-family:ui-monospace,Menlo,monospace">{code}</p>'
        f"<p {_MUTED}>It expires in {minutes} minutes. "
        "If you didn't try to sign in, you can ignore this email.</p>"
    )
    return subject, text, _WRAP.format(body=body)


def invite_email(workspace: str, inviter: str, url: str) -> tuple[str, str, str]:
    subject = f"{inviter} invited you to {workspace} on Meridian"
    text = (
        f"{inviter} invited you to the {workspace} workspace on Meridian.\n\n"
        f"Sign in with this email address to join: {url}"
    )
    # Names are user-controlled: escape before they reach HTML.
    ws, who, link = _html.escape(workspace), _html.escape(inviter), _html.escape(url, quote=True)
    body = (
        f'<p style="font-size:15px"><b>{who}</b> invited you to the <b>{ws}</b> workspace on Meridian.</p>'
        f'<p><a href="{link}" {_BUTTON}>Sign in to join</a></p>'
        f"<p {_MUTED}>Sign in with this email address (Google or an email code) "
        "and the workspace appears automatically.</p>"
    )
    return subject, text, _WRAP.format(body=body)
