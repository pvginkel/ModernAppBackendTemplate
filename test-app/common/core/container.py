"""Base dependency injection container."""

from dependency_injector import containers, providers

from sqlalchemy.orm import sessionmaker

from common.core.settings import CommonSettings


class CommonContainer(containers.DeclarativeContainer):
    """Base container with dependency declarations.

    Apps must satisfy these dependencies and define all services.

    This container only contains:
    - Dependency declarations (placeholders that apps must provide)
    - Simple derived providers (like db_session from session_maker)

    All service definitions (shutdown_coordinator, metrics_service, etc.)
    belong in AppContainer. This avoids inheritance issues when apps
    override `config` with a more specific Settings type.
    """

    # Configuration - must be provided by app
    config = providers.Dependency(instance_of=CommonSettings)

    # Alias for settings (used by auth routes)
    settings = providers.Callable(lambda c: c, c=config)


    # Database session maker - must be provided by app
    session_maker = providers.Dependency(instance_of=sessionmaker)
    db_session = providers.ContextLocalSingleton(session_maker.provided.call())

