"""Testing service for test operations like database reset and utilities."""

import html
import io
import logging
from pathlib import Path
from textwrap import dedent
from typing import Any

from PIL import Image, ImageDraw, ImageFont


from app.database import drop_all_tables, upgrade_database

from app.utils.reset_lock import ResetLock

logger = logging.getLogger(__name__)


class TestingService:
    """Service for testing operations like database reset."""

    IMAGE_WIDTH = 400
    IMAGE_HEIGHT = 100
    IMAGE_BACKGROUND_COLOR = "#2478BD"
    IMAGE_TEXT_COLOR = "#000000"
    PREVIEW_IMAGE_QUERY = "Fixture+Preview"
    _PDF_ASSET_PATH = Path(__file__).resolve().parents[1] / "assets" / "fake-pdf.pdf"

    def __init__(self, db: Any, reset_lock: ResetLock):
        self.db = db
        self.reset_lock = reset_lock
        self._cached_pdf_bytes: bytes | None = None


    def reset_database(self, seed: bool = False) -> dict[str, Any]:
        """Reset database to clean state with optional test data seeding."""
        if not self.reset_lock.acquire_reset():
            raise RuntimeError("Database reset already in progress")

        try:
            logger.info("Starting database reset", extra={"seed": seed})

            logger.info("Dropping all database tables")
            drop_all_tables()

            logger.info("Running database migrations")
            applied_migrations = upgrade_database(recreate=True)

            # Call app-specific hooks for master data and test data
            from flask import current_app
            from app.startup import post_migration_hook, load_test_data_hook

            post_migration_hook(current_app)

            if seed:
                logger.info("Loading test dataset via hook")
                load_test_data_hook(current_app)

            self.db.commit()

            return {
                "status": "complete",
                "mode": "testing",
                "seeded": seed,
                "migrations_applied": len(applied_migrations)
            }

        except Exception as e:
            logger.error(f"Database reset failed: {e}", extra={"seed": seed})
            self.db.rollback()
            raise
        finally:
            self.reset_lock.release_reset()


    def is_reset_in_progress(self) -> bool:
        return self.reset_lock.is_resetting()

    def create_fake_image(self, text: str) -> bytes:
        """Create a 400x100 PNG with centered text on a light blue background."""
        font = ImageFont.load_default()

        image = Image.new(
            "RGB",
            (self.IMAGE_WIDTH, self.IMAGE_HEIGHT),
            color=self.IMAGE_BACKGROUND_COLOR
        )

        if text:
            draw = ImageDraw.Draw(image)
            draw.text(
                (self.IMAGE_WIDTH / 2, self.IMAGE_HEIGHT / 2),
                text, font=font, fill=self.IMAGE_TEXT_COLOR, anchor="mm"
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
            banner_markup = dedent("""
                <div id="deployment-notification" data-testid="deployment-notification">
                  A new version of the app is available.
                  <button type="button" data-testid="deployment-notification-reload">
                    Click reload to reload the app.
                  </button>
                </div>
            """).strip()

        html_document = dedent(f"""
            <!DOCTYPE html>
            <html lang="en">
              <head>
                <meta charset="utf-8" />
                <title>{safe_title}</title>
                <meta property="og:title" content="{safe_title}" />
                <meta property="og:image" content="{preview_image_path}" />
              </head>
              <body>
                <div id="__app">
                  {banner_markup}
                  <main>
                    <h1>{safe_title}</h1>
                    <p>Deterministic testing fixture.</p>
                  </main>
                </div>
              </body>
            </html>
        """).strip()

        return html_document
