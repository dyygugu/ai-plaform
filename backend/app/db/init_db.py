from app.db.base import Base
from app.db.session import engine
import app.db.models  # noqa: F401
from sqlalchemy import inspect, text


def create_tables_for_dev() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_worker_dispatch_columns()


def _ensure_worker_dispatch_columns() -> None:
    inspector = inspect(engine)
    if "workers" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("workers")}
    missing_columns = {
        "is_platform_worker": "BOOLEAN DEFAULT 0 NOT NULL",
        "estimated_http_account_slots": "INTEGER DEFAULT 0 NOT NULL",
        "configured_http_account_slots": "INTEGER DEFAULT 0 NOT NULL",
        "effective_http_account_slots": "INTEGER DEFAULT 0 NOT NULL",
        "health_status": "VARCHAR(32) DEFAULT 'unknown' NOT NULL",
        "health_checked_at": "DATETIME",
        "health_fail_reasons": "TEXT DEFAULT '' NOT NULL",
        "disabled_reason": "TEXT DEFAULT '' NOT NULL",
    }
    with engine.begin() as connection:
        for column_name, ddl in missing_columns.items():
            if column_name not in existing:
                connection.execute(text(f"ALTER TABLE workers ADD COLUMN {column_name} {ddl}"))
