# Container Refactor: Remove Service Definitions from CommonContainer

## Problem

The current container inheritance model causes issues when apps override `config` with a more specific Settings type:

1. `CommonContainer` defines services that reference `config` (e.g., `shutdown_coordinator` uses `config.provided.GRACEFUL_SHUTDOWN_TIMEOUT`)
2. `AppContainer` (or `ServiceContainer` in ElectronicsInventory) overrides `config` with an app-specific `Settings` type
3. dependency-injector's resolution gets confused when parent class services reference `config` but the child overrides it
4. This causes `RecursionError` or `NameError` depending on how the references are written

We tried several approaches to work around this:
- Using `CommonContainer.provider_name` qualified references → causes recursion
- Re-declaring inherited providers in child class → verbose and error-prone
- Various combinations of the above → keeps breaking in different ways

## Solution

Move all service definitions from `CommonContainer` to `AppContainer`. Keep `CommonContainer` as a minimal base with only `Dependency` declarations (placeholders that must be satisfied by the app).

This works because:
- `Dependency` providers have no resolution logic - they're just placeholders
- All services that reference `config` are defined in `AppContainer`, the same class that owns the `config` override
- No cross-class resolution issues

## Changes Required

### 1. `template/common/core/container.py.jinja`

**Before:** Defines all common services (shutdown_coordinator, metrics_service, task_service, etc.)

**After:** Only Dependency declarations and simple aliases

```python
class CommonContainer(containers.DeclarativeContainer):
    """Base container with dependency declarations.

    Apps must satisfy these dependencies and define all services.
    """

    # Configuration - must be provided by app
    config = providers.Dependency(instance_of=CommonSettings)

    # Alias for settings (used by auth routes)
    settings = providers.Callable(lambda c: c, c=config)

    # Database session maker - must be provided by app (if use_database)
    session_maker = providers.Dependency(instance_of=sessionmaker)
    db_session = providers.ContextLocalSingleton(session_maker.provided.call())
```

**Remove from CommonContainer:**
- `shutdown_coordinator`
- `metrics_service`
- `metrics_coordinator`
- `connection_manager` (use_sse)
- `task_service`
- `s3_service` (use_s3)
- `oidc_authenticator` (use_oidc)
- `oidc_client` (use_oidc)

### 2. `template/app/container.py.jinja`

**Before:** Minimal, just inherits from CommonContainer

**After:** Defines all services explicitly, using Jinja conditionals for feature flags

```python
from dependency_injector import containers, providers
from common.core.container import CommonContainer
from common.core.shutdown import ShutdownCoordinator
from common.metrics.service import MetricsService
from common.metrics.coordinator import MetricsUpdateCoordinator
from common.tasks.service import TaskService
# ... other imports based on feature flags


class AppContainer(CommonContainer):
    """Application service container.

    Inherits dependency declarations from CommonContainer:
    - config (must be provided)
    - session_maker (must be provided, if use_database)
    - db_session (derived from session_maker)
    - settings (alias for config)
    """

    # Shutdown coordinator
    shutdown_coordinator = providers.Singleton(
        ShutdownCoordinator,
        graceful_shutdown_timeout=config.provided.GRACEFUL_SHUTDOWN_TIMEOUT,
    )

    # Metrics service
    metrics_service = providers.Singleton(
        MetricsService,
        shutdown_coordinator=shutdown_coordinator,
    )

    # Metrics update coordinator
    metrics_coordinator = providers.Singleton(
        MetricsUpdateCoordinator,
        shutdown_coordinator=shutdown_coordinator,
    )

    {% if use_sse %}
    # SSE Gateway connection manager
    connection_manager = providers.Singleton(
        ConnectionManager,
        gateway_url=config.provided.SSE_GATEWAY_URL,
        http_timeout=2.0,
    )

    # Task service with SSE broadcasting
    task_service = providers.Singleton(
        TaskService,
        shutdown_coordinator=shutdown_coordinator,
        broadcaster=connection_manager,
        max_workers=config.provided.TASK_MAX_WORKERS,
        task_timeout=config.provided.TASK_TIMEOUT_SECONDS,
        cleanup_interval=config.provided.TASK_CLEANUP_INTERVAL_SECONDS,
    )
    {% else %}
    # Null broadcaster for task service (no SSE)
    _null_broadcaster = providers.Singleton(NullBroadcaster)

    # Task service with null broadcaster
    task_service = providers.Singleton(
        TaskService,
        shutdown_coordinator=shutdown_coordinator,
        broadcaster=_null_broadcaster,
        max_workers=config.provided.TASK_MAX_WORKERS,
        task_timeout=config.provided.TASK_TIMEOUT_SECONDS,
        cleanup_interval=config.provided.TASK_CLEANUP_INTERVAL_SECONDS,
    )
    {% endif %}

    {% if use_s3 %}
    # S3 storage service
    s3_service = providers.Factory(S3Service, settings=config)
    {% endif %}

    {% if use_oidc %}
    # OIDC authenticator
    oidc_authenticator = providers.Singleton(
        OIDCAuthenticator,
        settings=config,
    )

    # OIDC client for token exchange
    oidc_client = providers.Singleton(
        OIDCClient,
        settings=config,
    )
    {% endif %}

    # Add your application-specific services below
```

