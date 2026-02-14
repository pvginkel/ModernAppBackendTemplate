# Porting an Existing App onto the Template

This guide documents the strategy for porting an existing Flask backend onto the ModernAppTemplate. It's based on the successful IoTSupport port and covers everything from initial generation to enabling feature flags post-port.

## Overview

The porting process has three phases:

1. **Generate** a fresh project with `copier copy` using the right feature flags
2. **Port** domain code into the generated project, adapting it to the template's hook contract
3. **Enable additional flags** later via `copier update -d` if you started with a subset

The key insight: the template owns infrastructure, the app owns domain logic. Porting means moving your domain code into the template's structure and wiring it through well-defined hook points.

## Prerequisites

- A working existing backend to port from
- The template repo at `/work/ModernAppTemplate/backend` with a version tag
- Understanding of which feature flags your app needs:

| Flag | What it adds |
|------|-------------|
| `use_database` | SQLAlchemy, Alembic, migrations, pool diagnostics, diagnostics service |
| `use_oidc` | OIDC auth (BFF pattern with JWT cookies), auth endpoints, testing auth |
| `use_s3` | S3 storage, CAS endpoints, image processing, testing content |
| `use_sse` | Server-Sent Events, SSE Gateway integration, frontend version notifications |

You can start with a subset of flags and enable more later (see Phase 3).

## Phase 1: Generate the Project

### 1.1 Run copier copy

```bash
cd /work/ModernAppTemplate/backend
poetry run copier copy . /work/<YourProject>/backend-new --trust \
  -d project_name=your-project \
  -d project_description="Your project description" \
  -d author_name="Your Name" \
  -d author_email="you@example.com" \
  -d repo_url="https://github.com/you/your-project.git" \
  -d image_name="registry:5000/your-project" \
  -d database_name=your_database \
  -d backend_port=5000 \
  -d use_database=true \
  -d use_oidc=true \
  -d use_s3=false \
  -d use_sse=false
```

This creates a working skeleton. The template generates two categories of files:

**Template-maintained** (overwritten by `copier update`):
- `app/__init__.py` — app factory
- `app/config.py` — infrastructure Settings
- `app/cli.py` — CLI entry point
- `app/database.py`, `app/extensions.py` — database setup
- `app/api/auth.py`, `app/api/health.py`, `app/api/metrics.py`, etc. — infrastructure endpoints
- `app/services/auth_service.py`, `app/services/health_service.py`, etc. — infrastructure services
- `app/utils/auth.py`, `app/utils/flask_error_handlers.py`, etc. — infrastructure utilities
- `tests/conftest_infrastructure.py` — test fixtures
- `alembic/env.py`, scripts, Jenkinsfile

**App-maintained** (`_skip_if_exists` — generated once as scaffolds):
- `app/startup.py` — hook implementations
- `app/services/container.py` — DI container
- `app/exceptions.py` — exception classes
- `app/consts.py` — project constants
- `app/app_config.py` — app-specific settings
- `app/models/__init__.py` — model imports
- `pyproject.toml` — dependencies
- `tests/conftest.py` — test fixtures
- `.env.example`, `Dockerfile`

### 1.2 Verify the skeleton works

```bash
cd /work/<YourProject>/backend-new
poetry install
python -m pytest tests/ -v
```

The scaffold should pass basic tests out of the box.

## Phase 2: Port Domain Code

### 2.1 Settings split — the most important concept

The template enforces a clean separation between infrastructure and domain settings:

- **`app/config.py` → `Settings`** (template-owned): flask_env, database_url, OIDC config, CORS, S3 credentials, SSE gateway URL, graceful shutdown timeout, task worker config
- **`app/app_config.py` → `AppSettings`** (app-owned): everything specific to your domain

**Example** — IoTSupport's `AppSettings` contains MQTT config, Keycloak admin credentials, Elasticsearch config, device provisioning URLs, rotation schedule, firmware paths. None of that belongs in the template's `Settings`.

Create your `AppSettings` class with a `load()` factory method:

```python
# app/app_config.py
from pydantic import BaseModel
from pydantic_settings import BaseSettings

class AppEnvironment(BaseSettings):
    """Raw environment variables for the app."""
    mqtt_url: str = ""
    mqtt_username: str = ""
    # ... your env vars

class AppSettings(BaseModel):
    """Normalized app settings with computed fields."""
    mqtt_url: str
    mqtt_username: str
    # ... your fields

    @staticmethod
    def load() -> "AppSettings":
        env = AppEnvironment()
        return AppSettings(
            mqtt_url=env.mqtt_url,
            mqtt_username=env.mqtt_username,
            # ...
        )
```

### 2.2 DI container — wiring domain services

Edit `app/services/container.py` to add your domain service providers alongside the template's infrastructure providers.

**Key rules:**
- Infrastructure settings use `config` (instance of `Settings`)
- Domain settings use `app_config` (instance of `AppSettings`)
- Services needing a database session use `providers.Factory` with `db_session`
- Stateful singletons (MQTT connections, etc.) use `providers.Singleton`

