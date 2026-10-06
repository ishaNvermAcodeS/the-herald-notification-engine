import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _default_digest_window(monkeypatch):
    """Tests must not depend on a developer's local demo override in .env."""
    monkeypatch.setattr(settings, "DIGEST_WINDOW_MS_OVERRIDE", None)
