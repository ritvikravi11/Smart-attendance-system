"""
Observability: recognition logs and audit logs.

TABLES IN THIS MODULE AND WHY EACH EXISTS
-----------------------------------------
recognition_logs -- Every face the system attempted to identify, matched or
                    not. This is your MEASUREMENT INSTRUMENT. Without it you
                    can only claim accuracy; with it you can prove it.

audit_logs       -- Every state-changing action and who performed it. The
                    answer to "who changed this attendance record and when?"
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    false as sa_false,
    true as sa_true,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK

if TYPE_CHECKING:
    from app.models.attendance import AttendanceSession
    from app.models.people import Student
    from app.models.user import User


class RecognitionLog(Base):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    It is tempting to skip this one -- it stores no business data and the
    system works without it. Skip it and you lose two things you cannot
    recover later.

    1. THRESHOLD CALIBRATION.
       Your similarity threshold (0.40 / 0.50) is currently a starting
       estimate. The correct engineering procedure is: collect a few hundred
       recognition attempts with known ground truth, plot the similarity
       distributions for genuine matches versus impostors, and choose the
       value that separates them. This table is where that data lives. Without
       it, your threshold remains a number someone guessed -- and "we used 0.5
       because a tutorial said so" is a weak answer under questioning.

    2. ACCURACY EVIDENCE FOR YOUR REPORT.
       "Our system achieves 96% recognition accuracy" is an assertion. The
       same sentence backed by 400 logged attempts, a confusion matrix, and
       an ROC curve is a result. This table is the difference.

    WHY matched_student_id IS NULLABLE  -- and the nullability is meaningful
    -----------------------------------------------------------------------
    NULL means UNKNOWN: a face was detected and embedded, but nothing in the
    index cleared the threshold. Those rows are exactly the interesting ones.
    They are your unknown-person alerts, and they are the impostor
    distribution you need for calibration. A NOT NULL foreign key would force
    you to discard precisely the events most worth studying.

    RETENTION NOTE
    --------------
    This table grows fast -- roughly 2 rows per second per active camera.
    A term of use produces millions of rows. It carries no data anyone needs
    after calibration, so a production deployment should purge rows older
    than ~90 days. Noting that here rather than discovering it when the disk
    fills.
    """

    __tablename__ = "recognition_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    session_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("attendance_sessions.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL for test recognitions outside any session",
    )

    matched_student_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL means UNKNOWN -- no match cleared the threshold",
    )

    confidence: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "Best similarity found, even when below threshold. Logging the "
            "near-misses is what makes the impostor distribution visible."
        ),
    )

    is_unknown: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_false(),
        comment="TRUE when no match cleared the threshold",
    )

    was_marked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_false(),
        comment=(
            "TRUE if this recognition produced a NEW attendance record. "
            "FALSE for repeat detections of an already-marked student -- "
            "which is the common case at 2 fps."
        ),
    )

    detection_score: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="SCRFD detector confidence, distinct from match confidence",
    )

    face_width_px: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "Bounding box width. Lets you correlate accuracy against face "
            "size -- useful for justifying the quality gate threshold."
        ),
    )

    processing_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="End-to-end pipeline latency; verifies NFR-1.1 empirically",
    )

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped[Optional["AttendanceSession"]] = relationship(
        "AttendanceSession", back_populates="recognition_logs"
    )
    matched_student: Mapped[Optional["Student"]] = relationship(
        "Student", back_populates="recognition_logs"
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN -1 AND 1",
            name="ck_reclog_confidence_range",
        ),
        # If it is unknown there must be no match, and vice versa.
        # Prevents a logging bug from silently corrupting your dataset --
        # which would be discovered only when your calibration made no sense.
        CheckConstraint(
            "(is_unknown = true  AND matched_student_id IS NULL) OR "
            "(is_unknown = false AND matched_student_id IS NOT NULL)",
            name="ck_reclog_unknown_consistency",
        ),
        Index("idx_reclog_session", "session_id"),
        # Unknown-person alerting: a small partial index over the rare rows.
        Index(
            "idx_reclog_unknown",
            "captured_at",
            postgresql_where=text("is_unknown = true"),
        ),
        # Supports the calibration query: pull confidence values by date range.
        Index("idx_reclog_captured", "captured_at"),
        {
            "comment": (
                "Every recognition attempt. The measurement instrument for "
                "threshold calibration and accuracy reporting."
            )
        },
    )

    def __repr__(self) -> str:
        target = self.matched_student_id or "UNKNOWN"
        return f"<RecognitionLog id={self.id} match={target} conf={self.confidence}>"


class AuditLog(Base):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Attendance affects whether students are permitted to sit examinations. It
    is consequential data, which means the question "who changed this record,
    when, and from what?" will eventually be asked -- most likely by a student
    disputing a result.

    Without an audit log the honest answer is "we cannot tell". With one it is
    a single query.

    WHY details IS JSONB
    --------------------
    Different actions carry different context. An UPDATE wants before/after
    values; a LOGIN wants a user agent; a SESSION_CLOSE wants counts. Adding a
    typed column per action would produce a sprawling table of mostly-NULLs.

    JSONB stores heterogeneous structure while remaining queryable --
    PostgreSQL can index inside a JSONB document, so
    `details->>'old_status' = 'absent'` is a real, indexable query rather than
    a string scan. This is the correct use of a semi-structured column: the
    shape genuinely varies per row.

    WHY user_id IS NULLABLE WITH ondelete="SET NULL"
    ------------------------------------------------
    Audit entries must outlive the users who created them. If a faculty member
    is deleted, the record of what they changed remains -- with user_id set to
    NULL rather than the whole row vanishing. Deleting audit history when
    deleting a user would defeat the entire purpose of auditing.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Actor. NULL if the user was later deleted, or for system actions.",
    )

    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="e.g. 'attendance_override', 'session_close', 'login_failed'",
    )

    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Table affected, e.g. 'attendance_records'",
    )

    entity_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment=(
            "Primary key of the affected row. Deliberately NOT a foreign key "
            "-- it points at many different tables, and it must remain valid "
            "even after the referenced row is deleted."
        ),
    )

    # with_variant keeps JSONB on PostgreSQL (where it is indexable and
    # queryable) while falling back to generic JSON on other dialects. This
    # is what lets the test suite run against in-memory SQLite without
    # weakening the production schema.
    details: Mapped[Optional[dict]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="Action-specific context, e.g. {'old': 'absent', 'new': 'present'}",
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True, comment="IPv4 or IPv6"
    )

    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="audit_entries"
    )

    __table_args__ = (
        # "Show the history of this specific record" -- the dispute query.
        Index("idx_audit_entity", "entity_type", "entity_id"),
        # "What happened recently?" -- DESC because audit views are always
        # newest-first, and a descending index serves that without a sort.
        Index("idx_audit_created", text("created_at DESC")),
        Index("idx_audit_user", "user_id"),
        Index("idx_audit_action", "action"),
        {"comment": "Immutable trail of state-changing actions"},
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"entity={self.entity_type}:{self.entity_id}>"
        )
