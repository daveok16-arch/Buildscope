"""Out-of-band delivery for password-reset instructions.

A reset token is a bearer credential: whoever holds it can set a new password. It must reach
the account owner over a channel the requester does not control, and it must never appear in an
HTTP response, a template or a log line. This module is the single place a reset is delivered,
so the development and production paths cannot be confused:

* The backend is chosen from `MAIL_BACKEND`. The default is `console`, which records that a
  request happened and sends nothing — the token is deliberately not written even at debug
  level, because a log is still a place a token could leak.
* `smtp` is the production path. It requires `SMTP_HOST`; when the backend is selected but not
  configured, delivery fails loudly with `DeliveryError` rather than silently falling back to
  a channel that would expose the token.
* `null` accepts the request and delivers nothing, for an environment where delivery is
  intentionally disabled.

No caller renders a token. `tests/test_auth_security.py` asserts that a token never appears in
a page, an API response or a log record.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger(__name__)

#: Delivery backends.
BACKEND_CONSOLE = "console"
BACKEND_SMTP = "smtp"
BACKEND_NULL = "null"


class DeliveryError(Exception):
    """A reset message could not be handed to its delivery channel."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class SmtpSettings:
    """The SMTP relay configuration, read from the environment."""

    host: str
    port: int
    username: str | None
    password: str | None
    sender: str
    use_tls: bool

    @classmethod
    def from_env(cls) -> "SmtpSettings | None":
        """Build the settings, or None when no relay host is configured."""
        host = (os.environ.get("SMTP_HOST") or "").strip()
        if not host:
            return None
        try:
            port = int(os.environ.get("SMTP_PORT", "587"))
        except ValueError:
            port = 587
        username = (os.environ.get("SMTP_USER") or "").strip() or None
        return cls(
            host=host,
            port=port,
            username=username,
            password=os.environ.get("SMTP_PASSWORD") or None,
            sender=(os.environ.get("SMTP_FROM") or "").strip() or username or "no-reply@localhost",
            use_tls=_env_bool("SMTP_USE_TLS", True),
        )


class ResetMailer:
    """Deliver password-reset instructions for one address.

    The mailer never decides whether an account exists: the caller only invokes it for a real
    account, and a missing account produces no message, so delivery cannot become an account
    enumeration oracle.
    """

    def __init__(self, *, backend: str | None = None, base_url: str = "") -> None:
        chosen = backend or os.environ.get("MAIL_BACKEND") or BACKEND_CONSOLE
        self.backend = chosen.strip().lower()
        self.base_url = (base_url or "").rstrip("/")

    def send_reset(self, *, to_email: str, token: str) -> None:
        """Deliver reset instructions, or raise DeliveryError if the channel refuses them."""
        if self.backend == BACKEND_NULL:
            return
        if self.backend == BACKEND_CONSOLE:
            self._send_console(to_email=to_email)
            return
        if self.backend == BACKEND_SMTP:
            self._send_smtp(to_email=to_email, token=token)
            return
        raise DeliveryError(f"unknown MAIL_BACKEND {self.backend!r}")

    def _reset_url(self, token: str) -> str:
        path = f"/reset-password/{token}"
        return f"{self.base_url}{path}" if self.base_url else path

    def _send_console(self, *, to_email: str) -> None:
        """Record that a reset was requested, without writing the token.

        The token is withheld even here: the console backend is for local development, and a
        log file is still a place a bearer credential could be read from.
        """
        log.info(
            "Password reset requested for %s. MAIL_BACKEND=console, so no message was sent; "
            "configure MAIL_BACKEND=smtp to deliver reset instructions.",
            to_email,
        )

    def _send_smtp(self, *, to_email: str, token: str) -> None:
        settings = SmtpSettings.from_env()
        if settings is None:
            raise DeliveryError("MAIL_BACKEND=smtp requires SMTP_HOST to be set")

        message = EmailMessage()
        message["Subject"] = "Reset your BuildScope password"
        message["From"] = settings.sender
        message["To"] = to_email
        message.set_content(
            "A password reset was requested for this address.\n\n"
            f"Reset link: {self._reset_url(token)}\n\n"
            "The link expires in one hour and can be used once. If you did not request this, "
            "you can ignore this message and your password will not change."
        )
        try:
            with smtplib.SMTP(settings.host, settings.port, timeout=10) as smtp:
                if settings.use_tls:
                    smtp.starttls()
                if settings.username and settings.password:
                    smtp.login(settings.username, settings.password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"could not deliver reset mail: {exc}") from exc
