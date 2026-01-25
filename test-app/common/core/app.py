"""Flask application factory."""

import logging
from typing import TYPE_CHECKING

from flask_cors import CORS

from common.core.flask_app import App
from common.core.spectree import configure_spectree

if TYPE_CHECKING:
    from common.core.container import CommonContainer
    from common.core.settings import CommonSettings

logger = logging.getLogger(__name__)


def create_app(
    container_class: type["CommonContainer"],
    settings: "CommonSettings | None" = None,
    skip_background_services: bool = False,
) -> App:
    """Create and configure the Flask application.

    Args:
        container_class: The container class to use (app's extended container)
        settings: Optional settings instance (created if not provided)
        skip_background_services: Skip starting background threads (for CLI/tests)

    Returns:
        Configured Flask application instance
    """
    app = App(__name__)

    # Load configuration
    if settings is None:
        from app.config import get_settings
        settings = get_settings()

    app.config.from_object(settings)

    # Initialize Flask-SQLAlchemy
    from common.database.extensions import db
    db.init_app(app)

    # Import models to register them with SQLAlchemy
    from app import models  # noqa: F401

    # Initialize SessionLocal for per-request sessions
    with app.app_context():
        from sqlalchemy.orm import Session, sessionmaker

        SessionLocal: sessionmaker[Session] = sessionmaker(
            class_=Session,
            bind=db.engine,
            autoflush=True,
            expire_on_commit=False,
        )

    # Initialize SpecTree for OpenAPI docs
    # App should call configure_spectree with its own title/description
    from app import configure_app_spectree
    configure_app_spectree(app)

    # Initialize service container
    container = container_class()
    container.config.override(settings)

    container.session_maker.override(SessionLocal)

    # Wire container with app's API modules
    from app import get_wire_modules
    wire_modules = get_wire_modules()
    container.wire(modules=wire_modules)

    app.container = container

    # Configure CORS
    CORS(app, origins=settings.CORS_ORIGINS)

    # Register error handlers
    from common.core.errors import register_error_handlers
    register_error_handlers(app)

    # Register health blueprint
    from common.health.routes import health_bp
    app.register_blueprint(health_bp)

    # Register metrics blueprint
    from common.metrics.routes import metrics_bp
    app.register_blueprint(metrics_bp)

    # Register SSE callback blueprint
    from common.sse.routes import sse_bp
    app.register_blueprint(sse_bp)

    # Register app's API blueprints
    from app.api import api_bp
    app.register_blueprint(api_bp)

    # Request teardown handler for database session management
    @app.teardown_request
    def close_session(exc: Exception | None) -> None:
        """Close the database session after each request."""
        try:
            db_session = container.db_session()
            needs_rollback = db_session.info.get("needs_rollback", False)

            if exc or needs_rollback:
                db_session.rollback()
            else:
                db_session.commit()

            db_session.info.pop("needs_rollback", None)
            db_session.close()

        finally:
            container.db_session.reset()

    # Start background services
    if not skip_background_services:
        # Initialize metrics service background updater
        try:
            metrics_service = container.metrics_service()
            metrics_service.start_background_updater(settings.METRICS_UPDATE_INTERVAL)
            app.logger.info("Prometheus metrics collection started")
        except Exception as e:
            app.logger.warning(f"Failed to start metrics collection: {e}")

        # Ensure S3 bucket exists
        try:
            s3_service = container.s3_service()
            s3_service.ensure_bucket_exists()
        except Exception as e:
            app.logger.warning(f"Failed to ensure S3 bucket exists: {e}")

    return app