```python
from app.services.container import ServiceContainer  # template scaffold

class ServiceContainer(containers.DeclarativeContainer):
    # --- Template infrastructure (keep these) ---
    config = providers.Dependency(instance_of=Settings)
    app_config = providers.Dependency(instance_of=AppSettings)
    session_maker = providers.Dependency(instance_of=sessionmaker)
    db_session = providers.ContextLocalSingleton(session_maker.provided.call())

    # ... all template infrastructure providers ...

    # --- Your domain services ---
    your_service = providers.Factory(
        YourService,
        db=db_session,
        config=app_config,  # domain settings, NOT infrastructure
    )
```

**Common mistake:** Using `Provide[ServiceContainer.config]` (infrastructure `Settings`) when you need `Provide[ServiceContainer.app_config]` (domain `AppSettings`). If an endpoint accesses domain-specific fields (MQTT URL, Keycloak admin, etc.), it needs `app_config`.

### 2.3 Startup hooks — the app's entry points

Edit `app/startup.py` to implement the hook contract:

#### `create_container()`
Return a `ServiceContainer` instance. Do any post-construction wiring here (e.g., registering URL interceptors, setting back-references on services).

#### `register_blueprints(api_bp, app)`
Register your domain blueprints on `api_bp`. **Critical**: guard against double-registration:

```python
def register_blueprints(api_bp: Blueprint, app: Flask) -> None:
    if not api_bp._got_registered_once:
        from app.api.your_endpoint import your_bp
        api_bp.register_blueprint(your_bp)

    # Blueprints that need direct app registration (rare):
    # app.register_blueprint(special_bp)
```

**Why the guard?** `api_bp` is a module-level singleton. Flask sets `_got_registered_once=True` after the first `app.register_blueprint(api_bp)`. Test suites call `create_app()` multiple times, so without the guard, the second call would fail trying to re-register sub-blueprints.

You can also do post-registration initialization here:
```python
    # Initialize services that need the container after blueprints are set up
    container = app.container
    container.some_service().container = container  # back-reference for background threads
    container.mqtt_service()  # triggers lifecycle registration
```

#### `register_error_handlers(app)`
Register handlers for your domain-specific exceptions:

```python
def register_error_handlers(app: Flask) -> None:
    from app.exceptions import YourDomainException
    from app.utils.flask_error_handlers import _mark_request_failed, build_error_response

    @app.errorhandler(YourDomainException)
    def handle_domain_error(error):
        _mark_request_failed()
        return build_error_response(error.message, {"message": "..."}, status_code=409)
```

#### `register_cli_commands(cli)`
Add Click commands for domain-specific operations (cron jobs, data sync, etc.).

**Important**: `import click` must be a regular import, not under `TYPE_CHECKING`. The `@click.pass_context` decorator needs it at runtime.

#### `post_migration_hook(app)` and `load_test_data_hook(app)`
Called by the CLI after migrations and test data loading respectively.

### 2.4 Exceptions

Edit `app/exceptions.py`. The template provides base classes:
- `BusinessLogicException` — base for all business errors
- `RecordNotFoundException` (404)
- `ResourceConflictException` (409)
- `InvalidOperationException` (409)
- `AuthenticationException` (401)
- `ValidationException` (400)

Add your domain-specific exceptions below:
```python
class ExternalServiceException(BusinessLogicException):
    """External service failure."""
    error_code = "EXTERNAL_SERVICE_ERROR"
```

### 2.5 Models, services, schemas, API endpoints

Copy your domain files into the generated project:

```
app/models/         — SQLAlchemy models
app/services/       — domain service classes
app/schemas/        — Pydantic request/response schemas
app/api/            — Flask blueprint endpoints
app/utils/          — domain-specific utilities
alembic/versions/   — database migrations
```

**Models**: Update `app/models/__init__.py` to import all models (Alembic needs this for autogenerate).

**API endpoints**: Each blueprint file should follow the pattern:
```python
from dependency_injector.wiring import Provide, inject
from app.services.container import ServiceContainer

@your_bp.route("/items", methods=["GET"])
@inject
def list_items(
    your_service: YourService = Provide[ServiceContainer.your_service],
) -> Any:
    ...
```

**Special directories**: If your app uses Flask's `render_template()`, create `app/templates/` and add your Jinja2 templates there (this directory doesn't come from the copier template).

### 2.6 Test fixtures

Edit `tests/conftest.py` to add domain fixtures on top of the infrastructure fixtures.

**Key pattern** — override `test_settings` if your tests need different infrastructure config:

```python
@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        secret_key="test-secret-key",
        flask_env="development",  # NOT "testing" — gates testing endpoints
        cors_origins=["http://localhost:3000"],
        oidc_enabled=False,
        # ...
    )
```

**Why `flask_env="development"`?** The template's default `test_settings` uses `flask_env="testing"`, which makes `config.is_testing` true. This disables testing endpoint guards, meaning your regular test client can access testing-only endpoints. If your tests expect testing endpoints to be gated for the regular client, override to `"development"` and create separate `testing_app`/`testing_client` fixtures with `flask_env="testing"`.

