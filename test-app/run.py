"""Development server entry point."""

import logging
import os
import threading

from paste.translogger import TransLogger  # type: ignore[import-untyped]
from waitress import serve

from app import create_app
from app.config import Settings
from app.utils.lifecycle_coordinator import LifecycleEvent


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = Settings.load()
    app = create_app(settings)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))

    lifecycle_coordinator = app.container.lifecycle_coordinator()

    debug_mode = settings.flask_env in ("development", "testing")

    if debug_mode:
        app.logger.info("Running in debug mode")

        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            lifecycle_coordinator.initialize()

        def signal_shutdown(lifecycle_event: LifecycleEvent) -> None:
            if lifecycle_event == LifecycleEvent.AFTER_SHUTDOWN:
                os._exit(0)

        lifecycle_coordinator.register_lifecycle_notification(signal_shutdown)
        app.run(host=host, port=port, debug=True)
    else:
        lifecycle_coordinator.initialize()

        def runner() -> None:
            wsgi = TransLogger(app, setup_console_handler=False)
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
