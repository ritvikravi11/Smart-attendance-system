"""
Attendance analytics: percentages, subject-wise and faculty-wise aggregation.

THE FORMULA (section 6 of the business logic doc) -- exact, because ambiguity
here produces disputes:

                    (sessions PRESENT) + (sessions LATE)
    percentage = ─────────────────────────────────────────────── x 100
                  (eligible closed sessions) - (excused sessions)

Eligible sessions:
    status = 'closed'              open ones are in progress; cancelled never happened
    session_date >= enrolment date  mid-semester joiners are not penalised

Excused absences are REMOVED FROM THE DENOMINATOR, not counted as attended.
A student on medical leave neither gains nor loses.

When the denominator is zero the percentage is None -- not 0%, not 100%. No
sessions have occurred, so no percentage exists. Returning 0% would flag every
student as a defaulter on day one; returning 100% would be a lie.
"""

import logging
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.academic import Enrollment, Subject, SubjectOffering
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.enums import AttendanceStatus, RoleName, SessionStatus
from app.models.people import Faculty, Student
from app.models.user import User
from app.services.attendance_calculator import calculate_offering_attendance_batch

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Read-only aggregation over attendance records."""

    def __init__(self, db: Session):
        self.db = db

    # ==================================================================
    # THE CORE CALCULATION
    # ==================================================================

    def calculate_offering_attendance(
        self, student_id: int, offering_id: int
    ) -> dict:
        """
        Attendance for one student in one offering.

        Every subtlety of the formula lives here, so this is the one place to
        change if the institution's policy differs.
        """
        enrollment = self.db.execute(
            select(Enrollment).where(
                Enrollment.student_id == student_id,
                Enrollment.offering_id == offering_id,
                Enrollment.is_active.is_(True),
            )
        ).scalar_one_or_none()

        if enrollment is None:
            raise NotFoundError("Student is not enrolled in this offering.")

        enrolled_on = enrollment.enrolled_at
        if enrolled_on.tzinfo is None:
            enrolled_on = enrolled_on.replace(tzinfo=timezone.utc)
        enrolled_date = enrolled_on.date()

        # ---- ELIGIBLE SESSIONS ----
        # status = 'closed' excludes both OPEN (still running) and CANCELLED
        # (never happened). session_date >= enrolment date protects joiners.
        eligible_session_ids = [
            row[0]
            for row in self.db.execute(
                select(AttendanceSession.id).where(
                    AttendanceSession.offering_id == offering_id,
                    AttendanceSession.status == SessionStatus.CLOSED.value,
                    AttendanceSession.session_date >= enrolled_date,
                )
            ).all()
        ]
        total_eligible = len(eligible_session_ids)

        if total_eligible == 0:
            # No sessions yet -> no percentage exists. None, not zero.
            return {
                "total_sessions": 0, "present": 0, "late": 0, "absent": 0,
                "excused": 0, "attended": 0, "denominator": 0,
                "percentage": None, "is_below_threshold": False,
            }

        # ---- STATUS COUNTS ----
        rows = self.db.execute(
            select(AttendanceRecord.status, func.count())
            .where(
                AttendanceRecord.student_id == student_id,
                AttendanceRecord.session_id.in_(eligible_session_ids),
            )
            .group_by(AttendanceRecord.status)
        ).all()
        counts = {status: count for status, count in rows}

        present = counts.get(AttendanceStatus.PRESENT.value, 0)
        late = counts.get(AttendanceStatus.LATE.value, 0)
        absent = counts.get(AttendanceStatus.ABSENT.value, 0)
        excused = counts.get(AttendanceStatus.EXCUSED.value, 0)

        # ---- THE FORMULA ----
        attended = present + late          # LATE counts as attended
        denominator = total_eligible - excused   # excused removed entirely

        if denominator <= 0:
            # Every eligible session was excused. Genuinely undefined.
            percentage = None
            below = False
        else:
            percentage = round(attended / denominator * 100, 2)
            below = percentage < settings.attendance_warning_threshold

        return {
            "total_sessions": total_eligible,
            "present": present,
            "late": late,
            "absent": absent,
            "excused": excused,
            "attended": attended,
            "denominator": denominator,
            "percentage": percentage,
            "is_below_threshold": below,
        }

    # ==================================================================
    # SUBJECT-WISE (student dashboard)
    # ==================================================================

    def student_summary(self, student_id: int, requesting_user: User) -> dict:
        """
        Full subject-wise breakdown for one student.

        AUTHORISATION: a student may view ONLY their own data. This is
        horizontal privilege escalation defence (NFR-2.8) -- the check that
        stops student A reading student B's attendance by changing an ID in
        the URL. Frequently missed, and trivially exploitable when it is.
        """
        student = self.db.execute(
            select(Student).options(joinedload(Student.user))
            .where(Student.id == student_id)
        ).scalar_one_or_none()

        if student is None:
            raise NotFoundError(f"Student {student_id} not found.")

        role = requesting_user.role.name
        if role == RoleName.STUDENT.value:
            if student.user_id != requesting_user.id:
                raise PermissionDeniedError("You may only view your own attendance.")
        elif role not in (RoleName.ADMIN.value, RoleName.FACULTY.value):
            raise PermissionDeniedError("Insufficient permissions.")

        offerings = self.db.execute(
            select(SubjectOffering, Subject, Faculty)
            .join(Enrollment, Enrollment.offering_id == SubjectOffering.id)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .join(Faculty, Faculty.id == SubjectOffering.faculty_id)
            .options(joinedload(Faculty.user))
            .where(
                Enrollment.student_id == student_id,
                Enrollment.is_active.is_(True),
                SubjectOffering.is_active.is_(True),
            )
            .order_by(Subject.code)
        ).all()

        subjects = []
        total_attended = 0
        total_denominator = 0

        for offering, subject, faculty in offerings:
            stats = self.calculate_offering_attendance(student_id, offering.id)

            subjects.append({
                "offering_id": offering.id,
                "subject_code": subject.code,
                "subject_name": subject.name,
                "section": offering.section,
                "faculty_name": faculty.user.full_name,
                **stats,
            })

            total_attended += stats["attended"]
            total_denominator += stats["denominator"]

        # OVERALL is computed by summing numerators and denominators across
        # subjects -- NOT by averaging the per-subject percentages.
        #
        # Averaging percentages weights a 5-session subject equally with a
        # 40-session one, which is wrong. Summing gives each SESSION equal
        # weight, which is what "overall attendance" actually means.
        overall = (
            round(total_attended / total_denominator * 100, 2)
            if total_denominator > 0
            else None
        )

        return {
            "student_id": student.id,
            "roll_number": student.roll_number,
            "student_name": student.user.full_name,
            "overall_percentage": overall,
            "overall_attended": total_attended,
            "overall_denominator": total_denominator,
            "is_defaulter": (
                overall is not None
                and overall < settings.attendance_warning_threshold
            ),
            "subjects": subjects,
        }

    # ==================================================================
    # FACULTY-WISE (faculty dashboard)
    # ==================================================================

    def faculty_summary(self, faculty_id: int, requesting_user: User) -> dict:
        """
        Aggregate across every offering a faculty member teaches.

        Answers "how are my classes doing?" -- which offering has the worst
        attendance, how many students are at risk, how many sessions have
        actually been conducted.
        """
        faculty = self.db.execute(
            select(Faculty).options(joinedload(Faculty.user))
            .where(Faculty.id == faculty_id)
        ).scalar_one_or_none()

        if faculty is None:
            raise NotFoundError(f"Faculty {faculty_id} not found.")

        role = requesting_user.role.name
        if role == RoleName.FACULTY.value and faculty.user_id != requesting_user.id:
            raise PermissionDeniedError("You may only view your own teaching summary.")
        if role == RoleName.STUDENT.value:
            raise PermissionDeniedError("Insufficient permissions.")

        offerings = self.db.execute(
            select(SubjectOffering, Subject)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .where(
                SubjectOffering.faculty_id == faculty_id,
                SubjectOffering.is_active.is_(True),
            )
            .order_by(Subject.code)
        ).all()

        summaries = []
        total_sessions = 0
        total_students = 0

        for offering, subject in offerings:
            student_ids = [
                row[0]
                for row in self.db.execute(
                    select(Enrollment.student_id).where(
                        Enrollment.offering_id == offering.id,
                        Enrollment.is_active.is_(True),
                    )
                ).all()
            ]

            session_rows = self.db.execute(
                select(AttendanceSession.status, func.count())
                .where(AttendanceSession.offering_id == offering.id)
                .group_by(AttendanceSession.status)
            ).all()
            by_status = {s: c for s, c in session_rows}

            closed = by_status.get(SessionStatus.CLOSED.value, 0)
            cancelled = by_status.get(SessionStatus.CANCELLED.value, 0)
            all_sessions = sum(by_status.values())

            # Class average uses the same sum-numerators approach as the
            # student overall, for the same reason.
            # PERFORMANCE FIX: was one call per student inside a loop over
            # offerings -- 368 queries for three offerings of 40 students.
            batch = calculate_offering_attendance_batch(self.db, offering.id)

            attended_sum = 0
            denominator_sum = 0
            defaulters = 0

            for sid in student_ids:
                stats = batch.get(sid)
                if stats is None:
                    continue
                attended_sum += stats["attended"]
                denominator_sum += stats["denominator"]
                if stats["is_below_threshold"]:
                    defaulters += 1

            average = (
                round(attended_sum / denominator_sum * 100, 2)
                if denominator_sum > 0
                else None
            )

            summaries.append({
                "offering_id": offering.id,
                "subject_code": subject.code,
                "subject_name": subject.name,
                "section": offering.section,
                "academic_year": offering.academic_year,
                "enrolled_count": len(student_ids),
                "total_sessions": all_sessions,
                "closed_sessions": closed,
                "cancelled_sessions": cancelled,
                "average_percentage": average,
                "defaulter_count": defaulters,
            })

            total_sessions += closed
            total_students += len(student_ids)

        return {
            "faculty_id": faculty.id,
            "faculty_name": faculty.user.full_name,
            "employee_code": faculty.employee_code,
            "offerings": summaries,
            "total_sessions_conducted": total_sessions,
            "total_students_taught": total_students,
        }

    # ==================================================================
    # DEFAULTERS
    # ==================================================================

    def get_defaulters(
        self, offering_id: int, threshold: float | None = None
    ) -> list[dict]:
        """
        Students below the attendance threshold in one offering.

        The `shortfall_sessions` figure answers the question students actually
        ask: "how many classes in a row must I attend to get back above 75%?"

        Solving for n where (attended + n) / (denominator + n) >= target:

            attended + n >= target * (denominator + n)
            n * (1 - target) >= target * denominator - attended
            n >= (target * denominator - attended) / (1 - target)

        Far more useful than a bare percentage, and it takes three lines.
        """
        target = (threshold or settings.attendance_warning_threshold) / 100.0

        student_ids = [
            row[0]
            for row in self.db.execute(
                select(Enrollment.student_id).where(
                    Enrollment.offering_id == offering_id,
                    Enrollment.is_active.is_(True),
                )
            ).all()
        ]

        batch = calculate_offering_attendance_batch(self.db, offering_id)

        defaulters = []
        for sid in student_ids:
            stats = batch.get(sid)
            if stats is None:
                continue
            if stats["percentage"] is None or not stats["is_below_threshold"]:
                continue

            attended = stats["attended"]
            denominator = stats["denominator"]

            if target >= 1.0:
                shortfall = -1  # unreachable target
            else:
                needed = (target * denominator - attended) / (1 - target)
                shortfall = max(0, int(needed) + (1 if needed % 1 > 0 else 0))

            student = self.db.execute(
                select(Student).options(joinedload(Student.user))
                .where(Student.id == sid)
            ).scalar_one()

            defaulters.append({
                "student_id": sid,
                "roll_number": student.roll_number,
                "student_name": student.user.full_name,
                "percentage": stats["percentage"],
                "attended": attended,
                "denominator": denominator,
                "shortfall_sessions": shortfall,
            })

        defaulters.sort(key=lambda d: d["percentage"])
        return defaulters
