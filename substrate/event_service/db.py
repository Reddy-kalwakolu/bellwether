"""Database engine and session dependency for event-service."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from substrate.event_service.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a request-scoped session; tests override this dependency."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
