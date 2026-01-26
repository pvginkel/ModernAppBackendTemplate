# ElectronicsInventory Migration Plan

Migration of `/work/ElectronicsInventory/backend` to use the Copier template.

## Project Details

- **Project name:** `electronics-inventory`
- **Project description:** `Hobby electronics parts inventory system with AI-assisted organization`
- **Source:** `/work/ElectronicsInventory/backend`
- **Template:** `/work/ModernAppTemplate/backend`

## Feature Flags

| Flag | Value | Rationale |
|------|-------|-----------|
| `use_database` | `true` | PostgreSQL with 20+ SQLAlchemy models |
| `use_oidc` | `false` | Public API, no authentication required |
| `use_s3` | `true` | Document and image storage via Ceph/S3 |
| `use_sse` | `true` | SSE Gateway for frontend version notifications |

## Pre-Migration: Template Updates Required

Before migrating ElectronicsInventory, the template needs updates based on EI's battle-tested implementations.

### 1. Update Template S3Service

**File:** `/work/ModernAppTemplate/backend/template/common/storage/s3_service.py`

Replace current implementation with EI's approach:

**Methods to include (from EI):**
- `upload_file(file_obj, s3_key, content_type)` → returns `bool`
- `download_file(s3_key)` → returns `BytesIO`
- `copy_file(source_key, target_key)` → returns `bool`
- `delete_file(s3_key)` → returns `bool`
- `file_exists(s3_key)` → returns `bool`
- `get_file_metadata(s3_key)` → returns `dict`
- `ensure_bucket_exists()` → returns `bool`

**Methods to remove (not used by any app):**
- `upload_bytes` - can use `upload_file` with `BytesIO`
- `download_to_file` - apps can write `BytesIO` themselves
- `list_files` - not used
- `generate_presigned_url` - not used

**Exception pattern:**
- `S3ServiceError` - base exception for S3 operations
- `S3ObjectNotFoundError(S3ServiceError)` - file not found

**Add tests:** Update `tests/test_s3.py` with tests for all methods, including:
- `test_copy_file_success`
- `test_copy_file_source_not_found`
- `test_get_file_metadata_success`
- `test_get_file_metadata_not_found`

### 2. Update Template ConnectionManager (SSE)

**File:** `/work/ModernAppTemplate/backend/template/common/sse/connection_manager.py`

Replace with EI's implementation which has:
- Comprehensive structured logging with `extra` dicts
- Per-operation metrics recording
- Pydantic request/response models
- Service type labeling for metrics
- Observer pattern for connection events

**Add tests:** Update `tests/test_connection_manager.py` (new file) with tests from EI:
- Connection registration tests
- Observer notification tests
- Broadcast send tests
- Error isolation tests

### 3. Update Template Health Routes

**File:** `/work/ModernAppTemplate/backend/template/common/health/routes.py.jinja`

Add from EI:
- Migration checking in `/health/readyz` (when `use_database` enabled)
- `/health/drain` endpoint with Bearer token auth

**Add settings:**
- `DRAIN_AUTH_KEY: str | None = None` in CommonSettings (when `use_database`)

**Add tests:** Update `tests/test_health.py` with:
- `test_readyz_with_pending_migrations`
- `test_drain_endpoint_success`
- `test_drain_endpoint_unauthorized`

---

## Migration Steps

### Phase 1: Apply Template

```bash
cd /work/ElectronicsInventory/backend

# Create .copier-answers.yml file
cat > .copier-answers.yml << 'EOF'
_src_path: ../../ModernAppTemplate/backend
project_name: electronics-inventory
project_description: Hobby electronics parts inventory system with AI-assisted organization
use_database: true
use_oidc: false
use_s3: true
use_sse: true
EOF

# Initial application - run copier directly
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

After initial migration, use `scripts/copier-update.sh` for future template updates.

This generates:
- `common/` directory (core, database, storage, sse, health, metrics, tasks)
- `scripts/copier-update.sh` for future updates
- Updated config templates
- Note: `common/auth/` will NOT be generated (use_oidc=false)

### Phase 2: Adapt Configuration

#### 2.1 Update `app/config.py`

```python
# Before: standalone Settings
from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # All settings defined here
    ...

# After: extends CommonSettings
from pydantic import Field
from common.core.settings import CommonSettings

class Settings(CommonSettings):
    # Only app-specific settings
    MOUSER_API_KEY: str | None = Field(default=None)
    OPENAI_API_KEY: str | None = Field(default=None)
    ANTHROPIC_API_KEY: str | None = Field(default=None)
    AI_MODEL: str = Field(default="gpt-4o-mini")
    AI_PROVIDER: str = Field(default="openai")
    # ... other app-specific settings
```

#### 2.2 Update `app/container.py`

Refactor `ServiceContainer` to extend `CommonContainer`:

```python
# Before: standalone container
from dependency_injector import containers, providers

