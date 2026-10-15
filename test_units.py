"""
UNIT TESTS -- pure logic, isolated from HTTP and (mostly) from the database.

WHAT MAKES A TEST A UNIT TEST
-----------------------------
It exercises ONE unit of behaviour with no dependency on anything slow or
shared. These run in milliseconds, so you can run them on every save.

The percentage tests do touch the database, because the calculation genuinely
reads sessions and records -- but they drive the SERVICE directly rather than
going through HTTP, so a routing change cannot break them.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.enums import AttendanceStatus, SessionStatus


# ===========================================================================
class TestPasswordHashing:
    """bcrypt behaviour. The security floor of the whole system."""

    def test_hash_is_not_the_password(self):
        assert hash_password("secret123") != "secret123"

    def test_same_password_different_hashes(self):
        """Per-password salting. Identical passwords must not collide."""
        assert hash_password("secret123") != hash_password("secret123")

    def test_correct_password_verifies(self):
        assert verify_password("secret123", hash_password("secret123"))

    def test_wrong_password_rejected(self):
        assert not verify_password("wrong", hash_password("secret123"))

    def test_uses_bcrypt_2b_with_cost_12(self):
        assert hash_password("x").startswith("$2b$12$")

    def test_malformed_hash_returns_false_not_raises(self):
        """A corrupt row must deny access, not 500 the login endpoint."""
        assert verify_password("anything", "not-a-hash") is False

    def test_empty_hash_returns_false(self):
        assert verify_password("anything", "") is False

    @pytest.mark.parametrize("length", [71, 72])
    def test_accepts_up_to_72_bytes(self, length):
        assert verify_password("a" * length, hash_password("a" * length))

    def test_rejects_over_72_bytes(self):
        """
        bcrypt silently TRUNCATES past 72 bytes, so two different long
        passwords could hash identically. Rejecting is safer than truncating.
        """
        with pytest.raises(ValueError, match="72-byte"):
            hash_password("a" * 73)

    def test_multibyte_counts_as_bytes_not_characters(self):
        """
        A 30-character Devanagari password exceeds 72 BYTES. The limit is on
        bytes, and getting this wrong lets multibyte passwords truncate.
        """
        long_unicode = "पासवर्ड" * 12  # well over 72 bytes
        with pytest.raises(ValueError):
            hash_password(long_unicode)


# ===========================================================================
class TestJwtTokens:
    """Token construction and the attacks it must resist."""

    def test_access_token_round_trips(self):
        payload = decode_token(create_access_token(42, "faculty"), "access")
        assert payload["sub"] == "42"
        assert payload["role"] == "faculty"

    def test_sub_is_a_string_per_rfc7519(self):
        payload = decode_token(create_access_token(42, "admin"), "access")
        assert isinstance(payload["sub"], str)

    def test_refresh_token_carries_no_role(self):
        """
        The role is re-read from the database on refresh, which is what closes
        the window where a demoted user keeps elevated access.
        """
        assert "role" not in decode_token(create_refresh_token(42), "refresh")

    def test_every_token_has_a_unique_jti(self):
        """
        JWT timestamps have SECOND resolution. Without a random jti, two tokens
        minted in the same second are byte-identical -- which violates the
        UNIQUE constraint on refresh_tokens.token_hash and 500s the second
        login. This test exists because that bug actually shipped once.
        """
        a = decode_token(create_refresh_token(1), "refresh")
        b = decode_token(create_refresh_token(1), "refresh")
        assert a["jti"] != b["jti"]

    def test_refresh_token_rejected_as_access_token(self):
        """Type confusion: a stolen refresh token must not grant API access."""
        with pytest.raises(Exception, match="Expected"):
            decode_token(create_refresh_token(1), "access")

    def test_access_token_rejected_as_refresh_token(self):
        with pytest.raises(Exception, match="Expected"):
            decode_token(create_access_token(1, "admin"), "refresh")

    def test_tampered_signature_rejected(self):
        token = create_access_token(1, "student")
        with pytest.raises(Exception):
            decode_token(token[:-6] + "aaaaaa", "access")

    def test_alg_none_forgery_rejected(self):
        """
        The classic JWT attack: set alg to "none", strip the signature, and a
        naive library accepts an attacker-authored token. Pinning algorithms=
        on decode is what stops it.
        """
        import base64
        import json

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=")
        body = base64.urlsafe_b64encode(
            json.dumps({"sub": "1", "role": "admin", "type": "access"}).encode()
        ).rstrip(b"=")
        with pytest.raises(Exception):
            decode_token(f"{header.decode()}.{body.decode()}.", "access")

    def test_expired_token_rejected(self):
        import jwt as pyjwt

        from app.core.config import settings

        expired = pyjwt.encode(
            {
                "sub": "1",
                "type": "access",
                "role": "admin",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(Exception):
            decode_token(expired, "access")

    def test_token_signed_with_wrong_secret_rejected(self):
        import jwt as pyjwt

        forged = pyjwt.encode(
            {
                "sub": "1",
                "type": "access",
                "role": "admin",
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            "attacker-chosen-secret",
            algorithm="HS256",
        )
        with pytest.raises(Exception):
            decode_token(forged, "access")

    def test_refresh_token_hash_is_deterministic_and_not_the_token(self):
        token = create_refresh_token(1)
        assert hash_refresh_token(token) == hash_refresh_token(token)
        assert hash_refresh_token(token) != token
        assert len(hash_refresh_token(token)) == 64  # SHA-256 hex


# ===========================================================================
class TestAttendanceEnums:
    """The status vocabulary. Small, but it drives every calculation."""

    def test_late_counts_as_attended(self):
        assert AttendanceStatus.LATE.value in AttendanceStatus.counts_as_attended()

    def test_excused_does_not_count_as_attended(self):
        assert AttendanceStatus.EXCUSED.value not in AttendanceStatus.counts_as_attended()

    def test_excused_excluded_from_denominator(self):
        """
        The rule that makes an excused absence neither help nor hurt: the
        session is removed from the student's total rather than counted.
        """
        assert (
            AttendanceStatus.EXCUSED.value
            not in AttendanceStatus.counts_in_denominator()
        )

    def test_absent_counts_in_denominator(self):
        assert AttendanceStatus.ABSENT.value in AttendanceStatus.counts_in_denominator()

    def test_str_enum_compares_to_plain_string(self):
        """StrEnum members must drop straight into SQL params and JSON."""
        assert AttendanceStatus.PRESENT == "present"
        assert SessionStatus.OPEN == "open"


# ===========================================================================
class TestPercentageCalculation:
    """
    The formula, exercised through the service with controlled data.

    Each test builds the exact scenario it describes, so the expected number
    can be verified by hand from the test body alone.
    """

    def _service(self, db):
        from app.services.analytics_service import AnalyticsService

        return AnalyticsService(db)

    def test_all_present_is_100_percent(self, db, offering, students, make_session_row):
        s = students[0]
        for i in range(4):
            make_session_row(date(2026, 3, i + 1), {s.id: "present"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] == 100.0
        assert stats["attended"] == 4

    def test_half_present_is_50_percent(self, db, offering, students, make_session_row):
        s = students[0]
        make_session_row(date(2026, 3, 1), {s.id: "present"})
        make_session_row(date(2026, 3, 2), {s.id: "absent"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] == 50.0

    def test_late_counts_toward_attendance(self, db, offering, students, make_session_row):
        s = students[0]
        make_session_row(date(2026, 3, 1), {s.id: "present"})
        make_session_row(date(2026, 3, 2), {s.id: "late"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] == 100.0, "late is a punctuality signal, not an absence"

    def test_excused_removed_from_denominator(self, db, offering, students, make_session_row):
        """
        3 sessions: present, absent, excused.
        Naive: 1/3 = 33.3%.  Correct: 1/2 = 50% -- the excused one does not apply.
        """
        s = students[0]
        make_session_row(date(2026, 3, 1), {s.id: "present"})
        make_session_row(date(2026, 3, 2), {s.id: "absent"})
        make_session_row(date(2026, 3, 3), {s.id: "excused"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["denominator"] == 2
        assert stats["percentage"] == 50.0

    def test_cancelled_session_excluded_entirely(
        self, db, offering, students, make_session_row
    ):
        """A class cancelled for a holiday must not count against anyone."""
        s = students[0]
        make_session_row(date(2026, 3, 1), {s.id: "present"})
        make_session_row(date(2026, 3, 2), {s.id: "absent"}, status="cancelled")
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["total_sessions"] == 1
        assert stats["percentage"] == 100.0

    def test_open_session_excluded(self, db, offering, students, make_session_row):
        """A session still in progress is not yet part of anyone's record."""
        s = students[0]
        make_session_row(date(2026, 3, 1), {s.id: "present"})
        make_session_row(date(2026, 3, 2), {s.id: "absent"}, status="open")
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["total_sessions"] == 1

    def test_no_sessions_returns_none_not_zero(self, db, offering, students):
        """
        THE EDGE CASE THAT MATTERS MOST. Returning 0% on day one would flag
        every student in the institution as a defaulter.
        """
        stats = self._service(db).calculate_offering_attendance(students[0].id, offering.id)
        assert stats["percentage"] is None
        assert stats["is_below_threshold"] is False

    def test_all_excused_returns_none(self, db, offering, students, make_session_row):
        """Denominator zero for a different reason -- still genuinely undefined."""
        s = students[0]
        make_session_row(date(2026, 3, 1), {s.id: "excused"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] is None

    def test_late_joiner_not_penalised_for_earlier_sessions(
        self, db, offering, make_student, make_session_row
    ):
        """
        A student enrolled in week 6 must not owe attendance for weeks 1-5.
        Without the enrolled_at filter they would appear to have missed
        everything through no fault of their own.
        """
        joiner = make_student(
            "R900",
            enrolled_in=offering,
            enrolled_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        make_session_row(date(2026, 3, 1), {})  # before they joined
        make_session_row(date(2026, 3, 5), {})  # before they joined
        make_session_row(date(2026, 3, 15), {joiner.id: "present"})

        stats = self._service(db).calculate_offering_attendance(joiner.id, offering.id)
        assert stats["total_sessions"] == 1
        assert stats["percentage"] == 100.0

    def test_below_threshold_flag(self, db, offering, students, make_session_row):
        s = students[0]
        for i in range(3):
            make_session_row(date(2026, 3, i + 1), {s.id: "absent"})
        make_session_row(date(2026, 3, 10), {s.id: "present"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] == 25.0
        assert stats["is_below_threshold"] is True

    def test_exactly_at_threshold_is_not_below(
        self, db, offering, students, make_session_row
    ):
        """
        BOUNDARY. 75.0% must be SAFE, not flagged. An off-by-one here would
        wrongly bar every student sitting exactly on the line.
        """
        s = students[0]
        for i in range(3):
            make_session_row(date(2026, 3, i + 1), {s.id: "present"})
        make_session_row(date(2026, 3, 4), {s.id: "absent"})
        stats = self._service(db).calculate_offering_attendance(s.id, offering.id)
        assert stats["percentage"] == 75.0
        assert stats["is_below_threshold"] is False

    def test_student_not_enrolled_raises(self, db, offering, make_student):
        from app.core.exceptions import NotFoundError

        outsider = make_student("R999")  # deliberately not enrolled
        with pytest.raises(NotFoundError):
            self._service(db).calculate_offering_attendance(outsider.id, offering.id)


# ===========================================================================
class TestShortfallCalculation:
    """
    "How many classes in a row do I need?" -- solved from
        (attended + n) / (denominator + n) >= target
    """

    def _service(self, db):
        from app.services.analytics_service import AnalyticsService

        return AnalyticsService(db)

    def test_shortfall_actually_reaches_the_threshold(
        self, db, offering, students, make_session_row
    ):
        s = students[0]
        for i in range(6):
            make_session_row(date(2026, 3, i + 1), {s.id: "absent"})
        make_session_row(date(2026, 3, 20), {s.id: "present"})

        defaulters = self._service(db).get_defaulters(offering.id)
        entry = next(d for d in defaulters if d["student_id"] == s.id)

        n = entry["shortfall_sessions"]
        resulting = (entry["attended"] + n) / (entry["denominator"] + n) * 100
        assert resulting >= 75.0, f"{n} classes only reaches {resulting:.1f}%"

    def test_shortfall_is_minimal(self, db, offering, students, make_session_row):
        """One fewer class must NOT be enough -- otherwise we over-state it."""
        s = students[0]
        for i in range(6):
            make_session_row(date(2026, 3, i + 1), {s.id: "absent"})
        make_session_row(date(2026, 3, 20), {s.id: "present"})

        entry = next(
            d
            for d in self._service(db).get_defaulters(offering.id)
            if d["student_id"] == s.id
        )
        n = entry["shortfall_sessions"]
        if n > 0:
            just_under = (entry["attended"] + n - 1) / (entry["denominator"] + n - 1) * 100
            assert just_under < 75.0, "shortfall is one larger than necessary"
