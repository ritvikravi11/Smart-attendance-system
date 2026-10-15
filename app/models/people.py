"""
Profile tables: students and faculty.

TABLES IN THIS MODULE AND WHY EACH EXISTS
-----------------------------------------
students -- Role-specific attributes for learners: roll number, semester,
            biometric consent, enrolment flag. Linked 1:1 to `users`.

faculty  -- Role-specific attributes for teaching staff: employee code,
            designation. Linked 1:1 to `users`.

WHY THESE ARE SEPARATE FROM `users`
-----------------------------------
This is the table-per-type inheritance pattern. The alternative is one wide
`users` table holding roll_number, semester, admission_year, employee_code,
and designation together -- where every faculty row has three NULLs and every
student row has two. That table violates normalisation (columns depend on the
role, not on the user), and the NULL count grows with every role added.

Splitting means:
  - Each table's columns are all meaningful for every row in it.
  - Constraints can be real: `semester` can be NOT NULL in `students`, which
    would be impossible in a shared table.
  - Adding a "Parent" role later adds a `parents` table without touching
    either of these.
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
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.academic import Department, Enrollment, SubjectOffering
    from app.models.attendance import AttendanceRecord
    from app.models.face import FaceEmbedding
    from app.models.logs import RecognitionLog
    from app.models.user import User


class Student(Base, SoftDeleteMixin, TimestampMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Holds the attributes that are meaningful for a learner and meaningless for
    anyone else: roll number, current semester, admission year, and -- most
    importantly -- biometric consent state.

    THE CONSENT COLUMN IS NOT DECORATION
    ------------------------------------
    `consent_given_at` is a legal requirement, not a nice-to-have. Face
    embeddings are biometric data. Under India's DPDP Act 2023 and GDPR
    Article 9, biometric data used to uniquely identify a person is a special
    category requiring explicit, informed, recorded consent.

    Storing a TIMESTAMP rather than a BOOLEAN is deliberate: it answers
    "when was consent given?", which is what an auditor actually asks. NULL
    means no consent -- and `EnrollmentService` refuses to process face images
    for any student whose `consent_given_at` is NULL. The check is enforced in
    code because a database constraint cannot express "you may not INSERT into
    a different table unless this column is set".

    WHY `face_enrolled` IS DENORMALISED
    -----------------------------------
    Strictly, this is derivable: a student is enrolled if a row exists in
    `face_embeddings` with is_primary = TRUE. Storing it here duplicates that
    fact, which normalisation would normally forbid.

    It is justified because the admin UI lists 1,000 students and shows an
    "enrolled?" badge on each. Deriving it means either an EXISTS subquery per
    row (1,000 queries) or a LEFT JOIN plus GROUP BY on every page load. This
    boolean turns that into a plain column read.

    This is a deliberate, documented denormalisation for read performance. The
    rule when you do this: exactly ONE place in the code may write it.
    `EnrollmentService` owns this column; nothing else touches it. An
    undocumented denormalisation is a bug waiting to happen; a documented one
    with a single writer is an optimisation.
    """

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # <-- THIS is what enforces 1:1 in the database
        comment="1:1 link to the authentication identity",
    )

    roll_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
        comment="Institutional identifier, e.g. '2226CSE042'. Natural key.",
    )

    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )

    semester: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="Current semester (1-8)",
    )

    phone: Mapped[Optional[str]] = mapped_column(
        String(15),
        nullable=True,
        comment="Contact number; E.164 or local format",
    )

    admission_year: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    # ---- Profile photo (Cloudinary integration) -----------------------
    profile_photo_url: Mapped[Optional[str]] = mapped_column(
        "profile_photo_url",
        String(500),
        nullable=True,
        comment="Cloudinary URL for student profile photo. Private delivery with secure URLs.",
    )

    profile_photo_public_id: Mapped[Optional[str]] = mapped_column(
        "profile_photo_public_id",
        String(200),
        nullable=True,
        comment="Cloudinary public ID for the photo asset.",
    )

    profile_photo_format: Mapped[Optional[str]] = mapped_column(
        "profile_photo_format",
        String(10),
        nullable=True,
        comment="jpeg | png",
    )

    profile_photo_updated_at: Mapped[Optional[datetime]] = mapped_column(
        "profile_photo_updated_at",
        DateTime(timezone=True),
        nullable=True,
        comment="When profile photo was last updated",
    )

    # ---- Biometric governance ------------------------------------------
    consent_given_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "NULL = no biometric consent. Face enrolment is REFUSED while "
            "this is NULL. Required by DPDP Act 2023 / GDPR Art. 9."
        ),
    )

    consent_revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set on withdrawal; triggers deletion of embeddings",
    )

    face_enrolled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_false(),
        comment=(
            "Denormalised: TRUE iff an active primary embedding exists. "
            "Written ONLY by EnrollmentService."
        ),
    )

    # ---- Relationships -------------------------------------------------
    user: Mapped["User"] = relationship("User", back_populates="student_profile")
    department: Mapped["Department"] = relationship(
        "Department", back_populates="students"
    )

    # CASCADE here is deliberate and privacy-motivated: deleting a student
    # must genuinely erase their biometric data (right to erasure).
    face_embeddings: Mapped[list["FaceEmbedding"]] = relationship(
        "FaceEmbedding",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    enrollments: Mapped[list["Enrollment"]] = relationship(
        "Enrollment", back_populates="student", cascade="all, delete-orphan"
    )

    # NO cascade: attendance records are historical fact and must survive.
    # This is why students are soft-deleted rather than removed.
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        "AttendanceRecord", back_populates="student"
    )

    recognition_logs: Mapped[list["RecognitionLog"]] = relationship(
        "RecognitionLog", back_populates="matched_student"
    )

    __table_args__ = (
        CheckConstraint(
            "semester BETWEEN 1 AND 8", name="ck_student_semester_range"
        ),
        # Consent cannot be revoked before it was granted. Cheap to state,
        # and it catches an entire class of ordering bug.
        CheckConstraint(
            "consent_revoked_at IS NULL OR consent_given_at IS NOT NULL",
            name="ck_student_consent_order",
        ),
        Index("idx_students_roll", "roll_number"),
        Index(
            "idx_students_dept_sem",
            "department_id",
            "semester",
            postgresql_where=text("is_active = true"),
        ),
        # Supports the admin filter "show students without face data".
        Index(
            "idx_students_not_enrolled",
            "id",
            postgresql_where=text("face_enrolled = false AND is_active = true"),
        ),
        {"comment": "Learner profile; 1:1 with users"},
    )

    @property
    def has_valid_consent(self) -> bool:
        """
        True only if consent was granted and not subsequently revoked.

        A property rather than a column because it is trivially derivable and
        caching it would create a third piece of state to keep in sync.
        """
        return self.consent_given_at is not None and self.consent_revoked_at is None

    def __repr__(self) -> str:
        return f"<Student id={self.id} roll={self.roll_number!r}>"


