"""Flask application factory."""

import logging

from flask import g
from flask_cors import CORS

from app.app import App
from app.config import Settings

from app.extensions import db



def create_app(settings: "Settings | None" = None, skip_background_services: bool = False) -> App:
    """Create and configure Flask application.

    This factory follows a hook-based pattern where app-specific behavior
    is injected through three functions in app/startup.py:
    - create_container(): builds the DI container with app-specific providers
    - register_blueprints(): registers domain resource blueprints
    - register_error_handlers(): registers app-specific error handlers
    """
    app = App(__name__)

    # Load configuration
    if settings is None:
        settings = Settings.load()

    # Validate configuration before proceeding
    settings.validate_production_config()

    app.config.from_object(settings.to_flask_config())


    # Initialize extensions
    db.init_app(app)

    # Import models to register them with SQLAlchemy
    from app import models  # noqa: F401

    # Import empty string normalization to register event handlers
    from app.utils import empty_string_normalization  # noqa: F401

    # Initialize SessionLocal for per-request sessions
    with app.app_context():
        from sqlalchemy.orm import Session, sessionmaker

        SessionLocal: sessionmaker[Session] = sessionmaker(
            class_=Session,
            bind=db.engine,
            autoflush=True,
            expire_on_commit=False,
        )

        # Enable SQLAlchemy pool logging via events if configured
        if settings.db_pool_echo:
            from app.utils.pool_diagnostics import setup_pool_logging

            setup_pool_logging(db.engine)


    # Initialize SpecTree for OpenAPI docs
    from app.utils.spectree_config import configure_spectree

    configure_spectree(app)

    # --- Hook 1: Create service container ---
    from app.startup import create_container

    container = create_container()
    container.config.override(settings)

    container.session_maker.override(SessionLocal)


    # Wire container to all API modules via package scanning
    container.wire(packages=['app.api'])

    app.container = container

    # Configure CORS
    CORS(app, origins=settings.cors_origins)

    # Initialize correlation ID tracking
    from app.utils import _init_request_id
    _init_request_id(app)


    # Set up log capture handler in testing mode
    if settings.is_testing:
        from app.utils.log_capture import LogCaptureHandler
        log_handler = LogCaptureHandler.get_instance()

        # Set lifecycle coordinator for connection_close events
        lifecycle_coordinator = container.lifecycle_coordinator()
        log_handler.set_lifecycle_coordinator(lifecycle_coordinator)

        # Attach to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        root_logger.setLevel(logging.INFO)

        app.logger.info("Log capture handler initialized for testing mode")


    # Register error handlers: core + business (template), then app-specific hook
    from app.utils.flask_error_handlers import (
        register_business_error_handlers,
        register_core_error_handlers,
    )

    register_core_error_handlers(app)
    register_business_error_handlers(app)

    # --- Hook 2: App-specific error handlers ---
    from app.startup import register_error_handlers

    register_error_handlers(app)

    # Register main API blueprint (includes auth hooks and auth_bp)
    from app.api import api_bp

    # --- Hook 3: App-specific blueprint registrations ---
    from app.startup import register_blueprints

    register_blueprints(api_bp, app)

    app.register_blueprint(api_bp)

    # Register template blueprints directly on the app (not under /api)
    from app.api.health import health_bp
    from app.api.metrics import metrics_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(metrics_bp)

    # Always register testing blueprint (runtime check handles access control)
    from app.api.testing import testing_bp
    app.register_blueprint(testing_bp)


    # Register SSE Gateway callback blueprint
    from app.api.sse import sse_bp
    app.register_blueprint(sse_bp)



    # Register CAS (Content-Addressable Storage) blueprint
    from app.api.cas import cas_bp
    app.register_blueprint(cas_bp)



    @app.teardown_request
    def close_session(exc: Exception | None) -> None:
        """Close the database session after each request."""
        try:
            db_session = container.db_session()

            needs_rollback = exc or getattr(g, "needs_rollback", False)
            if needs_rollback:
                db_session.rollback()
            else:
                db_session.commit()

            db_session.close()

        finally:
            container.db_session.reset()


    # Start background services only when not in CLI mode
    if not skip_background_services:
        # Start temp file manager cleanup thread during app creation
        temp_file_manager = container.temp_file_manager()
        temp_file_manager.start_cleanup_thread()


        # Ensure S3 bucket exists during startup
        try:
            s3_service = container.s3_service()
            s3_service.ensure_bucket_exists()
        except Exception as e:
            app.logger.warning(f"Failed to ensure S3 bucket exists: {e}")


        # Initialize metrics polling and start thread
        try:
            metrics_service = container.metrics_service()
            metrics_service.start_background_updater(settings.metrics_update_interval)
            app.logger.info("Prometheus metrics polling started")
        except Exception as e:
            app.logger.warning(f"Failed to start metrics polling: {e}")


        # Initialize request diagnostics if enabled
        from app.services.diagnostics_service import DiagnosticsService
        diagnostics_service = DiagnosticsService(settings)
        with app.app_context():
            diagnostics_service.init_app(app, db.engine)
        app.diagnostics_service = diagnostics_service



        # Initialize VersionService singleton to register its observer callback
        # with ConnectionManager. Must happen before fire_startup().
        container.version_service()


        # Signal that application startup is complete
        container.lifecycle_coordinator().fire_startup()

    return app
