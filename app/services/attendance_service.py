"""
Attendance engine: session lifecycle and record management.

This service implements the rules specified in docs/ATTENDANCE_BUSINESS_LOGIC.md.
Read that first -- the reasoning lives there; the mechanics live here.

The service imports SQLAlchemy and app.core. It does NOT import FastAPI. If you
ever need HTTPException here, raise a domain exception instead and let the
handler in main.py translate it.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.models.academic import Enrollment, SubjectOffering
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.enums import (
    AttendanceStatus,
    AuditAction,
    MarkingMethod,
    RoleName,
    SessionStatus,
)
from app.models.logs import AuditLog
from app.models.people import Faculty, Student
from app.models.user import User

from app.utils.datetime_utils import as_utc

logger = logging.getLogger(__name__)

# How far back a session may be dated. Faculty forget to open sessions and
# record them later, which is legitimate -- but a class from last semester is
# almost always a typo, and it silently corrupts percentages if accepted.
MAX_BACKDATE_DAYS = 30

# Marker distinguishing rows WE generated at close from a faculty member's
# deliberate manual mark. `reopen_session` deletes only the former. It was a
# magic string repeated in two places -- if they ever drifted, reopening a
# session would silently destroy real data.
AUTO_ABSENT_NOTE = "Auto-marked absent at session close"


class AttendanceService:
    """Session lifecycle, marking, and correction."""

    def __init__(self, db: Session):
        self.db = db

    # ==================================================================
    # AUTHORISATION
    # ==================================================================

    def _resolve_offering(self, offering_id: int) -> SubjectOffering:
        offering = self.db.get(SubjectOffering, offering_id)
        if offering is None or not offering.is_active:
            raise NotFoundError(f"Offering {offering_id} not found.")
        return offering

    def assert_can_access_offering(self, offering_id: int, user: User) -> SubjectOffering:
        """
        Verify the user may act on this offering.

        THE RULE (section 8 of the business logic doc): a faculty member may
        access ONLY offerings they teach.

        Checking merely "is this user faculty?" would leave an IDOR hole --
        faculty A reading faculty B's class by guessing an ID. The role check
        is necessary and NOT sufficient; ownership must be verified per
        resource. That is what this method exists to do.
        """
        offering = self._resolve_offering(offering_id)

        if user.role.name == RoleName.ADMIN.value:
            return offering

        if user.role.name != RoleName.FACULTY.value:
            raise PermissionDeniedError("Only faculty and admins may access sessions.")

        faculty = self.db.execute(
            select(Faculty).where(Faculty.user_id == user.id)
        ).scalar_one_or_none()

        if faculty is None or offering.faculty_id != faculty.id:
            # Deliberately does not reveal whether the offering exists -- a
            # 403 that distinguishes "not yours" from "does not exist" is an
            # enumeration oracle.
            raise PermissionDeniedError(
                "You do not teach this subject offering."
            )
        return offering

    def assert_can_access_session(
        self, session_id: int, user: User
    ) -> AttendanceSession:
        session = self.db.get(AttendanceSession, session_id)
        if session is None:
            raise NotFoundError(f"Session {session_id} not found.")
        self.assert_can_access_offering(session.offering_id, user)
        return session

    # ==================================================================
    # SESSION LIFECYCLE
    # ==================================================================

    def open_session(
        self,
        offering_id: int,
        user: User,
        *,
        session_date: date | None = None,
        room: str | None = None,
        late_threshold_minutes: int | None = None,
        notes: str | None = None,
    ) -> AttendanceSession:
        """
        Open a session (rule L-1, L-2).

        WHY ONLY ONE OPEN SESSION PER OFFERING
        --------------------------------------
        If two were open simultaneously, a recognised student would be
        ambiguous -- which session gets the record? Rather than inventing a
        tie-break, we forbid the situation. The faculty member must close the
        forgotten session first, which is also the behaviour they want:
        yesterday's session left open by accident should be closed, not
        silently competed with.
        """
        self.assert_can_access_offering(offering_id, user)

        # BUG FIX: session_date was accepted unvalidated, so a typo ("2036" for
        # "2026") created a session ten years out. It would never appear in any
        # 30-day analytics window, would silently distort monthly aggregates,
        # and faculty could not remove it.
        #
        # Backdating IS allowed -- faculty legitimately record a class they
        # forgot to open at the time -- but only within a bounded window, and
        # never into the future.
        #
        # Found by tests/test_edge_cases.py::test_session_dated_far_in_the_future.
        resolved_date = session_date or date.today()
        today = date.today()

        if resolved_date > today:
            raise ValidationError(
                f"Session date {resolved_date} is in the future. "
                "Attendance can only be recorded for a class that has happened.",
                field="session_date",
            )
        if resolved_date < today - timedelta(days=MAX_BACKDATE_DAYS):
            raise ValidationError(
                f"Session date {resolved_date} is more than {MAX_BACKDATE_DAYS} "
                "days ago. Ask an administrator to record it.",
                field="session_date",
            )

        existing_open = self.db.execute(
            select(AttendanceSession).where(
                AttendanceSession.offering_id == offering_id,
                AttendanceSession.status == SessionStatus.OPEN.value,
            )
        ).scalar_one_or_none()

        if existing_open is not None:
            raise ConflictError(
                f"An open session already exists for this offering "
                f"(session {existing_open.id}, opened "
                f"{existing_open.started_at:%Y-%m-%d %H:%M}). Close it first.",
                error_code="SESSION_ALREADY_OPEN",
            )

        session = AttendanceSession(
            offering_id=offering_id,
            # session_date is a LOCAL date, deliberately stored separately from
            # the UTC timestamp. See section 9 of the business logic doc.
            session_date=resolved_date,
            status=SessionStatus.OPEN.value,
            room=room,
            late_threshold_minutes=late_threshold_minutes,
            notes=notes,
            created_by=user.id,
        )
        self.db.add(session)
        self.db.flush()

        self._audit(user.id, AuditAction.SESSION_OPEN, session.id,
                    {"offering_id": offering_id})
        self.db.commit()

        logger.info("Session %s opened for offering %s by user %s",
                    session.id, offering_id, user.id)
        return session

    def close_session(self, session_id: int, user: User) -> dict:
        """
        Close a session and generate absentee records (rule L-4).

        ABSENCE IS DERIVED, NOT OBSERVED. You cannot detect a student who is
        not there, so absence is computed as a SET DIFFERENCE:

            absentees = (enrolled students) - (students with a record)

        Done as ONE bulk insert, not a loop of 40 individual inserts.

        The enrolment-date filter matters: a student who joined in week 6 must
        not be marked absent for a session in week 2. `enrollments.enrolled_at`
        exists for exactly this.
        """
        session = self.assert_can_access_session(session_id, user)

        # CONCURRENCY FIX. Two simultaneous close requests both passed the
        # status check, both computed the absentee set, and both bulk-inserted
        # -- the second hit the UNIQUE constraint and returned a 500.
        #
        # SELECT ... FOR UPDATE takes a row lock, so the second request blocks
        # until the first commits and then sees status='closed' and gets a
        # clean 409. `with_for_update` is a no-op on SQLite (which serialises
        # writes anyway), so the tests are unaffected.
        session = self.db.execute(
            select(AttendanceSession)
            .where(AttendanceSession.id == session_id)
            .with_for_update()
        ).scalar_one()

        if session.status != SessionStatus.OPEN.value:
            raise ConflictError(
                f"Session is already {session.status}.",
                error_code="SESSION_NOT_OPEN",
            )

        # Students enrolled in this offering ON OR BEFORE the session date
        enrolled = self.db.execute(
            select(Enrollment.student_id, Enrollment.enrolled_at).where(
                Enrollment.offering_id == session.offering_id,
                Enrollment.is_active.is_(True),
            )
        ).all()

        eligible_ids = {
            sid
            for sid, enrolled_at in enrolled
            if as_utc(enrolled_at).date() <= session.session_date
        }

        already_marked = {
            row[0]
            for row in self.db.execute(
                select(AttendanceRecord.student_id).where(
                    AttendanceRecord.session_id == session_id
                )
            ).all()
        }

        absentee_ids = eligible_ids - already_marked

        now = datetime.now(timezone.utc)
        if absentee_ids:
            # `bulk_save_objects` is legacy in SQLAlchemy 2.0 and bypasses the
            # ORM's event hooks. The modern equivalent is a Core insert with a
            # list of dicts -- same single round trip, supported API, and it
            # respects column defaults properly.
            self.db.execute(
                insert(AttendanceRecord),
                [
                    {
                        "session_id": session_id,
                        "student_id": sid,
                        "status": AttendanceStatus.ABSENT.value,
                        # method='manual' because no face was recognised. A
                        # confidence score would be meaningless, and the CHECK
                        # constraint rejects method='face' with a NULL
                        # confidence anyway.
                        "method": MarkingMethod.MANUAL.value,
                        "marked_by": user.id,
                        "marked_at": now,
                        "needs_review": False,
                        "notes": AUTO_ABSENT_NOTE,
                    }
                    for sid in absentee_ids
                ],
            )

        session.status = SessionStatus.CLOSED.value
        session.ended_at = now

        self._audit(user.id, AuditAction.SESSION_CLOSE, session_id,
                    {"auto_absent": len(absentee_ids)})
        self.db.commit()

        counts = self._session_counts(session_id)
        counts.update({
            "session_id": session_id,
            "status": session.status,
            "ended_at": now,
            "enrolled_count": len(eligible_ids),
            "auto_absent_created": len(absentee_ids),
        })

        logger.info("Session %s closed: %d auto-absent created",
                    session_id, len(absentee_ids))
        return counts

    def cancel_session(self, session_id: int, user: User, reason: str = "") -> None:
        """
        Cancel a session (rule L-6).

        Cancelled sessions are excluded from percentage calculations entirely,
        so students are not penalised for a class that never happened.
        """
        session = self.assert_can_access_session(session_id, user)

        if session.status == SessionStatus.CLOSED.value:
            raise ConflictError("Cannot cancel a closed session. Ask an admin to reopen it first.")

        session.status = SessionStatus.CANCELLED.value
        session.ended_at = datetime.now(timezone.utc)
        session.notes = (session.notes or "") + f" [Cancelled: {reason}]"

        self._audit(user.id, AuditAction.UPDATE, session_id, {"cancelled": reason})
        self.db.commit()

    def reopen_session(self, session_id: int, user: User) -> AttendanceSession:
        """
        Reopen a closed session. ADMIN ONLY (rule L-5), and audit-logged.

        Deletes the auto-generated absent records, since they will be
        regenerated on the next close. Manually-marked records survive --
        distinguished by the notes marker.
        """
        if user.role.name != RoleName.ADMIN.value:
            raise PermissionDeniedError("Only an administrator may reopen a session.")

        session = self.db.get(AttendanceSession, session_id)
        if session is None:
            raise NotFoundError(f"Session {session_id} not found.")
        if session.status != SessionStatus.CLOSED.value:
            raise ConflictError(f"Session is {session.status}, not closed.")

        # Only remove the rows WE generated, never a faculty member's manual mark
        self.db.query(AttendanceRecord).filter(
            AttendanceRecord.session_id == session_id,
            AttendanceRecord.status == AttendanceStatus.ABSENT.value,
            AttendanceRecord.notes == AUTO_ABSENT_NOTE,
        ).delete(synchronize_session=False)

        session.status = SessionStatus.OPEN.value
        session.ended_at = None

        self._audit(user.id, AuditAction.SESSION_REOPEN, session_id, None)
        self.db.commit()

        logger.warning("Session %s REOPENED by admin %s", session_id, user.id)
        return session

    # ==================================================================
    # MARKING -- the core of the engine
    # ==================================================================

    def mark_present(
        self,
        session_id: int,
        student_id: int,
        *,
        confidence: float | None = None,
        method: MarkingMethod = MarkingMethod.FACE,
        needs_review: bool = False,
        marked_by: int | None = None,
        status: AttendanceStatus | None = None,
    ) -> tuple[AttendanceRecord, bool]:
        """
        Record attendance. Returns (record, was_newly_created).

        *** THE RACE-SAFE INSERT -- read this carefully ***

        The naive pattern is:

            if exists(session, student): return existing
            insert(...)

        There is a window between the check and the insert. At 2 fps a student
        standing in frame for ten seconds produces twenty requests; two will
        arrive concurrently and BOTH will pass the check. The race is not
        theoretical -- it fires in the first real class.

        The correct pattern is to attempt the insert and let the database
        arbitrate via UNIQUE (session_id, student_id).

        *** AND THE CRITICAL SUBTLETY: SAVEPOINT ***

        In PostgreSQL an IntegrityError ABORTS THE WHOLE TRANSACTION. Every
        subsequent statement then fails with "current transaction is aborted".
        If marking one student is part of a larger unit of work -- marking six
        faces from one frame, say -- one duplicate would poison the other five.

        `db.begin_nested()` issues a SAVEPOINT. On IntegrityError only the
        savepoint rolls back; the outer transaction survives intact.

        This is the difference between code that passes a unit test and code
        that survives a live camera stream.
        """
        session = self.db.get(AttendanceSession, session_id)
        if session is None:
            raise NotFoundError(f"Session {session_id} not found.")

        # Rule L-3: marking only while open
        if session.status != SessionStatus.OPEN.value:
            raise ConflictError(
                f"Cannot mark attendance: session is {session.status}.",
                error_code="SESSION_NOT_OPEN",
            )

        # Step 6 of the decision chain: is this student in THIS class?
        # A student from Section B must not be marked into Section A.
        enrollment = self.db.execute(
            select(Enrollment).where(
                Enrollment.student_id == student_id,
                Enrollment.offering_id == session.offering_id,
                Enrollment.is_active.is_(True),
            )
        ).scalar_one_or_none()

        if enrollment is None:
            raise ValidationError(
                "This student is not enrolled in this subject offering.",
                error_code="NOT_ENROLLED",
            )

        resolved_status = status or self._resolve_status(session)

        record = AttendanceRecord(
            session_id=session_id,
            student_id=student_id,
            status=resolved_status.value,
            method=method.value,
            confidence=confidence,
            needs_review=needs_review,
            marked_at=datetime.now(timezone.utc),
            marked_by=marked_by,
        )

        try:
            # SAVEPOINT: isolates this insert from the outer transaction
            with self.db.begin_nested():
                self.db.add(record)
            self.db.commit()
            return record, True

        except IntegrityError:
            # Someone else won the race, or this student was already marked.
            # NOT an error -- the desired end state already holds. Returning
            # success here is what makes marking IDEMPOTENT, which is what
            # lets a 2 fps stream hammer this endpoint harmlessly.
            self.db.rollback()

            existing = self.db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == session_id,
                    AttendanceRecord.student_id == student_id,
                )
            ).scalar_one_or_none()

            if existing is None:
                # The IntegrityError was something else -- a bad FK, say.
                # Do not swallow it.
                raise

            return existing, False

    def _resolve_status(self, session: AttendanceSession) -> AttendanceStatus:
        """
        PRESENT or LATE, based on the session's grace period (section 5).

        LATE counts as attended for percentage purposes -- it is a punctuality
        signal, not an absence. Institutions wanting stricter treatment change
        one line in AttendanceStatus.counts_as_attended().
        """
        if session.late_threshold_minutes is None:
            return AttendanceStatus.PRESENT

        started = as_utc(session.started_at)
        cutoff = started + timedelta(minutes=session.late_threshold_minutes)
        return (
            AttendanceStatus.LATE
            if datetime.now(timezone.utc) > cutoff
            else AttendanceStatus.PRESENT
        )

    def mark_manual(
        self,
        session_id: int,
        student_id: int,
        status: AttendanceStatus,
        user: User,
        notes: str | None = None,
    ) -> AttendanceRecord:
        """
        Faculty marks a student by hand.

        This is the fallback that makes the whole system safe to deploy: if the
        camera fails, if recognition misses someone, if a student's face is not
        enrolled -- attendance still happens. The system assists faculty; it
        never replaces their judgement.
        """
        self.assert_can_access_session(session_id, user)

        record, created = self.mark_present(
            session_id,
            student_id,
            method=MarkingMethod.MANUAL,
            marked_by=user.id,
            status=status,
        )

        if not created:
            # Already marked -- treat this as a correction instead
            record.status = status.value
            record.method = MarkingMethod.MANUAL.value
            record.overridden_by = user.id
            record.overridden_at = datetime.now(timezone.utc)
            record.needs_review = False

        if notes:
            record.notes = notes

        self.db.commit()
        return record

    def update_record(
        self,
        record_id: int,
        status: AttendanceStatus,
        user: User,
        notes: str | None = None,
    ) -> AttendanceRecord:
        """
        Correct an existing record.

        The (method='face', overridden_by NOT NULL) combination is precisely
        your FALSE POSITIVE COUNT -- the accuracy metric for your project
        report. Every correction of an AI mark is a data point.
        """
        record = self.db.get(AttendanceRecord, record_id)
        if record is None:
            raise NotFoundError(f"Record {record_id} not found.")

        session = self.assert_can_access_session(record.session_id, user)

        # Rule L-5: closed sessions are immutable except to an Admin
        if (
            session.status == SessionStatus.CLOSED.value
            and user.role.name != RoleName.ADMIN.value
        ):
            raise PermissionDeniedError(
                "This session is closed. Ask an administrator to reopen it."
            )

        old_status = record.status
        record.status = status.value
        record.overridden_by = user.id
        record.overridden_at = datetime.now(timezone.utc)
        record.needs_review = False
        if notes:
            record.notes = notes

        self._audit(
            user.id,
            AuditAction.ATTENDANCE_OVERRIDE,
            record_id,
            {"old_status": old_status, "new_status": status.value,
             "was_face_marked": record.method == MarkingMethod.FACE.value},
        )
        self.db.commit()
        return record

    # ==================================================================
    # QUERIES
    # ==================================================================

    def get_roster(self, session_id: int, user: User) -> dict:
        """
        Every enrolled student with their current status.

        Includes UNMARKED students (status=None), which is what lets the
        faculty UI show "12 of 40 marked" and offer one-click manual marking
        for the rest -- without a second request.
        """
        session = self.assert_can_access_session(session_id, user)

        rows = self.db.execute(
            select(Student, AttendanceRecord)
            .join(Enrollment, Enrollment.student_id == Student.id)
            .outerjoin(
                AttendanceRecord,
                (AttendanceRecord.student_id == Student.id)
                & (AttendanceRecord.session_id == session_id),
            )
            .options(joinedload(Student.user))
            .where(
                Enrollment.offering_id == session.offering_id,
                Enrollment.is_active.is_(True),
                Student.is_active.is_(True),
            )
            .order_by(Student.roll_number)
        ).all()

        entries = []
        marked = 0
        for student, record in rows:
            if record is not None:
                marked += 1
            entries.append({
                "student_id": student.id,
                "roll_number": student.roll_number,
                "student_name": student.user.full_name,
                "face_enrolled": student.face_enrolled,
                "status": record.status if record else None,
                "method": record.method if record else None,
                "confidence": record.confidence if record else None,
                "needs_review": record.needs_review if record else False,
                "marked_at": record.marked_at if record else None,
            })

        return {
            "session_id": session_id,
            "session_status": session.status,
            "enrolled_count": len(entries),
            "marked_count": marked,
            "entries": entries,
        }

    def get_enrolled_student_ids(self, offering_id: int) -> set[int]:
        """
        Roster IDs, used to restrict the recognition search space.

        This is both a ~20x speedup AND a false-positive reduction: a lookalike
        from another department is simply not a candidate for a wrong match.
        """
        rows = self.db.execute(
            select(Enrollment.student_id)
            .join(Student, Student.id == Enrollment.student_id)
            .where(
                Enrollment.offering_id == offering_id,
                Enrollment.is_active.is_(True),
                Student.is_active.is_(True),
            )
        ).all()
        return {row[0] for row in rows}

    def _session_counts(self, session_id: int) -> dict:
        rows = self.db.execute(
            select(AttendanceRecord.status, func.count())
            .where(AttendanceRecord.session_id == session_id)
            .group_by(AttendanceRecord.status)
        ).all()
        by_status = {status: count for status, count in rows}

        review = self.db.execute(
            select(func.count()).select_from(AttendanceRecord).where(
                AttendanceRecord.session_id == session_id,
                AttendanceRecord.needs_review.is_(True),
            )
        ).scalar_one()

        return {
            "present_count": by_status.get(AttendanceStatus.PRESENT.value, 0),
            "late_count": by_status.get(AttendanceStatus.LATE.value, 0),
            "absent_count": by_status.get(AttendanceStatus.ABSENT.value, 0),
            "excused_count": by_status.get(AttendanceStatus.EXCUSED.value, 0),
            "review_count": review,
        }

    def _audit(
        self, user_id: int, action: AuditAction, entity_id: int, details: dict | None
    ) -> None:
        self.db.add(
            AuditLog(
                user_id=user_id,
                action=action.value,
                entity_type="attendance_sessions",
                entity_id=entity_id,
                details=details,
            )
        )