class ServiceContainer(containers.DeclarativeContainer):
    config = providers.Dependency(Settings)
    session_maker = providers.Dependency()
    db_session = providers.ContextLocalSingleton(...)
    shutdown_coordinator = providers.Singleton(ShutdownCoordinator, ...)
    metrics_service = providers.Singleton(MetricsService, ...)
    s3_service = providers.Factory(S3Service, ...)
    connection_manager = providers.Singleton(ConnectionManager, ...)
    task_service = providers.Singleton(TaskService, ...)
    # ... 30+ more services

# After: extends CommonContainer
from dependency_injector import containers, providers
from common.core.container import CommonContainer
from common.storage.s3_service import S3Service
from common.sse.connection_manager import ConnectionManager

class AppContainer(CommonContainer):
    """App container extending common infrastructure."""

    # Override s3_service to use common implementation
    s3_service = providers.Factory(
        S3Service,
        settings=CommonContainer.config.provided,
    )

    # Override connection_manager to use common implementation
    connection_manager = providers.Singleton(
        ConnectionManager,
        gateway_url=CommonContainer.config.provided.SSE_GATEWAY_URL,
        metrics_service=CommonContainer.metrics_service.provided,
        http_timeout=2.0,
    )

    # App-specific services (keep these)
    part_service = providers.Factory(PartService, ...)
    kit_service = providers.Factory(KitService, ...)
    # ... other domain services
```

#### 2.3 Update `app/__init__.py`

Adapt to use template's app factory helpers:

```python
from common.core.app import create_base_app, configure_database, configure_error_handlers
from common.core.spectree import configure_spectree
from common.health.routes import health_bp
from common.metrics.routes import metrics_bp
from common.sse.routes import sse_bp

from app.config import Settings
from app.container import AppContainer

