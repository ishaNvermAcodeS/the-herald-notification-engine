"""
Email delivery providers.

``RealEmailProvider`` (Resend HTTP API) is what the configured app uses.
``MockEmailProvider`` exists only so automated tests can script failures.
"""
import logging
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
    if settings.EMAIL_PROVIDER.lower() == "mock":
        return MockEmailProvider()
    return RealEmailProvider()
