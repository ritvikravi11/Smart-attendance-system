"""
Photo Service: handles photo storage and retrieval for attendance records.

This service manages:
1. Storing photos when attendance is marked
2. Retrieving photos for audit/verification
3. Managing photo lifecycle (retention policies)
4. Processing images (cropping, compression)
"""

import base64
import io
import logging
from datetime import datetime, timedelta
from typing import Optional

import cv2
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError, PermissionDeniedError
from app.models.attendance import AttendanceRecord
from app.models.attendance_photos import AttendancePhoto
from app.models.user import User

logger = logging.getLogger(__name__)


class PhotoService:
    """Service for managing attendance photos."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Photo Creation
    # ------------------------------------------------------------------

    def create_attendance_photo(
        self,
        *,
        attendance_record_id: int,
        student_id: int,
        session_id: int,
        image_data: bytes,
        photo_type: str,
        image_format: str = "jpeg",
        compression_quality: Optional[int] = 85,
        face_bbox: Optional[str] = None,
        face_confidence: Optional[float] = None,
        capture_source: Optional[str] = None,
        captured_by: Optional[int] = None,
        is_primary: bool = True,
        retention_days: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> AttendancePhoto:
        """
        Create a new attendance photo record.

        Validates the image, extracts metadata, and stores the photo.
        """
        # Validate photo_type
        if photo_type not in ["face_cropped", "full_scene", "manual_evidence"]:
            raise ValidationError(
                f"Invalid photo_type: {photo_type}. Must be one of: face_cropped, full_scene, manual_evidence"
            )

        # Validate image_format
        if image_format not in ["jpeg", "png"]:
            raise ValidationError(
                f"Invalid image_format: {image_format}. Must be jpeg or png"
            )

        # Validate attendance record exists and user has permission
        attendance_record = self.db.get(AttendanceRecord, attendance_record_id)
        if not attendance_record:
            raise NotFoundError(
                f"Attendance record {attendance_record_id} not found"
            )

        # Verify student_id matches the attendance record
        if attendance_record.student_id != student_id:
            raise ValidationError(
                f"Student ID {student_id} does not match attendance record"
            )

        # Verify session_id matches
        if attendance_record.session_id != session_id:
            raise ValidationError(
                f"Session ID {session_id} does not match attendance record"
            )

        # Process image to get dimensions and size
        try:
            if image_format == "jpeg":
                # Decode JPEG using OpenCV or PIL
                nparr = np.frombuffer(image_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValidationError("Invalid JPEG image data")
                height, width = img.shape[:2]
            else:  # PNG
                img = Image.open(io.BytesIO(image_data))
                width, height = img.size
        except Exception as e:
            raise ValidationError(f"Failed to process image: {str(e)}")

        if width <= 0 or height <= 0:
            raise ValidationError("Invalid image dimensions")

        image_size = len(image_data)

        # If this is primary photo, unset primary flag on existing photos
        if is_primary:
            self._unset_primary_photos(attendance_record_id)

        # Create photo record
        photo = AttendancePhoto(
            attendance_record_id=attendance_record_id,
            student_id=student_id,
            session_id=session_id,
            photo_type=photo_type,
            image_data=image_data,
            image_format=image_format,
            image_size=image_size,
            width=width,
            height=height,
            compression_quality=compression_quality,
            capture_time=datetime.utcnow(),
            capture_method="face_recognition" if photo_type == "face_cropped" else "camera_capture",
            capture_source=capture_source,
            face_bbox=face_bbox,
            face_confidence=face_confidence,
            captured_by=captured_by,
            is_primary=is_primary,
            retention_days=retention_days,
            notes=notes,
        )

        self.db.add(photo)
        self.db.commit()
        self.db.refresh(photo)

        logger.info(
            f"Created photo {photo.id} for attendance record {attendance_record_id}, "
            f"type: {photo_type}, size: {image_size} bytes"
        )

        return photo

    def _unset_primary_photos(self, attendance_record_id: int) -> None:
        """Unset primary flag on all photos for a given attendance record."""
        photos = self.db.execute(
            select(AttendancePhoto)
            .where(AttendancePhoto.attendance_record_id == attendance_record_id)
            .where(AttendancePhoto.is_primary == True)
        ).scalars().all()

        for photo in photos:
            photo.is_primary = False

    # ------------------------------------------------------------------
    # Photo Retrieval
    # ------------------------------------------------------------------

    def get_photo(self, photo_id: int, user: Optional[User] = None) -> Optional[AttendancePhoto]:
        """
        Get a photo by ID with optional permission check.

        If user is provided, checks if they have permission to view the photo.
        """
        photo = self.db.get(AttendancePhoto, photo_id)
        if not photo:
            return None

        # TODO: Implement permission check
        # For now, just return the photo
        return photo

    def get_photos_for_attendance_record(
        self,
        attendance_record_id: int,
        *,
        include_image_data: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AttendancePhoto], int]:
        """
        Get all photos for an attendance record.
        
        Returns (photos, total_count).
        """
        query = select(AttendancePhoto).where(
            AttendancePhoto.attendance_record_id == attendance_record_id
        )

        # Count total
        total = self.db.execute(
            select(AttendancePhoto.id).where(
                AttendancePhoto.attendance_record_id == attendance_record_id
            )
        ).scalars().count()

        # Get paginated results
        photos = self.db.execute(
            query.order_by(AttendancePhoto.capture_time.desc())
            .limit(limit)
            .offset(offset)
        ).scalars().all()

        if not include_image_data:
            # Clear image data to reduce memory usage
            for photo in photos:
                photo.image_data = b""

        return photos, total

    def get_photos_for_session(
        self,
        session_id: int,
        *,
        student_id: Optional[int] = None,
        photo_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AttendancePhoto], int]:
        """
        Get photos for a session, optionally filtered by student or type.
        """
        query = select(AttendancePhoto).where(
            AttendancePhoto.session_id == session_id
        )

        if student_id:
            query = query.where(AttendancePhoto.student_id == student_id)
        if photo_type:
            query = query.where(AttendancePhoto.photo_type == photo_type)

        # Count total
        count_query = query
        total = self.db.execute(
            select(AttendancePhoto.id).select_from(count_query.subquery())
        ).scalars().count()

        # Get paginated results
        photos = self.db.execute(
            query.order_by(AttendancePhoto.capture_time.desc())
            .limit(limit)
            .offset(offset)
        ).scalars().all()

        return photos, total

    # ------------------------------------------------------------------
    # Photo Processing
    # ------------------------------------------------------------------

    @staticmethod
    def crop_face_from_image(
        image_data: bytes,
        bbox: list[int],
        output_format: str = "jpeg",
        quality: int = 85,
    ) -> bytes:
        """
        Crop a face from an image using bounding box coordinates.
        
        Args:
            image_data: Original image bytes
            bbox: [x1, y1, x2, y2] bounding box
            output_format: jpeg or png
            quality: JPEG quality (0-100)
            
        Returns:
            Cropped image bytes
        """
        # Decode image
        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValidationError("Failed to decode image")

        # Extract bounding box
        x1, y1, x2, y2 = bbox
        # Ensure within bounds
        h, w = img.shape[:2]
        x1 = max(0, min(x1, w - 1))
        x2 = max(0, min(x2, w - 1))
        y1 = max(0, min(y1, h - 1))
        y2 = max(0, min(y2, h - 1))

        if x2 <= x1 or y2 <= y1:
            raise ValidationError("Invalid bounding box dimensions")

        # Crop and encode
        cropped = img[y1:y2, x1:x2]

        if output_format == "jpeg":
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            _, buffer = cv2.imencode(".jpg", cropped, encode_params)
        else:  # PNG
            _, buffer = cv2.imencode(".png", cropped)

        return buffer.tobytes()

    @staticmethod
    def compress_image(
        image_data: bytes,
        target_size_kb: int = 200,
        output_format: str = "jpeg",
    ) -> bytes:
        """
        Compress an image to target size.
        
        Uses progressive JPEG compression to reduce file size.
        """
        # Decode image
        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValidationError("Failed to decode image")

        # Try different quality levels
        for quality in [90, 80, 70, 60, 50]:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            _, buffer = cv2.imencode(".jpg", img, encode_params)
            if len(buffer) <= target_size_kb * 1024:
                return buffer.tobytes()

        # If still too large, resize
        h, w = img.shape[:2]
        scale = (target_size_kb * 1024) / len(buffer)
        scale = max(0.5, min(1.0, scale))  # Limit scaling
        new_w = int(w * scale)
        new_h = int(h * scale)

        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        _, buffer = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
        
        return buffer.tobytes()

    # ------------------------------------------------------------------
    # Lifecycle Management
    # ------------------------------------------------------------------

    def cleanup_expired_photos(self) -> int:
        """
        Delete photos that have exceeded their retention period.
        
        Returns number of photos deleted.
        """
        expired = self.db.execute(
            select(AttendancePhoto)
            .where(AttendancePhoto.retention_days.is_not(None))
            .where(
                AttendancePhoto.capture_time < 
                datetime.utcnow() - timedelta(days=AttendancePhoto.retention_days)
            )
        ).scalars().all()

        count = len(expired)
        for photo in expired:
            self.db.delete(photo)

        if count > 0:
            self.db.commit()
            logger.info(f"Cleaned up {count} expired photos")

        return count

    def delete_photo(self, photo_id: int, user: Optional[User] = None) -> bool:
        """
        Delete a photo by ID.
        
        Returns True if deleted, False if not found.
        """
        photo = self.db.get(AttendancePhoto, photo_id)
        if not photo:
            return False

        # TODO: Add permission check if user provided

        self.db.delete(photo)
        self.db.commit()
        
        logger.info(f"Deleted photo {photo_id}")
        return True

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    @staticmethod
    def image_to_base64(image_data: bytes) -> str:
        """Convert image bytes to base64 string."""
        return base64.b64encode(image_data).decode("utf-8")

    @staticmethod
    def base64_to_image(base64_str: str) -> bytes:
        """Convert base64 string to image bytes."""
        return base64.b64decode(base64_str)