class Faculty(Base, SoftDeleteMixin, TimestampMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Holds attributes meaningful only for teaching staff, and -- structurally
    more important -- provides the anchor that `subject_offerings.faculty_id`
    points at.

    That foreign key is what makes the authorisation rule expressible:

        "may this user view this attendance session?"
          -> session.offering.faculty.user_id == current_user.id

    Without this table, that chain has no link and faculty could read one
    another's class data.

    NOTE ON ondelete="RESTRICT" IN subject_offerings
    ------------------------------------------------
    A faculty member who has taught cannot be hard-deleted, because offerings
    reference them and those offerings anchor real attendance history. The
    correct operation is deactivation (is_active = FALSE), which removes them
    from dropdowns while preserving every historical link.
    """

    __tablename__ = "faculty"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="1:1 link to the authentication identity",
    )

    employee_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
        comment="Institutional staff identifier. Natural key.",
    )

    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )

    designation: Mapped[Optional[str]] = mapped_column(
        String(60),
        nullable=True,
        comment="e.g. 'Assistant Professor'",
    )

    phone: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="faculty_profile")
    department: Mapped["Department"] = relationship(
        "Department", back_populates="faculty_members"
    )
    offerings: Mapped[list["SubjectOffering"]] = relationship(
        "SubjectOffering", back_populates="faculty"
    )

    __table_args__ = (
        Index(
            "idx_faculty_dept",
            "department_id",
            postgresql_where=text("is_active = true"),
        ),
        {"comment": "Teaching staff profile; 1:1 with users"},
    )

    def __repr__(self) -> str:
        return f"<Faculty id={self.id} code={self.employee_code!r}>"
