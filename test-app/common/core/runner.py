"""Application runner with graceful shutdown support."""

import logging
import os
import threading
from typing import TYPE_CHECKING

from waitress import serve

from common.core.shutdown import LifetimeEvent

if TYPE_CHECKING:
    from common.core.container import CommonContainer


def run(container_class: type["CommonContainer"]) -> None:
    """Run the application with the given container class.

    This is the main entry point for running the application. It handles:
    - Logging setup
    - App creation via create_app()
    - Development vs production server selection
    - Graceful shutdown coordination

    Args:
        container_class: The app's container class (extends CommonContainer)

    Usage in run.py:
        from common import run
        from app.container import AppContainer

        if __name__ == "__main__":
            run(AppContainer)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Import here to avoid circular imports
    from common.core.app import create_app

    app = create_app(container_class)
    settings = app.config

    host = settings.get("HOST", "0.0.0.0")
    port = int(settings.get("PORT", 5000))

    # Get shutdown coordinator
    shutdown_coordinator = app.container.shutdown_coordinator()

    # Check environment
    flask_env = settings.get("FLASK_ENV", "development")
    debug_mode = flask_env in ("development", "testing")

    if debug_mode:
        app.logger.info("Running in debug mode")

        # Only initialize shutdown coordinator in actual Flask worker process
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            shutdown_coordinator.initialize()

        def signal_shutdown(lifetime_event: LifetimeEvent) -> None:
            if lifetime_event == LifetimeEvent.AFTER_SHUTDOWN:
                # Need os._exit because sys.exit doesn't work with reloader
                os._exit(0)

        shutdown_coordinator.register_lifetime_notification(signal_shutdown)

        app.run(host=host, port=port, debug=True)
    else:
        # Production mode
        shutdown_coordinator.initialize()

        def runner() -> None:
            threads = int(os.getenv("WAITRESS_THREADS", "4"))
            app.logger.info(f"Using Waitress WSGI server with {threads} threads")
            serve(app, host=host, port=port, threads=threads)

        # Run server in daemon thread so shutdown coordinator controls exit
        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        # Wait for shutdown signal
        event = threading.Event()

        def signal_shutdown_prod(lifetime_event: LifetimeEvent) -> None:
            if lifetime_event == LifetimeEvent.AFTER_SHUTDOWN:
                event.set()

        shutdown_coordinator.register_lifetime_notification(signal_shutdown_prod)

        event.wait()
