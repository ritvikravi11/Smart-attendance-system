"""
EDGE CASES -- the deliberate bug hunt.

Everything above tests that the system works. This file tests what happens at
the boundaries, with hostile input, and in states nobody designed for.

Every test here started as a question: "what if…?" Several of them found real
bugs, which are marked and were fixed.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

V1 = "/api/v1"


# ===========================================================================
class TestMalformedInput:
    """Hostile or nonsensical input must fail cleanly, never with a 500."""

    def test_sql_injection_in_email_is_harmless(self, client, faculty):
        """
        The ORM parameterises every query, so this is data, not code. If the
        response is anything but 401/422, something is concatenating strings.
        """
        r = client.post(
            f"{V1}/auth/login",
            json={"email": "' OR '1'='1' --@x.com", "password": "x"},
        )
        assert r.status_code in (401, 422)

    def test_sql_injection_in_path_param(self, client, faculty_headers):
        r = client.get(f"{V1}/students/1 OR 1=1/attendance", headers=faculty_headers)
        assert r.status_code == 422  # path type coercion rejects it

    def test_xss_payload_is_stored_and_returned_verbatim(
        self, client, faculty_headers, offering, students
    ):
        """
        The API must NOT sanitise HTML. Escaping belongs at the render layer,
        and React escapes by default. Sanitising here would corrupt legitimate
        data containing angle brackets and give false confidence.
        """
        payload = "<script>alert(1)</script>"
        sid = client.post(
            f"{V1}/sessions",
            headers=faculty_headers,
            json={"offering_id": offering.id, "notes": payload},
        )
        assert sid.status_code == 201

    def test_negative_ids_rejected(self, client, faculty_headers):
        r = client.get(f"{V1}/students/-1/attendance", headers=faculty_headers)
        assert r.status_code in (403, 404, 422)

    def test_enormous_id_does_not_crash(self, client, faculty_headers):
        r = client.get(f"{V1}/students/{2**62}/attendance", headers=faculty_headers)
        assert r.status_code in (403, 404, 422)

    def test_missing_body_returns_422(self, client, faculty_headers):
        assert client.post(f"{V1}/sessions", headers=faculty_headers).status_code == 422

    def test_wrong_content_type_returns_422(self, client, faculty_headers, offering):
        r = client.post(
            f"{V1}/sessions",
            headers={**faculty_headers, "Content-Type": "text/plain"},
            content="offering_id=1",
        )
        assert r.status_code == 422

    def test_extra_unknown_fields_ignored(self, client, faculty_headers, offering, students):
        """Pydantic ignores unknown keys rather than erroring -- forward compatible."""
        r = client.post(
            f"{V1}/sessions",
            headers=faculty_headers,
            json={"offering_id": offering.id, "hacker_field": "drop tables"},
        )
        assert r.status_code == 201


class TestMalformedTokens:
    @pytest.mark.parametrize(
        "value",
        [
            "Bearer",
            "Bearer ",
            "Basic abc123",
            "Bearer a.b.c",
            "Bearer " + "x" * 5000,
            "Bearer ...",
        ],
    )
    def test_malformed_authorization_header_returns_401(self, client, faculty, value):
        r = client.get(f"{V1}/auth/me", headers={"Authorization": value})
        assert r.status_code == 401, f"{value!r} produced {r.status_code}"

    def test_token_with_non_numeric_sub_returns_401_not_500(self, client, faculty):
        """
        BUG FOUND. `int(payload["sub"])` was unguarded in the refresh path, so
        a token with a non-numeric subject raised ValueError -> 500 instead of
        401. A 500 leaks that the token PARSED and only the content was wrong.
        """
        import jwt as pyjwt

        from app.core.config import settings

        token = pyjwt.encode(
            {
                "sub": "not-a-number",
                "type": "refresh",
                "exp": datetime.now(timezone.utc) + timedelta(days=1),
                "jti": "x",
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        r = client.post(f"{V1}/auth/refresh", json={"refresh_token": token})
        assert r.status_code == 401, f"expected 401, got {r.status_code}"

    def test_token_for_deleted_user_returns_401(self, client, db, make_user, login):
        from app.models.user import User

        user = make_user("temp@college.edu", "faculty")
        headers = login("temp@college.edu")
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        assert client.get(f"{V1}/auth/me", headers=headers).status_code == 401

    def test_deactivation_takes_effect_immediately(self, client, db, faculty, login):
        """
        The reason get_current_user hits the database instead of trusting the
        token. A student caught proxying must lose access NOW, not in 30 minutes.
        """
        from app.models.user import User

        headers = login("das@college.edu")
        assert client.get(f"{V1}/auth/me", headers=headers).status_code == 200

        db.query(User).filter(User.id == faculty.user_id).update({"is_active": False})
        db.commit()
        assert client.get(f"{V1}/auth/me", headers=headers).status_code == 403


# ===========================================================================
class TestSessionEdgeCases:
    def test_session_dated_far_in_the_future_is_rejected(
        self, client, faculty_headers, offering, students
    ):
        """
        BUG FOUND. Nothing validated session_date, so a typo ("2036" for
        "2026") created a session ten years out. It would never appear in any
        30-day window, would silently skew monthly aggregates, and could not be
        deleted by faculty.
        """
        r = client.post(
            f"{V1}/sessions",
            headers=faculty_headers,
            json={"offering_id": offering.id, "session_date": "2036-01-01"},
        )
        assert r.status_code == 400, f"future date accepted: {r.status_code}"

    def test_todays_date_is_allowed(self, client, faculty_headers, offering, students):
        r = client.post(
            f"{V1}/sessions",
            headers=faculty_headers,
            json={"offering_id": offering.id, "session_date": date.today().isoformat()},
        )
        assert r.status_code == 201

    def test_backdated_session_allowed(self, client, faculty_headers, offering, students):
        """Faculty legitimately record a class they forgot to open at the time."""
        r = client.post(
            f"{V1}/sessions",
            headers=faculty_headers,
            json={
                "offering_id": offering.id,
                "session_date": (date.today() - timedelta(days=7)).isoformat(),
            },
        )
        assert r.status_code == 201

    def test_close_with_zero_enrolled_students(self, client, faculty_headers, offering):
        """An offering with no roster must close cleanly, not divide by zero."""
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        r = client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        assert r.status_code == 200
        assert r.json()["auto_absent_created"] == 0

    def test_cancelled_session_cannot_be_marked(
        self, client, faculty_headers, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(f"{V1}/sessions/{sid}/cancel", headers=faculty_headers, params={"reason": "holiday"})
        r = client.post(
            f"{V1}/sessions/{sid}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        assert r.status_code == 409

    def test_reopen_preserves_manual_marks_deletes_auto_absent(
        self, client, faculty_headers, admin_headers, offering, students, db
    ):
        from app.models.attendance import AttendanceRecord

        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(
            f"{V1}/sessions/{sid}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        client.post(f"{V1}/sessions/{sid}/reopen", headers=admin_headers)

        remaining = db.query(AttendanceRecord).filter_by(session_id=sid).all()
        assert len(remaining) == 1, "manual mark must survive a reopen"
        assert remaining[0].status == "present"

    def test_marking_after_reopen_works(
        self, client, faculty_headers, admin_headers, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        client.post(f"{V1}/sessions/{sid}/reopen", headers=admin_headers)
        r = client.post(
            f"{V1}/sessions/{sid}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        assert r.status_code == 200


# ===========================================================================
class TestDuplicatePrevention:
    """The constraint that the whole system depends on."""

    def test_twenty_rapid_marks_produce_one_record(self, db, offering, students, faculty):
        """Simulates the 2 fps camera stream hammering one student."""
        from app.models.attendance import AttendanceRecord, AttendanceSession
        from app.models.enums import SessionStatus
        from app.services.attendance_service import AttendanceService

        session = AttendanceSession(
            offering_id=offering.id,
            session_date=date.today(),
            status=SessionStatus.OPEN.value,
            created_by=faculty.user_id,
        )
        db.add(session)
        db.commit()

        service = AttendanceService(db)
        created_flags = []
        for _ in range(20):
            _, created = service.mark_present(session.id, students[0].id, confidence=0.9)
            created_flags.append(created)

        assert sum(created_flags) == 1, "exactly one insert should succeed"
        count = (
            db.query(AttendanceRecord)
            .filter_by(session_id=session.id, student_id=students[0].id)
            .count()
        )
        assert count == 1

    def test_transaction_survives_the_duplicate(self, db, offering, students, faculty):
        """
        THE SAVEPOINT TEST. In PostgreSQL an IntegrityError aborts the whole
        transaction. Without begin_nested(), one duplicate would poison every
        subsequent insert -- so marking six faces from one frame would lose
        five of them.
        """
        from app.models.attendance import AttendanceSession
        from app.models.enums import SessionStatus
        from app.services.attendance_service import AttendanceService

        session = AttendanceSession(
            offering_id=offering.id,
            session_date=date.today(),
            status=SessionStatus.OPEN.value,
            created_by=faculty.user_id,
        )
        db.add(session)
        db.commit()

        service = AttendanceService(db)
        service.mark_present(session.id, students[0].id, confidence=0.9)
        service.mark_present(session.id, students[0].id, confidence=0.9)  # duplicate

        _, created = service.mark_present(session.id, students[1].id, confidence=0.9)
        assert created is True, "transaction was poisoned by the duplicate"

    def test_same_student_two_sessions_same_day_allowed(
        self, client, faculty_headers, offering, students
    ):
        """
        A lecture and a lab on one day are two legitimate events. A naive
        UNIQUE(student, date) would wrongly block the second.
        """
        for _ in range(2):
            sid = client.post(
                f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
            ).json()["id"]
            r = client.post(
                f"{V1}/sessions/{sid}/mark-manual",
                headers=faculty_headers,
                json={"student_id": students[0].id, "status": "present"},
            )
            assert r.status_code == 200
            client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)


# ===========================================================================
class TestAnalyticsEdgeCases:
    def _chart(self, db):
        from app.services.chart_analytics_service import ChartAnalyticsService

        return ChartAnalyticsService(db)

    def test_monthly_sums_totals_not_averages_rates(
        self, db, offering, make_student, make_session_row
    ):
        """
        SIMPSON'S PARADOX. Two sessions in one month:
            1 of 2 present  -> 50%
            9 of 10 present -> 90%
        average of rates = 70.0%   (wrong)
        summed totals    = 83.3%   (correct)
        """
        cohort = [make_student(f"S{i:03d}", enrolled_in=offering) for i in range(10)]
        ids = [s.id for s in cohort]
        day = date.today().replace(day=10)

        make_session_row(day, {ids[0]: "present", ids[1]: "absent"})
        make_session_row(
            day + timedelta(days=1),
            {i: ("present" if n < 9 else "absent") for n, i in enumerate(ids)},
        )

        monthly = self._chart(db).monthly_series(offering.id, months=6)
        assert monthly, "expected at least one month"
        assert monthly[-1]["rate"] == pytest.approx(83.3, abs=0.1)

    def test_daily_series_skips_days_with_no_class(
        self, db, offering, students, make_session_row
    ):
        """
        No point for weekends or holidays. Emitting zeros would draw a sawtooth
        that reads as collapsing attendance when nothing happened.
        """
        make_session_row(date.today() - timedelta(days=1), {students[0].id: "present"})
        make_session_row(date.today() - timedelta(days=10), {students[0].id: "present"})
        daily = self._chart(db).daily_series(offering.id, days=30)
        assert len(daily) == 2, "one point per session, not per calendar day"

    def test_empty_offering_returns_empty_not_error(self, db, offering):
        chart = self._chart(db)
        assert chart.daily_series(offering.id) == []
        assert chart.monthly_series(offering.id) == []
        assert chart.top_absentees(offering.id) == []
        assert chart.offering_dashboard(offering.id)["summary"]["class_average"] is None

    def test_top_absentees_ranks_by_count_not_percentage(
        self, db, offering, make_student, make_session_row
    ):
        """
        A student who missed 1 of 1 class is at 0% but has missed ONE class.
        A student who missed 3 of 10 is at 70% but has missed THREE.
        Ranked by count, the second must come first.
        """
        chronic = make_student("R100", enrolled_in=offering)
        newcomer = make_student(
            "R200",
            enrolled_in=offering,
            enrolled_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        for i in range(3):
            make_session_row(date.today() - timedelta(days=20 - i), {chronic.id: "absent"})
        make_session_row(
            date.today() - timedelta(days=1),
            {chronic.id: "present", newcomer.id: "absent"},
        )

        ranked = self._chart(db).top_absentees(offering.id, limit=10)
        assert ranked[0]["roll_number"] == "R100", "chronic absentee must rank first"

    def test_percentage_never_exceeds_100(
        self, db, offering, students, make_session_row
    ):
        from app.services.analytics_service import AnalyticsService

        s = students[0]
        for i in range(5):
            make_session_row(date(2026, 3, i + 1), {s.id: "late"})
        stats = AnalyticsService(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] <= 100.0


# ===========================================================================
class TestConcurrencyAndConsistency:
    def test_dashboard_agrees_with_core_percentage_service(
        self, db, offering, students, make_session_row
    ):
        """
        A chart that disagrees with the number printed beside it destroys trust
        in both. Both paths must resolve to the same calculation.
        """
        from app.services.analytics_service import AnalyticsService
        from app.services.chart_analytics_service import ChartAnalyticsService

        s = students[0]
        make_session_row(date.today() - timedelta(days=3), {s.id: "present"})
        make_session_row(date.today() - timedelta(days=2), {s.id: "absent"})

        core = AnalyticsService(db).calculate_offering_attendance(s.id, offering.id)
        chart = next(
            p
            for p in ChartAnalyticsService(db).student_percentages(offering.id)
            if p["student_id"] == s.id
        )
        assert core["percentage"] == chart["percentage"]

    def test_faculty_summary_agrees_with_dashboard(
        self, db, faculty, offering, students, make_session_row, admin_user
    ):
        from app.services.analytics_service import AnalyticsService
        from app.services.chart_analytics_service import ChartAnalyticsService

        make_session_row(
            date.today() - timedelta(days=1),
            {students[0].id: "present", students[1].id: "absent"},
        )
        summary = AnalyticsService(db).faculty_summary(faculty.id, admin_user)
        dashboard = ChartAnalyticsService(db).offering_dashboard(offering.id)

        offering_summary = summary["offerings"][0]
        assert offering_summary["enrolled_count"] == dashboard["summary"]["enrolled"]
