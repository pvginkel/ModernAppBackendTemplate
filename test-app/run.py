"""Development server entry point."""

import logging
import os
import threading

from paste.translogger import TransLogger  # type: ignore[import-untyped]
from waitress import serve

from app import create_app
from app.config import Settings
from app.consts import DEFAULT_BACKEND_PORT
from app.utils.lifecycle_coordinator import LifecycleEvent


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = Settings.load()
    app = create_app(settings)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", DEFAULT_BACKEND_PORT))

    # Get and initialize the lifecycle coordinator
    lifecycle_coordinator = app.container.lifecycle_coordinator()

    # Enable debug mode for development and testing environments
    debug_mode = settings.flask_env in ("development", "testing")

    if debug_mode:
        app.logger.info("Running in debug mode")

        # Only initialize the shutdown coordinator if we're in an actual
        # Flask worker process.
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            lifecycle_coordinator.initialize()

        def signal_shutdown(lifecycle_event: LifecycleEvent) -> None:
            if lifecycle_event == LifecycleEvent.AFTER_SHUTDOWN:
                # Need to call. os._exit. sys.exit doesn't work with the
                # reloader is exit.
                os._exit(0)

        lifecycle_coordinator.register_lifecycle_notification(signal_shutdown)

        app.run(host=host, port=port, debug=True)
    else:
        lifecycle_coordinator.initialize()

        def runner() -> None:
            # Production: Use Waitress WSGI server
            wsgi = TransLogger(app, setup_console_handler=False)

            # Thread count balances concurrency with DB connection pool size.
            # With pool_size=20 + max_overflow=30 = 50 connections available,
            # we match Waitress threads to avoid silent connection pool queuing.
            threads = int(os.getenv("WAITRESS_THREADS", 50))
            wsgi.logger.info(f"Using Waitress WSGI server with {threads} threads")
            serve(wsgi, host=host, port=port, threads=threads)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        event = threading.Event()

        def signal_shutdown_prod(lifecycle_event: LifecycleEvent) -> None:
            if lifecycle_event == LifecycleEvent.AFTER_SHUTDOWN:
                event.set()

        lifecycle_coordinator.register_lifecycle_notification(signal_shutdown_prod)

        event.wait()

if __name__ == "__main__":
    main()
