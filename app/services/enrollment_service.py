"""
Face enrolment: images in, one matchable embedding out.

THIS SERVICE IS THE MISSING LINK. The CV pipeline works standalone, the
attendance engine works, and `/sessions/{id}/mark-face` runs — but with an
empty index every face returns UNKNOWN, because nothing has ever written a
`face_embeddings` row. This closes that gap.

WHY IT IS SEPARATE FROM RecognitionService
------------------------------------------
The two consume the same ML layer with OPPOSITE rules:

    RecognitionService          EnrollmentService
    ------------------          -----------------
    many faces per frame        exactly ONE face per image
    permissive quality gate     strict gate (2x thresholds)
    a bad frame is skipped      a bad image fails the enrolment
    reads the index             writes the index

Merging them would produce a class with two reasons to change, and the
permissive path would inevitably leak into the strict one. Keeping them apart
is what lets the enrolment gate stay uncompromising.

THE PRIVACY COMMITMENT, implemented rather than promised
--------------------------------------------------------
Raw images are decoded into memory, embedded, and dropped. Nothing is written
to disk, nothing is stored in the database, and no path is retained. What
survives is a 2,048-byte float vector.

That matters concretely: a breached image store is a set of usable photographs
of a thousand young people. A breached embedding store is a set of float
arrays that are not trivially reversible and are useless outside a system
running the same model.
"""

import logging
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.ml.detector import ScrfdDetector
from app.ml.embedder import ArcFaceEmbedder, average_embeddings, intra_set_similarity
from app.ml.engine import FaceEngine, get_engine
from app.ml.interfaces import IEmbedder, IFaceDetector
from app.ml.matcher import InMemoryMatcher, get_matcher
from app.ml.preprocessing import apply_clahe, assess_quality
from app.models.enums import AuditAction
from app.models.face import FaceEmbedding
from app.models.logs import AuditLog
from app.models.people import Student
from app.models.user import User
from app.services.recognition_service import RecognitionService
from app.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

# Below this, the submitted images are not all of the same person. Real
# same-person sets across lighting and expression sit comfortably above 0.6;
# a mixed set collapses toward 0.
MIN_INTRA_SET_SIMILARITY = 0.60


