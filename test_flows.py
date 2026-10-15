"""
INTEGRATION TESTS -- complete workflows across every layer.

Unit tests prove the pieces work. API tests prove each endpoint works.
Integration tests prove they work TOGETHER, which is a different claim: it is
possible for every unit to pass while the composition is broken.

Each test here is a scenario a real person actually performs, start to finish.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

V1 = "/api/v1"


class TestFullAttendanceFlow:
    """The core journey: log in, teach a class, record who was there."""

    def test_complete_class_lifecycle(
        self, client, faculty, offering, students, login
    ):
        # ---- 1. Faculty signs in ----
        headers = login("das@college.edu")
        me = client.get(f"{V1}/auth/me", headers=headers).json()
        assert me["role"]["name"] == "faculty"

        # ---- 2. Opens a session ----
        session = client.post(
            f"{V1}/sessions",
            headers=headers,
            json={"offering_id": offering.id, "room": "A-204"},
        ).json()
        assert session["status"] == "open"
        assert session["enrolled_count"] == 5

        # ---- 3. Roster starts empty ----
        roster = client.get(f"{V1}/sessions/{session['id']}/roster", headers=headers).json()
        assert roster["marked_count"] == 0
        assert len(roster["entries"]) == 5

        # ---- 4. Marks three students ----
        for student in students[:3]:
            r = client.post(
                f"{V1}/sessions/{session['id']}/mark-manual",
                headers=headers,
                json={"student_id": student.id, "status": "present"},
            )
            assert r.status_code == 200

        roster = client.get(f"{V1}/sessions/{session['id']}/roster", headers=headers).json()
        assert roster["marked_count"] == 3

        # ---- 5. Closes; the remaining two become absent ----
        summary = client.post(f"{V1}/sessions/{session['id']}/close", headers=headers).json()
        assert summary["present_count"] == 3
        assert summary["auto_absent_created"] == 2

        # ---- 6. Attendance now shows up for a student ----
        report = client.get(
            f"{V1}/students/{students[0].id}/attendance", headers=headers
        ).json()
        assert report["overall_percentage"] == 100.0

        absentee = client.get(
            f"{V1}/students/{students[4].id}/attendance", headers=headers
        ).json()
        assert absentee["overall_percentage"] == 0.0

        # ---- 7. Analytics reflects the same class ----
        dashboard = client.get(
            f"{V1}/analytics/offerings/{offering.id}/dashboard", headers=headers
        ).json()
        assert len(dashboard["daily"]) == 1
        assert dashboard["daily"][0]["present"] == 3
        assert dashboard["daily"][0]["absent"] == 2
        assert dashboard["daily"][0]["rate"] == 60.0

    def test_correction_flow(self, client, faculty, offering, students, login, db):
        """
        Faculty marks someone present by mistake, then corrects it. The record
        must carry who changed it -- that trail is the false-positive metric.
        """
        from app.models.attendance import AttendanceRecord

        headers = login("das@college.edu")
        session = client.post(
            f"{V1}/sessions", headers=headers, json={"offering_id": offering.id}
        ).json()

        record = client.post(
            f"{V1}/sessions/{session['id']}/mark-manual",
            headers=headers,
            json={"student_id": students[0].id, "status": "present"},
        ).json()

        corrected = client.put(
            f"{V1}/attendance/{record['id']}",
            headers=headers,
            json={"status": "absent", "notes": "marked in error"},
        ).json()

        assert corrected["status"] == "absent"
        assert corrected["was_overridden"] is True

        stored = db.query(AttendanceRecord).filter_by(id=record["id"]).one()
        assert stored.overridden_by is not None
        assert stored.overridden_at is not None

    def test_percentage_across_multiple_sessions(
        self, client, faculty, offering, students, login
    ):
        """Four classes, one missed -> 75%, which is exactly on the line."""
        headers = login("das@college.edu")
        student = students[0]

        for i in range(4):
            session = client.post(
                f"{V1}/sessions",
                headers=headers,
                json={
                    "offering_id": offering.id,
                    "session_date": (date.today() - timedelta(days=4 - i)).isoformat(),
                },
            ).json()
            if i < 3:
                client.post(
                    f"{V1}/sessions/{session['id']}/mark-manual",
                    headers=headers,
                    json={"student_id": student.id, "status": "present"},
                )
            client.post(f"{V1}/sessions/{session['id']}/close", headers=headers)

        report = client.get(f"{V1}/students/{student.id}/attendance", headers=headers).json()
        assert report["overall_percentage"] == 75.0
        assert report["is_defaulter"] is False, "exactly 75% is safe, not flagged"


class TestStudentJourney:
    """What a student can and cannot do."""

    def test_student_sees_only_their_own_record(
        self, client, faculty, offering, students, login, make_session_row
    ):
        make_session_row(
            date.today() - timedelta(days=1),
            {students[0].id: "present", students[1].id: "absent"},
        )
        headers = login("r001@college.edu")

        own = client.get(f"{V1}/students/{students[0].id}/attendance", headers=headers)
        assert own.status_code == 200
        assert own.json()["overall_percentage"] == 100.0

        other = client.get(f"{V1}/students/{students[1].id}/attendance", headers=headers)
        assert other.status_code == 403

    def test_student_cannot_reach_faculty_endpoints(
        self, client, faculty, offering, students, login
    ):
        headers = login("r001@college.edu")
        for method, path in [
            ("post", f"{V1}/sessions"),
            ("get", f"{V1}/analytics/offerings/{offering.id}/dashboard"),
            ("get", f"{V1}/offerings/{offering.id}/defaulters"),
        ]:
            r = getattr(client, method)(
                path, headers=headers, **({"json": {"offering_id": offering.id}} if method == "post" else {})
            )
            assert r.status_code == 403, f"{method.upper()} {path} allowed a student"

    def test_subject_breakdown_appears(
        self, client, faculty, offering, students, login, make_session_row
    ):
        make_session_row(date.today() - timedelta(days=1), {students[0].id: "present"})
        headers = login("r001@college.edu")
        report = client.get(
            f"{V1}/students/{students[0].id}/attendance", headers=headers
        ).json()
        assert len(report["subjects"]) == 1
        assert report["subjects"][0]["subject_code"] == "CS301"
        assert report["subjects"][0]["faculty_name"] == "Prof Das"


class TestSessionRecoveryFlow:
    """Mistakes happen. The system must be recoverable without data loss."""

    def test_accidental_close_can_be_undone_by_admin(
        self, client, faculty, admin_user, offering, students, login
    ):
        faculty_headers = login("das@college.edu")
        admin_headers = login("admin@college.edu")

        session = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()
        client.post(
            f"{V1}/sessions/{session['id']}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        client.post(f"{V1}/sessions/{session['id']}/close", headers=faculty_headers)

        # Admin reopens
        assert (
            client.post(f"{V1}/sessions/{session['id']}/reopen", headers=admin_headers).status_code
            == 200
        )

        # The manual mark survived; the auto-absents were cleared
        roster = client.get(
            f"{V1}/sessions/{session['id']}/roster", headers=faculty_headers
        ).json()
        assert roster["marked_count"] == 1

        # And marking works again
        r = client.post(
            f"{V1}/sessions/{session['id']}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[1].id, "status": "present"},
        )
        assert r.status_code == 200

    def test_cancelled_class_does_not_hurt_students(
        self, client, faculty, offering, students, login
    ):
        headers = login("das@college.edu")

        # One real class the student attended
        s1 = client.post(
            f"{V1}/sessions",
            headers=headers,
            json={"offering_id": offering.id, "session_date": (date.today() - timedelta(days=2)).isoformat()},
        ).json()
        client.post(
            f"{V1}/sessions/{s1['id']}/mark-manual",
            headers=headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        client.post(f"{V1}/sessions/{s1['id']}/close", headers=headers)

        # One cancelled for a holiday
        s2 = client.post(
            f"{V1}/sessions", headers=headers, json={"offering_id": offering.id}
        ).json()
        client.post(
            f"{V1}/sessions/{s2['id']}/cancel", headers=headers, params={"reason": "holiday"}
        )

        report = client.get(
            f"{V1}/students/{students[0].id}/attendance", headers=headers
        ).json()
        assert report["overall_percentage"] == 100.0, "a cancelled class must not count"
        assert report["subjects"][0]["total_sessions"] == 1


class TestPasswordChangeFlow:
    def test_change_password_revokes_all_sessions(self, client, faculty, login):
        tokens = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "Passw0rd!"}
        ).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        r = client.post(
            f"{V1}/auth/change-password",
            headers=headers,
            json={"current_password": "Passw0rd!", "new_password": "NewPass123"},
        )
        assert r.status_code == 200

        # Old refresh token is dead
        assert (
            client.post(f"{V1}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code
            == 401
        )
        # Old password no longer works
        assert (
            client.post(
                f"{V1}/auth/login",
                json={"email": "das@college.edu", "password": "Passw0rd!"},
            ).status_code
            == 401
        )
        # New one does
        assert (
            client.post(
                f"{V1}/auth/login",
                json={"email": "das@college.edu", "password": "NewPass123"},
            ).status_code
            == 200
        )

    def test_wrong_current_password_blocks_change(self, client, faculty, faculty_headers):
        """Possessing an unattended session must not be enough to seize an account."""
        r = client.post(
            f"{V1}/auth/change-password",
            headers=faculty_headers,
            json={"current_password": "wrong", "new_password": "NewPass123"},
        )
        assert r.status_code == 401


class TestMultiSubjectFlow:
    def test_overall_sums_rather_than_averages_subject_percentages(
        self, client, db, faculty, subject, offering, department, make_student,
        make_session_row, login, admin_user,
    ):
        """
        A student in two subjects: 1/1 in a small one, 3/10 in a large one.
            average of subject percentages : (100 + 30) / 2 = 65.0%
            summed numerators/denominators : 4 / 11        = 36.4%
        The second is correct -- each SESSION carries equal weight.
        """
        from app.models.academic import Enrollment, Subject, SubjectOffering

        subject2 = Subject(
            code="CS302", name="Algorithms", department_id=department.id, semester=3
        )
        db.add(subject2)
        db.commit()
        offering2 = SubjectOffering(
            subject_id=subject2.id,
            faculty_id=faculty.id,
            section="A",
            academic_year="2026-2027",
        )
        db.add(offering2)
        db.commit()

        student = make_student("R500", enrolled_in=offering)
        db.add(
            Enrollment(
                student_id=student.id,
                offering_id=offering2.id,
                enrolled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()

        # Subject 1: 1 of 1
        make_session_row(date(2026, 3, 1), {student.id: "present"})
        # Subject 2: 3 of 10
        for i in range(10):
            status = "present" if i < 3 else "absent"
            make_session_row(
                date(2026, 4, i + 1), {student.id: status}, offering_obj=offering2
            )

        headers = login("das@college.edu")
        report = client.get(f"{V1}/students/{student.id}/attendance", headers=headers).json()

        assert report["overall_attended"] == 4
        assert report["overall_denominator"] == 11
        assert report["overall_percentage"] == pytest.approx(36.4, abs=0.1)
        assert report["overall_percentage"] != pytest.approx(65.0, abs=1.0)