def create_app(settings: Settings | None = None, skip_background_services: bool = False) -> App:
    settings = settings or Settings()

    # Create base app with common configuration
    app = create_base_app(settings)

    # Configure database (template helper)
    configure_database(app, settings)

    # Create and wire container
    container = AppContainer()
    container.config.override(settings)
    # ... wire modules

    # Register blueprints
    app.register_blueprint(health_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(sse_bp, url_prefix="/api/sse")
    app.register_blueprint(api_bp, url_prefix="/api")  # App-specific

    # Configure error handlers (template helper)
    configure_error_handlers(app)

    return app
```

#### 2.4 Update `run.py`

```python
from common.core.runner import run_app
from app import create_app

if __name__ == "__main__":
    run_app(create_app)
```

### Phase 3: Remove Duplicated Code

#### 3.1 Files to DELETE (replaced by template)

```
app/services/s3_service.py          → common/storage/s3_service.py
app/services/connection_manager.py  → common/sse/connection_manager.py
app/services/task_service.py        → common/tasks/service.py
app/services/shutdown_coordinator.py → common/core/shutdown.py
app/api/health.py                   → common/health/routes.py
app/api/metrics.py                  → common/metrics/routes.py
app/utils/flask_error_handlers.py   → common/core/errors.py
app/utils/shutdown_coordinator.py   → common/core/shutdown.py (duplicate)
```

#### 3.2 Files to KEEP (app-specific)

```
app/services/metrics_service.py     → Subclass of common/metrics/service.py
app/services/part_service.py        → Domain service
app/services/kit_service.py         → Domain service
app/services/ai_service.py          → Domain service
app/services/document_service.py    → Domain service
# ... all other domain services

app/api/parts.py                    → Domain API
app/api/kits.py                     → Domain API
# ... all other domain APIs

app/models/*                        → All SQLAlchemy models
app/schemas/*                       → All Pydantic schemas
```

#### 3.3 Update Imports Throughout Codebase

Search and replace:

| Old Import | New Import |
|------------|------------|
| `from app.services.s3_service import S3Service` | `from common.storage.s3_service import S3Service` |
| `from app.services.connection_manager import ConnectionManager` | `from common.sse.connection_manager import ConnectionManager` |
| `from app.services.task_service import TaskService` | `from common.tasks.service import TaskService` |
| `from app.services.shutdown_coordinator import ShutdownCoordinator` | `from common.core.shutdown import ShutdownCoordinator` |
| `from app.exceptions import InvalidOperationException` | `from common.storage.s3_service import S3ServiceError` (for S3 ops) |

### Phase 4: App-Specific Extensions

#### 4.1 MetricsService Subclass

Create `app/services/metrics_service.py`:

```python
from common.metrics.service import MetricsService as BaseMetricsService

class MetricsService(BaseMetricsService):
    """Electronics inventory metrics extending base metrics service."""

    def __init__(self, container, settings, shutdown_coordinator):
        super().__init__(settings, shutdown_coordinator)
        self._container = container
        # Initialize app-specific gauges/counters
        self._init_inventory_metrics()
        self._init_ai_metrics()
        self._init_pick_list_metrics()

    def update_metrics(self) -> None:
        """Update all metrics including app-specific ones."""
        super().update_metrics()
        self._update_inventory_metrics()
        self._update_storage_metrics()
        self._update_activity_metrics()

    # ... keep all existing app-specific metric methods
```

#### 4.2 CAS Extension for S3

Create `app/services/cas_service.py`:

```python
"""Content-Addressable Storage service for document deduplication."""

import hashlib
from common.storage.s3_service import S3Service

class CASService:
    """CAS operations built on top of S3Service."""

    def __init__(self, s3_service: S3Service):
        self._s3 = s3_service

    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content).hexdigest()

    def generate_cas_key(self, content: bytes) -> str:
        """Generate CAS S3 key from content hash."""
        content_hash = self.compute_hash(content)
        return f"cas/{content_hash}"

    def upload_cas(self, content: bytes, content_type: str | None = None) -> str:
        """Upload content using CAS key. Returns the CAS key."""
        from io import BytesIO
        cas_key = self.generate_cas_key(content)
        if not self._s3.file_exists(cas_key):
            self._s3.upload_file(BytesIO(content), cas_key, content_type)
        return cas_key
```

### Phase 5: Update Tests

#### 5.1 Tests to MOVE to Template

These tests cover shared functionality and should be in `/work/ModernAppTemplate/backend/tests/`:

- `test_s3_service.py` → core S3 tests (already exists, update)
- `test_connection_manager.py` → SSE tests (create new)
- `test_health_api.py` → health endpoint tests (already exists, update)

#### 5.2 Tests to UPDATE in App

Update imports in remaining test files:

```python
# Before
from app.services.s3_service import S3Service

# After
from common.storage.s3_service import S3Service
```

#### 5.3 Tests to ADD in App

- `tests/test_cas_service.py` - CAS-specific functionality
- `tests/test_metrics_service.py` - app-specific metrics (update existing)

### Phase 6: Database Migrations

#### 6.1 Preserve Existing Migrations

Keep `alembic/versions/` directory intact - these are app-specific.

#### 6.2 Update Alembic Configuration

Replace `alembic/env.py` with template version (imports from `common.database`).

Replace `alembic.ini` with template version.

#### 6.3 Verify Migrations

```bash
# Check current state
poetry run alembic current

# Verify upgrade works
poetry run alembic upgrade head

# Check for pending migrations
poetry run alembic check
```

---

## Verification Checklist

### Functional Tests

- [ ] `poetry run pytest tests/ -v` - all tests pass
- [ ] App starts: `python run.py`
- [ ] Health checks work:
  - [ ] `GET /health/healthz` returns 200
  - [ ] `GET /health/readyz` returns 200 (checks DB + migrations)
  - [ ] `POST /health/drain` with auth token works
- [ ] Metrics work: `GET /metrics` returns Prometheus format
- [ ] SSE works:
  - [ ] SSE Gateway callback endpoint responds
  - [ ] Version events broadcast to connected clients
- [ ] S3 works:
  - [ ] File upload succeeds
  - [ ] File download succeeds
  - [ ] CAS deduplication works
- [ ] Database works:
  - [ ] Migrations apply cleanly
  - [ ] CRUD operations succeed

### Code Quality

- [ ] `poetry run ruff check .` - no linting errors
- [ ] `poetry run mypy .` - no type errors
- [ ] No duplicate code between `app/` and `common/`

### Configuration

- [ ] `.copier-answers.yml` exists with correct values
- [ ] `.env.example` has all required variables
- [ ] `pyproject.toml` has correct dependencies

---

## File Changes Summary

### New Files
- `common/` directory (from template)
- `app/services/cas_service.py` (CAS extension)
- `.copier-answers.yml`

### Modified Files
- `app/config.py` - extend CommonSettings
- `app/container.py` - extend CommonContainer (renamed from ServiceContainer)
- `app/__init__.py` - use template app factory
- `run.py` - use template runner
- `app/services/metrics_service.py` - subclass BaseMetricsService
- `pyproject.toml` - updated dependencies
- `alembic/env.py` - use template version
- `alembic.ini` - use template version
- All files with imports from deleted services

### Deleted Files
- `app/services/s3_service.py`
- `app/services/connection_manager.py`
- `app/services/task_service.py`
- `app/services/shutdown_coordinator.py`
- `app/api/health.py`
- `app/api/metrics.py`
- `app/utils/flask_error_handlers.py`
- `app/utils/shutdown_coordinator.py`

---

## Rollback Plan

If migration fails:

1. Git reset to pre-migration state
2. Remove `common/` directory
3. Remove `.copier-answers.yml`
4. Restore deleted files from git

```bash
git checkout -- .
git clean -fd common/
```
