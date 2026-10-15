"""
Emit the complete PostgreSQL DDL that these models generate.

Useful for (a) reviewing the schema before running a migration, and
(b) pasting into your project report as the physical schema appendix.

Usage:  python scripts/dump_ddl.py > docs/schema.sql
"""

import sys

sys.path.insert(0, "/home/claude/backend")

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base

dialect = postgresql.dialect()

print("-- =====================================================================")
print("-- AI Attendance Management System -- PostgreSQL Schema")
print("-- Generated from SQLAlchemy models. Do not edit by hand;")
print("-- change the models and regenerate.")
print("-- =====================================================================")
print()

for table in Base.metadata.sorted_tables:
    ddl = str(CreateTable(table).compile(dialect=dialect)).strip()
    print(f"-- {'-' * 68}")
    comment = table.comment or ""
    print(f"-- {table.name.upper()}" + (f" -- {comment}" if comment else ""))
    print(f"-- {'-' * 68}")
    print(ddl + ";")
    print()

    for index in sorted(table.indexes, key=lambda i: i.name or ""):
        print(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    if table.indexes:
        print()

# Summary statistics
n_tables = len(Base.metadata.tables)
n_indexes = sum(len(t.indexes) for t in Base.metadata.tables.values())
n_fks = sum(len(t.foreign_keys) for t in Base.metadata.tables.values())
n_checks = sum(
    1
    for t in Base.metadata.tables.values()
    for c in t.constraints
    if type(c).__name__ == "CheckConstraint"
)
n_uniques = sum(
    1
    for t in Base.metadata.tables.values()
    for c in t.constraints
    if type(c).__name__ == "UniqueConstraint"
)
n_partial = sum(
    1
    for t in Base.metadata.tables.values()
    for i in t.indexes
    if i.dialect_options.get("postgresql", {}).get("where") is not None
)

print("-- =====================================================================")
print(f"-- Tables:            {n_tables}")
print(f"-- Indexes:           {n_indexes}  (of which {n_partial} are partial)")
print(f"-- Foreign keys:      {n_fks}")
print(f"-- CHECK constraints: {n_checks}")
print(f"-- UNIQUE constraints:{n_uniques}")
print("-- =====================================================================")
