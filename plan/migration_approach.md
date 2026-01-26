# Template Migration Approach

This document describes the approach for migrating existing Flask applications to use the Copier template.

## Overview

The template provides shared infrastructure (`common/`) while apps retain their domain-specific code (`app/`). Migration involves:

1. Applying the Copier template to generate the `common/` directory and configuration files
2. Adapting the app's code to use template infrastructure
3. Removing duplicated code that's now provided by the template
4. Ensuring tests pass

## Pre-Migration Checklist

Before starting migration:

- [ ] Identify which template features to enable (`use_database`, `use_oidc`, `use_s3`, `use_sse`)
- [ ] Review app's current implementations of shared services (health, metrics, S3, SSE, etc.)
- [ ] Identify app-specific extensions to template services (e.g., custom metrics, health checks)
- [ ] Ensure comprehensive test coverage exists for app functionality
- [ ] Document any app-specific patterns that differ from template conventions

## Feature Flag Selection

| Flag | Enable When |
|------|-------------|
| `use_database` | App uses PostgreSQL with SQLAlchemy/Alembic |
| `use_oidc` | App requires OIDC authentication |
| `use_s3` | App uses S3-compatible storage |
| `use_sse` | App uses SSE Gateway for real-time events |

## Container Architecture

Apps **must extend** `CommonContainer` from the template. This provides:

- Inherited infrastructure services (shutdown_coordinator, metrics_service, task_service)
- Consistent dependency injection patterns
- Automatic wiring of common services

**Pattern:**

```python
# app/container.py
from dependency_injector import containers, providers
from common.core.container import CommonContainer

class AppContainer(CommonContainer):
    """App-specific container extending common infrastructure."""

    # App-specific services
    my_service = providers.Factory(
        MyService,
        settings=CommonContainer.config.provided,
    )
```

**Key rules:**

1. Always extend `CommonContainer`, never create standalone containers
2. Reference parent config via `CommonContainer.config.provided`
3. Keep domain services in `AppContainer`, infrastructure in `CommonContainer`
4. Use `providers.Factory` for request-scoped services, `providers.Singleton` for app-scoped

## Migration Steps

### Phase 1: Apply Template

1. **Create `.copier-answers.yml`** with feature flags:
   ```yaml
   _src_path: ../../ModernAppTemplate/backend
   project_name: my-app
   project_description: My app description
   use_database: true
   use_oidc: false
   use_s3: true
   use_sse: true
   ```

2. **Run Copier** for initial migration:
   ```bash
   poetry run copier copy \
       --answers-file .copier-answers.yml \
       --trust \
       --defaults \
       --overwrite \
       --skip app/__init__.py \
       --skip app/config.py \
       --skip app/container.py \
       --skip app/api/__init__.py \
       --skip app/models/__init__.py \
       --skip tests/conftest.py \
       ../../ModernAppTemplate/backend .
   ```

3. **Review generated files** - Copier will generate:
   - `common/` directory with all shared infrastructure
   - `scripts/copier-update.sh` for future updates
   - Updated `pyproject.toml`
   - Configuration files (`.env.example`, `alembic.ini` if database enabled)

4. **App-specific files preserved** via `--skip` flags:
   - `app/__init__.py`, `app/config.py`, `app/container.py`
   - `app/api/__init__.py`, `app/models/__init__.py`
   - `tests/conftest.py`

### Phase 2: Adapt App Code

1. **Update `app/config.py`** - Extend `CommonSettings`:
   ```python
   from common.core.settings import CommonSettings

   class Settings(CommonSettings):
       # App-specific settings only
       MY_APP_SETTING: str = Field(default="value")
   ```

2. **Update `app/container.py`** - Extend `CommonContainer`:
   ```python
   from common.core.container import CommonContainer

   class AppContainer(CommonContainer):
       # App-specific services only
   ```

3. **Update `app/__init__.py`** - Use template's app factory pattern:
   - Import `create_base_app` from `common.core.app`
   - Register app-specific blueprints
   - Wire app-specific container providers

4. **Update `run.py`** - Use template's runner:
   ```python
   from common.core.runner import run_app
   from app import create_app

   if __name__ == "__main__":
       run_app(create_app)
   ```

