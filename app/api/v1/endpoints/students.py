"""
Student biometric endpoints: consent, enrolment, status, erasure.

ARCHITECTURAL ROLE
------------------
This module is the HTTP boundary and nothing more. Every handler:

    1. receives input Pydantic has already validated
    2. has authorisation confirmed by a dependency, before the body runs
    3. calls exactly ONE service method
    4. shapes the response

There are no business rules here, no database access, and -- deliberately --
no try/except around domain errors. `EnrollmentService` raises domain
exceptions (`PermissionDeniedError`, `ValidationError`, `ConflictError`), and
the single handler in `main.py` translates each into the correct status code
with a consistent body. Catching them here would duplicate that mapping and
let the two drift.

If a handler in this file ever grows past a dozen lines, logic has leaked out
of the service and should be pushed back.
"""

from typing import Optional
from fastapi import APIRouter, File, UploadFile, status

from app.api.deps import AdminUser, DbSession
from app.core.exceptions import ValidationError
from app.schemas.auth import ErrorResponse
from app.schemas.student import (
    ConsentRequest,
    ConsentResponse,
    FaceDeletionResponse,
    FaceEnrollmentResponse,
    FaceStatusResponse,
    StudentCreateRequest,
    DepartmentResponse,
    StudentListResponse,
    StudentResponse,
)
from app.services.enrollment_service import EnrollmentService
from app.services.student_service import StudentService

router = APIRouter(prefix="/students", tags=["Student Biometrics"])

# Ceiling on one enrolment upload. 15 images at 10 MB each would be 150 MB
# held in memory while embedding runs -- enough to exhaust a small server with
# a handful of concurrent requests. The per-image cap in decode_frame does not
# bound the TOTAL, so the aggregate is checked here.
MAX_TOTAL_UPLOAD_BYTES = 40 * 1024 * 1024  # 40 MB


@router.get("/departments", response_model=list[DepartmentResponse])
def list_departments(user: AdminUser, db: DbSession) -> list[DepartmentResponse]:
    return [
        DepartmentResponse(id=department.id, code=department.code, name=department.name)
        for department in StudentService(db).list_departments()
    ]


@router.get("", response_model=StudentListResponse, summary="List active students")
def list_students(user: AdminUser, db: DbSession) -> StudentListResponse:
    students, total = StudentService(db).list_students()
    return StudentListResponse(
        students=[
            StudentResponse(
                student_id=student.id,
                full_name=student.user.full_name,
                email=student.user.email,
                roll_number=student.roll_number,
                department_id=student.department_id,
                department_name=student.department.name,
                semester=student.semester,
                phone=student.phone,
                admission_year=student.admission_year,
                face_enrolled=student.face_enrolled,
            )
            for student in students
        ],
        total=total,
    )


