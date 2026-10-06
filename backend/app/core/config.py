import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

# Search for .env from the project root, then backend/
_ROOT = Path(__file__).resolve().parents[3]   # …/The_Herald
_BACKEND = Path(__file__).resolve().parents[2] # …/The_Herald/backend
_ENV_FILES = [
    str(_ROOT / ".env"),
    str(_BACKEND / ".env"),
]


class Settings(BaseSettings):
    APP_NAME: str = "The Herald"
    APP_ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_V1_PREFIX: str = "/v1"
    SECRET_KEY: str = "dev-secret-key-herald"
    API_KEY: str = "herald-dev-api-key"

    # Database
    DATABASE_URL: str = f"postgresql+asyncpg://{os.environ.get('USER', 'postgres')}@localhost:5432/the_herald"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Email delivery (Phase 8). "resend" or "smtp" = real providers, "mock" = tests only.
    EMAIL_PROVIDER: str = "resend"
    EMAIL_API_KEY: str = ""
    EMAIL_FROM: str = "onboarding@resend.dev"
    EMAIL_FROM_NAME: str = "The Herald"
    EMAIL_TIMEOUT_SECONDS: float = 15.0
    # SMTP (EMAIL_PROVIDER=smtp), e.g. Gmail with an App Password. Sends to any recipient, no domain needed.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    # Delivery retry (Phase 8)
    DELIVERY_MAX_ATTEMPTS: int = 3
    RETRY_BASE_DELAY_SECONDS: float = 5.0

    # Digest (Phase 6). Overrides the workflow step's durationMs when set (demo/testing).
    DIGEST_WINDOW_MS_OVERRIDE: int | None = None

    # AI smart digest (Phase 9). "groq" = real LLM, "mock" = tests only.
    AI_PROVIDER: str = "groq"
    AI_API_KEY: str = ""
    AI_MODEL: str = "openai/gpt-oss-20b"
    AI_TIMEOUT_SECONDS: float = 20.0

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]

    # Workflows registered in system (default campus workflows)
    REGISTERED_WORKFLOWS: List[str] = [
        "campus-maintenance-alert",
        "campus-bulletin",
        "campus-emergency",
    ]

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
