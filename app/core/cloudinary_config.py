"""
Cloudinary configuration for student profile photo storage.

WHY CLOUDINARY
--------------
1. Offloads image storage from the database
2. Provides automatic image optimization, resizing, and format conversion
3. Secure delivery with signed URLs
4. No need to manage file storage infrastructure

SECURITY NOTES
--------------
- Photos are stored as "authenticated" type (not public)
- Signed URLs expire after a configurable time
- API secrets are never exposed to the frontend
"""

import cloudinary
import cloudinary.uploader
import cloudinary.api
import cv2
import numpy as np
from typing import Optional, Dict, Any
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class CloudinaryConfig:
    """Cloudinary configuration and utilities."""

    # Maximum file size: 5MB
    MAX_FILE_SIZE = 5 * 1024 * 1024
    MIN_IMAGE_WIDTH = 100
    MIN_IMAGE_HEIGHT = 100
    
    # Allowed image formats
    ALLOWED_FORMATS = ["jpg", "jpeg", "png", "gif", "webp"]
    
    # Default upload options
    DEFAULT_UPLOAD_OPTIONS = {
        "folder": "smart_attendance/students",
        "resource_type": "image",
        "use_filename": True,
        "unique_filename": True,
        "overwrite": False,
        "type": "authenticated",  # Private delivery
    }
    
    # Default transformation options
    DEFAULT_TRANSFORMATIONS = {
        "width": 500,
        "height": 500,
        "crop": "fill",
        "gravity": "face",
        "quality": "auto",
        "format": "auto",
    }

    @classmethod
    def is_configured(cls) -> bool:
        """Check if Cloudinary is properly configured."""
        return all([
            settings.cloudinary_cloud_name,
            settings.cloudinary_api_key,
            settings.cloudinary_api_secret
        ])

    @classmethod
    def configure(cls) -> None:
        """Configure Cloudinary with settings from environment."""
        if not cls.is_configured():
            logger.warning(
                "Cloudinary not fully configured. "
                "Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            )
            return

        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=settings.cloudinary_secure,
        )
        logger.info(f"Cloudinary configured for cloud: {settings.cloudinary_cloud_name}")

    @classmethod
    def upload_student_photo(
        cls,
        image_bytes: bytes,
        student_id: int,
        student_roll_number: str,
        filename: Optional[str] = None,
        upload_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Upload a student profile photo to Cloudinary.
        
        Args:
            image_bytes: Raw image data
            student_id: Student database ID
            student_roll_number: Student roll number (for naming)
            filename: Optional custom filename
            upload_options: Optional custom upload options
            
        Returns:
            Cloudinary upload result dictionary
        """
        if not cls.is_configured():
            raise RuntimeError("Cloudinary is not configured")

        # Generate filename if not provided
        if not filename:
            filename = f"student_{student_id}_{student_roll_number}"

        # Merge default options with custom options
        options = cls.DEFAULT_UPLOAD_OPTIONS.copy()
        if upload_options:
            options.update(upload_options)

        # Add student-specific metadata
        options["public_id"] = f"students/{student_id}/{filename}"
        options["context"] = f"student_id={student_id}|roll_number={student_roll_number}"

        try:
            # Upload to Cloudinary
            result = cloudinary.uploader.upload(
                image_bytes,
                **options
            )
            
            logger.info(
                f"Uploaded photo for student {student_id} (roll: {student_roll_number}) "
                f"to Cloudinary. Public ID: {result['public_id']}, "
                f"Size: {result.get('bytes', 0)} bytes"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Cloudinary upload failed for student {student_id}: {str(e)}")
            raise

    @classmethod
    def delete_student_photo(cls, public_id: str) -> Dict[str, Any]:
        """
        Delete a student photo from Cloudinary.
        
        Args:
            public_id: Cloudinary public ID
            
        Returns:
            Cloudinary deletion result
        """
        if not cls.is_configured():
            raise RuntimeError("Cloudinary is not configured")

        try:
            result = cloudinary.uploader.destroy(public_id)
            
            if result.get("result") == "ok":
                logger.info(f"Deleted Cloudinary asset: {public_id}")
            else:
                logger.warning(f"Cloudinary deletion may have failed for {public_id}: {result}")
            
            return result
            
        except Exception as e:
            logger.error(f"Cloudinary deletion failed for {public_id}: {str(e)}")
            raise

    @classmethod
    def generate_secure_url(
        cls,
        public_id: str,
        transformations: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate a secure, signed URL for a Cloudinary asset.
        
        Args:
            public_id: Cloudinary public ID
            transformations: Optional image transformations
            
        Returns:
            Signed URL with transformations
        """
        if not cls.is_configured():
            raise RuntimeError("Cloudinary is not configured")

        # Merge default transformations with custom ones
        transform_options = cls.DEFAULT_TRANSFORMATIONS.copy()
        if transformations:
            transform_options.update(transformations)

        try:
            # Generate secure URL with transformations
            url = cloudinary.utils.cloudinary_url(
                public_id,
                **transform_options,
                secure=True,
                type="authenticated",
                sign_url=True,
            )[0]
            
            return url
            
        except Exception as e:
            logger.error(f"Failed to generate Cloudinary URL for {public_id}: {str(e)}")
            raise

    @classmethod
    def validate_file_size(cls, image_bytes: bytes) -> bool:
        """Validate file size is within limits."""
        return len(image_bytes) <= cls.MAX_FILE_SIZE

    @classmethod
    def validate_file_format(cls, filename: str) -> bool:
        """Validate file format is allowed."""
        if not filename:
            return False
        
        # Extract extension
        if '.' not in filename:
            return False
            
        extension = filename.rsplit('.', 1)[1].lower()
        return extension in cls.ALLOWED_FORMATS

    @classmethod
    def validate_image_content(cls, image_bytes: bytes) -> bool:
        """Verify the payload is decodable image data, not just a renamed file."""
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        return bool(
            image is not None
            and image.shape[1] >= cls.MIN_IMAGE_WIDTH
            and image.shape[0] >= cls.MIN_IMAGE_HEIGHT
        )


# Configure Cloudinary on module import
CloudinaryConfig.configure()