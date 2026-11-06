"""Email notification delivery service.

Provides a pluggable provider interface:
- LogEmailProvider: default in development, logs the email without external network calls.
- MockEmailProvider: in-memory message store for unit/integration tests.
- SMTPEmailProvider: sends email via standard SMTP (compatible with SES, SendGrid, Mailgun, Postmark, etc.).

External email dispatch is executed asynchronously via a background thread pool executor
so network latency or SMTP server issues never block or fail database transactions.
"""

from __future__ import annotations

import email.message
import logging
import os
import smtplib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("careflow.email")

# Background thread pool for non-blocking email delivery
_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="careflow-email")


@dataclass
class SentEmail:
    to_email: str
    subject: str
    body: str


class EmailProvider(Protocol):
    def send(self, to_email: str, subject: str, body: str) -> bool:
        ...


class LogEmailProvider:
    """Logs the email instead of calling an external SMTP server."""
    def send(self, to_email: str, subject: str, body: str) -> bool:
        log.info("Email [LogProvider] to=%s subject=%r body_len=%d", to_email, subject, len(body))
        return True


class MockEmailProvider:
    """Stores sent emails in memory for test assertions."""

    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    def send(self, to_email: str, subject: str, body: str) -> bool:
        self.sent.append(SentEmail(to_email=to_email, subject=subject, body=body))
        log.debug("Email [MockProvider] to=%s subject=%r", to_email, subject)
        return True

    def clear(self) -> None:
        self.sent.clear()


class SMTPEmailProvider:
    """Sends email via standard SMTP server."""

    def __init__(
        self,
        host: str,
        port: int = 587,
        user: str = "",
        password: str = "",
        from_email: str = "noreply@careflow.local",
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_email = from_email
        self.use_tls = use_tls

    def send(self, to_email: str, subject: str, body: str) -> bool:
        if not self.host:
            log.warning("SMTP host not configured. Cannot send email to %s", to_email)
            return False

        msg = email.message.EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.set_content(body)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                if self.use_tls:
                    server.starttls()
                if self.user and self.password:
                    server.login(self.user, self.password)
                server.send_message(msg)
            log.info("SMTP email successfully sent to %s (subject=%r)", to_email, subject)
            return True
        except Exception as exc:
            log.error("Failed to send SMTP email to %s: %s", to_email, exc)
            return False


# Singleton provider instance
_ACTIVE_PROVIDER: EmailProvider | None = None
_MOCK_INSTANCE = MockEmailProvider()


def get_provider() -> EmailProvider:
    global _ACTIVE_PROVIDER
    if _ACTIVE_PROVIDER is not None:
        return _ACTIVE_PROVIDER

    env = os.environ.get("APP_ENV", "development")
    provider_name = os.environ.get("EMAIL_PROVIDER", "").lower()

    if env == "testing" or provider_name == "mock":
        _ACTIVE_PROVIDER = _MOCK_INSTANCE
    elif provider_name == "smtp":
        host = os.environ.get("SMTP_HOST", "")
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ.get("SMTP_USER", "")
        password = os.environ.get("SMTP_PASSWORD", "")
        from_email = os.environ.get("MAIL_FROM", "noreply@careflow.local")
        use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
        _ACTIVE_PROVIDER = SMTPEmailProvider(
            host=host, port=port, user=user, password=password, from_email=from_email, use_tls=use_tls
        )
    else:
        _ACTIVE_PROVIDER = LogEmailProvider()

    return _ACTIVE_PROVIDER


def set_provider(provider: EmailProvider) -> None:
    """Override active provider (useful in tests)."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = provider


def get_mock_provider() -> MockEmailProvider:
    """Return mock provider instance for inspection in tests."""
    return _MOCK_INSTANCE


def send_email(to_email: str | None, subject: str, body: str, *, async_send: bool = True) -> bool:
    """Send an email notification.

    If async_send is True, the dispatch is scheduled on a background worker thread
    so the current request thread is never blocked.
    """
    if not to_email or "@" not in to_email:
        return False

    cleaned_email = to_email.strip().lower()
    cleaned_subject = " ".join(subject.split()).strip()
    if not cleaned_subject or not body:
        return False

    provider = get_provider()

    env = os.environ.get("APP_ENV", "development")
    if env == "testing" or not async_send or isinstance(provider, MockEmailProvider):
        try:
            return provider.send(cleaned_email, cleaned_subject, body)
        except Exception as exc:
            log.error("Error sending email synchronously: %s", exc)
            return False

    def _deliver():
        try:
            provider.send(cleaned_email, cleaned_subject, body)
        except Exception as exc:
            log.error("Background email delivery failed for %s: %s", cleaned_email, exc)

    _EXECUTOR.submit(_deliver)
    return True
