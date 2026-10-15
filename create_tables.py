"""
Create all database tables directly from SQLAlchemy models.

This script bypasses Alembic migrations (which are PostgreSQL-specific) and
creates the schema from the ORM model definitions. Safe for SQLite.

Usage:
    python create_tables.py
"""

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
logger = logging.getLogger("create_tables")


def main() -> int:
    from app.core.config import settings
    from app.db.session import engine
    # Import ALL models so Base.metadata knows about every table
    from app.models import Base  # noqa: F401

    logger.info("Database URL: %s", settings.database_url)
    logger.info("Creating all tables from model definitions...")

    Base.metadata.create_all(bind=engine)

    # List created tables
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    logger.info("Created %d tables: %s", len(tables), ", ".join(sorted(tables)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
