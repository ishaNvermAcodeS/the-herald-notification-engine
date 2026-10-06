"""
Database session management for The Herald.

Provides:
- Async engine (for production FastAPI use)
- Sync engine (for Alembic migrations and test helpers)
- get_db dependency for FastAPI routes
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import settings

# ── Async engine (used by FastAPI route handlers) ─────────────────────────────
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Sync engine (used by Alembic migrations and sync test helpers) ─────────────
# asyncpg URL → psycopg2 URL for sync operations
_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

sync_engine = create_engine(
    _sync_url,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


# ── FastAPI dependency ─────────────────────────────────────────────────────────
async def get_db() -> AsyncSession:  # type: ignore[override]
    """Yield an async database session for use in FastAPI route dependencies."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
