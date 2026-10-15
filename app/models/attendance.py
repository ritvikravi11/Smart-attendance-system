"""
Attendance core: sessions and records.

TABLES IN THIS MODULE AND WHY EACH EXISTS
-----------------------------------------
attendance_sessions -- One occurrence of a class. "CS301 section B, 6 Aug 2026,
                       09:00, room A-204". Groups the records that belong
                       together and provides the lifecycle (open/closed) that
                       makes "mark the rest absent" a well-defined operation.

attendance_records  -- One student's status in one session. This is the table
                       the entire system exists to populate. It carries the
                       most important constraint in the schema.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    false as sa_false,
    true as sa_true,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK, TimestampMixin
from app.models.enums import AttendanceStatus, MarkingMethod, SessionStatus

if TYPE_CHECKING:
    from app.models.academic import SubjectOffering
    from app.models.attendance_photos import AttendancePhoto
    from app.models.logs import RecognitionLog
    from app.models.people import Student
    from app.models.user import User


class AttendanceSession(Base, TimestampMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Consider the alternative: storing attendance as flat rows of
    (student, subject, date, status) with no session entity. Four things break:

    1. "MARK EVERYONE ELSE ABSENT" BECOMES UNDEFINED.
       Absence is derived, not observed -- you cannot detect a student who is
       not in the room. It is computed as (enrolled students) MINUS (marked
       students). That subtraction needs a bounded set to subtract from. The
       session IS that boundary.

    2. TWO CLASSES ON ONE DAY COLLIDE.
       A subject with a morning lecture and an afternoon lab on the same date
       produces two legitimate attendance events. Keyed only on (student,
       subject, date), the second overwrites the first.

    3. NO LIFECYCLE.
       Records must be mutable while the class is running and immutable
       afterwards. Without a session there is no state to hold "open" versus
       "closed", so there is no point at which attendance becomes final.

    4. CANCELLED CLASSES CORRUPT PERCENTAGES.
       A class cancelled for a holiday must not count against students. With
       a session you set status='cancelled' and exclude it from the
       denominator. Without one, the absence of records is indistinguishable
       from everybody being absent.

    THE STATUS LIFECYCLE
    --------------------
        open ---> closed        (normal completion; absentees generated)
        open ---> cancelled     (class did not take place)
        closed ---> open        (Admin only, audit-logged, for corrections)

    Attendance percentage counts sessions WHERE status = 'closed'. Open
    sessions are still in progress; cancelled ones never happened.
    """

    __tablename__ = "attendance_sessions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    offering_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("subject_offerings.id", ondelete="RESTRICT"),
        nullable=False,
        comment=(
            "Points to the OFFERING, not the subject. This is what makes "
            "the faculty authorisation check possible."
        ),
    )

    session_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment=(
            "Local calendar date of the class. Stored separately from "
            "started_at because 'which day was this class' is a local "
            "question, and deriving it from a UTC timestamp gives the wrong "
            "answer for late-evening classes."
        ),
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Exact moment the session opened (UTC)",
    )

    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="NULL while open; set on close",
    )

    # server_default takes the LITERAL value; do not wrap it in SQL quotes.
    status: Mapped[str] = mapped_column(
        String(15),
        nullable=False,
        server_default=SessionStatus.OPEN.value,
        comment="open | closed | cancelled",
    )

    room: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="Physical location, e.g. 'A-204'",
    )

    late_threshold_minutes: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment=(
            "Grace period. Students recognised after this many minutes are "
            "marked LATE rather than PRESENT. NULL disables late marking."
        ),
    )

    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Which user opened this session (accountability)",
    )

    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ---- Relationships -------------------------------------------------
    offering: Mapped["SubjectOffering"] = relationship(
        "SubjectOffering", back_populates="sessions"
    )
    creator: Mapped["User"] = relationship("User", foreign_keys=[created_by])

    # CASCADE: deleting a session should remove its records. They have no
    # meaning without their session -- an attendance record for a session
    # that never happened is not history, it is orphaned garbage.
    records: Mapped[list["AttendanceRecord"]] = relationship(
        "AttendanceRecord",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    recognition_logs: Mapped[list["RecognitionLog"]] = relationship(
        "RecognitionLog", back_populates="session"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed', 'cancelled')",
            name="ck_session_status_valid",
        ),
        # A closed session must have an end time; an open one must not.
        # This makes the state machine's invariants unbreakable rather than
        # merely conventional.
        CheckConstraint(
            "(status = 'open'  AND ended_at IS NULL) OR "
            "(status <> 'open' AND ended_at IS NOT NULL)",
            name="ck_session_ended_matches_status",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_session_end_after_start",
        ),
        # The main listing query: "sessions for this offering, newest first".
        Index("idx_sessions_offering_date", "offering_id", "session_date"),
        # THE hot lookup during marking: "is there an open session here?"
        # A partial index means this scans only the handful of currently-open
        # sessions, never the thousands of closed ones.
        Index(
            "idx_sessions_open",
            "offering_id",
            postgresql_where=text("status = 'open'"),
        ),
        # Supports "all sessions today" across the institution.
        Index("idx_sessions_date", "session_date"),
        {"comment": "One occurrence of a class; groups attendance records"},
    )

    @property
    def is_open(self) -> bool:
        return self.status == SessionStatus.OPEN.value

    def __repr__(self) -> str:
        return (
            f"<AttendanceSession id={self.id} offering_id={self.offering_id} "
            f"date={self.session_date} status={self.status!r}>"
        )


