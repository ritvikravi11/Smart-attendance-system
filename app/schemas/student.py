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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import EmailStr, field_validator
import re


class StudentCreateRequest(BaseModel):
    """Fields required to create a student and their login identity."""

    full_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    roll_number: str = Field(..., min_length=1, max_length=30)
    department_id: int = Field(..., ge=1)
    semester: int = Field(..., ge=1, le=8)
    phone: str | None = Field(None, max_length=15)
    admission_year: int | None = Field(None, ge=1900, le=2200)

    @field_validator("full_name", "roll_number")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("roll_number")
    @classmethod
    def normalize_roll_number(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{0,29}", value):
            raise ValueError("Roll number may contain only letters, numbers, '.', '_' , '/' or '-'.")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.fullmatch(r"\+?[0-9 ()-]{7,15}", value):
            raise ValueError("Phone number format is invalid.")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: int
    full_name: str
    email: EmailStr
    roll_number: str
    department_id: int
    department_name: str
    semester: int
    phone: str | None
    admission_year: int | None
    face_enrolled: bool


class StudentListResponse(BaseModel):
    students: list[StudentResponse]
    total: int


class DepartmentResponse(BaseModel):
    id: int
    code: str
    name: str


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
