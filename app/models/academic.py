"""
Academic structure: departments, subjects, offerings, enrollments.

TABLES IN THIS MODULE AND WHY EACH EXISTS
-----------------------------------------
departments       -- Organisational unit. Referenced by students, faculty, and
                     subjects. Exists so "Computer Science" is stored once
                     rather than typed into 1,000 student rows.

subjects          -- The CATALOGUE entry. "CS301 - Data Structures, 4 credits,
                     semester 3". A subject is a description of a course that
                     exists in the syllabus.

subject_offerings -- The INSTANCE. "CS301, section B, taught by Prof. Das,
                     2026-2027". This is the table most student projects miss,
                     and its absence breaks authorisation entirely. Full
                     reasoning below.

enrollments       -- Resolves the many-to-many between students and offerings.
                     A student takes many subjects; a subject has many students.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.attendance import AttendanceSession
    from app.models.people import Faculty, Student


class Department(Base, SoftDeleteMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Without it, every student row would store the literal string "Computer
    Science and Engineering". A thousand students means a thousand copies of
    the same string. Three consequences follow, and all three are real:

      1. STORAGE waste is the least important one.
      2. UPDATE ANOMALY: renaming the department to "Computer Science &
         Engineering" requires touching 1,000 rows, and any row missed leaves
         the data inconsistent.
      3. INCONSISTENCY: someone types "Comp. Sci." on one form. Now a filter
         for "Computer Science and Engineering" silently omits that student.

    This is the textbook justification for Second Normal Form, and departments
    are the cleanest example of it in this schema.
    """

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        unique=True,
        comment="Short code, e.g. 'CSE'. Used in roll numbers and reports.",
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Full name, e.g. 'Computer Science and Engineering'",
    )

    students: Mapped[list["Student"]] = relationship(
        "Student", back_populates="department"
    )
    faculty_members: Mapped[list["Faculty"]] = relationship(
        "Faculty", back_populates="department"
    )
    subjects: Mapped[list["Subject"]] = relationship(
        "Subject", back_populates="department"
    )

    def __repr__(self) -> str:
        return f"<Department id={self.id} code={self.code!r}>"


