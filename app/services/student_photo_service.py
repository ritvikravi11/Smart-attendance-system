"""
Student Photo Service: handles student profile photo uploads to Cloudinary.

This service manages:
1. Uploading student profile photos to Cloudinary
2. Validating image format and size
3. Storing Cloudinary URLs in database
4. Managing photo lifecycle
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.cloudinary_config import CloudinaryConfig
from app.core.exceptions import NotFoundError, ValidationError, ServiceUnavailableError
from app.models.people import Student
from app.models.user import User

logger = logging.getLogger(__name__)


class StudentPhotoService:
    """Service for managing student profile photos using Cloudinary."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Photo Upload
    # ------------------------------------------------------------------

    def upload_profile_photo(
        self,
        *,
        student_id: int,
        image_data: bytes,
        filename: str,
        uploaded_by: Optional[int] = None,
        upload_options: Optional[Dict[str, Any]] = None,
    ) -> Student:
        """
        Upload student profile photo to Cloudinary and store reference in database.
        
        Args:
            student_id: Student ID
            image_data: Raw image bytes
            filename: Original filename (used for validation)
            uploaded_by: User ID who uploaded the photo
            upload_options: Optional Cloudinary upload options
            
        Returns:
            Updated Student object with Cloudinary URL
        """
        # Check if Cloudinary is configured
        if not CloudinaryConfig.is_configured():
            raise ServiceUnavailableError(
                "Cloudinary service is not configured. Please contact administrator.",
                error_code="CLOUDINARY_NOT_CONFIGURED",
            )

        # Validate student exists
        student = self.db.get(Student, student_id)
        if not student:
            raise NotFoundError(f"Student {student_id} not found")

        # Validate file size
        if not CloudinaryConfig.validate_file_size(image_data):
            raise ValidationError(
                f"Image size {len(image_data)} bytes exceeds maximum {CloudinaryConfig.MAX_FILE_SIZE} bytes"
            )

        # Validate file format
        if not CloudinaryConfig.validate_file_format(filename):
            raise ValidationError(
                f"File format not allowed. Allowed formats: {', '.join(CloudinaryConfig.ALLOWED_FORMATS)}"
            )
        if not CloudinaryConfig.validate_image_content(image_data):
            raise ValidationError(
                "The file is not a valid image or is smaller than 100x100 pixels."
            )

        # Upload to Cloudinary
        try:
            cloudinary_result = CloudinaryConfig.upload_student_photo(
                image_bytes=image_data,
                student_id=student_id,
                student_roll_number=student.roll_number,
                filename=filename,
                upload_options=upload_options,
            )
        except Exception as e:
            logger.error(f"Cloudinary upload failed for student {student_id}: {str(e)}")
            raise ServiceUnavailableError(
                f"Failed to upload photo to Cloudinary: {str(e)}",
                error_code="CLOUDINARY_UPLOAD_FAILED",
            )

        # Generate secure URL for the photo
        secure_url = CloudinaryConfig.generate_secure_url(
            public_id=cloudinary_result["public_id"],
            transformations={"width": 500, "height": 500, "crop": "fill", "gravity": "face"}
        )

        # Extract format from Cloudinary result
        image_format = cloudinary_result.get("format", "jpg")
        if image_format == "jpg":
            image_format = "jpeg"

        # Update student record with Cloudinary references
        student.profile_photo_url = secure_url
        student.profile_photo_public_id = cloudinary_result["public_id"]
        student.profile_photo_format = image_format
        student.profile_photo_updated_at = datetime.utcnow()

        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            try:
                CloudinaryConfig.delete_student_photo(cloudinary_result["public_id"])
            except Exception:
                logger.exception(
                    "Failed to clean up Cloudinary asset after database failure: %s",
                    cloudinary_result["public_id"],
                )
            raise ServiceUnavailableError(
                "The photo upload could not be saved. Please try again.",
                error_code="PHOTO_DATABASE_SAVE_FAILED",
            ) from exc
        self.db.refresh(student)

        logger.info(
            f"Uploaded profile photo for student {student_id} "
            f"(roll: {student.roll_number}) to Cloudinary. "
            f"Public ID: {cloudinary_result['public_id']}"
        )

        return student

    # ------------------------------------------------------------------
    # Photo Retrieval
    # ------------------------------------------------------------------

    def get_profile_photo_url(self, student_id: int, transformations: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Get student profile photo URL (with optional transformations).
        
        Args:
            student_id: Student ID
            transformations: Optional Cloudinary transformation options
            
        Returns:
            Cloudinary URL or None if no photo exists
        """
        student = self.db.get(Student, student_id)
        if not student:
            raise NotFoundError(f"Student {student_id} not found")
        
        if not student.profile_photo_public_id:
            return None

        # Generate fresh signed URL (they expire)
        try:
            if CloudinaryConfig.is_configured():
                url = CloudinaryConfig.generate_secure_url(
                    public_id=student.profile_photo_public_id,
                    transformations=transformations,
                )
                return url
            else:
                # Return stored URL if Cloudinary not configured
                return student.profile_photo_url
        except Exception as e:
            logger.error(f"Failed to generate Cloudinary URL for student {student_id}: {str(e)}")
            return student.profile_photo_url  # Return stored URL as fallback

    def get_profile_photo_metadata(self, student_id: int) -> dict:
        """
        Get student profile photo metadata.
        
        Returns:
            Dictionary with photo metadata
        """
        student = self.db.get(Student, student_id)
        if not student:
            raise NotFoundError(f"Student {student_id} not found")

        # Generate fresh URL for metadata
        photo_url = None
        if student.profile_photo_public_id and CloudinaryConfig.is_configured():
            try:
                photo_url = CloudinaryConfig.generate_secure_url(
                    public_id=student.profile_photo_public_id,
                    transformations={"width": 200, "height": 200, "crop": "fill"}
                )
            except Exception:
                photo_url = student.profile_photo_url

        return {
            "has_photo": student.profile_photo_public_id is not None,
            "photo_url": photo_url,
            "public_id": student.profile_photo_public_id,
            "format": student.profile_photo_format,
            "updated_at": student.profile_photo_updated_at,
            "student_id": student.id,
            "roll_number": student.roll_number,
            "student_name": student.user.full_name if student.user else None,
            "cloudinary_configured": CloudinaryConfig.is_configured(),
        }

    # ------------------------------------------------------------------
    # Photo Management
    # ------------------------------------------------------------------

    def delete_profile_photo(self, student_id: int) -> bool:
        """
        Delete student profile photo from Cloudinary and database.
        
        Returns:
            True if deleted, False if no photo existed
        """
        student = self.db.get(Student, student_id)
        if not student:
            raise NotFoundError(f"Student {student_id} not found")

        if student.profile_photo_public_id is None:
            return False

        # Delete from Cloudinary
        try:
            if CloudinaryConfig.is_configured():
                CloudinaryConfig.delete_student_photo(student.profile_photo_public_id)
        except Exception as e:
            logger.error(f"Cloudinary deletion failed for student {student_id}: {str(e)}")
            # Continue to clear database record even if Cloudinary deletion fails

        # Clear photo fields in database
        student.profile_photo_url = None
        student.profile_photo_public_id = None
        student.profile_photo_format = None
        student.profile_photo_updated_at = datetime.utcnow()

        self.db.commit()
        
        logger.info(f"Deleted profile photo for student {student_id}")
        return True

    def list_students_with_photos(
        self,
        *,
        department_id: Optional[int] = None,
        semester: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Student], int]:
        """
        List students with profile photos.
        
        Returns:
            (students, total_count)
        """
        query = select(Student).where(
            Student.profile_photo_public_id.is_not(None)
        )

        if department_id:
            query = query.where(Student.department_id == department_id)
        if semester:
            query = query.where(Student.semester == semester)

        # Count total
        total = self.db.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0

        # Get paginated results
        students = self.db.execute(
            query.order_by(Student.roll_number)
            .limit(limit)
            .offset(offset)
        ).scalars().all()

        return students, total

    def list_students_without_photos(
        self,
        *,
        department_id: Optional[int] = None,
        semester: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Student], int]:
        """
        List students without profile photos.
        
        Returns:
            (students, total_count)
        """
        query = select(Student).where(
            Student.profile_photo_public_id.is_(None)
        )

        if department_id:
            query = query.where(Student.department_id == department_id)
        if semester:
            query = query.where(Student.semester == semester)

        # Count total
        total = self.db.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0

        # Get paginated results
        students = self.db.execute(
            query.order_by(Student.roll_number)
            .limit(limit)
            .offset(offset)
        ).scalars().all()

        return students, total

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    @staticmethod
    def validate_image_format(filename: str) -> bool:
        """
        Validate image format from filename.
        
        Returns:
            True if format is allowed
        """
        if not filename or '.' not in filename:
            return False
            
        extension = filename.rsplit('.', 1)[1].lower()
        return extension in CloudinaryConfig.ALLOWED_FORMATS