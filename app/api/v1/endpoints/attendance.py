"""
Attendance session and recognition endpoints.

Thin handlers, as in the auth module: validate, authorise, delegate, shape.
All business rules live in the services.
"""

from fastapi import APIRouter, File, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_staff
from app.models.enums import AttendanceStatus
from app.schemas.attendance import (
    AbsenteeEntry,
    AttendanceRecordResponse,
    DailyPoint,
    MonthlyPoint,
    OfferingDashboard,
    StudentPercentage,
    SubjectPercentage,
    DefaulterEntry,
    FacultyAttendanceSummary,
    ManualMarkRequest,
    MarkFaceResponse,
    RecognitionHealthResponse,
    RecognitionResponse,
    RecordUpdateRequest,
    SessionCloseResponse,
    SessionCreate,
    SessionResponse,
    SessionRosterResponse,
    StudentAttendanceSummary,
)
from app.schemas.auth import ErrorResponse, MessageResponse
from app.services.analytics_service import AnalyticsService
from app.services.attendance_service import AttendanceService
from app.services.chart_analytics_service import ChartAnalyticsService
from app.services.recognition_service import RecognitionService

router = APIRouter(tags=["Attendance"])


# ===========================================================================
# SESSIONS
# ===========================================================================


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open an attendance session",
    responses={
        403: {"model": ErrorResponse, "description": "You do not teach this offering"},
        409: {"model": ErrorResponse, "description": "A session is already open"},
    },
)
def open_session(payload: SessionCreate, user: CurrentUser, db: DbSession):
    """
    Open a session for one offering.

    Only one session may be open per offering at a time -- a second attempt
    returns 409. Faculty may open sessions only for offerings they teach.
    """
    service = AttendanceService(db)
    session = service.open_session(
        offering_id=payload.offering_id,
        user=user,
        session_date=payload.session_date,
        room=payload.room,
        late_threshold_minutes=payload.late_threshold_minutes,
        notes=payload.notes,
    )
    roster = service.get_enrolled_student_ids(session.offering_id)
    return SessionResponse(
        **{c.name: getattr(session, c.name) for c in session.__table__.columns
           if c.name in SessionResponse.model_fields},
        enrolled_count=len(roster),
    )


@router.post(
    "/sessions/{session_id}/close",
    response_model=SessionCloseResponse,
    summary="Close a session and auto-mark absentees",
)
def close_session(session_id: int, user: CurrentUser, db: DbSession):
    """
    Close the session.

    Every enrolled student without a record is marked ABSENT in one bulk
    insert -- absence is derived as a set difference, never observed.
    Students who enrolled after this session's date are excluded.
    """
    service = AttendanceService(db)
    return SessionCloseResponse(**service.close_session(session_id, user))


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=MessageResponse,
    summary="Cancel a session (excluded from percentages)",
)
def cancel_session(
    session_id: int,
    user: CurrentUser,
    db: DbSession,
    reason: str = Query("", max_length=200),
):
    """Cancelled sessions are excluded from attendance percentages entirely."""
    AttendanceService(db).cancel_session(session_id, user, reason)
    return MessageResponse(message="Session cancelled.")


@router.post(
    "/sessions/{session_id}/reopen",
    response_model=SessionResponse,
    summary="Reopen a closed session (admin only)",
)
def reopen_session(session_id: int, user: CurrentUser, db: DbSession):
    """
    Admin-only, audit-logged. Removes auto-generated absent records but
    preserves any records a human marked by hand.
    """
    service = AttendanceService(db)
    session = service.reopen_session(session_id, user)
    return SessionResponse(
        **{c.name: getattr(session, c.name) for c in session.__table__.columns
           if c.name in SessionResponse.model_fields}
    )


@router.get(
    "/sessions/{session_id}/roster",
    response_model=SessionRosterResponse,
    summary="Live roster with per-student status",
)
def get_roster(session_id: int, user: CurrentUser, db: DbSession):
    """
    Every enrolled student, marked or not.

    Unmarked students appear with status=null, which is what lets the faculty
    UI show "12 of 40 marked" and offer one-click manual marking without a
    second request.
    """
    return SessionRosterResponse(**AttendanceService(db).get_roster(session_id, user))


# ===========================================================================
# MARKING
# ===========================================================================


@router.post(
    "/sessions/{session_id}/mark-face",
    response_model=MarkFaceResponse,
    summary="Recognise faces in a frame AND write attendance",
    responses={
        409: {"model": ErrorResponse, "description": "Session not open"},
    },
)
async def mark_by_face(
    session_id: int,
    user: CurrentUser,
    db: DbSession,
    frame: UploadFile = File(..., description="JPEG or PNG frame from the camera"),
):
    """
    The automatic marking endpoint. Called at ~2 fps by the faculty UI.

    IDEMPOTENT: a student standing in frame for ten seconds generates twenty
    calls; the first creates a record and the other nineteen are harmless
    no-ops returning `already_marked: true`. That property is what makes a
    continuous camera stream safe against this endpoint.

    Recognition is restricted to students enrolled in this offering, which is
    both faster and less prone to false positives.
    """
    data = await frame.read()
    service = RecognitionService(db)
    image = service.decode_frame(data)
    return MarkFaceResponse(**service.identify_and_mark(session_id, image, user))


