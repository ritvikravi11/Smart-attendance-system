"""
Recognition orchestration: frame in, attendance records out.

WHY THIS IS A SEPARATE SERVICE FROM AttendanceService
-----------------------------------------------------
Recognition determines WHO. Attendance determines WHAT TO RECORD.

Merging them would produce one class that knows both computer vision and
business rules -- a Single Responsibility violation. Keeping them separate
means `/recognition/identify` (read-only, no writes) and
`/sessions/{id}/mark-face` (recognise AND persist) share the same recognition
code with zero duplication, and the read-only endpoint carries no risk of
accidentally writing attendance.

This is also the only service-to-service call in the system, and it points
one way: Recognition -> Attendance, never the reverse.
"""

import logging
import time

import cv2
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from dataclasses import dataclass, field

from app.core.exceptions import ConflictError, ValidationError
from app.ml.detector import ScrfdDetector
from app.ml.embedder import ArcFaceEmbedder
from app.ml.engine import get_engine
from app.ml.interfaces import DetectedFace, MatchResult
from app.ml.matcher import get_matcher
from app.ml.preprocessing import assess_quality, preprocess_frame, scale_bbox
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.enums import MarkingMethod, SessionStatus
from app.models.logs import RecognitionLog
from app.models.people import Student
from app.models.user import User
from app.services.attendance_service import AttendanceService

logger = logging.getLogger(__name__)


@dataclass
class FaceOutcome:
    """
    One detected face and what happened to it.

    CODE SMELL FIXED. This was a plain dict carrying a `"_match"` key holding a
    MatchResult object, popped before serialisation. Two problems:

      1. FRAGILE. Forget the pop in ONE code path and FastAPI tries to
         serialise a MatchResult, which fails at response time -- a 500 on an
         endpoint whose logic succeeded.
      2. INVISIBLE CONTRACT. Nothing declared which keys exist. A typo
         (`entry["studnet_id"]`) is a silent None, not an error.

    A dataclass makes the internal field genuinely internal: `to_dict()`
    controls exactly what crosses the API boundary, and it cannot be forgotten
    because it is the only way out.
    """

    bbox: list[int]
    detection_score: float
    quality_passed: bool
    quality_reason: str

    student_id: int | None = None
    student_name: str | None = None
    roll_number: str | None = None
    confidence: float = 0.0
    is_unknown: bool = False
    needs_review: bool = False

    marked: bool = False
    already_marked: bool = False
    rejected_reason: str | None = None

    # Internal. Never serialised -- it holds a MatchResult, which Pydantic
    # cannot encode, and the API has no business seeing it.
    match: "MatchResult | None" = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """The API representation. `match` is structurally excluded."""
        return {
            "bbox": self.bbox,
            "detection_score": self.detection_score,
            "quality_passed": self.quality_passed,
            "quality_reason": self.quality_reason,
            "student_id": self.student_id,
            "student_name": self.student_name,
            "roll_number": self.roll_number,
            "confidence": self.confidence,
            "is_unknown": self.is_unknown,
            "needs_review": self.needs_review,
            "marked": self.marked,
            "already_marked": self.already_marked,
            "rejected_reason": self.rejected_reason,
        }