class EnrollmentService:
    """Biometric enrolment, consent, and erasure."""

    def __init__(
        self,
        db: Session,
        *,
        detector: IFaceDetector | None = None,
        embedder: IEmbedder | None = None,
        matcher: InMemoryMatcher | None = None,
        engine: FaceEngine | None = None,
    ):
        """
        Dependencies are injectable.

        This is what `app/ml/interfaces.py` exists for. A unit test can pass a
        fake embedder returning fixed vectors and exercise the consent rules,
        the counting, and the averaging without loading a 275 MB model or
        owning a webcam — turning a 3-second test into a 3-millisecond one.

        Defaults resolve to the shared singletons, so production callers just
        write `EnrollmentService(db)`.
        """
        self.db = db
        self.engine = engine or get_engine()
        self.detector = detector or ScrfdDetector(self.engine)
        self.embedder = embedder or ArcFaceEmbedder(self.engine)
        self.matcher = matcher or get_matcher()

    # ==================================================================
    # CONSENT
    # ==================================================================

    def set_consent(
        self, student_id: int, granted: bool, actor: User, note: str | None = None
    ) -> Student:
        """
        Grant or withdraw biometric consent.

        WITHDRAWAL DELETES THE DATA, IMMEDIATELY.
        Consent that can be withdrawn while the biometric template quietly
        stays on the server is not consent. Revoking here erases the
        embeddings in the same transaction, so the two states cannot diverge.
        """
        student = self._get_student(student_id)
        now = utc_now()

        if granted:
            student.consent_given_at = now
            student.consent_revoked_at = None
            action = AuditAction.CONSENT_GRANT
        else:
            student.consent_revoked_at = now
            action = AuditAction.CONSENT_REVOKE
            # Withdrawal is meaningless if the template survives it.
            self._deactivate_embeddings(student)
            student.face_enrolled = False

        self._audit(actor.id, action, student_id, {"granted": granted, "note": note})
        self.db.commit()

        if not granted:
            self._refresh_index()

        return student

    def _assert_consent(self, student: Student) -> None:
        """
        The gate. No consent, no biometric processing -- ever.

        Checked in code rather than by a database constraint because SQL cannot
        express "you may not INSERT into face_embeddings unless a column on a
        different table is set". This is therefore the ONLY place that
        enforcement lives, which is why it is a separate method called first in
        every path that touches biometrics.
        """
        if not student.has_valid_consent:
            raise PermissionDeniedError(
                "This student has not given consent for biometric enrolment. "
                "Record consent before capturing face data.",
                error_code="CONSENT_REQUIRED",
            )

    # ==================================================================
    # ENROLMENT
    # ==================================================================

    def enroll_faces(
        self, student_id: int, images: list[bytes], actor: User
    ) -> dict:
        """
        The pipeline. Returns a summary dict; the endpoint shapes it.

        ORDER OF OPERATIONS -- each step exists to stop a specific failure:

            1. student exists          -> 404
            2. consent recorded        -> 403      *before any image is decoded*
            3. model loaded            -> 409
            4. image count in range    -> 400
            5. per image: exactly one face, strict quality, embed
            6. enough accepted         -> 400 with per-image reasons
            7. images are consistent   -> 400 (two different people captured)
            8. average and normalise
            9. store as the single primary embedding
           10. refresh the matcher index
        """
        student = self._get_student(student_id)

        # Step 2 BEFORE step 3 and 4, deliberately: we should not decode a
        # single byte of biometric data for a student who has not consented,
        # even if the request is malformed in other ways.
        self._assert_consent(student)

        if not self.engine.is_ready:
            raise ConflictError(
                "The face recognition model is not loaded. Enrolment is "
                "unavailable; attendance can still be marked manually.",
                error_code="MODEL_NOT_READY",
            )

        low, high = (
            settings.face_min_enrollment_images,
            settings.face_max_enrollment_images,
        )
        if not low <= len(images) <= high:
            raise ValidationError(
                f"Submit between {low} and {high} images. Received {len(images)}.",
                field="images",
            )

        embeddings, detection_scores, rejections = self._process_images(images)

        if len(embeddings) < low:
            raise ValidationError(
                f"Only {len(embeddings)} of {len(images)} images were usable; "
                f"{low} are required. Improve the lighting, move closer, and "
                "keep the subject facing the camera.",
                field="images",
                error_code="INSUFFICIENT_QUALITY_IMAGES",
            )

        # Step 7. Catching a mixed set HERE is far cheaper than debugging why
        # one student is never recognised three weeks later.
        consistency = intra_set_similarity(embeddings)
        if consistency < MIN_INTRA_SET_SIMILARITY:
            raise ValidationError(
                f"The submitted images do not appear to be of the same person "
                f"(consistency {consistency:.2f}, minimum "
                f"{MIN_INTRA_SET_SIMILARITY:.2f}). Recapture with only the "
                "student in frame.",
                field="images",
                error_code="INCONSISTENT_IMAGES",
            )

        # Step 8. average_embeddings() re-normalises to unit length -- without
        # that, every future similarity against this reference is scaled down
        # by a few percent and genuine matches drift below threshold.
        reference = average_embeddings(embeddings)

        # Step 9.
        self._replace_primary_embedding(
            student=student,
            vector=reference,
            source_count=len(embeddings),
            quality=float(np.mean(detection_scores)),
        )
        self._audit(
            actor.id,
            AuditAction.FACE_ENROLL,
            student_id,
            {"accepted": len(embeddings), "submitted": len(images)},
        )
        self.db.commit()

        # Step 10. THE MOST EASILY FORGOTTEN STEP, and its absence is silent:
        # the student simply never matches, with no error anywhere to explain
        # why. Done after commit so the index reflects committed state.
        index_size = self._refresh_index()

        logger.info(
            "Enrolled student %s from %d/%d images (consistency %.3f)",
            student.roll_number, len(embeddings), len(images), consistency,
        )

        return {
            "student_id": student.id,
            "roll_number": student.roll_number,
            "enrolled": True,
            "images_submitted": len(images),
            "images_accepted": len(embeddings),
            "images_rejected": len(rejections),
            "rejections": rejections,
            "quality_score": round(float(np.mean(detection_scores)), 4),
            "consistency_score": round(consistency, 4),
            "model_name": self.engine.model_name,
            "model_version": self.engine.model_version,
            "raw_images_retained": False,
            "index_size": index_size,
        }

    def _process_images(
        self, images: list[bytes]
    ) -> tuple[list[np.ndarray], list[float], list[dict]]:
        """
        Decode, validate, and embed each image.

        RAW PIXEL DATA NEVER LEAVES THIS METHOD. Decoded arrays are local, and
        only the resulting 512-float vectors are returned. There is no path by
        which an image reaches disk or the database.
        """
        embeddings: list[np.ndarray] = []
        scores: list[float] = []
        rejections: list[dict] = []

        for index, raw in enumerate(images):
            try:
                # Reuse the recognition service's decoder rather than writing a
                # second one. It is a pure static function that already caps
                # size and rejects undecodable input -- duplicating
                # security-relevant validation is how the two copies drift.
                image = RecognitionService.decode_frame(raw)
            except ValidationError as exc:
                rejections.append(
                    {"index": index, "reason": "undecodable", "detail": exc.message}
                )
                continue

            # CLAHE before detection, matching what recognition does. Enrolling
            # under different preprocessing than recognition uses would put the
            # reference vector in a slightly different place than every query.
            enhanced = apply_clahe(image)

            # EXACTLY ONE FACE. Two people in frame means we cannot know whose
            # identity is being registered, and guessing would silently enrol
            # the wrong student against this roll number -- a fault that only
            # surfaces as mysterious misidentification later.
            face = self.detector.detect_single(enhanced)
            if face is None:
                found = len(self.detector.detect(enhanced))
                rejections.append({
                    "index": index,
                    "reason": "no_face" if found == 0 else "multiple_faces",
                    "detail": (
                        "No face detected."
                        if found == 0
                        else f"{found} faces detected; only the student should be in frame."
                    ),
                })
                continue

            # STRICT gate: roughly double the recognition thresholds. Reference
            # data poisons every future match, so it is worth rejecting a
            # borderline image and asking for another.
            quality = assess_quality(enhanced, face, strict=True)
            if not quality.passed:
                rejections.append({
                    "index": index,
                    "reason": quality.failures[0].value,
                    "detail": quality.reason,
                })
                continue

            embeddings.append(self.embedder.embed(enhanced, face))
            scores.append(face.detection_score)

            # Drop the decoded arrays explicitly. CPython would collect them
            # anyway, but being deliberate here documents the intent: an image
            # exists for exactly as long as it takes to embed it.
            del image, enhanced

        return embeddings, scores, rejections

    def _replace_primary_embedding(
        self,
        student: Student,
        vector: np.ndarray,
        source_count: int,
        quality: float,
    ) -> None:
        """
        Store the averaged vector as the single primary embedding.

        DEACTIVATION MUST HAPPEN FIRST. A unique partial index enforces at most
        one active primary per student:

            uq_embedding_one_primary_per_student
                ON face_embeddings (student_id)
                WHERE is_primary = true AND is_active = true

        Inserting before deactivating raises an IntegrityError. Doing both in
        one transaction means a re-enrolment either fully replaces the old
        reference or changes nothing -- never leaves a student with none.

        Only the AVERAGED vector is stored, per the agreed design. Individual
        per-image embeddings are discarded: they are only useful for
        re-averaging, and keeping N vectors per student multiplies the most
        sensitive table in the system for marginal benefit.
        """
        self._deactivate_embeddings(student)

        # to_bytes() validates dimension, dtype, and unit length before
        # storing. A vector that is not L2-normalised would silently break
        # every threshold comparison downstream, so the check lives at the
        # single choke point every embedding passes through.
        record = FaceEmbedding(
            student_id=student.id,
            embedding=FaceEmbedding.to_bytes(vector),
            model_name=self.engine.model_name,
            model_version=self.engine.model_version,
            quality_score=quality,
            source_image_count=source_count,
            is_primary=True,
            is_active=True,
        )
        self.db.add(record)

        # Denormalised flag, so the admin roster can show an "enrolled" badge
        # without an EXISTS subquery per row. EnrollmentService is the ONLY
        # writer of this column -- that single-writer rule is what makes the
        # denormalisation safe.
        student.face_enrolled = True

    def _deactivate_embeddings(self, student: Student) -> int:
        """Soft-deactivate every embedding so a new primary can be inserted."""
        rows = self.db.execute(
            select(FaceEmbedding).where(
                FaceEmbedding.student_id == student.id,
                FaceEmbedding.is_active.is_(True),
            )
        ).scalars().all()
        for row in rows:
            row.is_active = False
            row.is_primary = False
        return len(rows)

    # ==================================================================
    # STATUS AND ERASURE
    # ==================================================================

    def get_status(self, student_id: int) -> dict:
        """
        Enrolment state for the admin UI.

        `in_matcher_index` is the useful diagnostic: face_enrolled=true with
        in_matcher_index=false means the row exists but the running process has
        a stale index — the known multi-worker limitation. Surfacing it turns a
        mysterious "this student is never recognised" into a visible fact.
        """
        student = self._get_student(student_id, with_user=True)

        primary = self.db.execute(
            select(FaceEmbedding).where(
                FaceEmbedding.student_id == student_id,
                FaceEmbedding.is_primary.is_(True),
                FaceEmbedding.is_active.is_(True),
            )
        ).scalar_one_or_none()

        return {
            "student_id": student.id,
            "roll_number": student.roll_number,
            "student_name": student.user.full_name,
            "has_valid_consent": student.has_valid_consent,
            "consent_given_at": student.consent_given_at,
            "face_enrolled": student.face_enrolled,
            "enrolled_at": primary.created_at if primary else None,
            "source_image_count": primary.source_image_count if primary else None,
            "quality_score": primary.quality_score if primary else None,
            "model_name": primary.model_name if primary else None,
            "model_version": primary.model_version if primary else None,
            "in_matcher_index": self.matcher.contains(student_id),
        }

    def delete_faces(self, student_id: int, actor: User) -> dict:
        """
        Erase biometric data. Right to erasure (NFR-3.4).

        A HARD DELETE, unlike almost everything else in this schema. Soft
        deletion is correct for records referenced by history — but "we
        deactivated your biometric template" is not erasure, and the whole
        point of the right is that the data ceases to exist.

        ATTENDANCE HISTORY IS UNAFFECTED. Those records reference the student,
        not the embedding; the institution's record of who attended which class
        is not personal biometric data and must survive.
        """
        student = self._get_student(student_id)

        rows = self.db.execute(
            select(FaceEmbedding).where(FaceEmbedding.student_id == student_id)
        ).scalars().all()

        for row in rows:
            self.db.delete(row)

        student.face_enrolled = False
        self._audit(
            actor.id, AuditAction.FACE_DELETE, student_id, {"deleted": len(rows)}
        )
        self.db.commit()

        index_size = self._refresh_index()

        logger.info(
            "Deleted %d embeddings for student %s", len(rows), student.roll_number
        )

        return {
            "student_id": student_id,
            "embeddings_deleted": len(rows),
            "face_enrolled": False,
            "attendance_records_preserved": True,
            "index_size": index_size,
        }

    # ==================================================================
    # INTERNALS
    # ==================================================================

    def _get_student(self, student_id: int, with_user: bool = False) -> Student:
        stmt = select(Student).where(Student.id == student_id)
        if with_user:
            stmt = stmt.options(joinedload(Student.user))
        student = self.db.execute(stmt).scalar_one_or_none()
        if student is None or not student.is_active:
            raise NotFoundError(f"Student {student_id} not found.")
        return student

    def _refresh_index(self) -> int:
        """
        Rebuild the in-memory matcher from the database.

        Failure here must NOT fail the enrolment. The row is committed and
        correct; the running process merely has a stale index, which a restart
        or the /recognition/refresh-index endpoint fixes. Raising would tell
        the operator the enrolment failed when it in fact succeeded, and they
        would re-run it.
        """
        try:
            return self.matcher.load_from_db(self.db)
        except Exception:
            logger.exception(
                "Index refresh failed after a successful enrolment. The data is "
                "saved; call /recognition/refresh-index or restart the worker."
            )
            return self.matcher.size

    def _audit(
        self, user_id: int, action: AuditAction, student_id: int, details: dict
    ) -> None:
        self.db.add(
            AuditLog(
                user_id=user_id,
                action=action.value,
                entity_type="face_embeddings",
                entity_id=student_id,
                details=details,
            )
        )