@router.post(
    "/sessions/{session_id}/mark-manual",
    response_model=AttendanceRecordResponse,
    summary="Mark or correct a student by hand",
)
def mark_manual(
    session_id: int, payload: ManualMarkRequest, user: CurrentUser, db: DbSession
):
    """
    The fallback that makes the system safe to deploy.

    If the camera fails, if recognition misses someone, if a student's face
    is not enrolled -- attendance still happens. The system assists faculty;
    it never replaces their judgement.
    """
    service = AttendanceService(db)
    record = service.mark_manual(
        session_id, payload.student_id, payload.status, user, payload.notes
    )
    return AttendanceRecordResponse.model_validate(record)


@router.put(
    "/attendance/{record_id}",
    response_model=AttendanceRecordResponse,
    summary="Correct an existing attendance record",
)
def update_record(
    record_id: int, payload: RecordUpdateRequest, user: CurrentUser, db: DbSession
):
    """
    Override a record.

    Corrections of face-marked records are your false-positive count -- the
    accuracy metric for your project report. Every one is recorded with who
    changed it and when.
    """
    service = AttendanceService(db)
    record = service.update_record(record_id, payload.status, user, payload.notes)
    return AttendanceRecordResponse.model_validate(record)


# ===========================================================================
# RECOGNITION (read-only)
# ===========================================================================


@router.post(
    "/recognition/identify",
    response_model=RecognitionResponse,
    summary="Identify faces WITHOUT writing attendance",
)
async def identify(
    user: CurrentUser,
    db: DbSession,
    frame: UploadFile = File(...),
    offering_id: int | None = Query(
        None, description="Restrict the search to this offering's roster"
    ),
):
    """
    Pure recognition. Writes no attendance.

    Deliberately separate from mark-face: an endpoint that mutates state
    should always be distinguishable from one that does not. This one is safe
    for testing, debugging, and future features (visitor identification, exam
    hall verification) with no risk of touching attendance data.
    """
    data = await frame.read()
    service = RecognitionService(db)
    image = service.decode_frame(data)

    candidates = None
    if offering_id is not None:
        AttendanceService(db).assert_can_access_offering(offering_id, user)
        candidates = AttendanceService(db).get_enrolled_student_ids(offering_id)

    faces, elapsed = service.identify(image, candidate_ids=candidates)

    # No manual stripping. FaceOutcome.to_dict() decides what crosses the
    # boundary, so a future code path cannot forget to remove internal state.
    return RecognitionResponse(
        faces=[f.to_dict() for f in faces],
        processing_ms=elapsed,
        index_size=service.matcher.size,
    )


@router.get(
    "/recognition/health",
    response_model=RecognitionHealthResponse,
    summary="Model and index status",
)
def recognition_health(user: CurrentUser, db: DbSession):
    """Diagnostic: is the model loaded, how many students are indexed."""
    return RecognitionHealthResponse(**RecognitionService(db).health())


@router.post(
    "/recognition/refresh-index",
    response_model=MessageResponse,
    summary="Rebuild the embedding index from the database",
)
def refresh_index(user: CurrentUser, db: DbSession):
    """
    Manual index rebuild.

    Normally automatic after enrolment. Exposed for the multi-worker case
    where one process has a stale index -- the known limitation documented in
    the deployment diagram.
    """
    count = RecognitionService(db).refresh_index()
    return MessageResponse(message=f"Index rebuilt with {count} students.")


# ===========================================================================
# ANALYTICS
# ===========================================================================


@router.get(
    "/students/{student_id}/attendance",
    response_model=StudentAttendanceSummary,
    summary="Subject-wise attendance for one student",
)
def student_attendance(student_id: int, user: CurrentUser, db: DbSession):
    """
    Per-subject breakdown plus an overall figure.

    A student may view ONLY their own record -- horizontal privilege
    escalation defence. Faculty and admin may view any.

    `percentage` is null when no eligible sessions have occurred: no
    percentage exists yet. The UI shows a dash, not 0%.
    """
    service = AnalyticsService(db)
    return StudentAttendanceSummary(**service.student_summary(student_id, user))


