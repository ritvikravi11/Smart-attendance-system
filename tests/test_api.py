"""
API TESTS -- the HTTP contract.

These assert what a CLIENT sees: status codes, response shapes, and above all
who is allowed to do what. They go through the real router, the real
dependencies, and the real services.

THE AUTHORISATION MATRIX IS THE POINT OF THIS FILE.
Most API bugs in real systems are not wrong data -- they are the right data
shown to the wrong person. Every protected endpoint is tested from three
directions: the owner, a peer who should be denied, and an unauthenticated
caller.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

V1 = "/api/v1"


# ===========================================================================
class TestAuthEndpoints:
    def test_login_returns_token_pair(self, client, faculty):
        r = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "Passw0rd!"}
        )
        assert r.status_code == 200
        body = r.json()
        assert {"access_token", "refresh_token", "token_type", "expires_in"} <= body.keys()
        assert body["token_type"] == "bearer"

    def test_login_is_case_insensitive_on_email(self, client, faculty):
        r = client.post(
            f"{V1}/auth/login",
            json={"email": "DAS@College.EDU", "password": "Passw0rd!"},
        )
        assert r.status_code == 200

    def test_wrong_password_returns_401(self, client, faculty):
        r = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "nope"}
        )
        assert r.status_code == 401

    def test_unknown_email_gives_identical_response_to_wrong_password(
        self, client, faculty
    ):
        """
        Account enumeration defence. If these differ, an attacker submits a
        list of addresses and learns which are registered.
        """
        wrong_pw = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "nope"}
        )
        no_user = client.post(
            f"{V1}/auth/login", json={"email": "ghost@college.edu", "password": "nope"}
        )
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json()["detail"] == no_user.json()["detail"]

    def test_deactivated_account_rejected(self, client, make_user):
        make_user("gone@college.edu", "faculty", active=False)
        r = client.post(
            f"{V1}/auth/login", json={"email": "gone@college.edu", "password": "Passw0rd!"}
        )
        assert r.status_code == 403

    def test_malformed_email_returns_422_with_standard_envelope(self, client, roles):
        r = client.post(f"{V1}/auth/login", json={"email": "not-an-email", "password": "x"})
        assert r.status_code == 422
        assert r.json()["error_code"] == "VALIDATION_ERROR"

    def test_lockout_after_five_failures(self, client, faculty):
        for _ in range(5):
            client.post(
                f"{V1}/auth/login",
                json={"email": "das@college.edu", "password": "wrong"},
            )
        r = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "wrong"}
        )
        assert r.status_code == 423

    def test_correct_password_still_blocked_while_locked(self, client, faculty):
        """Lockout is checked BEFORE the password, so a locked account cannot be probed."""
        for _ in range(6):
            client.post(
                f"{V1}/auth/login",
                json={"email": "das@college.edu", "password": "wrong"},
            )
        r = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "Passw0rd!"}
        )
        assert r.status_code == 423

    def test_me_requires_a_token(self, client):
        assert client.get(f"{V1}/auth/me").status_code == 401

    def test_me_never_exposes_password_hash(self, client, faculty_headers):
        body = client.get(f"{V1}/auth/me", headers=faculty_headers).json()
        for secret in ("password_hash", "failed_login_count", "locked_until"):
            assert secret not in body, f"{secret} leaked in the response"

    def test_refresh_rotates_the_token(self, client, faculty):
        tokens = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "Passw0rd!"}
        ).json()
        r = client.post(f"{V1}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 200
        assert r.json()["refresh_token"] != tokens["refresh_token"]

    def test_old_refresh_token_dies_after_rotation(self, client, faculty):
        tokens = client.post(
            f"{V1}/auth/login", json={"email": "das@college.edu", "password": "Passw0rd!"}
        ).json()
        client.post(f"{V1}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        replay = client.post(
            f"{V1}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401, "a rotated token must not be reusable"

    def test_garbage_refresh_token_returns_401_not_500(self, client, faculty):
        r = client.post(f"{V1}/auth/refresh", json={"refresh_token": "garbage"})
        assert r.status_code == 401


# ===========================================================================
class TestSessionEndpoints:
    def test_faculty_opens_session_for_own_offering(
        self, client, faculty_headers, offering, students
    ):
        r = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        )
        assert r.status_code == 201
        assert r.json()["status"] == "open"
        assert r.json()["enrolled_count"] == 5

    def test_faculty_cannot_open_session_for_another_offering(
        self, client, login, other_faculty, offering
    ):
        """IDOR defence. Ownership, not just role."""
        headers = login("rao@college.edu")
        r = client.post(f"{V1}/sessions", headers=headers, json={"offering_id": offering.id})
        assert r.status_code == 403

    def test_student_cannot_open_a_session(self, client, student_headers, offering):
        r = client.post(
            f"{V1}/sessions", headers=student_headers, json={"offering_id": offering.id}
        )
        assert r.status_code == 403

    def test_unauthenticated_cannot_open_a_session(self, client, offering):
        assert (
            client.post(f"{V1}/sessions", json={"offering_id": offering.id}).status_code
            == 401
        )

    def test_second_open_session_rejected(self, client, faculty_headers, offering):
        client.post(f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id})
        r = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        )
        assert r.status_code == 409

    def test_nonexistent_offering_returns_404(self, client, faculty_headers, faculty):
        r = client.post(f"{V1}/sessions", headers=faculty_headers, json={"offering_id": 99999})
        assert r.status_code in (403, 404)

    def test_roster_lists_all_enrolled_including_unmarked(
        self, client, faculty_headers, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        body = client.get(f"{V1}/sessions/{sid}/roster", headers=faculty_headers).json()
        assert body["enrolled_count"] == 5
        assert body["marked_count"] == 0
        assert all(e["status"] is None for e in body["entries"])

    def test_other_faculty_cannot_read_roster(
        self, client, faculty_headers, login, other_faculty, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        r = client.get(f"{V1}/sessions/{sid}/roster", headers=login("rao@college.edu"))
        assert r.status_code == 403

    def test_close_auto_marks_absentees(
        self, client, faculty_headers, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(
            f"{V1}/sessions/{sid}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        body = client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers).json()
        assert body["present_count"] == 1
        assert body["auto_absent_created"] == 4

    def test_cannot_mark_in_closed_session(
        self, client, faculty_headers, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        r = client.post(
            f"{V1}/sessions/{sid}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        assert r.status_code == 409

    def test_cannot_close_twice(self, client, faculty_headers, offering, students):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        assert (
            client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers).status_code
            == 409
        )

    def test_faculty_cannot_reopen(self, client, faculty_headers, offering, students):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        assert (
            client.post(f"{V1}/sessions/{sid}/reopen", headers=faculty_headers).status_code
            == 403
        )

    def test_admin_can_reopen(
        self, client, faculty_headers, admin_headers, offering, students
    ):
        sid = client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]
        client.post(f"{V1}/sessions/{sid}/close", headers=faculty_headers)
        assert (
            client.post(f"{V1}/sessions/{sid}/reopen", headers=admin_headers).status_code
            == 200
        )


# ===========================================================================
class TestMarkingEndpoints:
    @pytest.fixture
    def open_session(self, client, faculty_headers, offering, students):
        return client.post(
            f"{V1}/sessions", headers=faculty_headers, json={"offering_id": offering.id}
        ).json()["id"]

    def test_manual_mark_succeeds(self, client, faculty_headers, open_session, students):
        r = client.post(
            f"{V1}/sessions/{open_session}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "present"},
        )
        assert r.status_code == 200
        assert r.json()["method"] == "manual"

    def test_marking_twice_does_not_duplicate(
        self, client, faculty_headers, open_session, students
    ):
        for _ in range(3):
            client.post(
                f"{V1}/sessions/{open_session}/mark-manual",
                headers=faculty_headers,
                json={"student_id": students[0].id, "status": "present"},
            )
        roster = client.get(
            f"{V1}/sessions/{open_session}/roster", headers=faculty_headers
        ).json()
        assert roster["marked_count"] == 1

    def test_student_not_enrolled_rejected(
        self, client, faculty_headers, open_session, make_student
    ):
        outsider = make_student("R888")  # not enrolled in this offering
        r = client.post(
            f"{V1}/sessions/{open_session}/mark-manual",
            headers=faculty_headers,
            json={"student_id": outsider.id, "status": "present"},
        )
        assert r.status_code == 400

    def test_invalid_status_rejected(
        self, client, faculty_headers, open_session, students
    ):
        r = client.post(
            f"{V1}/sessions/{open_session}/mark-manual",
            headers=faculty_headers,
            json={"student_id": students[0].id, "status": "teleported"},
        )
        assert r.status_code == 422

    def test_undecodable_frame_returns_400_not_500(
        self, client, faculty_headers, open_session
    ):
        import io

        r = client.post(
            f"{V1}/sessions/{open_session}/mark-face",
            headers=faculty_headers,
            files={"frame": ("x.jpg", io.BytesIO(b"not an image"), "image/jpeg")},
        )
        assert r.status_code in (400, 409), "must fail cleanly, never leak a traceback"

    def test_empty_frame_rejected(self, client, faculty_headers, open_session):
        import io

        r = client.post(
            f"{V1}/sessions/{open_session}/mark-face",
            headers=faculty_headers,
            files={"frame": ("x.jpg", io.BytesIO(b""), "image/jpeg")},
        )
        assert r.status_code in (400, 409)


# ===========================================================================
class TestAnalyticsEndpoints:
    def test_student_sees_own_attendance(self, client, student_headers, students):
        r = client.get(f"{V1}/students/{students[0].id}/attendance", headers=student_headers)
        assert r.status_code == 200

    def test_student_cannot_see_another_student(self, client, student_headers, students):
        """Horizontal privilege escalation -- the most commonly missed check."""
        r = client.get(f"{V1}/students/{students[1].id}/attendance", headers=student_headers)
        assert r.status_code == 403

    def test_faculty_can_see_any_student(self, client, faculty_headers, students):
        r = client.get(f"{V1}/students/{students[1].id}/attendance", headers=faculty_headers)
        assert r.status_code == 200

    def test_faculty_cannot_see_another_faculty_summary(
        self, client, faculty_headers, other_faculty
    ):
        r = client.get(f"{V1}/faculty/{other_faculty.id}/attendance", headers=faculty_headers)
        assert r.status_code == 403

    def test_admin_can_see_any_faculty(self, client, admin_headers, faculty):
        assert (
            client.get(f"{V1}/faculty/{faculty.id}/attendance", headers=admin_headers).status_code
            == 200
        )

    @pytest.mark.parametrize(
        "path",
        ["dashboard", "daily", "monthly", "students", "absentees"],
    )
    def test_analytics_endpoints_respond(
        self, client, faculty_headers, offering, students, path
    ):
        r = client.get(f"{V1}/analytics/offerings/{offering.id}/{path}", headers=faculty_headers)
        assert r.status_code == 200

    @pytest.mark.parametrize(
        "path",
        ["dashboard", "daily", "monthly", "students", "absentees"],
    )
    def test_analytics_require_authentication(self, client, offering, path):
        r = client.get(f"{V1}/analytics/offerings/{offering.id}/{path}")
        assert r.status_code == 401

    def test_other_faculty_denied_analytics(
        self, client, login, other_faculty, offering, students
    ):
        r = client.get(
            f"{V1}/analytics/offerings/{offering.id}/dashboard",
            headers=login("rao@college.edu"),
        )
        assert r.status_code == 403

    def test_empty_offering_returns_empty_series_not_error(
        self, client, faculty_headers, offering
    ):
        body = client.get(
            f"{V1}/analytics/offerings/{offering.id}/dashboard", headers=faculty_headers
        ).json()
        assert body["daily"] == []
        assert body["summary"]["class_average"] is None

    @pytest.mark.parametrize("days", [0, -5, 400])
    def test_daily_rejects_out_of_range_days(
        self, client, faculty_headers, offering, days
    ):
        r = client.get(
            f"{V1}/analytics/offerings/{offering.id}/daily",
            headers=faculty_headers,
            params={"days": days},
        )
        assert r.status_code == 422


# ===========================================================================
class TestSystemEndpoints:
    def test_health_is_public(self, client):
        r = client.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

    def test_health_discloses_nothing(self, client):
        """Reachable by anyone, so it must not reveal version or environment."""
        assert set(client.get("/health").json().keys()) == {"status"}

    def test_openapi_spec_generates(self, client):
        assert client.get("/openapi.json").status_code == 200

    def test_unknown_route_returns_404(self, client):
        assert client.get(f"{V1}/does-not-exist").status_code == 404