# Guard against decompression-bomb style uploads and accidental 4K frames.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class RecognitionService:
    """Runs the CV pipeline and, optionally, writes attendance."""

    def __init__(self, db: Session):
        self.db = db
        self.engine = get_engine()
        self.detector = ScrfdDetector(self.engine)
        self.embedder = ArcFaceEmbedder(self.engine)
        self.matcher = get_matcher()
        self.attendance = AttendanceService(db)

    # ------------------------------------------------------------------
    # Frame decoding
    # ------------------------------------------------------------------

    @staticmethod
    def decode_frame(data: bytes) -> np.ndarray:
        """
        Turn uploaded bytes into a BGR array.

        Validation here is a security boundary, not a formality: this endpoint
        accepts arbitrary bytes from the network. A size cap prevents memory
        exhaustion, and a decode check prevents a malformed file propagating
        as a confusing NoneType error deeper in the pipeline.
        """
        if not data:
            raise ValidationError("Empty image upload.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValidationError(
                f"Image exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit."
            )

        array = np.frombuffer(data, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)

        if image is None:
            raise ValidationError(
                "Could not decode the image. Send JPEG or PNG."
            )
        return image

    # ------------------------------------------------------------------
    # Pure recognition (no writes)
    # ------------------------------------------------------------------

    def identify(
        self,
        frame: np.ndarray,
        *,
        candidate_ids: set[int] | None = None,
        session_id: int | None = None,
        log: bool = True,
    ) -> tuple[list["FaceOutcome"], int]:
        """
        Detect and identify every face in a frame. Writes NO attendance.

        Returns (results, processing_ms).

        This is steps 3-5 of the decision chain in the business logic doc:
        quality gate, then similarity thresholds. Enrolment checking and
        persistence happen in identify_and_mark().
        """
        if not self.engine.is_ready:
            raise ConflictError(
                "Recognition model is not loaded.",
                error_code="MODEL_NOT_READY",
            )

        start = time.perf_counter()

        # Preprocess at reduced scale, then map coordinates back so the
        # frontend draws boxes on the ORIGINAL frame it is displaying.
        processed, scale = preprocess_frame(frame)
        faces = self.detector.detect(processed)

        results: list[FaceOutcome] = []

        for face in faces:
            quality = assess_quality(processed, face)
            original_bbox = scale_bbox(face.bbox, scale)

            entry = FaceOutcome(
                bbox=original_bbox.to_list(),
                detection_score=round(face.detection_score, 4),
                quality_passed=quality.passed,
                quality_reason=quality.reason,
            )

            # STEP 3: quality gate. Rejected faces are reported but NOT
            # embedded -- embedding costs ~285ms, and a 40px blurry face
            # would produce a low-information vector that might match the
            # WRONG student. Rejecting is both cheaper and safer.
            if not quality.passed:
                results.append(entry)
                continue

            embedding = self.embedder.embed(processed, face)

            # STEPS 4-5: threshold bands, restricted to the roster
            match = self.matcher.search(embedding, candidate_ids=candidate_ids)

            entry.confidence = round(match.confidence, 4)
            entry.is_unknown = match.is_unknown
            entry.needs_review = match.needs_review
            entry.match = match

            if match.student_id is not None:
                student = self._get_student(match.student_id)
                if student is not None:
                    entry.student_id = student.id
                    entry.student_name = student.user.full_name
                    entry.roll_number = student.roll_number

            if log:
                self._log_recognition(
                    session_id=session_id,
                    match=match,
                    face=face,
                    quality_width=quality.face_width,
                    was_marked=False,
                )

            results.append(entry)

        if log and results:
            self.db.commit()

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return results, elapsed_ms

    # ------------------------------------------------------------------
    # Recognition + persistence
    # ------------------------------------------------------------------

    def identify_and_mark(
        self, session_id: int, frame: np.ndarray, user: User
    ) -> dict:
        """
        The full automatic marking path -- the decision chain end to end.

        1. session open?           -> 409
        2. faculty owns offering?  -> 403      (assert_can_access_session)
        3. quality gate            -> skip silently
        4. similarity >= 0.40?     -> else UNKNOWN, log, write nothing
        5. similarity >= 0.50?     -> else mark with needs_review
        6. enrolled here?          -> else reject
        7. already marked?         -> return existing, no duplicate
        8. past late threshold?    -> LATE else PRESENT
        9. write record + log
        """
        # Steps 1-2
        session = self.attendance.assert_can_access_session(session_id, user)

        if session.status != SessionStatus.OPEN.value:
            raise ConflictError(
                f"Cannot mark attendance: session is {session.status}.",
                error_code="SESSION_NOT_OPEN",
            )

        # Roster restriction: fewer candidates means faster AND more accurate
        roster_ids = self.attendance.get_enrolled_student_ids(session.offering_id)

        results, elapsed_ms = self.identify(
            frame,
            candidate_ids=roster_ids,
            session_id=session_id,
            log=False,  # logged below with the marked flag included
        )

        newly_marked = 0

        for entry in results:
            match = entry.match

            if not entry.quality_passed:
                entry.rejected_reason = entry.quality_reason
                continue

            if match is None or match.is_unknown or match.student_id is None:
                # Step 4: UNKNOWN. No record written. Logged as an impostor
                # observation, which is the data you need for threshold
                # calibration and unknown-person alerting.
                if match is not None:
                    self._log_recognition(session_id, match, None, 0, False)
                continue

            # Steps 6-8, all inside mark_present()
            try:
                record, created = self.attendance.mark_present(
                    session_id=session_id,
                    student_id=match.student_id,
                    confidence=match.confidence,
                    method=MarkingMethod.FACE,
                    needs_review=match.needs_review,
                )
                entry.marked = created
                entry.already_marked = not created
                if created:
                    newly_marked += 1

            except ValidationError as exc:
                # Step 6 failure: recognised, but not enrolled in THIS class.
                # A student from Section B must not be marked into Section A.
                entry.rejected_reason = exc.message
                logger.info(
                    "Student %s recognised but not enrolled in offering %s",
                    match.student_id, session.offering_id,
                )

            self._log_recognition(
                session_id, match, None, 0, was_marked=entry.marked
            )

        self.db.commit()

        total_marked = self.db.execute(
            select(func.count())
            .select_from(AttendanceRecord)
            .where(AttendanceRecord.session_id == session_id)
        ).scalar_one()

        return {
            # to_dict() is the ONLY way out, so the internal MatchResult
            # cannot leak into a response by omission.
            "faces": [e.to_dict() for e in results],
            "processing_ms": elapsed_ms,
            "newly_marked": newly_marked,
            "total_marked": total_marked,
            "enrolled_count": len(roster_ids),
        }

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    def refresh_index(self) -> int:
        """
        Rebuild the in-memory index from the database.

        Called at startup and after every enrolment. Forgetting to call it
        after enrolment means the new student is invisible until restart --
        which is exactly the stale-index bug documented in the deployment
        diagram for multi-worker setups.
        """
        count = self.matcher.load_from_db(self.db)
        logger.info("Embedding index refreshed: %d students", count)
        return count

    def health(self) -> dict:
        engine_health = self.engine.health()
        index_health = self.matcher.health()
        return {
            "model_loaded": engine_health["ready"],
            "model_name": engine_health["model_name"],
            "model_version": engine_health["model_version"],
            "index_size": index_health["size"],
            "index_memory_kb": index_health["memory_kb"],
            "match_threshold": index_health["match_threshold"],
            "review_threshold": index_health["review_threshold"],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_student(self, student_id: int) -> Student | None:
        return self.db.execute(
            select(Student).options(joinedload(Student.user))
            .where(Student.id == student_id)
        ).scalar_one_or_none()

    def _log_recognition(
        self,
        session_id: int | None,
        match: MatchResult,
        face: DetectedFace | None,
        quality_width: int,
        was_marked: bool,
    ) -> None:
        """
        Record every recognition attempt, matched or not.

        This table is your MEASUREMENT INSTRUMENT. Without it, the threshold
        stays a guess and accuracy claims are unprovable. With it, you can
        plot genuine vs impostor distributions and defend your numbers.
        """
        self.db.add(
            RecognitionLog(
                session_id=session_id,
                matched_student_id=match.student_id,
                confidence=match.confidence,
                is_unknown=match.is_unknown,
                was_marked=was_marked,
                detection_score=face.detection_score if face else None,
                face_width_px=quality_width or (face.bbox.width if face else None),
            )
        )