@router.get(
    "/faculty/{faculty_id}/attendance",
    response_model=FacultyAttendanceSummary,
    summary="Faculty-wise attendance across all their offerings",
)
def faculty_attendance(faculty_id: int, user: CurrentUser, db: DbSession):
    """Class averages, session counts, and defaulter counts per offering."""
    service = AnalyticsService(db)
    return FacultyAttendanceSummary(**service.faculty_summary(faculty_id, user))


@router.get(
    "/offerings/{offering_id}/defaulters",
    response_model=list[DefaulterEntry],
    dependencies=[],
    summary="Students below the attendance threshold",
)
def defaulters(
    offering_id: int,
    user: CurrentUser,
    db: DbSession,
    threshold: float | None = Query(None, ge=0, le=100),
):
    """
    Students at risk, with `shortfall_sessions` -- how many consecutive
    future classes they must attend to climb back above the threshold.

    That number is what students actually want to know, and it is more
    actionable than a bare percentage.
    """
    AttendanceService(db).assert_can_access_offering(offering_id, user)
    service = AnalyticsService(db)
    return [DefaulterEntry(**d) for d in service.get_defaulters(offering_id, threshold)]


# ===========================================================================
# CHART ANALYTICS
# ===========================================================================


@router.get(
    "/analytics/offerings/{offering_id}/dashboard",
    response_model=OfferingDashboard,
    summary="All analytics for one class in a single snapshot",
)
def offering_dashboard(offering_id: int, user: CurrentUser, db: DbSession):
    """
    Everything the analytics page renders, in one request.

    Bundled rather than split into five endpoints so the charts are guaranteed
    to agree with each other. Five separate calls could interleave with a
    session closing, leaving the daily chart including a session the student
    percentages do not -- numbers that silently contradict each other on screen.
    """
    AttendanceService(db).assert_can_access_offering(offering_id, user)
    return OfferingDashboard(**ChartAnalyticsService(db).offering_dashboard(offering_id))


@router.get(
    "/analytics/offerings/{offering_id}/daily",
    response_model=list[DailyPoint],
    summary="Attendance per session over recent days",
)
def daily_analytics(
    offering_id: int,
    user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
):
    """
    One point per CLOSED session, not per calendar day.

    Days with no class produce no point. Emitting zeros for weekends and
    holidays would draw a sawtooth that reads as collapsing attendance when
    in fact nothing happened.
    """
    AttendanceService(db).assert_can_access_offering(offering_id, user)
    return [DailyPoint(**p) for p in ChartAnalyticsService(db).daily_series(offering_id, days=days)]


@router.get(
    "/analytics/offerings/{offering_id}/monthly",
    response_model=list[MonthlyPoint],
    summary="Attendance aggregated by month",
)
def monthly_analytics(
    offering_id: int,
    user: CurrentUser,
    db: DbSession,
    months: int = Query(6, ge=1, le=24),
):
    """
    Monthly rate computed from summed totals, never averaged from daily rates.
    Averaging percentages would weight a 5-student session equally with a
    40-student one.
    """
    AttendanceService(db).assert_can_access_offering(offering_id, user)
    return [MonthlyPoint(**p) for p in ChartAnalyticsService(db).monthly_series(offering_id, months=months)]


@router.get(
    "/analytics/offerings/{offering_id}/students",
    response_model=list[StudentPercentage],
    summary="Every enrolled student's attendance percentage",
)
def student_percentages(offering_id: int, user: CurrentUser, db: DbSession):
    """
    ALL students, not only those below threshold -- a distribution built from
    defaulters alone would show only the left tail.
    """
    AttendanceService(db).assert_can_access_offering(offering_id, user)
    return [StudentPercentage(**s) for s in ChartAnalyticsService(db).student_percentages(offering_id)]


@router.get(
    "/analytics/faculty/{faculty_id}/subjects",
    response_model=list[SubjectPercentage],
    summary="Class average per subject taught",
)
def subject_percentages(faculty_id: int, user: CurrentUser, db: DbSession):
    """Compare across the offerings one faculty member teaches."""
    AnalyticsService(db).faculty_summary(faculty_id, user)  # reuses the authz check
    return [SubjectPercentage(**s) for s in ChartAnalyticsService(db).subject_percentages(faculty_id)]


@router.get(
    "/analytics/offerings/{offering_id}/absentees",
    response_model=list[AbsenteeEntry],
    summary="Students ranked by classes missed",
)
def top_absentees(
    offering_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(10, ge=1, le=100),
):
    """
    Ranked by ABSENCE COUNT, not percentage -- deliberately a different
    question from the defaulter list.

    A student at 50% who missed 1 of 2 classes is at risk. A student at 67%
    who missed 15 of 45 is not turning up. The first is a threshold problem;
    the second is a pastoral one.
    """
    AttendanceService(db).assert_can_access_offering(offering_id, user)
    return [AbsenteeEntry(**a) for a in ChartAnalyticsService(db).top_absentees(offering_id, limit=limit)]