@router.post(
    "",
    response_model=StudentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student account",
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def create_student(
    payload: StudentCreateRequest, user: AdminUser, db: DbSession
) -> StudentResponse:
    student = StudentService(db).create(payload)
    return StudentResponse(
        student_id=student.id,
        full_name=student.user.full_name,
        email=student.user.email,
        roll_number=student.roll_number,
        department_id=student.department_id,
        department_name=student.department.name,
        semester=student.semester,
        phone=student.phone,
        admission_year=student.admission_year,
        face_enrolled=student.face_enrolled,
    )


@router.post(
    "/{student_id}/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
    summary="Record or withdraw biometric consent",
    responses={
        403: {"model": ErrorResponse, "description": "Admin role required"},
        404: {"model": ErrorResponse, "description": "Student not found"},
    },
)
def set_consent(
    student_id: int,
    payload: ConsentRequest,
    user: AdminUser,
    db: DbSession,
) -> ConsentResponse:
    """
    Grant or withdraw a student's consent for biometric enrolment.

    **Consent is mandatory before any face data can be captured.** Under
    India's DPDP Act 2023 and GDPR Article 9, biometric data used to identify a
    person requires explicit, recorded consent. Attempting enrolment without it
    returns 403 with `error_code: CONSENT_REQUIRED`.

    **Withdrawing consent deletes the stored template immediately**, in the
    same transaction. Consent that can be withdrawn while the biometric data
    quietly remains on the server is not consent.

    200 rather than 201: this changes state on an existing student, it does not
    create a resource.
    """
    service = EnrollmentService(db)
    student = service.set_consent(
        student_id, granted=payload.granted, actor=user, note=payload.note
    )
    return ConsentResponse(
        student_id=student.id,
        roll_number=student.roll_number,
        consent_given_at=student.consent_given_at,
        consent_revoked_at=student.consent_revoked_at,
        has_valid_consent=student.has_valid_consent,
        face_enrolled=student.face_enrolled,
    )


@router.post(
    "/{student_id}/faces",
    response_model=FaceEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enrol a student's face from multiple images",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Too few usable images, or the images are of different people",
        },
        403: {
            "model": ErrorResponse,
            "description": "Admin role required, or the student has not consented",
        },
        404: {"model": ErrorResponse, "description": "Student not found"},
        409: {"model": ErrorResponse, "description": "Recognition model not loaded"},
        413: {"model": ErrorResponse, "description": "Upload too large"},
    },
)
async def enroll_faces(
    student_id: int,
    user: AdminUser,
    db: DbSession,
    images: list[UploadFile] = File(
        ...,
        description="5 to 15 JPEG or PNG images, each containing exactly one face.",
    ),
) -> FaceEnrollmentResponse:
    """
    Capture a student's face and store a single reference embedding.

    Send 5–15 images as `multipart/form-data`. Vary the angle, expression and
    lighting slightly between shots — averaging across variation is what makes
    the reference robust, and near-identical frames add nothing.

    **Each image must contain exactly one face.** Two people in frame is
    rejected, because there is no way to know whose identity is being
    registered and guessing would silently enrol the wrong student.

    **Raw images are never stored.** They are decoded in memory, embedded, and
    discarded. What persists is a 2,048-byte vector. The response echoes
    `raw_images_retained: false` so the guarantee is visible, not just promised.

    Returns 201 with per-image rejection reasons, so an operator knows exactly
    which shots to retake rather than being told "7 of 12 failed".

    Note this is `async def` while the other handlers are `def`. FastAPI runs a
    sync handler in a threadpool; an async one runs on the event loop. `await
    file.read()` is genuine async I/O, so async is correct here -- and the
    CPU-bound embedding that follows is why the *service* is the right place to
    later move inference into a threadpool.
    """
    payloads: list[bytes] = []
    total = 0

    for upload in images:
        data = await upload.read()
        total += len(data)
        if total > MAX_TOTAL_UPLOAD_BYTES:
            # Raised before reading the remainder, so a hostile client cannot
            # force us to buffer an unbounded upload just to reject it.
            from app.core.exceptions import AppException

            error = AppException(
                f"Upload exceeds {MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)} MB in total.",
                error_code="UPLOAD_TOO_LARGE",
            )
            error.status_code = 413
            raise error
        payloads.append(data)

    service = EnrollmentService(db)
    result = service.enroll_faces(student_id, payloads, actor=user)
    return FaceEnrollmentResponse(**result)


@router.get(
    "/{student_id}/faces/status",
    response_model=FaceStatusResponse,
    summary="Check a student's enrolment state",
    responses={
        403: {"model": ErrorResponse, "description": "Admin role required"},
        404: {"model": ErrorResponse, "description": "Student not found"},
    },
)
def face_status(
    student_id: int,
    user: AdminUser,
    db: DbSession,
) -> FaceStatusResponse:
    """
    Report whether a student is enrolled, and how good the enrolment is.

    The field worth watching is **`in_matcher_index`**. If `face_enrolled` is
    true but `in_matcher_index` is false, the database row exists while this
    worker process holds a stale in-memory index — so the student will not be
    recognised until the index is refreshed. That is the known multi-worker
    limitation, and surfacing it turns a mystery ("why is she never marked?")
    into a fact with an obvious fix: `POST /recognition/refresh-index`.

    Returns quality and consistency figures but never the embedding itself.
    """
    service = EnrollmentService(db)
    return FaceStatusResponse(**service.get_status(student_id))


