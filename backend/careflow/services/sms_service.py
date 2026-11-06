"""SMS notification delivery service.

Provides a pluggable provider interface:
- LogProvider: default in development, logs the message without external calls.
- MockProvider: in-memory message store for unit/integration tests.
- TwilioProvider: sends SMS via Twilio REST API.

External messaging is executed asynchronously via a background thread pool executor
so a network or provider issue never delays, blocks, or aborts an application transaction.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("careflow.sms")

# Background thread pool for non-blocking external SMS delivery
_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="careflow-sms")


def clean_phone_number(raw_phone: str | None) -> str:
    """Normalize phone number to digits with optional leading '+'."""
    if not raw_phone:
        return ""
    cleaned = re.sub(r"[^\d+]", "", raw_phone.strip())
    # If phone does not start with +, ensure clean digits
    return cleaned


def sanitize_sms_body(body: str) -> str:
    """Ensure SMS message is clean, concise, and within standard length."""
    # Strip excess whitespace
    text = " ".join(body.split()).strip()
    # Truncate at 160 characters (standard single SMS segment)
    if len(text) > 160:
        return text[:157] + "..."
    return text


class SMSProvider(Protocol):
    def send(self, to_phone: str, message: str) -> bool:
        ...


class LogSMSProvider:
    """Logs the SMS instead of calling an external network API."""

    def send(self, to_phone: str, message: str) -> bool:
        log.info("SMS [LogProvider] to=%s message=%r", to_phone, message)
        return True


@dataclass
class SentSMS:
    to_phone: str
    message: str


class MockSMSProvider:
    """Stores sent messages in memory for test assertions."""

    def __init__(self) -> None:
        self.sent: list[SentSMS] = []

    def send(self, to_phone: str, message: str) -> bool:
        self.sent.append(SentSMS(to_phone=to_phone, message=message))
        log.debug("SMS [MockProvider] to=%s message=%r", to_phone, message)
        return True

    def clear(self) -> None:
        self.sent.clear()


class TwilioSMSProvider:
    """Sends SMS via Twilio Messages API using standard urllib."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def send(self, to_phone: str, message: str) -> bool:
        if not self.account_sid or not self.auth_token or not self.from_number:
            log.warning("Twilio SMS credentials incomplete. Cannot send to %s", to_phone)
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = urllib.parse.urlencode({
            "To": to_phone,
            "From": self.from_number,
            "Body": message,
        }).encode("utf-8")

        auth_bytes = f"{self.account_sid}:{self.auth_token}".encode("latin1")
        auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"

        req = urllib.request.Request(url, data=data, headers={"Authorization": auth_header})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                if status in (200, 201):
                    log.info("Twilio SMS successfully queued for %s", to_phone)
                    return True
                log.warning("Twilio returned non-200 code: %s", status)
                return False
        except urllib.error.HTTPError as exc:
            try:
                err_detail = exc.read().decode("utf-8")
            except Exception:
                err_detail = str(exc)
            log.error("Twilio SMS HTTP error: %s detail=%s", exc.code, err_detail)
            return False
        except Exception as exc:
            log.error("Twilio SMS unexpected error: %s", exc)
            return False


# Singleton provider instance
_ACTIVE_PROVIDER: SMSProvider | None = None
_MOCK_INSTANCE = MockSMSProvider()


def get_provider() -> SMSProvider:
    global _ACTIVE_PROVIDER
    if _ACTIVE_PROVIDER is not None:
        return _ACTIVE_PROVIDER

    env = os.environ.get("APP_ENV", "development")
    provider_name = os.environ.get("SMS_PROVIDER", "").lower()

    if env == "testing" or provider_name == "mock":
        _ACTIVE_PROVIDER = _MOCK_INSTANCE
    elif provider_name == "twilio":
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        from_num = os.environ.get("TWILIO_FROM_NUMBER", "")
        _ACTIVE_PROVIDER = TwilioSMSProvider(sid, token, from_num)
    else:
        _ACTIVE_PROVIDER = LogSMSProvider()

    return _ACTIVE_PROVIDER


def set_provider(provider: SMSProvider) -> None:
    """Override active provider (useful in test setups)."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = provider


def get_mock_provider() -> MockSMSProvider:
    """Return mock provider instance for inspection in tests."""
    return _MOCK_INSTANCE


def send_sms(to_phone: str | None, message: str, *, async_send: bool = True) -> bool:
    """Send an SMS notification to the given phone number.

    If async_send is True, the dispatch is scheduled on a background worker thread
    so the current request thread is never blocked.
    """
    if not to_phone:
        return False

    cleaned_phone = clean_phone_number(to_phone)
    if not cleaned_phone or len(cleaned_phone) < 5:
        log.warning("Invalid phone number provided for SMS: %r", to_phone)
        return False

    sanitized_message = sanitize_sms_body(message)
    if not sanitized_message:
        return False

    provider = get_provider()

    # In testing mode or when async_send is disabled, send synchronously
    env = os.environ.get("APP_ENV", "development")
    if env == "testing" or not async_send or isinstance(provider, MockSMSProvider):
        try:
            return provider.send(cleaned_phone, sanitized_message)
        except Exception as exc:
            log.error("Error sending SMS synchronously: %s", exc)
            return False

    def _deliver():
        try:
            provider.send(cleaned_phone, sanitized_message)
        except Exception as exc:
            log.error("Background SMS delivery failed for %s: %s", cleaned_phone, exc)

    _EXECUTOR.submit(_deliver)
    return True
