"""Dependency injection container for test-app."""

from dependency_injector import containers, providers

from sqlalchemy.orm import sessionmaker

from app.config import Settings

from app.services.auth_service import AuthService
from app.services.oidc_client_service import OidcClientService

from app.services.connection_manager import ConnectionManager

from app.services.version_service import VersionService


from app.services.image_service import ImageService
from app.services.s3_service import S3Service

from app.services.item_service import ItemService
from app.services.metrics_service import MetricsService
from app.services.task_service import TaskService
from app.services.testing_service import TestingService
from app.utils.lifecycle_coordinator import LifecycleCoordinator
from app.utils.reset_lock import ResetLock
from app.utils.temp_file_manager import TempFileManager


class ServiceContainer(containers.DeclarativeContainer):
    """Container for test-app services.

    Infrastructure providers are defined here. App-specific providers
    should be added in this file after generation (this file is _skip_if_exists).
    """

    # Configuration and database session providers
    config = providers.Dependency(instance_of=Settings)

    session_maker = providers.Dependency(instance_of=sessionmaker)
    db_session = providers.ContextLocalSingleton(
        session_maker.provided.call()
    )


    # S3 and image services
    s3_service = providers.Factory(S3Service, settings=config)
    image_service = providers.Factory(ImageService, s3_service=s3_service, settings=config)


    # Lifecycle coordinator
    lifecycle_coordinator = providers.Singleton(
        LifecycleCoordinator,
        graceful_shutdown_timeout=config.provided.graceful_shutdown_timeout,
    )

    # Utility services
    temp_file_manager = providers.Singleton(
        TempFileManager,
        base_path="/tmp/test-app_cache",
        cleanup_age_hours=24,
        lifecycle_coordinator=lifecycle_coordinator,
    )

    # Metrics service
    metrics_service = providers.Singleton(
        MetricsService,
        container=providers.Self(),
        lifecycle_coordinator=lifecycle_coordinator,
    )


    # Auth services
    auth_service = providers.Singleton(AuthService, config=config)
    oidc_client_service = providers.Singleton(OidcClientService, config=config)



    # ConnectionManager for SSE Gateway
    connection_manager = providers.Singleton(
        ConnectionManager,
        gateway_url=config.provided.sse_gateway_url,
        http_timeout=2.0,
    )


    # TaskService
    task_service = providers.Singleton(
        TaskService,
        lifecycle_coordinator=lifecycle_coordinator,
        connection_manager=connection_manager,
        max_workers=config.provided.task_max_workers,
        task_timeout=config.provided.task_timeout_seconds,
        cleanup_interval=config.provided.task_cleanup_interval_seconds,
    )


    # Version service
    version_service = providers.Singleton(
        VersionService,
        settings=config,
        lifecycle_coordinator=lifecycle_coordinator,
        connection_manager=connection_manager,
    )


    # Testing utilities
    reset_lock = providers.Singleton(ResetLock)
    testing_service = providers.Factory(
        TestingService,

        db=db_session,

        reset_lock=reset_lock,
    )

    # --- App-specific providers ---
    item_service = providers.Factory(
        ItemService,
        db=db_session,
    )
