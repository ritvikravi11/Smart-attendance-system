"""
API contracts for student biometric operations.

Reminder of the split this project maintains:
    models/  describes the DATABASE
    schemas/ describes the API

They are deliberately different objects. `FaceEmbedding` has an `embedding`
column holding 2,048 bytes of biometric template; nothing in this file exposes
it, and that omission is the security boundary made explicit rather than left
to chance.

WHAT IS DELIBERATELY ABSENT FROM EVERY RESPONSE BELOW
-----------------------------------------------------
  - the embedding bytes themselves (NFR-2.4: embeddings never leave the server)
  - any raw or processed image
  - file paths

A caller can learn WHETHER a student is enrolled and HOW GOOD the enrolment is.
They cannot obtain the biometric template.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# CONSENT
# ---------------------------------------------------------------------------


class ConsentRequest(BaseModel):
    """
    Record or withdraw biometric consent.

    WHY THIS IS AN EXPLICIT ENDPOINT AND NOT A CHECKBOX ON THE STUDENT FORM
    ----------------------------------------------------------------------
    Under India's DPDP Act 2023 and GDPR Article 9, biometric data used to
    identify a person is a special category requiring explicit, informed,
    recorded consent. "Explicit" means a deliberate act, not a pre-ticked box
    bundled into a registration form the student skimmed.

    A separate endpoint forces a separate action, and stores the moment it
    happened.
    """

    granted: bool = Field(
        ...,
        description="True to grant consent, False to withdraw it.",
    )
    note: str | None = Field(
        None,
        max_length=255,
        description="Optional record of how consent was obtained.",
    )


class ConsentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: int
    roll_number: str
    consent_given_at: datetime | None
    consent_revoked_at: datetime | None
    has_valid_consent: bool
    face_enrolled: bool


# ---------------------------------------------------------------------------
# ENROLMENT
# ---------------------------------------------------------------------------


class ImageRejection(BaseModel):
    """
    Why one submitted image was not usable.

    WHY THE INDEX AND A SPECIFIC REASON, rather than a count of failures
    -------------------------------------------------------------------
    "7 of 12 images rejected" tells the operator nothing they can act on.
    "image 3: too_blurry, image 4: no face detected" tells them to hold the
    camera still and re-shoot those two.

    The `reason` values come from `QualityFailure` in the ML layer, so the same
    vocabulary appears here, in the live camera overlay, and in the logs.
    """

    index: int = Field(..., description="Zero-based position in the upload.")
    reason: str = Field(..., description="e.g. too_small, too_blurry, extreme_pose")
    detail: str | None = None


class FaceEnrollmentResponse(BaseModel):
    """
    Outcome of an enrolment attempt.

    Note what this returns and what it does not: counts, quality figures, and
    the model identity — never the embedding.
    """

    student_id: int
    roll_number: str
    enrolled: bool

    images_submitted: int
    images_accepted: int
    images_rejected: int
    rejections: list[ImageRejection] = []

    quality_score: float | None = Field(
        None,
        description="Mean detection confidence across accepted images (0-1).",
    )
    consistency_score: float | None = Field(
        None,
        description=(
            "Mean pairwise similarity between accepted images. Low values mean "
            "the images are not all of the same person."
        ),
    )

    model_name: str | None = None
    model_version: str | None = None

    # Proof the privacy commitment was honoured, returned to the caller so it
    # is visible rather than merely promised in a document.
    raw_images_retained: bool = Field(
        False,
        description="Always false. Images are processed in memory and discarded.",
    )

    index_size: int = Field(
        ...,
        description="Students in the matcher index after this enrolment.",
    )


class FaceStatusResponse(BaseModel):
    """Enrolment state, for the admin UI to render a badge and decide next steps."""

    student_id: int
    roll_number: str
    student_name: str

    has_valid_consent: bool
    consent_given_at: datetime | None

    face_enrolled: bool
    enrolled_at: datetime | None = None
    source_image_count: int | None = None
    quality_score: float | None = None

    model_name: str | None = None
    model_version: str | None = None

    in_matcher_index: bool = Field(
        ...,
        description=(
            "Whether this student is currently loaded in the live index. False "
            "with face_enrolled=true means the index is stale and needs a refresh."
        ),
    )


class FaceDeletionResponse(BaseModel):
    """
    Result of erasing a student's biometric data.

    Supports the right to erasure (NFR-3.4). `attendance_records_preserved`
    exists to make the distinction explicit: deleting biometric data must NOT
    delete attendance history, which is an institutional record rather than
    personal biometric data.
    """

    student_id: int
    embeddings_deleted: int
    face_enrolled: bool = False
    attendance_records_preserved: bool = True
    index_size: int
