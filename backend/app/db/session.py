from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def normalize_database_url(url: str) -> str:
    """Normalize common PostgreSQL URLs to the installed psycopg driver.

    SQLAlchemy's bare ``postgresql://`` URL historically resolves to psycopg2,
    which this project does not install. Production deploys may provide either
    ``postgresql://...`` or ``postgresql+psycopg://...``; both should use the
    explicit psycopg v3 dialect while preserving every credential, host, path
    and query parameter exactly as supplied. The URL is never logged here.
    """
    normalized = url.strip()
    if normalized.startswith("postgresql+psycopg://"):
        return normalized
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgresql://")
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgres://")
    return normalized


def _create_engine() -> Engine:
    settings = get_settings()
    url = normalize_database_url(settings.DATABASE_URL)

    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **kwargs)

    # Enable foreign-key enforcement for SQLite connections.
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _create_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session, ensuring proper cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_db_and_tables() -> None:
    """Create all tables defined by the Base metadata.

    Must be called explicitly (e.g. from tests or bootstrap). Not invoked on
    import or application startup.
    """
    # Import models so they are registered with Base.metadata.
    import app.db.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


# Import Base here to avoid circular imports.
from app.db.base import Base  # noqa: E402
