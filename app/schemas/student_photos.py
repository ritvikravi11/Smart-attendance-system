"""
Student profile photo schemas for Cloudinary integration.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class StudentPhotoUploadRequest(BaseModel):
    """Request schema for uploading a student profile photo."""
    filename: str = Field(..., description="Original filename")
    description: Optional[str] = Field(None, description="Optional photo description")


class StudentPhotoResponse(BaseModel):
    """Response schema for student profile photo."""
    student_id: int
    roll_number: str
    student_name: Optional[str]
    
    photo_url: Optional[str] = Field(None, description="Cloudinary secure URL")
    public_id: Optional[str] = Field(None, description="Cloudinary public ID")
    format: Optional[str] = Field(None, description="Image format")
    updated_at: Optional[datetime] = Field(None, description="When photo was last updated")
    
    has_photo: bool = Field(..., description="Whether student has a profile photo")
    cloudinary_configured: bool = Field(..., description="Whether Cloudinary is configured")

    model_config = ConfigDict(from_attributes=True)


class StudentPhotoUploadResponse(BaseModel):
    """Response after successful photo upload."""
    student_id: int
    roll_number: str
    photo_url: str
    public_id: str
    format: str
    updated_at: datetime
    message: str = Field("Profile photo uploaded successfully")


class StudentPhotoDeleteResponse(BaseModel):
    """Response after deleting a photo."""
    student_id: int
    deleted: bool
    message: str


class StudentPhotoListResponse(BaseModel):
    """Response for listing students with/without photos."""
    students: list[StudentPhotoResponse]
    total: int
    has_more: bool


class CloudinaryStatusResponse(BaseModel):
    """Response for Cloudinary configuration status."""
    configured: bool
    cloud_name: Optional[str] = None
    secure: bool = True
    max_file_size_mb: float = Field(5.0, description="Maximum file size in MB")
    allowed_formats: list[str] = Field(
        default=["jpg", "jpeg", "png", "gif", "webp"],
        description="Allowed image formats"
    )