@router.delete(
    "/{student_id}/faces",
    response_model=FaceDeletionResponse,
    status_code=status.HTTP_200_OK,
    summary="Erase a student's biometric data",
    responses={
        403: {"model": ErrorResponse, "description": "Admin role required"},
        404: {"model": ErrorResponse, "description": "Student not found"},
    },
)
def delete_faces(
    student_id: int,
    user: AdminUser,
    db: DbSession,
) -> FaceDeletionResponse:
    """
    Permanently delete a student's face embeddings (right to erasure).

    This is a **hard delete**, unlike almost every other removal in this
    system. Soft deletion is correct for records referenced by history, but
    "we deactivated your biometric template" is not erasure — the point of the
    right is that the data ceases to exist.

    **Attendance history is preserved.** Those records reference the student,
    not the embedding; the institution's record of who attended which class is
    not personal biometric data and must survive. The response states this
    explicitly so nobody has to guess.

    200 rather than 204, because the response carries a body: how many
    embeddings were removed and the resulting index size.
    """
    service = EnrollmentService(db)
    return FaceDeletionResponse(**service.delete_faces(student_id, actor=user))



# -------------------------------------------------------------------
# STUDENT PROFILE PHOTOS (Cloudinary integration)
# -------------------------------------------------------------------
from app.schemas.student_photos import (
    StudentPhotoUploadRequest,
    StudentPhotoResponse,
    StudentPhotoUploadResponse,
    StudentPhotoDeleteResponse,
    StudentPhotoListResponse,
    CloudinaryStatusResponse,
)
from app.services.student_photo_service import StudentPhotoService


@router.get(
    "/{student_id}/photo",
    response_model=StudentPhotoResponse,
    summary="Get student profile photo information",
    responses={
        403: {"model": ErrorResponse, "description": "Admin or self-access required"},
        404: {"model": ErrorResponse, "description": "Student not found"},
    },
)
def get_student_photo(
    student_id: int,
    user: AdminUser,  # Admin or faculty can view any student's photo
    db: DbSession,
) -> StudentPhotoResponse:
    """
    Get student profile photo metadata and URL.
    
    Returns Cloudinary URL and metadata. The URL is signed and expires.
    """
    service = StudentPhotoService(db)
    metadata = service.get_profile_photo_metadata(student_id)
    
    # Get student name from the student object
    student = db.get(Student, student_id)
    if not student:
        raise NotFoundError(f"Student {student_id} not found")
    
    return StudentPhotoResponse(
        student_id=student_id,
        roll_number=student.roll_number,
        student_name=student.user.full_name if student.user else None,
        photo_url=metadata["photo_url"],
        public_id=metadata["public_id"],
        format=metadata["format"],
        updated_at=metadata["updated_at"],
        has_photo=metadata["has_photo"],
        cloudinary_configured=metadata["cloudinary_configured"],
    )