### Phase 3: Remove Duplicated Code

Remove app code that's now provided by template:

| App Code to Remove | Replaced By |
|--------------------|-------------|
| `app/services/s3_service.py` | `common/storage/s3_service.py` |
| `app/services/connection_manager.py` | `common/sse/connection_manager.py` |
| `app/services/metrics_service.py` (base) | `common/metrics/service.py` |
| `app/services/task_service.py` | `common/tasks/service.py` |
| `app/services/shutdown_coordinator.py` | `common/core/shutdown.py` |
| `app/api/health.py` (base) | `common/health/routes.py` |
| `app/api/metrics.py` | `common/metrics/routes.py` |
| `app/utils/flask_error_handlers.py` | `common/core/errors.py` |

### Phase 4: Handle App-Specific Extensions

When app has extensions to template services:

**Option A: Subclass the template service**
```python
# app/services/metrics_service.py
from common.metrics.service import MetricsService as BaseMetricsService

class MetricsService(BaseMetricsService):
    """App-specific metrics extending base."""

    def update_metrics(self) -> None:
        super().update_metrics()
        # Add app-specific metrics
        self._update_inventory_metrics()
```

**Option B: Add supplementary routes**
```python
# app/api/health.py - supplements common/health/routes.py
from flask import Blueprint

health_ext_bp = Blueprint("health_ext", __name__)

@health_ext_bp.route("/health/drain", methods=["POST"])
def drain():
    """App-specific drain endpoint."""
    ...
```

### Phase 5: Update Tests

1. **Move shared tests to template** - Tests for `common/` code belong in `/work/ModernAppTemplate/backend/tests/`
2. **Update app test imports** - Point to `common.*` instead of `app.services.*`
3. **Update fixtures** - Use template's test fixtures where applicable
4. **Run full test suite** - Ensure all tests pass

### Phase 6: Database Migrations (if applicable)

If `use_database=true`:

1. **Preserve existing migrations** - Keep `alembic/versions/` intact
2. **Update `alembic/env.py`** - Use template's version (imports from `common.database`)
3. **Update `alembic.ini`** - Use template's version
4. **Verify migrations work**:
   ```bash
   poetry run alembic upgrade head
   poetry run alembic check
   ```

## Post-Migration Verification

- [ ] All tests pass: `poetry run pytest tests/ -v`
- [ ] App starts without errors: `python run.py`
- [ ] Health endpoints work: `GET /health/healthz`, `GET /health/readyz`
- [ ] Metrics endpoint works: `GET /metrics`
- [ ] Feature-specific checks:
  - Database: migrations apply, queries work
  - S3: file upload/download works
  - SSE: connections and events work
  - OIDC: login flow works (if enabled)

## Common Issues

### Import Errors After Migration

**Symptom:** `ModuleNotFoundError: No module named 'app.services.s3_service'`

**Fix:** Update imports throughout codebase to use `common.*`:
```python
# Before
from app.services.s3_service import S3Service

# After
from common.storage.s3_service import S3Service
```

### Container Wiring Issues

**Symptom:** `DependencyNotFoundError` or services returning `None`

**Fix:** Ensure `AppContainer` extends `CommonContainer` and wiring includes both:
```python
container.wire(modules=[
    "app.api",
    "common.health.routes",
    "common.metrics.routes",
    # ... other modules
])
```

### Database Session Issues

**Symptom:** `DetachedInstanceError` or session not found

**Fix:** Ensure `db_session` is properly configured in container:
```python
db_session = providers.ContextLocalSingleton(
    lambda: container.session_maker()()
)
```

## Template Updates

After initial migration, template updates can be applied using the provided script:

```bash
./scripts/copier-update.sh
```

This script:
- Reads `_src_path` from `.copier-answers.yml` to find the template
- Re-applies the template with `--overwrite`
- Automatically skips app-specific files:
  - `app/__init__.py`, `app/config.py`, `app/container.py`
  - `app/api/__init__.py`, `app/models/__init__.py`
  - `tests/conftest.py`, `tests/test_health.py`
  - `scripts/args.sh`, `Jenkinsfile`

This preserves app-specific files while updating `common/` infrastructure.
