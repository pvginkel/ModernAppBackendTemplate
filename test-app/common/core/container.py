"""Base dependency injection container."""

from dependency_injector import containers, providers

from sqlalchemy.orm import sessionmaker

from common.core.settings import CommonSettings
from common.core.shutdown import ShutdownCoordinator
from common.metrics.service import MetricsService
from common.tasks.service import TaskService

from common.sse.connection_manager import ConnectionManager


from common.storage.s3_service import S3Service


from common.auth.oidc import OIDCAuthenticator



class CommonContainer(containers.DeclarativeContainer):
    """Base container with common infrastructure services.

    Apps should extend this class to add their own services:

        from common.core.container import CommonContainer

        class AppContainer(CommonContainer):
            my_service = providers.Factory(MyService, db=CommonContainer.db_session)
    """

    # Configuration - must be overridden by app
    config = providers.Dependency(instance_of=CommonSettings)

    # Database session maker - must be overridden by app
    session_maker = providers.Dependency(instance_of=sessionmaker)
    db_session = providers.ContextLocalSingleton(session_maker.provided.call())

    # Shutdown coordinator - singleton
    shutdown_coordinator = providers.Singleton(
        ShutdownCoordinator,
        graceful_shutdown_timeout=config.provided.GRACEFUL_SHUTDOWN_TIMEOUT,
    )

    # Metrics service - singleton with background updater
    metrics_service = providers.Singleton(
        MetricsService,
        shutdown_coordinator=shutdown_coordinator,
    )

    # SSE Gateway connection manager
    connection_manager = providers.Singleton(
        ConnectionManager,
        gateway_url=config.provided.SSE_GATEWAY_URL,
        metrics_service=metrics_service,
        http_timeout=2.0,
    )

    # Task service with SSE broadcasting
    task_service = providers.Singleton(
        TaskService,
        metrics_service=metrics_service,
        shutdown_coordinator=shutdown_coordinator,
        broadcaster=connection_manager,
        max_workers=config.provided.TASK_MAX_WORKERS,
        task_timeout=config.provided.TASK_TIMEOUT_SECONDS,
        cleanup_interval=config.provided.TASK_CLEANUP_INTERVAL_SECONDS,
    )


    # S3 storage service
    s3_service = providers.Factory(S3Service, settings=config)


    # OIDC authenticator
    oidc_authenticator = providers.Singleton(
        OIDCAuthenticator,
        settings=config,
    )