@router.post(
    "/{student_id}/photo",
    response_model=StudentPhotoUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload student profile photo",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid image format or size"},
        403: {"model": ErrorResponse, "description": "Admin role required"},
        404: {"model": ErrorResponse, "description": "Student not found"},
        503: {"model": ErrorResponse, "description": "Cloudinary service unavailable"},
    },
)
async def upload_student_photo(
    student_id: int,
    user: AdminUser,
    db: DbSession,
    file: UploadFile = File(..., description="Profile photo image (max 5MB)"),
    payload: StudentPhotoUploadRequest = None,
) -> StudentPhotoUploadResponse:
    """
    Upload a student profile photo to Cloudinary.
    
    Accepts JPEG, PNG, GIF, or WebP images up to 5MB.
    The photo is uploaded to Cloudinary and a secure URL is stored in the database.
    """
    # Read file data
    image_data = await file.read()
    if file.content_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        raise ValidationError("Only JPEG, PNG, GIF, and WebP images are supported.")
    
    # Use payload filename if provided, otherwise use uploaded filename
    filename = payload.filename if payload and payload.filename else file.filename
    
    # Upload to Cloudinary
    service = StudentPhotoService(db)
    student = service.upload_profile_photo(
        student_id=student_id,
        image_data=image_data,
        filename=filename,
        uploaded_by=user.id,
    )
    
    return StudentPhotoUploadResponse(
        student_id=student.id,
        roll_number=student.roll_number,
        photo_url=student.profile_photo_url,
        public_id=student.profile_photo_public_id,
        format=student.profile_photo_format,
        updated_at=student.profile_photo_updated_at,
        message=f"Profile photo uploaded for {student.roll_number}",
    )


@router.delete(
    "/{student_id}/photo",
    response_model=StudentPhotoDeleteResponse,
    summary="Delete student profile photo",
    responses={
        403: {"model": ErrorResponse, "description": "Admin role required"},
        404: {"model": ErrorResponse, "description": "Student not found"},
    },
)
def delete_student_photo(
    student_id: int,
    user: AdminUser,
    db: DbSession,
) -> StudentPhotoDeleteResponse:
    """
    Delete student profile photo from Cloudinary and database.
    """
    service = StudentPhotoService(db)
    deleted = service.delete_profile_photo(student_id)
    
    student = db.get(Student, student_id)
    if not student:
        raise NotFoundError(f"Student {student_id} not found")
    
    return StudentPhotoDeleteResponse(
        student_id=student_id,
        deleted=deleted,
        message=f"Profile photo {'deleted' if deleted else 'not found'} for {student.roll_number}",
    )


@router.get(
    "/photos/with-photos",
    response_model=StudentPhotoListResponse,
    summary="List students with profile photos",
    responses={
        403: {"model": ErrorResponse, "description": "Admin role required"},
    },
)
def list_students_with_photos(
    user: AdminUser,
    db: DbSession,
    department_id: Optional[int] = None,
    semester: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> StudentPhotoListResponse:
    """
    List students who have profile photos.
    """
    service = StudentPhotoService(db)
    students, total = service.list_students_with_photos(
        department_id=department_id,
        semester=semester,
        limit=limit,
        offset=offset,
    )
    
    photo_responses = []
    for student in students:
        metadata = service.get_profile_photo_metadata(student.id)
        photo_responses.append(
            StudentPhotoResponse(
                student_id=student.id,
                roll_number=student.roll_number,
                student_name=student.user.full_name if student.user else None,
                photo_url=metadata["photo_url"],
                public_id=metadata["public_id"],
                format=metadata["format"],
                updated_at=metadata["updated_at"],
                has_photo=metadata["has_photo"],
                cloudinary_configured=metadata["cloudinary_configured"],
            )
        )
    
    return StudentPhotoListResponse(
        students=photo_responses,
        total=total,
        has_more=(offset + len(students)) < total,
    )


@router.get(
    "/photos/cloudinary-status",
    response_model=CloudinaryStatusResponse,
    summary="Check Cloudinary configuration status",
)
def check_cloudinary_status() -> CloudinaryStatusResponse:
    """
    Check if Cloudinary is configured and get upload limits.
    """
    from app.core.cloudinary_config import CloudinaryConfig
    from app.core.config import settings
    
    return CloudinaryStatusResponse(
        configured=CloudinaryConfig.is_configured(),
        cloud_name=settings.cloudinary_cloud_name,
        secure=settings.cloudinary_secure,
        max_file_size_mb=CloudinaryConfig.MAX_FILE_SIZE / (1024 * 1024),
        allowed_formats=CloudinaryConfig.ALLOWED_FORMATS,
    )