"""
Time-series and distribution analytics.

Extends AnalyticsService with the aggregations the charts need. Kept in a
separate module because the percentage logic in analytics_service.py is
business-critical and heavily tested -- these are read-only derivations on top
of it, and mixing them would make that file harder to review.

A NOTE ON WHERE THE AGGREGATION HAPPENS
---------------------------------------
Grouping by month is naturally a database job: PostgreSQL has date_trunc, and
pushing the work down is usually right.

We aggregate in Python instead, deliberately:

  1. PORTABILITY. date_trunc is PostgreSQL-specific; SQLite (used by the test
     suite) has strftime with different semantics. Writing the grouping once in
     Python means one implementation and one set of tests, rather than two
     dialect-specific code paths where only one is ever exercised.

  2. SCALE. A college offering holds perhaps 60 sessions a semester and 40
     students -- 2,400 records. Python groups that in single-digit
     milliseconds. Pushing it to SQL would optimise something that is not slow.

THIS IS A TRADE-OFF, NOT A FREE WIN. At 100,000+ records per offering the
right move is date_trunc plus a GROUP BY, and possibly a materialised view.
Recording the reasoning here means the next person knows which way to go and
why, instead of guessing.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.models.academic import Enrollment, Subject, SubjectOffering
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.enums import AttendanceStatus, SessionStatus
from app.models.people import Faculty, Student
from app.services.analytics_service import AnalyticsService
from app.services.attendance_calculator import calculate_offering_attendance_batch

logger = logging.getLogger(__name__)

MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


class ChartAnalyticsService:
    """Aggregations shaped for charting."""

    def __init__(self, db: Session):
        self.db = db
        self.core = AnalyticsService(db)

    # ==================================================================
    # 1. DAILY SERIES
    # ==================================================================

    def daily_series(
        self, offering_id: int, *, days: int = 30
    ) -> list[dict]:
        """
        One point per session date: how many attended, and the rate.

        WHY SESSION DATE AND NOT CALENDAR DATE
        --------------------------------------
        A calendar series would emit a point for every day in the window,
        including weekends, holidays and days this subject simply is not
        taught. Those days are not zero-attendance -- they are NO DATA, and
        plotting them as zeros draws a sawtooth that looks like collapsing
        attendance when nothing happened at all.

        Only dates on which a session was actually CLOSED produce a point.
        Cancelled sessions are excluded for the same reason: the class did not
        happen, so there is nothing to report.
        """
        cutoff = date.today() - timedelta(days=days)

        sessions = self.db.execute(
            select(AttendanceSession)
            .where(
                AttendanceSession.offering_id == offering_id,
                AttendanceSession.status == SessionStatus.CLOSED.value,
                AttendanceSession.session_date >= cutoff,
            )
            .order_by(AttendanceSession.session_date)
        ).scalars().all()

        if not sessions:
            return []

        session_ids = [s.id for s in sessions]

        records = self.db.execute(
            select(AttendanceRecord.session_id, AttendanceRecord.status)
            .where(AttendanceRecord.session_id.in_(session_ids))
        ).all()

        by_session = defaultdict(lambda: defaultdict(int))
        for sid, status in records:
            by_session[sid][status] += 1

        series = []
        for s in sessions:
            counts = by_session[s.id]
            present = counts.get(AttendanceStatus.PRESENT.value, 0)
            late = counts.get(AttendanceStatus.LATE.value, 0)
            absent = counts.get(AttendanceStatus.ABSENT.value, 0)
            excused = counts.get(AttendanceStatus.EXCUSED.value, 0)

            attended = present + late
            # Excused students are removed from the denominator, exactly as in
            # the percentage formula. Keeping the two consistent matters --
            # a chart that disagrees with the number beside it destroys trust
            # in both.
            denominator = attended + absent

            series.append({
                "session_id": s.id,
                "date": s.session_date.isoformat(),
                "present": present,
                "late": late,
                "absent": absent,
                "excused": excused,
                "attended": attended,
                "total": denominator,
                "rate": round(attended / denominator * 100, 1) if denominator else None,
            })

        return series

    # ==================================================================
    # 2. MONTHLY SERIES
    # ==================================================================

    def monthly_series(
        self, offering_id: int, *, months: int = 6
    ) -> list[dict]:
        """
        One point per month: sessions held and the aggregate rate.

        WHY THE RATE IS RECOMPUTED FROM TOTALS, not averaged from daily rates
        --------------------------------------------------------------------
        Averaging the daily percentages would weight a session with 5 students
        present the same as one with 40. That is Simpson's paradox waiting to
        happen: a month can show a rising average while total attendance falls.

        Summing attended and summing the denominator, then dividing once, gives
        each STUDENT-SESSION equal weight -- which is what "attendance for the
        month" actually means. Same reasoning as the overall percentage in
        analytics_service.py.
        """
        daily = self.daily_series(offering_id, days=months * 31)
        if not daily:
            return []

        buckets = defaultdict(lambda: {
            "attended": 0, "total": 0, "sessions": 0,
            "present": 0, "late": 0, "absent": 0,
        })

        for point in daily:
            y, m, _ = point["date"].split("-")
            key = f"{y}-{m}"
            b = buckets[key]
            b["attended"] += point["attended"]
            b["total"] += point["total"]
            b["sessions"] += 1
            b["present"] += point["present"]
            b["late"] += point["late"]
            b["absent"] += point["absent"]

        series = []
        for key in sorted(buckets)[-months:]:
            b = buckets[key]
            year, month = key.split("-")
            series.append({
                "month": key,
                "label": f"{MONTH_NAMES[int(month) - 1]} {year[2:]}",
                "sessions": b["sessions"],
                "present": b["present"],
                "late": b["late"],
                "absent": b["absent"],
                "attended": b["attended"],
                "total": b["total"],
                # ONE division at the end, not an average of averages.
                "rate": round(b["attended"] / b["total"] * 100, 1) if b["total"] else None,
            })

        return series

    # ==================================================================
    # 3. PER-STUDENT PERCENTAGES
    # ==================================================================

    def student_percentages(self, offering_id: int) -> list[dict]:
        """
        Every enrolled student's percentage in one offering.

        Note this returns ALL students, not only defaulters. The defaulter
        endpoint answers "who is in trouble?"; this answers "how is the class
        distributed?", which needs the whole population -- a histogram built
        from defaulters alone would show only the left tail and mislead badly.
        """
        offering = self.db.get(SubjectOffering, offering_id)
        if offering is None:
            raise NotFoundError(f"Offering {offering_id} not found.")

        rows = self.db.execute(
            select(Student)
            .join(Enrollment, Enrollment.student_id == Student.id)
            .options(joinedload(Student.user))
            .where(
                Enrollment.offering_id == offering_id,
                Enrollment.is_active.is_(True),
                Student.is_active.is_(True),
            )
            .order_by(Student.roll_number)
        ).scalars().all()

        # PERFORMANCE FIX. This previously called
        # calculate_offering_attendance() once per student -- 122 queries for a
        # class of 40, measured. The batch version issues three queries
        # regardless of class size.
        #
        # The numbers are identical; tests/test_refactor.py asserts that on
        # every student, because two implementations of one formula drift the
        # moment either is edited alone.
        stats_by_student = calculate_offering_attendance_batch(self.db, offering_id)

        results = []
        for student in rows:
            stats = stats_by_student.get(student.id)
            if stats is None:
                continue  # enrolment deactivated between the two queries
            results.append({
                "student_id": student.id,
                "roll_number": student.roll_number,
                "student_name": student.user.full_name,
                **stats,
            })
        return results

    # ==================================================================
    # 4. SUBJECT PERCENTAGES
    # ==================================================================

    def subject_percentages(self, faculty_id: int) -> list[dict]:
        """
        Class average per offering, for one faculty member.

        Answers "which of my classes is struggling?" -- a comparison across
        subjects, which is what a bar chart is genuinely good at.
        """
        offerings = self.db.execute(
            select(SubjectOffering, Subject)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .where(
                SubjectOffering.faculty_id == faculty_id,
                SubjectOffering.is_active.is_(True),
            )
            .order_by(Subject.code)
        ).all()

        results = []
        for offering, subject in offerings:
            students = self.student_percentages(offering.id)

            attended = sum(s["attended"] for s in students)
            denominator = sum(s["denominator"] for s in students)
            below = sum(1 for s in students if s["is_below_threshold"])

            results.append({
                "offering_id": offering.id,
                "subject_code": subject.code,
                "subject_name": subject.name,
                "section": offering.section,
                "enrolled_count": len(students),
                "attended": attended,
                "denominator": denominator,
                "rate": round(attended / denominator * 100, 1) if denominator else None,
                "below_threshold_count": below,
            })

        return results

    # ==================================================================
    # 5. TOP ABSENTEES
    # ==================================================================

    def top_absentees(
        self, offering_id: int, *, limit: int = 10
    ) -> list[dict]:
        """
        The students who have missed the most.

        RANKED BY ABSENCE COUNT, NOT BY PERCENTAGE -- and the distinction is
        the point of this endpoint.

        A student who attended 1 of 2 classes sits at 50%, which looks alarming.
        A student who attended 30 of 45 sits at 67%, which looks better -- but
        they have missed FIFTEEN classes against the first student's one.

        Percentage answers "who is at risk of being barred from the exam?" and
        the defaulter endpoint already does that. Absence count answers "who is
        actually not turning up?", which is the pastoral question, and it is a
        different list. Ranking this one by percentage would just duplicate the
        defaulter view under another name.
        """
        students = self.student_percentages(offering_id)

        ranked = [s for s in students if s["absent"] > 0]
        # Sort by absences descending; break ties with the lower percentage
        # first, so between two students who missed 8 classes the one in more
        # trouble appears higher.
        ranked.sort(key=lambda s: (-s["absent"], s["percentage"] if s["percentage"] is not None else 100))

        return [
            {
                "student_id": s["student_id"],
                "roll_number": s["roll_number"],
                "student_name": s["student_name"],
                "absent": s["absent"],
                "attended": s["attended"],
                "total_sessions": s["total_sessions"],
                "percentage": s["percentage"],
                "is_below_threshold": s["is_below_threshold"],
            }
            for s in ranked[:limit]
        ]

    # ==================================================================
    # DASHBOARD BUNDLE
    # ==================================================================

    def offering_dashboard(self, offering_id: int) -> dict:
        """
        Everything the analytics page needs, in ONE request.

        WHY BUNDLE RATHER THAN FIVE ENDPOINTS
        -------------------------------------
        The page renders five charts that must agree with each other. Five
        separate requests could interleave with a session closing between them,
        so the daily chart would include a session the student percentages do
        not -- and the numbers on screen would silently contradict each other.

        One request means one consistent snapshot. It also removes four network
        round trips, which on a college wifi is the difference between the page
        appearing and the page loading.
        """
        students = self.student_percentages(offering_id)
        rates = [s["percentage"] for s in students if s["percentage"] is not None]

        return {
            "offering_id": offering_id,
            "daily": self.daily_series(offering_id, days=30),
            "monthly": self.monthly_series(offering_id, months=6),
            "students": students,
            "top_absentees": self.top_absentees(offering_id, limit=10),
            "summary": {
                "enrolled": len(students),
                "class_average": round(sum(rates) / len(rates), 1) if rates else None,
                "below_threshold": sum(1 for s in students if s["is_below_threshold"]),
                "threshold": settings.attendance_warning_threshold,
            },
        }
