"""
REFACTOR EQUIVALENCE TESTS.

A performance refactor is only safe if it produces IDENTICAL output. These
tests pin the batch calculator against the original single-student calculation
on every student, in every scenario the original was tested for.

WHY THIS FILE MATTERS MORE THAN THE PERFORMANCE NUMBER
------------------------------------------------------
There are now two implementations of one formula. That is a real risk: the
moment someone edits `calculate_offering_attendance` without touching
`calculate_offering_attendance_batch`, the dashboard silently disagrees with
the student's own attendance page — and neither looks broken.

These tests are the tripwire. If they ever fail, the two have drifted.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.analytics_service import AnalyticsService
from app.services.attendance_calculator import calculate_offering_attendance_batch


class TestBatchMatchesSingle:
    """Every scenario the single-student version was tested for."""

    def _assert_identical(self, db, offering, students):
        single = AnalyticsService(db)
        batch = calculate_offering_attendance_batch(db, offering.id)

        for student in students:
            expected = single.calculate_offering_attendance(student.id, offering.id)
            actual = batch[student.id]
            assert actual == expected, (
                f"drift for {student.roll_number}: "
                f"batch={actual} single={expected}"
            )

    def test_all_present(self, db, offering, students, make_session_row):
        for i in range(4):
            make_session_row(
                date(2026, 3, i + 1), {s.id: "present" for s in students}
            )
        self._assert_identical(db, offering, students)

    def test_mixed_statuses(self, db, offering, students, make_session_row):
        statuses = ["present", "absent", "late", "excused", "present"]
        for i in range(5):
            make_session_row(
                date(2026, 3, i + 1),
                {s.id: statuses[(i + n) % 5] for n, s in enumerate(students)},
            )
        self._assert_identical(db, offering, students)

    def test_cancelled_and_open_sessions_ignored(
        self, db, offering, students, make_session_row
    ):
        make_session_row(date(2026, 3, 1), {s.id: "present" for s in students})
        make_session_row(
            date(2026, 3, 2), {s.id: "absent" for s in students}, status="cancelled"
        )
        make_session_row(
            date(2026, 3, 3), {s.id: "absent" for s in students}, status="open"
        )
        self._assert_identical(db, offering, students)

    def test_partially_marked_sessions(self, db, offering, students, make_session_row):
        """Some students have no record at all for some sessions."""
        make_session_row(date(2026, 3, 1), {students[0].id: "present"})
        make_session_row(date(2026, 3, 2), {students[1].id: "absent"})
        self._assert_identical(db, offering, students)

    def test_no_sessions_at_all(self, db, offering, students):
        self._assert_identical(db, offering, students)

    def test_late_joiner_enrolment_boundary(
        self, db, offering, students, make_student, make_session_row
    ):
        """
        The hardest case: eligible sessions differ PER STUDENT, which is why
        this could not be one flat GROUP BY.
        """
        joiner = make_student(
            "R900",
            enrolled_in=offering,
            enrolled_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        for day in (1, 5, 10, 15):
            make_session_row(
                date(2026, 3, day),
                {s.id: "present" for s in [*students, joiner]},
            )
        self._assert_identical(db, offering, [*students, joiner])

    def test_all_excused(self, db, offering, students, make_session_row):
        """Denominator zero -- percentage must be None in both paths."""
        make_session_row(date(2026, 3, 1), {s.id: "excused" for s in students})
        self._assert_identical(db, offering, students)
        assert calculate_offering_attendance_batch(db, offering.id)[
            students[0].id
        ]["percentage"] is None

    def test_exact_threshold_boundary(self, db, offering, students, make_session_row):
        for i in range(3):
            make_session_row(date(2026, 3, i + 1), {students[0].id: "present"})
        make_session_row(date(2026, 3, 4), {students[0].id: "absent"})

        stats = calculate_offering_attendance_batch(db, offering.id)[students[0].id]
        assert stats["percentage"] == 75.0
        assert stats["is_below_threshold"] is False
        self._assert_identical(db, offering, students)


class TestBatchQueryCount:
    """
    The point of the refactor: query count must be CONSTANT in student count.

    A performance test that asserts a time budget is flaky -- it fails on a
    loaded CI machine for no real reason. Asserting the number of QUERIES is
    deterministic and tests the actual property we care about.
    """

    def _count_queries(self, engine, fn):
        from sqlalchemy import event

        counter = {"n": 0}

        def before(conn, cursor, statement, params, context, many):
            counter["n"] += 1

        event.listen(engine, "before_cursor_execute", before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return counter["n"]

    def test_query_count_does_not_grow_with_students(
        self, db, engine, offering, make_student, make_session_row
    ):
        small = [make_student(f"A{i:03d}", enrolled_in=offering) for i in range(3)]
        make_session_row(date(2026, 3, 1), {s.id: "present" for s in small})
        few = self._count_queries(
            engine, lambda: calculate_offering_attendance_batch(db, offering.id)
        )

        many = [make_student(f"B{i:03d}", enrolled_in=offering) for i in range(30)]
        make_session_row(date(2026, 3, 2), {s.id: "present" for s in many})
        lots = self._count_queries(
            engine, lambda: calculate_offering_attendance_batch(db, offering.id)
        )

        assert few == lots, (
            f"query count grew from {few} to {lots} when students went 3 -> 33; "
            "the N+1 has come back"
        )
        assert lots <= 4, f"expected a constant handful of queries, got {lots}"

    def test_dashboard_query_count_is_bounded(
        self, db, engine, offering, students, make_session_row
    ):
        from app.services.chart_analytics_service import ChartAnalyticsService

        for i in range(10):
            make_session_row(
                date(2026, 3, i + 1), {s.id: "present" for s in students}
            )

        n = self._count_queries(
            engine, lambda: ChartAnalyticsService(db).offering_dashboard(offering.id)
        )
        # Was 247 before the refactor, for exactly this shape of data.
        assert n < 30, f"dashboard issued {n} queries; the N+1 has come back"
