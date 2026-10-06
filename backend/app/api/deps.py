from typing import Iterator

from sqlalchemy.orm import Session

from app.db.session import SyncSessionLocal


def get_session() -> Iterator[Session]:
    with SyncSessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
