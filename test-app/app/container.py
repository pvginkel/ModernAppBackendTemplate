"""Application dependency injection container."""

from dependency_injector import providers

from common.core.container import CommonContainer


class AppContainer(CommonContainer):
    """Application-specific service container.

    Extend CommonContainer with your application's services.
    Common services are available via inheritance:
    - shutdown_coordinator
    - metrics_service
    - task_service

    - db_session


    - s3_service


    - connection_manager


    Example:
        from app.services.my_service import MyService

        my_service = providers.Factory(
            MyService,
            db=CommonContainer.db_session,  # if using database
        )
    """

    # Add your application-specific services here
    pass
