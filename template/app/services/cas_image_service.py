"""CAS image service for thumbnail generation and processing."""

import io
import logging
import os
from pathlib import Path

from PIL import Image

from app.app_config import AppSettings
from app.exceptions import InvalidOperationException
from app.schemas.upload_document import DocumentContentSchema
from app.services.s3_service import S3Service

logger = logging.getLogger(__name__)


class CasImageService:
    """Service for image processing and thumbnail generation."""

    def __init__(self, s3_service: S3Service, app_settings: AppSettings):
        """Initialize CAS image service with S3 service.

        Args:
            s3_service: S3 service for file operations
            app_settings: Application-specific settings
        """
        self.s3_service = s3_service
        self.app_settings = app_settings
        self._ensure_thumbnail_directory()

    def _ensure_thumbnail_directory(self) -> None:
        """Ensure thumbnail storage directory exists."""
        thumbnail_path = Path(self.app_settings.thumbnail_storage_path)
        thumbnail_path.mkdir(parents=True, exist_ok=True)

    def get_thumbnail_for_hash(self, content_hash: str, size: int) -> str:
        """Get thumbnail for CAS content hash, generating if necessary.

        This method is used by the CAS endpoint which is stateless (no DB access).
        The hash is provided directly from the URL path.

        Args:
            content_hash: SHA-256 hash (64-char hex)
            size: Thumbnail size in pixels

        Returns:
            Path to thumbnail file

        Raises:
            InvalidOperationException: If thumbnail generation fails
        """
        # Use hash as cache key instead of attachment_id
        thumbnail_path = os.path.join(
            self.app_settings.thumbnail_storage_path,
            f"{content_hash}_{size}.jpg"
        )

        # Check if thumbnail already exists
        if os.path.exists(thumbnail_path):
            return thumbnail_path

        # Generate thumbnail from S3 using CAS key
        s3_key = f"cas/{content_hash}"

        try:
            # Download original image from S3
            image_data = self.s3_service.download_file(s3_key)

            # Open and process image with PIL
            with Image.open(image_data) as img:
                # Convert to RGB if necessary (for PNG with transparency, etc.)
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')

                # Create thumbnail maintaining aspect ratio
                img.thumbnail((size, size), Image.Resampling.LANCZOS)

                # Save thumbnail to disk
                img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)

            return thumbnail_path

        except Exception as e:
            raise InvalidOperationException("generate thumbnail from hash", str(e)) from e

    def convert_image_to_png(self, content: bytes) -> DocumentContentSchema | None:
        """Try to convert an image to PNG format.

        This method attempts to convert unsupported image formats (like .ico)
        to PNG so they can be used despite not being in the ALLOWED_IMAGE_TYPES list.

        Args:
            content: Raw image bytes

        Returns:
            DocumentContentSchema with PNG data if conversion succeeds, None if it fails
        """
        try:
            # Try to open the image with PIL
            with Image.open(io.BytesIO(content)) as img:
                # Convert to RGBA to handle transparency properly
                if img.mode not in ('RGBA', 'RGB'):
                    img = img.convert('RGBA')

                # Create a new BytesIO object for PNG output
                png_output = io.BytesIO()

                # Save as PNG
                img.save(png_output, format='PNG')
                png_output.seek(0)

                return DocumentContentSchema(
                    content=png_output.getvalue(),
                    content_type='image/png'
                )

        except Exception as e:
            logger.warning(f"Image conversion to PNG failed: {str(e)}")
            # If PIL can't handle it, return None
            return None
