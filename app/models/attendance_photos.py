"""
Attendance photos storage: store photos for audit trail when attendance is marked.

WHY THIS TABLE EXISTS
---------------------
The system currently only stores face embeddings for recognition, but doesn't store
actual photos when attendance is marked. This creates audit trail issues:
1. No visual proof of attendance
2. Cannot manually verify matches
3. No evidence for disputes

This table stores:
- Cropped face photos when attendance is marked via face recognition
- Full scene photos optionally
- Photos for manual attendance marking

PRIVACY CONSIDERATIONS
----------------------
Photos are stored with:
1. Proper access controls - only authorized users can view
2. Retention policies - photos can be automatically deleted after a period
3. Encryption at rest (if configured in storage layer)

STORAGE STRATEGY
----------------
- Store as BYTEA in database for simplicity (PostgreSQL handles up to 1GB)
- Could be extended to use filesystem/S3 storage with path references
- Photos are compressed JPEG to save space
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK, TimestampMixin
from app.models.enums import MarkingMethod

if TYPE_CHECKING:
    from app.models.attendance import AttendanceRecord
    from app.models.people import Student
    from app.models.user import User


class AttendancePhoto(Base, TimestampMixin):
    """
    Photo evidence for attendance marking.

    One attendance record can have multiple photos (e.g., cropped face + full scene).
    Photos can be captured from:
    1. Face recognition - cropped face region
    2. Manual marking - optional photo evidence
    3. Full scene - contextual photo showing the room
    """

    __tablename__ = "attendance_photos"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    attendance_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("attendance_records.id", ondelete="CASCADE"),
        nullable=False,
        comment="Which attendance record this photo belongs to",
    )

    photo_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="face_cropped | full_scene | manual_evidence",
    )

    image_data: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        comment="JPEG compressed image data",
    )

    image_format: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="jpeg",
        comment="jpeg | png",
    )

    image_size: Mapped[int] = mapped_column(
        nullable=False,
        comment="Size in bytes",
    )

    width: Mapped[int] = mapped_column(
        nullable=False,
        comment="Image width in pixels",
    )

    height: Mapped[int] = mapped_column(
        nullable=False,
        comment="Image height in pixels",
    )

    compression_quality: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="JPEG quality 0-100, if applicable",
    )

    capture_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the photo was captured (UTC)",
    )

    capture_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="face_recognition | manual_upload | camera_capture",
    )

    capture_source: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Source device/identifier",
    )

    face_bbox: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="JSON: [x1, y1, x2, y2] if face_cropped type",
    )

    face_confidence: Mapped[Optional[float]] = mapped_column(
        nullable=True,
        comment="Face detection confidence if applicable",
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Student in the photo (redundant but useful for queries)",
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="Session (redundant but useful for queries)",
    )

    captured_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who captured/uploaded the photo",
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Primary photo for this attendance record",
    )

    retention_days: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="Auto-delete after X days (NULL = keep forever)",
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional notes about the photo",
    )

    # ---- Relationships -------------------------------------------------
    attendance_record: Mapped["AttendanceRecord"] = relationship(
        "AttendanceRecord", back_populates="photos"
    )
    student: Mapped["Student"] = relationship("Student")
    session: Mapped["AttendanceSession"] = relationship("AttendanceSession")
    capturer: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[captured_by]
    )

    __table_args__ = (
        CheckConstraint(
            "photo_type IN ('face_cropped', 'full_scene', 'manual_evidence')",
            name="ck_photo_type_valid",
        ),
        CheckConstraint(
            "capture_method IN ('face_recognition', 'manual_upload', 'camera_capture')",
            name="ck_capture_method_valid",
        ),
        CheckConstraint(
            "image_format IN ('jpeg', 'png')",
            name="ck_image_format_valid",
        ),
        CheckConstraint(
            "image_size > 0",
            name="ck_image_size_positive",
        ),
        CheckConstraint(
            "width > 0 AND height > 0",
            name="ck_image_dimensions_positive",
        ),
        CheckConstraint(
            "face_confidence IS NULL OR face_confidence BETWEEN 0 AND 1",
            name="ck_face_confidence_range",
        ),
        CheckConstraint(
            "compression_quality IS NULL OR compression_quality BETWEEN 0 AND 100",
            name="ck_compression_quality_range",
        ),
        # Common queries
        Index("idx_photos_attendance_record", "attendance_record_id"),
        Index("idx_photos_student_session", "student_id", "session_id"),
        Index("idx_photos_session", "session_id"),
        Index("idx_photos_capture_time", "capture_time"),
        # Partial index for primary photos
        Index(
            "idx_photos_primary",
            "attendance_record_id",
            unique=True,
            postgresql_where="is_primary = true",
        ),
        {
            "comment": "Photo evidence for attendance records. Provides audit trail.",
        },
    )

    @property
    def file_extension(self) -> str:
        """Get file extension based on image format."""
        return self.image_format

    @property
    def is_face_cropped(self) -> bool:
        """Check if this is a cropped face photo."""
        return self.photo_type == "face_cropped"

    @property
    def is_full_scene(self) -> bool:
        """Check if this is a full scene photo."""
        return self.photo_type == "full_scene"

    @property
    def is_manual_evidence(self) -> bool:
        """Check if this is manual evidence photo."""
        return self.photo_type == "manual_evidence"

    def __repr__(self) -> str:
        return (
            f"<AttendancePhoto id={self.id} "
            f"record_id={self.attendance_record_id} "
            f"student_id={self.student_id} "
            f"type={self.photo_type} "
            f"size={self.image_size} bytes>"
        )