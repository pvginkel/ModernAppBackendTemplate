"""Dependency injection container for the test app."""

from dependency_injector import containers, providers
from sqlalchemy.orm import sessionmaker

from app.app_config import AppSettings
from app.config import Settings
from app.services.auth_service import AuthService
from app.services.cas_image_service import CasImageService
from app.services.frontend_version_service import FrontendVersionService
from app.services.health_service import HealthService
from app.services.item_service import ItemService
from app.services.metrics_service import MetricsService
from app.services.oidc_client_service import OidcClientService
from app.services.s3_service import S3Service
from app.services.sse_connection_manager import SSEConnectionManager
from app.services.task_service import TaskService
from app.services.testing_service import TestingService
from app.utils.lifecycle_coordinator import LifecycleCoordinator
from app.utils.temp_file_manager import TempFileManager


class ServiceContainer(containers.DeclarativeContainer):
    """Container for service dependency injection."""

    # Configuration providers
    config = providers.Dependency(instance_of=Settings)
    app_config = providers.Dependency(instance_of=AppSettings)
    session_maker = providers.Dependency(instance_of=sessionmaker)
    db_session = providers.ContextLocalSingleton(
        session_maker.provided.call()
    )

    # S3 storage services
    s3_service = providers.Factory(S3Service, settings=config)
    cas_image_service = providers.Factory(
        CasImageService,
        s3_service=s3_service,
        app_settings=app_config,
    )
    testing_service = providers.Factory(TestingService)

    # Lifecycle coordinator - manages startup and graceful shutdown
    lifecycle_coordinator = providers.Singleton(
        LifecycleCoordinator,
        graceful_shutdown_timeout=config.provided.graceful_shutdown_timeout,
    )

    # Health service - callback registry for health checks
    health_service = providers.Singleton(
        HealthService,
        lifecycle_coordinator=lifecycle_coordinator,
        settings=config,
    )

    # Temp file manager
    temp_file_manager = providers.Singleton(
        TempFileManager,
        lifecycle_coordinator=lifecycle_coordinator,
    )

    # Metrics service - background thread for Prometheus metrics
    metrics_service = providers.Singleton(
        MetricsService,
        container=providers.Self(),
        lifecycle_coordinator=lifecycle_coordinator,
    )

    # Auth services - OIDC authentication
    auth_service = providers.Singleton(AuthService, config=config)
    oidc_client_service = providers.Singleton(OidcClientService, config=config)

    # SSE connection manager (always included - TaskService depends on it)
    sse_connection_manager = providers.Singleton(
        SSEConnectionManager,
        gateway_url=config.provided.sse_gateway_url,
        http_timeout=2.0,
    )

    # Task service - in-memory task management
    task_service = providers.Singleton(
        TaskService,
        lifecycle_coordinator=lifecycle_coordinator,
        sse_connection_manager=sse_connection_manager,
        max_workers=config.provided.task_max_workers,
        task_timeout=config.provided.task_timeout_seconds,
        cleanup_interval=config.provided.task_cleanup_interval_seconds,
    )

    # Frontend version service - SSE version notifications
    frontend_version_service = providers.Singleton(
        FrontendVersionService,
        settings=config,
        lifecycle_coordinator=lifecycle_coordinator,
        sse_connection_manager=sse_connection_manager,
    )

    # === App-specific services ===

    # Item service - CRUD operations for items
    item_service = providers.Factory(
        ItemService,
        db_session=db_session,
    )
