"""
Database engine and session management.

WHY THIS FILE EXISTS
--------------------
Two distinct things live here, and conflating them is a common source of bugs:

  ENGINE  -- created ONCE for the whole application. It owns the connection
             pool. Creating engines repeatedly leaks connections until
             PostgreSQL refuses new ones.

  SESSION -- created PER REQUEST. It is a unit of work: a transaction plus an
             identity map of the objects loaded within it. Sharing one session
             across requests means one user's uncommitted changes become
             visible to another. Never do it.

The rule: one engine per process, one session per request.
"""

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
import shutil

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ---------------------------------------------------------------------------
# ENGINE -- module-level, created once at import time
# ---------------------------------------------------------------------------
_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    # SQLite does not support connection pooling options like pool_size,
    # max_overflow, or pool_recycle. It also requires check_same_thread=False
    # when used with FastAPI (which serves requests from multiple threads).
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        echo=settings.sql_echo,
    )

    # Enable WAL mode and foreign key enforcement for SQLite.
    # Without PRAGMA foreign_keys = ON, SQLite silently ignores FK constraints.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

else:
    engine = create_engine(
        settings.database_url,
        # ---- Connection pool tuning ----
        # pool_size: connections kept open permanently. Each one consumes memory
        # on the PostgreSQL server (~10 MB), so this is not free. 10 comfortably
        # serves ~50 concurrent users because requests hold a connection only for
        # the milliseconds they are actually querying.
        pool_size=10,
        # max_overflow: extra connections opened under burst load, then discarded.
        # Total ceiling is pool_size + max_overflow = 30.
        max_overflow=20,
        # pool_pre_ping: issues a cheap "SELECT 1" before handing out a pooled
        # connection. Without it, a connection killed by a network blip or a
        # PostgreSQL restart is handed to your code and fails mid-query with a
        # confusing error. Costs a fraction of a millisecond; prevents a whole
        # category of intermittent production bug. Always enable it.
        pool_pre_ping=True,
        # pool_recycle: force-close connections older than this many seconds.
        # Many networks silently drop idle TCP connections after ~an hour;
        # recycling before that avoids inheriting a dead socket.
        pool_recycle=3600,
        # echo: log every SQL statement. Invaluable while learning -- you can see
        # exactly what the ORM generates, including accidental N+1 queries.
        # Driven by config so it can be enabled in dev and off in production.
        echo=settings.sql_echo,
    )

# ---------------------------------------------------------------------------
# SESSION FACTORY
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(
    bind=engine,
    # autocommit=False: changes are staged until you explicitly commit.
    # This is what makes a request atomic -- if step 3 of 5 fails, nothing
    # from steps 1-2 persists.
    autocommit=False,
    # autoflush=False: do not silently emit UPDATEs before every query.
    # Autoflush causes surprising behaviour where merely reading data triggers
    # a write. Explicit flush() when you need a generated ID is clearer.
    autoflush=False,
    # expire_on_commit=False: keep loaded objects usable after commit.
    # With the default True, accessing any attribute post-commit triggers a
    # fresh SELECT -- and in FastAPI, the response is serialised AFTER the
    # session closes, producing a DetachedInstanceError. This setting is
    # effectively mandatory for FastAPI.
    expire_on_commit=False,
    class_=Session,
)


def ensure_sqlite_schema() -> None:
    """Safely create missing SQLite objects after backing up the database."""
    if not _is_sqlite:
        return

    from app.models import Base

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing_tables = [
        table for table in Base.metadata.sorted_tables if table.name not in existing_tables
    ]
    missing_columns = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        missing_columns.extend(
            (table.name, column)
            for column in table.columns
            if column.name not in existing
        )

    if not missing_tables and not missing_columns:
        return

    database_path = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    if database_path.exists():
        backup_path = database_path.with_name(
            f"{database_path.stem}.backup-{datetime.now(timezone.utc):%Y%m%d%H%M%S}.db"
        )
        shutil.copy2(database_path, backup_path)

    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        for table_name, column in missing_columns:
            column_type = column.type.compile(dialect=engine.dialect)
            default = ""
            if column.server_default is not None:
                default = f" DEFAULT {column.server_default.arg}"
            connection.execute(
                text(
                    f'ALTER TABLE "{table_name}" ADD COLUMN '
                    f'"{column.name}" {column_type}{default}'
                )
            )


ensure_sqlite_schema()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a request-scoped database session.

    Usage:
        @router.get("/students")
        def list_students(db: Session = Depends(get_db)):
            return db.query(Student).all()

    WHY THE try/finally MATTERS
    ---------------------------
    The `finally` block runs whether the endpoint returns normally or raises.
    Without it, an unhandled exception would leak the connection back to
    nobody -- the pool would slowly drain until the application hung with no
    obvious cause. This pattern guarantees the connection always returns.

    WHY THIS DOES NOT COMMIT
    ------------------------
    Committing here would make every request a single implicit transaction,
    removing the service layer's ability to control transaction boundaries.
    Services commit deliberately; on any exception, the session closes without
    committing and PostgreSQL rolls back automatically.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
