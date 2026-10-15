"""
Data retention: purge rows that have outlived their usefulness.

WHY THIS SCRIPT EXISTS
----------------------
Three tables grow without bound and nothing ever removed a row:

  recognition_logs   ~2 rows/second per active camera. Six cameras running six
                     hours a day for a 90-day term is roughly 23 MILLION rows.
                     Every one of them carries no value once the similarity
                     threshold has been calibrated.

  refresh_tokens     One row per login, forever. Revoked and long-expired
                     tokens are dead weight -- and they slow the very lookup
                     that runs on every token refresh.

  audit_logs         One row per state change. Genuinely worth keeping longer,
                     but not forever.

The design document flagged recognition_logs retention as a known issue and
then nothing implemented it. That is the most common way a "known issue"
becomes an outage: it is written down, and writing it down feels like handling
it.

WHY A SCRIPT AND NOT A BACKGROUND TASK IN THE APP
-------------------------------------------------
An in-process scheduler would run once per worker, so four Uvicorn workers
would run four concurrent deletes. A cron job runs exactly once regardless of
how the application is deployed, and it can be scheduled for a quiet hour.

Windows Task Scheduler, cron, or a Kubernetes CronJob -- all fine. What matters
is that it runs somewhere other than inside the request path.

Usage:
    python -m scripts.purge_old_data --dry-run     # report, change nothing
    python -m scripts.purge_old_data               # actually delete
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.logs import AuditLog, RecognitionLog
from app.models.user import RefreshToken

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
logger = logging.getLogger("purge")


def _count_before(db, model, column, cutoff) -> int:
    return db.execute(
        select(func.count()).select_from(model).where(column < cutoff)
    ).scalar_one()


def purge(dry_run: bool = False) -> dict[str, int]:
    """
    Delete expired rows. Returns {table: rows_removed}.

    DELETES ARE BATCHED because a single DELETE over 20 million rows takes a
    long lock, and on PostgreSQL that blocks concurrent writes to the same
    table. Chunking keeps each transaction short so attendance marking is never
    stalled by housekeeping.
    """
    now = datetime.now(timezone.utc)
    removed: dict[str, int] = {}

    targets = [
        (
            "recognition_logs",
            RecognitionLog,
            RecognitionLog.captured_at,
            now - timedelta(days=settings.recognition_log_retention_days),
        ),
        (
            "audit_logs",
            AuditLog,
            AuditLog.created_at,
            now - timedelta(days=settings.audit_log_retention_days),
        ),
        (
            # Expired tokens only. A token that is merely revoked but not yet
            # expired is still evidence for a security investigation.
            "refresh_tokens",
            RefreshToken,
            RefreshToken.expires_at,
            now,
        ),
    ]

    db = SessionLocal()
    try:
        for name, model, column, cutoff in targets:
            total = _count_before(db, model, column, cutoff)
            removed[name] = total

            if total == 0:
                logger.info("%-20s nothing to remove", name)
                continue

            if dry_run:
                logger.info(
                    "%-20s would remove %s rows older than %s",
                    name, f"{total:,}", cutoff.date(),
                )
                continue

            deleted = 0
            batch_size = 10_000
            while deleted < total:
                # Delete by primary key in chunks. `IN (subquery LIMIT n)` is
                # portable; PostgreSQL-specific `DELETE ... LIMIT` is not.
                ids = db.execute(
                    select(model.id).where(column < cutoff).limit(batch_size)
                ).scalars().all()
                if not ids:
                    break
                db.execute(delete(model).where(model.id.in_(ids)))
                db.commit()  # short transaction, lock released between batches
                deleted += len(ids)
                logger.info("%-20s %s / %s", name, f"{deleted:,}", f"{total:,}")

            logger.info("%-20s removed %s rows", name, f"{deleted:,}")

    finally:
        db.close()

    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge expired rows")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without deleting anything",
    )
    args = parser.parse_args()

    logger.info(
        "Retention: recognition_logs %sd, audit_logs %sd, refresh_tokens on expiry",
        settings.recognition_log_retention_days,
        settings.audit_log_retention_days,
    )
    if args.dry_run:
        logger.info("DRY RUN -- nothing will be deleted")

    try:
        purge(dry_run=args.dry_run)
        return 0
    except Exception:
        logger.exception("Purge failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