Add domain-specific `test_app_settings`:
```python
@pytest.fixture
def test_app_settings() -> AppSettings:
    return AppSettings(
        mqtt_url="mqtt://localhost",
        # ... your test values
    )
```

Add domain factories and service fixtures as needed.

### 2.7 Dependencies

Edit `pyproject.toml` to add your domain-specific dependencies (MQTT client, Elasticsearch, etc.). This file is `_skip_if_exists`, so copier won't overwrite it.

### 2.8 Environment files

- `.env` — your actual environment configuration (gitignored)
- `.env.test` — test-specific overrides, loaded by `conftest_infrastructure.py` if it exists (gitignored)
- `.env.example` — documentation of all env vars (committed)

## Phase 2 Verification

Run the full test suite:
```bash
cd /work/<YourProject>/backend-new
python -m pytest tests/ --tb=short -q
ruff check .
```

## Phase 3: Enabling Additional Feature Flags

If you started with a subset of flags (e.g., `use_s3=false`), you can enable them later:

```bash
cd /work/<YourProject>/backend-new
poetry run copier update --trust --defaults -d use_s3=true -d use_sse=true
```

This will:
- Update template-managed files (`__init__.py`, `config.py`, `conftest_infrastructure.py`) with the new feature sections
- Add new template files (`s3_service.py`, `cas.py`, `sse.py`, etc.)
- Update `.copier-answers.yml`

**What it won't do** (because of `_skip_if_exists`):
- Update `container.py` — you must manually add the new service providers
- Update `pyproject.toml` — you must manually add new dependencies if needed
- Update `.env.example` — you should document the new env vars

### Manual steps after enabling S3

1. Add to `container.py`:
   ```python
   from app.services.s3_service import S3Service
   from app.services.cas_image_service import CasImageService

   s3_service = providers.Factory(S3Service, settings=config)
   cas_image_service = providers.Factory(
       CasImageService, s3_service=s3_service, app_settings=app_config,
   )
   ```
2. Add S3 env vars to `.env` and `.env.test`:
   ```
   S3_ENDPOINT_URL=http://your-s3:port
   S3_ACCESS_KEY_ID=...
   S3_SECRET_ACCESS_KEY=...
   S3_BUCKET_NAME=your-bucket
   ```

### Manual steps after enabling SSE

1. Add to `container.py`:
   ```python
   from app.services.frontend_version_service import FrontendVersionService

   frontend_version_service = providers.Singleton(
       FrontendVersionService,
       settings=config,
       lifecycle_coordinator=lifecycle_coordinator,
       sse_connection_manager=sse_connection_manager,
   )
   ```

## Common Pitfalls

### 1. `click` import under `TYPE_CHECKING`

The scaffold `startup.py` puts `import click` under `if TYPE_CHECKING:`. This works for type annotations (because of `from __future__ import annotations`), but **breaks at runtime** when `@click.pass_context` is used as a decorator. Move `import click` to a regular import if you use Click decorators.

### 2. `Provide[ServiceContainer.config]` vs `.app_config`

Infrastructure endpoints (health, auth, metrics) use `Provide[ServiceContainer.config]` to get the template's `Settings`. Domain endpoints that access app-specific fields must use `Provide[ServiceContainer.app_config]` to get `AppSettings`. Using the wrong one gives `AttributeError` at request time.

### 3. Blueprint double-registration

`api_bp` is a module-level singleton. Without the `if not api_bp._got_registered_once:` guard, the second `create_app()` call in tests will fail. Always guard sub-blueprint registration.

### 4. `flask_env="testing"` in regular test client

If your regular `test_settings` uses `flask_env="testing"`, testing-only endpoints become accessible to the regular client. This breaks tests that expect those endpoints to be gated. Override `test_settings` with `flask_env="development"` and create separate testing-mode fixtures.

### 5. Auth/self endpoint returns 200 (not 401) when OIDC is disabled

The template's `/api/auth/self` endpoint returns a default `local-user` with 200 when OIDC is disabled. Tests that expect 401 without a session need to be adapted.

### 6. `TestingService.clear_all_sessions()`

If your test fixtures need to clear test sessions between tests, ensure your `TestingService` has a `clear_all_sessions()` method. The template provides it.

### 7. MetricsService — domain-specific metrics

If your services record domain-specific Prometheus metrics through `MetricsService`, add a lazy initialization method (e.g., `_ensure_domain_metrics()`) that creates the metric objects on first use. Don't access metric attributes directly without initializing them first.

## File Ownership Quick Reference

| Category | Examples | Who edits | Copier behavior |
|----------|---------|-----------|-----------------|
| Template infrastructure | `app/__init__.py`, `app/config.py`, `app/api/auth.py`, `tests/conftest_infrastructure.py` | Template only | Overwritten on update |
| App scaffolds | `app/startup.py`, `app/services/container.py`, `app/exceptions.py`, `pyproject.toml` | App developer | Generated once, never overwritten |
| Domain code | `app/models/*.py`, `app/api/your_endpoints.py`, `app/services/your_services.py` | App developer | Not touched by template |
| Migrations | `alembic/versions/*.py` | App developer | Not touched by template |