class AttendanceRecord(Base, TimestampMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    This is the point of the entire system. Every other table exists to make
    these rows correct: users to authenticate the marker, offerings to
    authorise them, embeddings to identify the student, sessions to bound the
    event.

    ***  THE MOST IMPORTANT CONSTRAINT IN THE SCHEMA  ***

        UniqueConstraint("session_id", "student_id")

    This single line is the complete implementation of duplicate prevention
    (FR-4.3). It deserves a full explanation, because the reasoning is the
    difference between a system that works and one that appears to.

    THE NAIVE APPROACH, and why it fails:

        existing = db.query(AttendanceRecord).filter(...).first()
        if existing:
            return existing          # already marked
        db.add(AttendanceRecord(...))  # <-- gap between check and insert
        db.commit()

    There is a window between the SELECT and the INSERT. Two requests that
    both pass the check before either commits will both insert. This is the
    classic time-of-check-to-time-of-use race.

    That window is not theoretical here. The camera streams at 2 frames per
    second. A student standing in frame for ten seconds generates twenty
    recognition events. Network jitter routinely delivers two of them
    concurrently. The race WILL fire, probably within the first minute of
    your first real class, and it produces duplicate records that silently
    inflate attendance counts.

    THE CORRECT APPROACH -- let the database arbitrate:

        try:
            db.add(AttendanceRecord(...))
            db.commit()
        except IntegrityError:
            db.rollback()
            return existing_record     # someone else won the race; fine

    PostgreSQL evaluates the unique index atomically. Exactly one INSERT
    succeeds; the other raises IntegrityError. No window exists.

    Note what the except branch does NOT do: it does not treat the collision
    as an error. The student is marked; the desired end state holds; the
    operation returns success. This makes marking IDEMPOTENT -- calling it
    twenty times produces the same result as calling it once. That property
    is what lets a 2 fps stream hammer the endpoint harmlessly.

    Application-level checking is defence in depth and worth keeping for a
    friendly error message. But the database constraint is the actual
    guarantee, and it holds even against a buggy service, a rogue script, or
    a psql session.
    """

    __tablename__ = "attendance_records"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        # RESTRICT, not CASCADE: attendance history must survive attempts to
        # delete a student. This is why students are soft-deleted.
        ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="present | absent | late | excused",
    )

    method: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="face | manual -- audit trail of how this row was created",
    )

    confidence: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "Cosine similarity at match time (0-1). NULL for manual marks. "
            "Retained for threshold calibration and accuracy reporting."
        ),
    )

    needs_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_false(),
        comment=(
            "TRUE when confidence fell in the grey band (0.40-0.50). "
            "Surfaces the record for faculty confirmation rather than "
            "silently accepting an uncertain match."
        ),
    )

    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the mark was made (UTC). Used for late calculation.",
    )

    marked_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Which user marked this. NULL when the AI marked it.",
    )

    overridden_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "Set when a human corrects an AI mark. The pair "
            "(method='face', overridden_by NOT NULL) is precisely your "
            "false-positive count -- the accuracy metric for your report."
        ),
    )

    overridden_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Free text, e.g. reason for an excused absence",
    )

    # ---- Relationships -------------------------------------------------
    session: Mapped["AttendanceSession"] = relationship(
        "AttendanceSession", back_populates="records"
    )
    student: Mapped["Student"] = relationship(
        "Student", back_populates="attendance_records"
    )
    marker: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[marked_by]
    )
    overrider: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[overridden_by]
    )
    photos: Mapped[list["AttendancePhoto"]] = relationship(
        "AttendancePhoto",
        back_populates="attendance_record",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # ============================================================
        # THE constraint. Everything above explains this line.
        # ============================================================
        UniqueConstraint(
            "session_id", "student_id", name="uq_attendance_session_student"
        ),
        CheckConstraint(
            "status IN ('present', 'absent', 'late', 'excused')",
            name="ck_record_status_valid",
        ),
        CheckConstraint(
            "method IN ('face', 'manual')", name="ck_record_method_valid"
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_record_confidence_range",
        ),
        # CROSS-COLUMN INTEGRITY -- makes malformed rows impossible rather
        # than merely unlikely:
        #   a face-marked row MUST carry a confidence score
        #   a manually-marked row MUST record who marked it
        # Without this, a bug could write method='face' with no confidence,
        # and your accuracy statistics would be quietly wrong.
        CheckConstraint(
            "(method = 'face'   AND confidence IS NOT NULL) OR "
            "(method = 'manual' AND marked_by  IS NOT NULL)",
            name="ck_record_method_requires_provenance",
        ),
        # An override must record both who and when, or neither.
        CheckConstraint(
            "(overridden_by IS NULL AND overridden_at IS NULL) OR "
            "(overridden_by IS NOT NULL AND overridden_at IS NOT NULL)",
            name="ck_record_override_complete",
        ),
        # Student dashboard: "all my records". Very hot query.
        Index("idx_records_student", "student_id"),
        # Session roster: "everyone marked in this session".
        Index("idx_records_session", "session_id"),
        # Composite index supporting per-subject percentage calculation
        # without a separate lookup per session.
        Index("idx_records_student_status", "student_id", "status"),
        # Faculty review queue -- tiny partial index over only flagged rows.
        Index(
            "idx_records_needs_review",
            "session_id",
            postgresql_where=text("needs_review = true"),
        ),
        {
            "comment": (
                "One student's status in one session. UNIQUE(session_id, "
                "student_id) is the duplicate-prevention guarantee."
            )
        },
    )

    @property
    def was_overridden(self) -> bool:
        """True if a human corrected this record after the AI wrote it."""
        return self.overridden_by is not None

    @property
    def counts_as_attended(self) -> bool:
        return self.status in AttendanceStatus.counts_as_attended()

    def __repr__(self) -> str:
        return (
            f"<AttendanceRecord session_id={self.session_id} "
            f"student_id={self.student_id} status={self.status!r} "
            f"method={self.method!r}>"
        )
