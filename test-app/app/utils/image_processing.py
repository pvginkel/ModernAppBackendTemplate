"""Image processing utilities."""

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.exceptions import InvalidOperationException


def validate_image_format(file_data: bytes) -> str:
    """Validate image format and return detected format.

    Args:
        file_data: Image file data

    Returns:
        Image format string (e.g., 'JPEG', 'PNG')

    Raises:
        InvalidOperationException: If not a valid image or unsupported format
    """
    try:
        with Image.open(BytesIO(file_data)) as img:
            # Validate format is supported
            if img.format not in ['JPEG', 'PNG', 'WEBP', 'BMP', 'TIFF']:
                raise InvalidOperationException("validate image format", f"Unsupported image format: {img.format}")
            return img.format
    except Exception as e:
        raise InvalidOperationException("validate image file", str(e)) from e


def get_image_dimensions(file_data: bytes) -> tuple[int, int]:
    """Get image dimensions.

    Args:
        file_data: Image file data

    Returns:
        Tuple of (width, height)

    Raises:
        InvalidOperationException: If not a valid image
    """
    try:
        with Image.open(BytesIO(file_data)) as img:
            return img.size
    except Exception as e:
        raise InvalidOperationException("get image dimensions", str(e)) from e


def resize_image(file_data: bytes, max_width: int, max_height: int) -> bytes:
    """Resize image maintaining aspect ratio.

    Args:
        file_data: Image file data
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels

    Returns:
        Resized image data as bytes

    Raises:
        InvalidOperationException: If image processing fails
    """
    try:
        with Image.open(BytesIO(file_data)) as img:
            # Convert to RGB if necessary
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')

            # Calculate new dimensions maintaining aspect ratio
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            # Save to bytes
            output = BytesIO()
            img.save(output, 'JPEG', quality=85, optimize=True)
            return output.getvalue()

    except Exception as e:
        raise InvalidOperationException("resize image", str(e)) from e


def create_thumbnail(file_data: bytes, size: int) -> bytes:
    """Create square thumbnail from image.

    Args:
        file_data: Image file data
        size: Thumbnail size in pixels (square)

    Returns:
        Thumbnail data as JPEG bytes

    Raises:
        InvalidOperationException: If thumbnail creation fails
    """
    try:
        with Image.open(BytesIO(file_data)) as img:
            # Convert to RGB if necessary
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')

            # Create thumbnail maintaining aspect ratio
            img.thumbnail((size, size), Image.Resampling.LANCZOS)

            # Save as JPEG
            output = BytesIO()
            img.save(output, 'JPEG', quality=85, optimize=True)
            return output.getvalue()

    except Exception as e:
        raise InvalidOperationException("create thumbnail", str(e)) from e


def extract_image_metadata(file_data: bytes) -> dict[str, Any]:
    """Extract metadata from image file.

    Args:
        file_data: Image file data

    Returns:
        Dictionary containing image metadata

    Raises:
        InvalidOperationException: If metadata extraction fails
    """
    try:
        with Image.open(BytesIO(file_data)) as img:
            metadata = {
                'width': img.width,
                'height': img.height,
                'format': img.format,
                'mode': img.mode,
                'has_transparency': img.mode in ('RGBA', 'LA') or 'transparency' in img.info
            }

            # Extract EXIF data if available
            if hasattr(img, '_getexif') and img._getexif() is not None:
                exif_data = img._getexif()
                if exif_data:
                    metadata['has_exif'] = True
                    # Extract useful EXIF tags
                    for tag_id, value in exif_data.items():
                        if tag_id == 274:  # Orientation
                            metadata['orientation'] = value
                        elif tag_id == 306:  # DateTime
                            metadata['datetime'] = str(value)
                        elif tag_id == 272:  # Model
                            metadata['camera_model'] = str(value)

            return metadata

    except Exception as e:
        raise InvalidOperationException("extract image metadata", str(e)) from e


def optimize_image_for_storage(file_data: bytes, max_size_mb: float = 5.0) -> bytes:
    """Optimize image for storage by reducing quality if needed.

    Args:
        file_data: Image file data
        max_size_mb: Maximum file size in MB

    Returns:
        Optimized image data

    Raises:
        InvalidOperationException: If optimization fails
    """
    max_size_bytes = int(max_size_mb * 1024 * 1024)

    # If already under the limit, return as-is
    if len(file_data) <= max_size_bytes:
        return file_data

    try:
        with Image.open(BytesIO(file_data)) as img:
            # Convert to RGB if necessary
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')

            # Try different quality settings
            for quality in [85, 75, 65, 55, 45]:
                output = BytesIO()
                img.save(output, 'JPEG', quality=quality, optimize=True)
                optimized_data = output.getvalue()

                if len(optimized_data) <= max_size_bytes:
                    return optimized_data

            # If still too large, resize the image
            scale_factor = (max_size_bytes / len(file_data)) ** 0.5
            new_width = int(img.width * scale_factor)
            new_height = int(img.height * scale_factor)

            resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            output = BytesIO()
            resized_img.save(output, 'JPEG', quality=75, optimize=True)
            return output.getvalue()

    except Exception as e:
        raise InvalidOperationException("optimize image", str(e)) from e


def is_image_file(filename: str) -> bool:
    """Check if filename has an image extension.

    Args:
        filename: Filename to check

    Returns:
        True if filename appears to be an image
    """
    image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif', '.svg'}
    return Path(filename).suffix.lower() in image_extensions


def get_content_type_from_format(image_format: str) -> str:
    """Get MIME content type from PIL image format.

    Args:
        image_format: PIL image format (e.g., 'JPEG', 'PNG')

    Returns:
        MIME content type
    """
    format_mapping = {
        'JPEG': 'image/jpeg',
        'PNG': 'image/png',
        'WEBP': 'image/webp',
        'BMP': 'image/bmp',
        'TIFF': 'image/tiff',
        'SVG': 'image/svg+xml'
    }
    return format_mapping.get(image_format, 'image/jpeg')
