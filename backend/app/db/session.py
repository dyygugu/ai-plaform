from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.settings import get_settings

settings = get_settings()


def _is_sqlite_url(database_url: str) -> bool:
    return str(database_url or "").startswith("sqlite")


def _engine_options(database_url: str) -> dict:
    options = {"pool_pre_ping": True}
    if _is_sqlite_url(database_url):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return options


engine = create_engine(settings.database_url, **_engine_options(settings.database_url))


if _is_sqlite_url(settings.database_url):
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
