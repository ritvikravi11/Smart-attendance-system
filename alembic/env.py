"""
Alembic migration environment.

WHY THIS FILE MATTERS MORE THAN IT LOOKS
----------------------------------------
Alembic generates migrations by comparing `Base.metadata` (what the models
declare) against the live database (what exists). A model only lands in
`Base.metadata` when its MODULE IS IMPORTED.

So if `app/models/face.py` is never imported here, Alembic does not know
`face_embeddings` exists -- and if the table is already present, autogenerate
helpfully writes a migration to DROP IT.

Importing `app.models` (whose __init__ imports every model) is what prevents
that. This is the single most common Alembic mistake.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the settings and the FULL model registry.
from app.core.config import settings
from app.models import Base  # noqa: F401 -- imports every model as a side effect

config = context.config

# The URL comes from .env, never from alembic.ini. Credentials must not sit in
# a file that gets committed.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """
    Fix how Alembic renders dialect-specific column types.

    BUG THIS FIXES -- caught by actually running a migration rather than
    assuming one would work.

    Autogenerate emits this for our JSONB column:

        sa.Column('details', postgresql.JSONB(astext_type=Text()), ...)
                                                          ^^^^
    ...but the generated file only imports `sqlalchemy as sa` and
    `from sqlalchemy.dialects import postgresql`. Bare `Text` is undefined, so
    the migration dies with:

        NameError: name 'Text' is not defined

    at import time -- BEFORE any DDL runs. The failure surfaces on the very
    first `alembic upgrade head`, which is the worst possible moment: a fresh
    deployment, usually under time pressure.

    This hook renders the type with the `sa.` prefix so the generated file is
    self-consistent. Returning False for everything else lets Alembic use its
    normal rendering.
    """
    if type_ == "type" and obj.__class__.__module__.startswith("sqlalchemy.dialects.postgresql"):
        # Ensure the dialect import is present in the generated file
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        if obj.__class__.__name__ == "JSONB":
            return "postgresql.JSONB(astext_type=sa.Text())"
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. Useful for review before applying."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type detects a VARCHAR(30) -> VARCHAR(50) change.
            # Off by default, which means column type changes are silently
            # missed by autogenerate.
            compare_type=True,
            # compare_server_default catches the DEFAULT-clause bugs that bit
            # this project three times (server_default="true" rendering as the
            # string 'true').
            compare_server_default=True,
            # Renders dialect types with correct imports -- see render_item.
            render_item=render_item,
            # Partial indexes and CHECK constraints are named explicitly in the
            # models, so autogenerate can track them across revisions.
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
