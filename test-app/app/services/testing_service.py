"""Testing service for test utilities like content generation and auth sessions."""

import html
import io
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

logger = logging.getLogger(__name__)


@dataclass
class TestSession:
    """Represents a test authentication session."""

    subject: str
    name: str | None
    email: str | None
    roles: list[str]


class TestingService:
    """Service for testing utilities like deterministic content generation and auth sessions.

    Test session state is stored at the class level so it persists across
    Factory-created instances within the same process.
    """

    IMAGE_WIDTH = 400
    IMAGE_HEIGHT = 100
    IMAGE_BACKGROUND_COLOR = "#2478BD"
    IMAGE_TEXT_COLOR = "#000000"
    PREVIEW_IMAGE_QUERY = "Fixture+Preview"
    _PDF_ASSET_PATH = Path(__file__).resolve().parents[1] / "assets" / "fake-pdf.pdf"

    # Class-level storage for test sessions (token -> session data)
    _sessions: dict[str, TestSession] = {}

    # Forced error status for /api/auth/self (single-shot)
    _forced_auth_error: int | None = None

    def __init__(self) -> None:
        self._cached_pdf_bytes: bytes | None = None

    # ── Test session management ──────────────────────────────────────

    def create_session(
        self,
        subject: str,
        name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> str:
        """Create a test session and return a session token.

        Args:
            subject: User subject identifier
            name: User display name
            email: User email address
            roles: User roles (defaults to empty list)

        Returns:
            Session token to be stored in cookie
        """
        token = f"test-session-{secrets.token_urlsafe(16)}"
        session = TestSession(
            subject=subject,
            name=name,
            email=email,
            roles=roles or [],
        )
        TestingService._sessions[token] = session

        logger.info(
            "Created test session: subject=%s name=%s email=%s roles=%s",
            subject,
            name,
            email,
            roles,
        )

        return token

    def get_session(self, token: str) -> TestSession | None:
        """Get a test session by token.

        Args:
            token: Session token from cookie

        Returns:
            TestSession if found, None otherwise
        """
        return TestingService._sessions.get(token)

    def clear_session(self, token: str) -> bool:
        """Clear a test session.

        Args:
            token: Session token to clear

        Returns:
            True if session was cleared, False if not found
        """
        if token in TestingService._sessions:
            del TestingService._sessions[token]
            logger.info("Cleared test session")
            return True
        return False

    def clear_all_sessions(self) -> None:
        """Clear all test sessions (for test isolation)."""
        TestingService._sessions.clear()
        logger.debug("Cleared all test sessions")

    def set_forced_auth_error(self, status_code: int) -> None:
        """Set a forced error for the next /api/auth/self request.

        Args:
            status_code: HTTP status code to return
        """
        TestingService._forced_auth_error = status_code
        logger.info("Set forced auth error: status=%d", status_code)

    def consume_forced_auth_error(self) -> int | None:
        """Consume and return the forced auth error (single-shot).

        Returns:
            HTTP status code if set, None otherwise
        """
        error = TestingService._forced_auth_error
        TestingService._forced_auth_error = None
        if error:
            logger.info("Consumed forced auth error: status=%d", error)
        return error

    # ── Content generation (requires Pillow — S3 feature) ────────────

    def create_fake_image(self, text: str) -> bytes:
        """Create a 400x100 PNG with centered text on a light blue background."""
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.load_default()

        image = Image.new(
            "RGB",
            (self.IMAGE_WIDTH, self.IMAGE_HEIGHT),
            color=self.IMAGE_BACKGROUND_COLOR,
        )

        if text:
            draw = ImageDraw.Draw(image)
            draw.text(
                (self.IMAGE_WIDTH / 2, self.IMAGE_HEIGHT / 2),
                text,
                font=font,
                fill=self.IMAGE_TEXT_COLOR,
                anchor="mm",
            )

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def get_pdf_fixture(self) -> bytes:
        """Return the deterministic PDF asset bundled with the application."""
        if self._cached_pdf_bytes is None:
            self._cached_pdf_bytes = self._PDF_ASSET_PATH.read_bytes()
        return self._cached_pdf_bytes

    def render_html_fixture(self, title: str, include_banner: bool = False) -> str:
        """Render deterministic HTML content for Playwright fixtures."""
        safe_title = html.escape(title)
        preview_image_path = f"/api/testing/content/image?text={self.PREVIEW_IMAGE_QUERY}"

        banner_markup = ""
        if include_banner:
            banner_markup = dedent(
                """
                <div
                  id="deployment-notification"
                  class="deployment-notification w-full bg-blue-600 text-white px-4 py-3 text-center text-sm font-medium shadow-md"
                  data-testid="deployment-notification"
                >
                  A new version of the app is available.
                  <button
                    type="button"
                    data-testid="deployment-notification-reload"
                    class="underline hover:no-underline font-semibold focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2 focus:ring-offset-blue-600 rounded px-1"
                  >
                    Click reload to reload the app.
                  </button>
                </div>
                """
            ).strip()

        html_document = dedent(
            f"""
            <!DOCTYPE html>
            <html lang="en">
              <head>
                <meta charset="utf-8" />
                <meta http-equiv="X-UA-Compatible" content="IE=edge" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>{safe_title}</title>
                <meta name="description" content="Deterministic testing fixture" />
                <meta property="og:title" content="{safe_title}" />
                <meta property="og:type" content="article" />
                <meta property="og:image" content="{preview_image_path}" />
                <meta property="og:image:alt" content="Preview image for Playwright fixture" />
                <meta name="twitter:card" content="summary_large_image" />
                <meta name="twitter:title" content="{safe_title}" />
                <meta name="twitter:image" content="{preview_image_path}" />
                <link rel="icon" href="{preview_image_path}" />
                <style>
                  body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    margin: 0;
                    padding: 0;
                    background: #f5f7fa;
                    color: #1f2933;
                  }}
                  main {{
                    max-width: 720px;
                    margin: 3rem auto;
                    background: #ffffff;
                    padding: 2rem;
                    border-radius: 12px;
                    box-shadow: 0 10px 25px rgba(15, 23, 42, 0.1);
                  }}
                  h1 {{
                    margin-top: 0;
                    font-size: 2rem;
                    color: #111827;
                  }}
                  p {{
                    line-height: 1.6;
                    margin-bottom: 1rem;
                  }}
                  .meta {{
                    font-size: 0.875rem;
                    color: #4b5563;
                    margin-bottom: 2rem;
                  }}
                </style>
              </head>
              <body>
                <div id="__app">
                  {banner_markup}
                  <main>
                    <h1>{safe_title}</h1>
                    <div class="meta">Fixture generated for deterministic Playwright document ingestion.</div>
                    <p>
                      This page is served by the backend testing utilities. It exposes
                      predictable content for validating document ingestion, HTML metadata extraction, and banner
                      detection flows without relying on external services.
                    </p>
                    <p>
                      The associated preview image is hosted at <code>{preview_image_path}</code> and is referenced
                      via Open Graph and Twitter metadata.
                    </p>
                  </main>
                </div>
              </body>
            </html>
            """
        ).strip()

        return html_document
