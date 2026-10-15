"""
Photo-related Pydantic schemas.

These define the request/response shapes for photo upload and retrieval.
Photos can be attached to attendance records for audit trail.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class AttendancePhotoBase(BaseModel):
    """Base schema for attendance photos."""
    photo_type: str = Field(
        ...,
        description="face_cropped | full_scene | manual_evidence",
        examples=["face_cropped"]
    )
    notes: Optional[str] = Field(None, description="Optional notes about the photo")
    retention_days: Optional[int] = Field(
        None,
        ge=1,
        le=3650,
        description="Auto-delete after X days (NULL = keep forever)"
    )
    is_primary: bool = Field(True, description="Primary photo for this attendance record")


class AttendancePhotoCreate(AttendancePhotoBase):
    """Schema for creating a new attendance photo."""
    attendance_record_id: int = Field(..., description="Attendance record ID")
    student_id: int = Field(..., description="Student ID")
    session_id: int = Field(..., description="Session ID")


class AttendancePhotoUpload(AttendancePhotoCreate):
    """Schema for uploading a photo with image data."""
    image_data: bytes = Field(..., description="Base64 encoded image data")
    image_format: str = Field("jpeg", description="jpeg | png")
    compression_quality: Optional[int] = Field(None, ge=0, le=100, description="JPEG quality 0-100")
    face_bbox: Optional[str] = Field(None, description="JSON: [x1, y1, x2, y2] if face_cropped type")
    face_confidence: Optional[float] = Field(None, ge=0, le=1, description="Face detection confidence")
    capture_source: Optional[str] = Field(None, description="Source device/identifier")


class AttendancePhotoResponse(AttendancePhotoBase):
    """Schema for photo response (without actual image data)."""
    id: int
    attendance_record_id: int
    student_id: int
    session_id: int
    image_format: str
    image_size: int
    width: int
    height: int
    capture_time: datetime
    capture_method: str
    capture_source: Optional[str]
    face_bbox: Optional[str]
    face_confidence: Optional[float]
    captured_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AttendancePhotoDetailResponse(AttendancePhotoResponse):
    """Schema for photo response with image data."""
    image_data: str = Field(..., description="Base64 encoded image data")


class AttendancePhotoListResponse(BaseModel):
    """Schema for listing photos."""
    photos: list[AttendancePhotoResponse]
    total: int
    has_more: bool


class PhotoUploadResponse(BaseModel):
    """Response after uploading a photo."""
    photo_id: int
    attendance_record_id: int
    student_id: int
    session_id: int
    photo_type: str
    image_size: int
    message: str


class AttendanceWithPhotosResponse(BaseModel):
    """Attendance record with its photos."""
    id: int
    session_id: int
    student_id: int
    status: str
    method: str
    confidence: Optional[float]
    needs_review: bool
    marked_at: datetime
    marked_by: Optional[int]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    photos: list[AttendancePhotoResponse]
    photo_count: int = Field(0, description="Number of photos for this record")

    model_config = ConfigDict(from_attributes=True)