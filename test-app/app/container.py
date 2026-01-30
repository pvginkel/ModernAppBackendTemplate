"""Application dependency injection container."""

from dependency_injector import providers

from common.core.container import CommonContainer
from common.core.shutdown import ShutdownCoordinator
from common.metrics.service import MetricsService
from common.metrics.coordinator import MetricsUpdateCoordinator
from common.tasks.service import TaskService
from common.tasks.protocols import NullBroadcaster

from common.sse.connection_manager import ConnectionManager


from common.storage.s3_service import S3Service


from common.auth.oidc import OIDCAuthenticator
from common.auth.oidc_client import OIDCClient



class AppContainer(CommonContainer):
    """Application service container.

    Inherits dependency declarations from CommonContainer:
    - config (must be provided)

    - session_maker (must be provided)
    - db_session (derived from session_maker)

    - settings (alias for config)

    All services are defined here to avoid inheritance issues when
    overriding config with an app-specific Settings type.
    """

    # Shutdown coordinator
    shutdown_coordinator = providers.Singleton(
        ShutdownCoordinator,
        graceful_shutdown_timeout=CommonContainer.config.provided.GRACEFUL_SHUTDOWN_TIMEOUT,
    )

    # Metrics service - minimal, just shutdown metrics and get_metrics_text()
    metrics_service = providers.Singleton(
        MetricsService,
        shutdown_coordinator=shutdown_coordinator,
    )

    # Metrics update coordinator - for services with gauge metrics
    metrics_coordinator = providers.Singleton(
        MetricsUpdateCoordinator,
        shutdown_coordinator=shutdown_coordinator,
    )


    # SSE Gateway connection manager (owns its own metrics)
    connection_manager = providers.Singleton(
        ConnectionManager,
        gateway_url=CommonContainer.config.provided.SSE_GATEWAY_URL,
        http_timeout=2.0,
    )

    # Task service with SSE broadcasting (owns its own metrics)
    task_service = providers.Singleton(
        TaskService,
        shutdown_coordinator=shutdown_coordinator,
        broadcaster=connection_manager,
        max_workers=CommonContainer.config.provided.TASK_MAX_WORKERS,
        task_timeout=CommonContainer.config.provided.TASK_TIMEOUT_SECONDS,
        cleanup_interval=CommonContainer.config.provided.TASK_CLEANUP_INTERVAL_SECONDS,
    )



    # S3 storage service
    s3_service = providers.Factory(S3Service, settings=CommonContainer.config)



    # OIDC authenticator
    oidc_authenticator = providers.Singleton(
        OIDCAuthenticator,
        settings=CommonContainer.config,
    )

    # OIDC client for token exchange
    oidc_client = providers.Singleton(
        OIDCClient,
        settings=CommonContainer.config,
    )


    # Add your application-specific services below