class Subject(Base, SoftDeleteMixin, TimestampMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    It holds the *catalogue definition* of a course: its code, its title, how
    many credits it carries, which semester it belongs to. These facts are
    stable across years and independent of who teaches it.

    THE CRITICAL DISTINCTION: SUBJECT vs OFFERING
    ---------------------------------------------
    A `Subject` is "CS301 Data Structures exists in the syllabus".
    An `Offering` is "CS301 is being taught to section B by Prof. Das this year".

    Attendance attaches to the OFFERING, never to the SUBJECT. See the
    SubjectOffering docstring for why this distinction is load-bearing rather
    than academic.
    """

    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        comment="Syllabus code, e.g. 'CS301'",
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        comment="Course title, e.g. 'Data Structures and Algorithms'",
    )

    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )

    semester: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="Which semester this subject belongs to (1-8)",
    )

    credits: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
        comment="Credit weight; used for weighted attendance reports",
    )

    department: Mapped["Department"] = relationship(
        "Department", back_populates="subjects"
    )
    offerings: Mapped[list["SubjectOffering"]] = relationship(
        "SubjectOffering", back_populates="subject"
    )

    __table_args__ = (
        # A CHECK constraint is validation the DATABASE enforces. Pydantic
        # validates at the API boundary, but a migration script, a seed
        # script, or a psql session bypasses Pydantic entirely. This does not.
        CheckConstraint(
            "semester BETWEEN 1 AND 8",
            name="ck_subject_semester_range",
        ),
        CheckConstraint(
            "credits IS NULL OR credits BETWEEN 1 AND 10",
            name="ck_subject_credits_range",
        ),
        Index(
            "idx_subjects_dept_sem",
            "department_id",
            "semester",
            postgresql_where=text("is_active = true"),
        ),
        {"comment": "Catalogue of courses; the syllabus definition"},
    )

    def __repr__(self) -> str:
        return f"<Subject id={self.id} code={self.code!r}>"


class SubjectOffering(Base, SoftDeleteMixin, TimestampMixin):
    """
    WHY THIS TABLE EXISTS  ***  READ THIS ONE CAREFULLY  ***
    --------------------------------------------------------
    This is the single most important structural decision in the schema, and
    the one most commonly missed. It is worth understanding fully.

    THE NAIVE DESIGN (what most projects do):

        attendance_sessions.subject_id -> subjects.id

    That looks reasonable. It breaks in four separate ways:

    1. AUTHORISATION BECOMES IMPOSSIBLE.
       Your security rule is "a faculty member may only view attendance for
       subjects they teach". With the naive design, ask the database: "may
       Prof. Sharma view this session?" There is no answer. The session points
       to CS301. Nothing anywhere records WHO TEACHES CS301. The check cannot
       be written, so faculty can read each other's data. This is a real
       vulnerability class -- Insecure Direct Object Reference -- not a
       theoretical concern.

    2. MULTIPLE SECTIONS COLLIDE.
       Section A (Prof. Sharma, 60 students) and Section B (Prof. Das, 55
       students) both study CS301. With the naive design, both sections' 
       attendance lands in the same bucket, mixed together and unseparable.

    3. HISTORY CORRUPTS ON REASSIGNMENT.
       Prof. Sharma leaves; Prof. Nayak takes over CS301. Under the naive
       design there is no record of who taught it before -- so last year's
       attendance now appears to belong to Prof. Nayak, who was not even
       employed then.

    4. YEARS RUN TOGETHER.
       2025-26 and 2026-27 attendance for CS301 accumulate in the same place
       with nothing distinguishing them.

    THE FIX: introduce the offering as a first-class entity.

        subject   -- WHAT is taught          (CS301 Data Structures)
        faculty   -- WHO teaches it          (Prof. Das)
        section   -- to WHICH group          (Section B)
        year      -- WHEN                    (2026-2027)
        --------------------------------------------------------
        offering  -- one specific teaching instance

    Attendance now attaches to the offering. All four problems dissolve:

      1. Authorisation is a JOIN:
         session -> offering -> faculty_id, compare to current user. Done.
      2. Each section has its own offering, its own roster, its own sessions.
      3. Last year's offering keeps last year's faculty_id forever.
      4. academic_year separates the years.

    In ER terms, this is a resolved ternary relationship. Any time three or
    more entities must associate simultaneously -- here subject, faculty, and
    student-group -- the correct model is an associative entity, not extra
    foreign keys sprinkled across the participants.

    If you take one schema lesson from this project, take this one.
    """

    __tablename__ = "subject_offerings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    subject_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
        comment="WHAT is being taught",
    )

    faculty_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("faculty.id", ondelete="RESTRICT"),
        nullable=False,
        comment="WHO teaches it. This column is the basis of authorisation.",
    )

    # NOTE: server_default takes the LITERAL value, not a quoted SQL string.
    # Writing server_default="'A'" renders as DEFAULT '''A''' and stores the
    # three-character value 'A' *including the quote marks*. Caught by the
    # validation harness -- exactly the kind of bug that survives code review
    # and surfaces as a mysterious filter mismatch weeks later.
    section: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default="A",
        comment="Student group, e.g. 'A', 'B'",
    )

    academic_year: Mapped[str] = mapped_column(
        String(9),
        nullable=False,
        comment="Format '2026-2027'. Separates years cleanly.",
    )

    subject: Mapped["Subject"] = relationship("Subject", back_populates="offerings")
    faculty: Mapped["Faculty"] = relationship("Faculty", back_populates="offerings")
    enrollments: Mapped[list["Enrollment"]] = relationship(
        "Enrollment", back_populates="offering", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["AttendanceSession"]] = relationship(
        "AttendanceSession", back_populates="offering"
    )

    __table_args__ = (
        # One offering per (subject, section, year). Prevents accidentally
        # creating two competing offerings for the same class, which would
        # split a roster in half.
        UniqueConstraint(
            "subject_id",
            "section",
            "academic_year",
            name="uq_offering_subject_section_year",
        ),
        # Enforces the '2026-2027' shape. Uses SQL-standard LIKE rather than
        # PostgreSQL's `~` regex operator so the constraint is portable and
        # testable on other dialects. In LIKE, `_` matches exactly one
        # character, so this pattern means: 4 chars, hyphen, 4 chars.
        CheckConstraint(
            "academic_year LIKE '____-____'",
            name="ck_offering_year_format",
        ),
        # THE authorisation index. "Which offerings does this faculty member
        # teach?" runs on every single faculty request. It must be fast.
        Index(
            "idx_offerings_faculty",
            "faculty_id",
            postgresql_where=text("is_active = true"),
        ),
        Index("idx_offerings_subject_year", "subject_id", "academic_year"),
        {
            "comment": (
                "One teaching instance: subject + faculty + section + year. "
                "Attendance attaches HERE, never directly to a subject."
            )
        },
    )

    def __repr__(self) -> str:
        return (
            f"<SubjectOffering id={self.id} subject_id={self.subject_id} "
            f"section={self.section!r} year={self.academic_year!r}>"
        )


class Enrollment(Base, SoftDeleteMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    A student takes many subjects. A subject has many students. Relational
    databases cannot express many-to-many directly -- there is no column type
    that holds "a list of students". The universal solution is a junction
    table holding both foreign keys.

    WHY IT IS A REAL ENTITY, NOT JUST A LINK
    ----------------------------------------
    This table carries its own attributes, which is what elevates it from
    plumbing to a genuine entity:

      enrolled_at -- The student who joins in week 6 must NOT be counted
                     absent for weeks 1-5. Attendance percentage is computed
                     only over sessions on or after this date. Without this
                     column, mid-semester joiners appear to have terrible
                     attendance through no fault of their own.

      is_active   -- A student who drops the subject stops accruing absences
                     from that point, but their existing records survive.

    THIS TABLE ALSO DEFINES THE RECOGNITION SEARCH SPACE
    ----------------------------------------------------
    When marking attendance for an offering, the face matcher searches only
    students enrolled in THAT offering -- roughly 40-60 people rather than the
    full 1,000. That is both a ~20x speedup and, more importantly, a large
    reduction in false-positive probability: a lookalike from another
    department simply is not a candidate for a wrong match.
    """

    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )

    offering_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("subject_offerings.id", ondelete="CASCADE"),
        nullable=False,
    )

    enrolled_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        comment=(
            "Attendance percentage counts only sessions on or after this "
            "date, so mid-semester joiners are not unfairly penalised."
        ),
    )

    student: Mapped["Student"] = relationship("Student", back_populates="enrollments")
    offering: Mapped["SubjectOffering"] = relationship(
        "SubjectOffering", back_populates="enrollments"
    )

    __table_args__ = (
        # A student cannot enrol in the same offering twice. This is the
        # defining constraint of a junction table -- without it the table is
        # just a bag of duplicated pairs.
        UniqueConstraint(
            "student_id", "offering_id", name="uq_enrollment_student_offering"
        ),
        # "Give me the roster for this offering" -- runs on every session open
        # and every recognition request (to build the search space).
        Index(
            "idx_enroll_offering",
            "offering_id",
            postgresql_where=text("is_active = true"),
        ),
        # "Which subjects is this student taking?" -- the student dashboard.
        Index(
            "idx_enroll_student",
            "student_id",
            postgresql_where=text("is_active = true"),
        ),
        {"comment": "Junction: resolves many-to-many students <-> offerings"},
    )

    def __repr__(self) -> str:
        return (
            f"<Enrollment student_id={self.student_id} "
            f"offering_id={self.offering_id}>"
        )
