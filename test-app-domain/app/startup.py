"""App-specific startup hooks for test-app.

These functions are called by the template-owned create_app() factory and by
CLI command handlers at well-defined hook points.

Hook points called by create_app():
  - create_container()
  - register_blueprints()
  - register_error_handlers()

Hook points called by CLI command handlers:
  - post_migration_hook()  -- after upgrade-db migrations
  - load_test_data_hook()  -- after load-test-data database recreation
"""

from flask import Blueprint, Flask

from app.services.container import ServiceContainer


def create_container() -> ServiceContainer:
    """Create and configure the application's service container.

    Returns a fully constructed ServiceContainer with app-specific providers.
    """
    container = ServiceContainer()
    return container


def register_blueprints(api_bp: Blueprint, app: Flask) -> None:
    """Register all app-specific blueprints.

    Domain resource blueprints are registered on api_bp (under /api prefix).
    Template blueprints (health, metrics, testing, SSE, CAS) are registered
    by create_app() directly and are NOT included here.
    """
    if not api_bp._got_registered_once:
        from app.api.items import items_bp

        api_bp.register_blueprint(items_bp)


def register_error_handlers(app: Flask) -> None:
    """Register app-specific error handlers.

    Core + business error handlers are already registered by create_app().
    Add app-specific handlers here if needed.
    """
    pass


def post_migration_hook(app: Flask) -> None:
    """Run after database migrations complete.

    Called by the upgrade-db CLI handler after migrations.
    Use this to sync master data or perform other post-migration tasks.
    """
    pass


def load_test_data_hook(app: Flask) -> None:
    """Load test fixtures after database recreation.

    Called by the load-test-data CLI handler after the database has been
    recreated from scratch.
    """
    pass
