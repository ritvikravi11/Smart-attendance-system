"""
Datetime helpers.

WHY THIS MODULE EXISTS
----------------------
`_as_utc` was defined twice -- once in auth_service.py and once in
attendance_service.py -- with identical bodies. Two copies of one function is
a bug waiting for someone to fix one and not the other, and the failure mode
here is subtle: a naive/aware comparison raises TypeError at runtime, on a code
path that looked correct in review.

The duplication was not laziness; it happened because each service hit the same
problem independently. That is exactly when a shared module is warranted --
not on the first occurrence, but on the second.
"""

from datetime import date, datetime, timezone


def as_utc(value: datetime | None) -> datetime | None:
    """
    Coerce a datetime read from the database into timezone-aware UTC.

    WHY THIS IS NECESSARY
    ---------------------
    We store TIMESTAMPTZ and always write timezone-aware values. What comes
    BACK depends on the driver: psycopg2 preserves tzinfo, but some drivers --
    and SQLite, used in the test suite -- return NAIVE datetimes with tzinfo
    stripped.

    Comparing a naive datetime against an aware one raises:

        TypeError: can't compare offset-naive and offset-aware datetimes

    which surfaces as a 500 on an endpoint that reads perfectly. Normalising at
    the boundary where database values enter Python logic is the robust fix;
    wrapping each comparison in try/except is not.

    A naive value is ASSUMED to be UTC, which is safe because everything in
    this system is written as UTC by policy.
    """
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def utc_date(value: datetime | None) -> date | None:
    """The UTC calendar date of a timestamp, tz-normalised first."""
    normalised = as_utc(value)
    return normalised.date() if normalised else None


def utc_now() -> datetime:
    """
    Timezone-aware current time.

    Use this instead of `datetime.utcnow()`, which returns a NAIVE datetime
    (no tzinfo), compares incorrectly against aware values from the database,
    and is deprecated as of Python 3.12.
    """
    return datetime.now(timezone.utc)
