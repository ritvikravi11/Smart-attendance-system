"""
Enumerations shared across models.

WHY DEFINE THESE AS PYTHON ENUMS
--------------------------------
Three bad alternatives exist:

  1. Bare strings scattered through the codebase. One typo -- "presnt" -- and
     you have a row that no query will ever find. The bug surfaces weeks later
     as a wrong attendance percentage.
  2. A lookup TABLE for every small fixed set (a `statuses` table with four
     rows). This is over-normalisation: it costs a JOIN on every query to
     retrieve a value that will never change during the product's lifetime.
  3. PostgreSQL native ENUM type. Tempting, but altering a native enum in
     PostgreSQL is painful -- adding a value requires ALTER TYPE, and removing
     one is effectively impossible without recreating the type and every
     dependent column.

We use VARCHAR + a CHECK constraint instead. This gives database-level
enforcement (an invalid value is rejected by PostgreSQL itself, not just by
the application) while remaining trivially alterable via a normal migration.

RULE OF THUMB: use a lookup table when the set of values is *data* that an
admin might edit (departments, subjects). Use an enum + CHECK when the set is
*logic* that only a code change should alter (attendance status, marking
method). Roles sit on the boundary -- we made them a table because a future
version may add roles like "HOD" or "Parent" without a deployment.
"""

from enum import StrEnum


class RoleName(StrEnum):
    """
    Canonical role identifiers.

    These mirror the rows seeded into the `roles` table. The enum exists so
    application code can write `RoleName.FACULTY` instead of the string
    "faculty", getting autocomplete and typo protection.

    StrEnum (Python 3.11+) members compare equal to their string values, so
    `RoleName.ADMIN == "admin"` is True. That means these drop straight into
    SQL parameters and JSON responses with no conversion.
    """

    ADMIN = "admin"
    FACULTY = "faculty"
    STUDENT = "student"


class AttendanceStatus(StrEnum):
    """
    The status recorded for one student in one session.

    PRESENT  -- recognised by face, or manually marked present by faculty.
    ABSENT   -- enrolled but not marked present when the session closed.
                Note this is *derived*, never observed: you cannot detect
                someone who is not there. It is computed as
                (enrolled students) minus (marked students) at session close.
    LATE     -- present, but arrived after a configurable grace period.
                Counts toward attendance percentage.
    EXCUSED  -- authorised absence (medical, official duty). Excluded from
                the percentage *denominator* so it neither helps nor hurts.
    """

    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"

    @classmethod
    def counts_as_attended(cls) -> set[str]:
        """Statuses that count in the numerator of attendance percentage."""
        return {cls.PRESENT.value, cls.LATE.value}

    @classmethod
    def counts_in_denominator(cls) -> set[str]:
        """
        Statuses that count in the denominator.

        EXCUSED is deliberately omitted -- an excused absence should not
        penalise the student, so the session is removed from their total
        rather than counted as a miss.
        """
        return {cls.PRESENT.value, cls.LATE.value, cls.ABSENT.value}


class MarkingMethod(StrEnum):
    """
    How an attendance record came to exist. This is an audit trail column.

    FACE   -- written automatically by the recognition pipeline. MUST carry a
              confidence score (enforced by CHECK constraint).
    MANUAL -- written by a human via the UI, including the bulk 'absent' rows
              generated at session close. MUST carry marked_by (enforced by
              CHECK constraint).

    Keeping this column lets you answer "how many records did the AI actually
    produce versus how many did faculty correct?" -- which is precisely the
    accuracy metric your project report needs.
    """

    FACE = "face"
    MANUAL = "manual"


class SessionStatus(StrEnum):
    """
    Lifecycle state of an attendance session.

    OPEN      -- accepting marks. Recognition writes here.
    CLOSED    -- finalised. Absentees have been generated. Records are
                 immutable except by Admin.
    CANCELLED -- the class did not happen (holiday, faculty absent). Excluded
                 from attendance percentage calculations entirely, so students
                 are not penalised for a class that never occurred.

    The CANCELLED state is why percentage must be computed over sessions
    WHERE status = 'closed', not over all sessions.
    """

    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class AuditAction(StrEnum):
    """Actions recorded in the audit log."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    SESSION_OPEN = "session_open"
    SESSION_CLOSE = "session_close"
    SESSION_REOPEN = "session_reopen"
    ATTENDANCE_OVERRIDE = "attendance_override"
    FACE_ENROLL = "face_enroll"
    FACE_DELETE = "face_delete"
    CONSENT_GRANT = "consent_grant"
    CONSENT_REVOKE = "consent_revoke"
