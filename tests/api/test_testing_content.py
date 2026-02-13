"""Tests for testing content API endpoints."""

import io
import json
from pathlib import Path

from flask.testing import FlaskClient
from PIL import Image


class TestTestingContentEndpoints:
    """Test testing API content endpoints for Playwright integration."""

    def test_content_image_endpoint_generates_expected_png(self, client: FlaskClient):
        """Test that the content image endpoint returns a deterministic PNG."""
        response = client.get(
            "/api/testing/content/image", query_string={"text": "Hello"}
        )

        assert response.status_code == 200
        assert response.mimetype == "image/png"
        assert (
            response.headers.get("Content-Disposition")
            == "attachment; filename=testing-content-image.png"
        )
        assert (
            response.headers.get("Cache-Control")
            == "no-store, no-cache, must-revalidate, max-age=0"
        )
        assert response.headers.get("Pragma") == "no-cache"

        image_stream = io.BytesIO(response.data)

        with Image.open(image_stream) as image:
            assert image.format == "PNG"
            assert image.mode == "RGB"
            assert image.size == (400, 100)

            background_pixel = image.getpixel((10, 10))
            assert background_pixel == (36, 120, 189)

            has_dark_pixel = any(
                all(channel <= 32 for channel in pixel) for pixel in image.getdata()
            )
            assert has_dark_pixel, "Expected at least one dark pixel representing rendered text"

    def test_content_image_endpoint_requires_text_parameter(self, client: FlaskClient):
        """Test that the content image endpoint enforces required query parameters."""
        response = client.get("/api/testing/content/image")

        assert response.status_code == 400
        payload = response.get_json()

        if isinstance(payload, str):
            payload = json.loads(payload)

        assert isinstance(payload, list)
        assert payload, "Expected validation error details"
        first_error = payload[0]
        assert first_error.get("msg") == "Field required"
        assert first_error.get("loc") == ["text"]

    def test_content_pdf_endpoint_returns_bundled_asset(self, client: FlaskClient):
        """Test that the content PDF endpoint streams the bundled fixture."""
        response = client.get("/api/testing/content/pdf")

        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert (
            response.headers.get("Content-Disposition")
            == "attachment; filename=testing-content.pdf"
        )
        assert (
            response.headers.get("Cache-Control")
            == "no-store, no-cache, must-revalidate, max-age=0"
        )

        # Tests run as `cd test-app && python -m pytest ../tests/`, so CWD is test-app/
        pdf_path = Path.cwd() / "app" / "assets" / "fake-pdf.pdf"
        expected_bytes = pdf_path.read_bytes()
        assert response.data == expected_bytes
        assert response.headers.get("Content-Length") == str(len(expected_bytes))

    def test_content_html_endpoint_renders_expected_markup(self, client: FlaskClient):
        """Test HTML content fixture without banner markup."""
        response = client.get(
            "/api/testing/content/html", query_string={"title": "Fixture Title"}
        )

        assert response.status_code == 200
        assert response.mimetype == "text/html"

        html_body = response.get_data(as_text=True)
        assert "<title>Fixture Title</title>" in html_body
        assert 'data-testid="deployment-notification"' not in html_body
        assert (
            'og:image" content="/api/testing/content/image?text=Fixture+Preview"'
            in html_body
        )
        assert response.headers.get("Content-Length") == str(len(response.data))

    def test_content_html_with_banner_includes_banner_markup(self, client: FlaskClient):
        """Test HTML content fixture that includes the deployment banner markup."""
        response = client.get(
            "/api/testing/content/html-with-banner",
            query_string={"title": "Release Title"},
        )

        assert response.status_code == 200
        html_body = response.get_data(as_text=True)
        assert 'data-testid="deployment-notification"' in html_body
        assert 'data-testid="deployment-notification-reload"' in html_body
        assert "Release Title" in html_body

    def test_content_html_endpoints_require_title(self, client: FlaskClient):
        """HTML fixtures should enforce the required title query parameter."""
        for path in [
            "/api/testing/content/html",
            "/api/testing/content/html-with-banner",
        ]:
            response = client.get(path)
            assert response.status_code == 400
            payload = response.get_json()
            if isinstance(payload, str):
                payload = json.loads(payload)
            first_error = payload[0]
            assert first_error.get("loc") == ["title"]

    def test_testing_service_dependency_injection(self, app, container=None):
        """Test that testing service is properly configured via DI."""
        with app.app_context():
            testing_service = app.container.testing_service()
            assert testing_service is not None