### 3. No changes needed to type hint files

These files use `CommonContainer` as a type hint and will continue to work since `AppContainer` still inherits from it:
- `template/common/core/app.py.jinja`
- `template/common/core/flask_app.py`
- `template/common/core/runner.py.jinja`

## Migration Steps for Downstream Apps

### Apps generated from template (e.g., ZigbeeControl)

1. Regenerate from template (if using Copier)
2. Or manually:
   - Copy new `common/core/container.py` from template
   - Update `app/container.py` to add all service definitions

### Apps with custom containers (e.g., ElectronicsInventory)

1. Update `common/core/container.py` to remove service definitions
2. Update `app/services/container.py`:
   - Remove unnecessary inheritance workarounds
   - Ensure all required services are defined in ServiceContainer
   - Services can reference `config` directly (no `CommonContainer.` prefix needed)

Example for ElectronicsInventory's ServiceContainer:
```python
class ServiceContainer(CommonContainer):
    # Override config with EI's Settings type
    config = providers.Dependency(instance_of=Settings)

    # All services defined here - no inheritance issues
    shutdown_coordinator = providers.Singleton(
        ShutdownCoordinator,
        graceful_shutdown_timeout=config.provided.GRACEFUL_SHUTDOWN_TIMEOUT,
    )

    # EI's custom MetricsService
    metrics_service = providers.Singleton(
        MetricsService,
        container=providers.Self(),
        shutdown_coordinator=shutdown_coordinator,
    )

    # ... rest of services
```

## Verification

After implementing:

1. Regenerate test-app:
   ```bash
   cd /work/ModernAppTemplate/backend
   rm -rf test-app
   copier copy template test-app --trust \
     -d project_name=test-app \
     -d project_description="Test application" \
     -d author_name="Test Author" \
     -d author_email="test@example.com" \
     -d use_database=true \
     -d use_oidc=true \
     -d use_s3=true \
     -d use_sse=true
   cd test-app && echo "# Test App" > README.md && poetry install
   ```

2. Run template tests:
   ```bash
   cd /work/ModernAppTemplate/backend/test-app
   python -m pytest ../tests/ -v
   python -m pytest tests/ -v
   ```

3. Update ElectronicsInventory and run its tests:
   ```bash
   cd /work/ElectronicsInventory/backend
   python -m pytest tests/ -x -q
   ```

## Changelog Entry

After implementation, add to `changelog.md`:

```markdown
## YYYY-MM-DD

### Container refactor: Services moved from CommonContainer to AppContainer

**What changed:** Moved all service definitions from `CommonContainer` to `AppContainer`.
`CommonContainer` now only contains Dependency declarations (config, session_maker) that
apps must satisfy. This fixes inheritance issues when apps override `config` with a
more specific Settings type.

**Why:** The previous inheritance model caused RecursionError or NameError when:
1. CommonContainer defined services referencing `config`
2. AppContainer overrode `config` with an app-specific type
3. dependency-injector's resolution got confused by cross-class references

**Migration steps:**

1. Update `common/core/container.py`:
   - Copy from template, or
   - Remove all service definitions except `config`, `settings`, `session_maker`, `db_session`

2. Update `app/container.py` (or `app/services/container.py`):
   - Add service definitions that were removed from CommonContainer:
     - `shutdown_coordinator`
     - `metrics_service`
     - `metrics_coordinator`
     - `task_service`
     - `connection_manager` (if using SSE)
     - `s3_service` (if using S3)
     - `oidc_authenticator`, `oidc_client` (if using OIDC)
   - Services can now reference `config` directly without qualification

3. If you had workarounds like `CommonContainer.shutdown_coordinator` or re-declared
   inherited providers, remove them - direct references now work.
```
