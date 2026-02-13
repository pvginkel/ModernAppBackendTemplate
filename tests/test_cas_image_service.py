"""Unit tests for CasImageService."""

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from app.app_config import AppSettings
from app.services.cas_image_service import CasImageService


class _TestAppSettings(AppSettings):
    """AppSettings subclass with thumbnail_storage_path for CAS image tests."""

    thumbnail_storage_path: str = "/tmp/thumbnails"


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Create temporary directory for thumbnails."""
    return tmp_path


@pytest.fixture
def mock_s3_service():
    """Create mock S3Service for CasImageService."""
    return MagicMock()


@pytest.fixture
def cas_app_settings(temp_dir: Path):
    return _TestAppSettings(thumbnail_storage_path=str(temp_dir))


@pytest.fixture
def cas_image_service(app: Flask, mock_s3_service, cas_app_settings):
    """Create CasImageService with temporary directory."""
    return CasImageService(mock_s3_service, cas_app_settings)


class TestCasImageService:
    """Test CasImageService functionality."""

    def test_init_creates_thumbnail_directory(
        self, temp_dir, mock_s3_service, cas_app_settings
    ):
        """Test that CasImageService creates thumbnail directory."""
        CasImageService(mock_s3_service, cas_app_settings)
        assert Path(cas_app_settings.thumbnail_storage_path).exists()


def test_convert_image_to_png_success(cas_image_service):
    """Test successful image conversion to PNG."""
    img = Image.new("RGB", (50, 50), color="blue")
    jpeg_buffer = io.BytesIO()
    img.save(jpeg_buffer, format="JPEG")
    jpeg_content = jpeg_buffer.getvalue()

    result = cas_image_service.convert_image_to_png(jpeg_content)

    assert result is not None
    assert result.content_type == "image/png"
    assert isinstance(result.content, bytes)
    assert len(result.content) > 0

    converted_img = Image.open(io.BytesIO(result.content))
    assert converted_img.format == "PNG"
    assert converted_img.size == (50, 50)


def test_convert_image_to_png_with_transparency(cas_image_service):
    """Test image conversion preserves transparency."""
    img = Image.new("RGBA", (32, 32), color=(255, 0, 0, 128))
    png_buffer = io.BytesIO()
    img.save(png_buffer, format="PNG")
    original_content = png_buffer.getvalue()

    result = cas_image_service.convert_image_to_png(original_content)

    assert result is not None
    assert result.content_type == "image/png"

    converted_img = Image.open(io.BytesIO(result.content))
    assert converted_img.mode in ("RGBA", "LA")


def test_convert_image_to_png_invalid_data(cas_image_service):
    """Test conversion with invalid image data."""
    invalid_content = b"This is not an image"

    result = cas_image_service.convert_image_to_png(invalid_content)

    assert result is None


def test_convert_image_to_png_empty_data(cas_image_service):
    """Test conversion with empty data."""
    result = cas_image_service.convert_image_to_png(b"")

    assert result is None
