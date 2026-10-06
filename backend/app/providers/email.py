"""
Email delivery providers.

``RealEmailProvider`` (Resend HTTP API) is what the configured app uses.
``MockEmailProvider`` exists only so automated tests can script failures.
"""
import hashlib
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("herald.email")


class EmailSendError(Exception):
    """Delivery failed. ``retryable`` tells the retry engine whether to try again."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class EmailProvider(ABC):
    @abstractmethod
    def send(self, *, to: str, subject: str, body: str, idempotency_key: str) -> str:
        """Send one email. Returns the provider's message id. Raises EmailSendError."""


class RealEmailProvider(EmailProvider):
    """Resend (https://resend.com) transactional email over HTTPS."""

    API_URL = "https://api.resend.com/emails"

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_address: Optional[str] = None,
        from_name: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self._api_key = api_key if api_key is not None else settings.EMAIL_API_KEY
        self._from = f"{from_name or settings.EMAIL_FROM_NAME} <{from_address or settings.EMAIL_FROM}>"
        self._timeout = timeout or settings.EMAIL_TIMEOUT_SECONDS

    def send(self, *, to: str, subject: str, body: str, idempotency_key: str) -> str:
        if not self._api_key:
            raise EmailSendError("EMAIL_API_KEY is not configured", retryable=False)
        try:
            resp = httpx.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    # Resend de-duplicates on this key, so a retry after a lost
                    # response cannot produce a second email.
                    "Idempotency-Key": idempotency_key,
                },
                json={"from": self._from, "to": [to], "subject": subject, "text": body},
                timeout=self._timeout,
            )
        except httpx.HTTPError as err:
            raise EmailSendError(f"network error: {type(err).__name__}", retryable=True)

        if resp.status_code < 300:
            return str(resp.json().get("id", ""))
        retryable = resp.status_code == 429 or resp.status_code >= 500
        # Provider error text is safe to log; the key is never part of it.
        raise EmailSendError(f"provider returned {resp.status_code}: {resp.text[:300]}", retryable)


class SmtpEmailProvider(EmailProvider):
    """
    Real email over SMTP (STARTTLS). Works with Gmail + an App Password and can
    deliver to any address. SMTP has no idempotency key; duplicate sends are
    prevented upstream because a Message already marked SENT is never re-sent.
    """

    def __init__(self, host=None, port=None, user=None, password=None, from_address=None, from_name=None, timeout=None):
        self._host = host or settings.SMTP_HOST
        self._port = port or settings.SMTP_PORT
        self._user = user if user is not None else settings.SMTP_USER
        self._password = password if password is not None else settings.SMTP_PASSWORD
        self._from_address = from_address or settings.EMAIL_FROM
        if self._user and self._from_address == "onboarding@resend.dev":
            self._from_address = self._user  # Gmail only sends as the authenticated account
        self._from_name = from_name or settings.EMAIL_FROM_NAME
        self._timeout = timeout or settings.EMAIL_TIMEOUT_SECONDS

    def send(self, *, to: str, subject: str, body: str, idempotency_key: str) -> str:
        if not (self._user and self._password):
            raise EmailSendError("SMTP_USER / SMTP_PASSWORD are not configured", retryable=False)
        msg = EmailMessage()
        msg["From"] = formataddr((self._from_name, self._from_address))
        msg["To"] = to
        msg["Subject"] = subject
        msg_id = f"<{hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]}@herald.local>"
        msg["Message-ID"] = msg_id
        msg.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self._user, self._password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise EmailSendError("SMTP authentication failed (check SMTP_USER / App Password)", retryable=False)
        except smtplib.SMTPRecipientsRefused:
            raise EmailSendError(f"recipient refused by SMTP server: {to}", retryable=False)
        except smtplib.SMTPResponseException as err:
            raise EmailSendError(f"smtp error {err.smtp_code}", retryable=400 <= err.smtp_code < 500)
        except (smtplib.SMTPException, OSError) as err:
            raise EmailSendError(f"smtp connection error: {type(err).__name__}", retryable=True)
        return msg_id


class MockEmailProvider(EmailProvider):
    """
    Test double. ``failures`` is a list of exceptions raised on successive
    attempts (e.g. [EmailSendError("boom")] fails once, then succeeds).
    """

    def __init__(self, failures: Optional[List[Exception]] = None):
        self._failures = list(failures or [])
        self.attempts = 0
        self.sent: List[Dict[str, str]] = []

    def send(self, *, to: str, subject: str, body: str, idempotency_key: str) -> str:
        self.attempts += 1
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append({"to": to, "subject": subject, "body": body, "key": idempotency_key})
        return f"mock-{len(self.sent)}"


def get_email_provider() -> EmailProvider:
    kind = settings.EMAIL_PROVIDER.lower()
    if kind == "mock":
        return MockEmailProvider()
    if kind == "smtp":
        return SmtpEmailProvider()
    return RealEmailProvider()
