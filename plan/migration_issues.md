# Migration Issues: ElectronicsInventory → ModernAppTemplate

This document captures all issues encountered during the initial migration attempt of ElectronicsInventory (EI) to use the ModernAppTemplate Copier template. The migration was partially completed but revealed several issues that require template changes before re-attempting.

---

## Issue 1: MetricsService Design Flaw (Major)

**Problem**: The EI `MetricsService` contains domain-specific metrics (inventory counts, kit metrics, AI usage, etc.) alongside infrastructure metrics (SSE Gateway, shutdown). This creates circular dependencies:

- `MetricsService` needs `DashboardService` to pull inventory data
- Domain services need `MetricsService` to record events
- Background metric updates require database access from a background thread

**Workaround Applied**: Changed `MetricsService` to use explicit factory injection:
```python
metrics_service = providers.Singleton(
    MetricsService,
    shutdown_coordinator=shutdown_coordinator,
    db_session_factory=providers.Object(db_session),
    dashboard_service_factory=providers.Object(dashboard_service),
    db_session_reset=db_session.reset,
)
```

**Root Cause**: Prometheus metrics (Counter, Gauge, Histogram) register themselves with a global `CollectorRegistry`. They don't need a central service to own them. Each domain service should own its metrics.

**Resolution**: See `plan/metrics_redesign_plan.md` for the comprehensive redesign.

---

## Issue 2: Container Inheritance Blocked

**Problem**: The original goal was for `AppContainer` to extend a `CommonContainer` from the template. This was blocked by Issue 1 - the `providers.Self()` / factory injection patterns don't work well with container inheritance.

**Current State**: EI defines all providers in a flat `AppContainer` instead of inheriting from a common base.

**Resolution**: After implementing the metrics redesign, container inheritance should work cleanly.

---

## Issue 3: S3Service API Mismatch

**Problem**: The template's `S3Service` was not a direct copy from EI. It had different:
- Property names: `s3_client`/`_s3_client` vs `client`/`_client`
- Method signatures: different return types (`str` vs `bool`), different parameter names
- Exceptions: `InvalidOperationException` vs `S3ServiceError`/`S3ObjectNotFoundError`
- Missing method: `generate_cas_key()` for content-addressable storage

**Files Changed**:
- `template/common/storage/s3_service.py` - Modified to add `generate_cas_key()`, changed API
- `tests/test_s3_service.py` (EI) - Updated to use new API and exceptions

**Issues with Current Approach**:
- The template S3Service diverged from what EI actually uses
- Several methods were removed/simplified (list_files, generate_presigned_url, etc.)
- Should have been a blind copy from EI, then generalized

---

## Issue 4: ConnectionManager Not a Blind Copy

**Problem**: Similar to S3Service, the template's `ConnectionManager` was not a direct copy from EI. Changes included:
- Added extensive logging with structured extra fields
- Added inline comments explaining behavior
- Methods work the same but have more verbose implementations

**Current State**: The template version has more documentation but is functionally equivalent.

**Resolution**: Should recreate from EI source, then decide what (if anything) should be generalized.

---

## Issue 5: Test Patch Paths

**Problem**: Tests that mock S3 or ConnectionManager needed path updates after moving to `common.*`:

| Old Path | New Path |
|----------|----------|
| `app.services.s3_service.boto3.client` | `common.storage.s3_service.boto3.client` |
| `app.services.connection_manager.requests.post` | `common.sse.connection_manager.requests.post` |

**Files Changed**: Multiple test files in EI.

---

## Issue 6: SQLite Threading Issues

**Problem**: The background metrics updater runs in a separate thread. When using SQLite in tests, this caused segfaults because SQLite connections can't be shared across threads.

**Workaround Applied**: Added `skip_background_services=True` parameter to `create_app()`:
```python
# tests/conftest.py
app = create_app(settings, skip_background_services=True)
```

**Note**: This is a test-only issue. Production uses PostgreSQL which handles threading properly.

---

## Issue 7: Document Service Exception Handling

**Problem**: `DocumentService` caught `InvalidOperationException` for S3 errors, but the common S3Service raises `S3ServiceError`.

**File Changed**: `app/services/document_service.py`
```python
# Before
from app.exceptions import InvalidOperationException
except InvalidOperationException:

# After
from common.storage.s3_service import S3ServiceError
except S3ServiceError:
```

---

## Issue 8: Import Path Updates

**Problem**: Many imports needed updating from `app.*` to `common.*`:

| Component | Old Import | New Import |
|-----------|------------|------------|
| ShutdownCoordinator | `app.utils.shutdown_coordinator` | `common.core.shutdown` |
| S3Service | `app.services.s3_service` | `common.storage.s3_service` |
| ConnectionManager | `app.services.connection_manager` | `common.sse.connection_manager` |
| TaskService | `app.services.task_service` | `common.tasks.service` |

---

## Issue 9: Diagnostics Service Initialization

**Problem**: The `DiagnosticsService` was being initialized inside the `if not skip_background_services:` block, but tests need it attached to the app for assertions.

**Workaround Applied**: Moved diagnostics initialization outside the conditional block.

---

## Changes to Keep

The following template changes should be kept (not reverted):

1. **`template/common/database/health.py`** - Database health check improvements
2. **`template/common/health/routes.py.jinja`** - Health endpoint improvements
3. **`template/common/core/settings.py.jinja`** - Settings base class improvements

---

## Changes to Discard

The following should be discarded and recreated from EI:

1. **`template/common/storage/s3_service.py`** - Should be blind copy from EI, not modified version
2. **`template/common/sse/connection_manager.py`** - Should be blind copy from EI

---

## Recommended Migration Order (Redo)

1. **Template Changes First**:
   - Execute `metrics_redesign_plan.md` Tasks 1-2 (create template infrastructure)
   - Reset S3Service and ConnectionManager to blind copies from EI
   - Keep database/health improvements

2. **EI Migration Second**:
   - Run Copier update
   - Execute `metrics_redesign_plan.md` Tasks 3-8 (migrate EI)
   - Update import paths
   - Fix test patch paths

3. **Verify**:
   - All 1144 EI tests pass
   - Container inheritance works
   - No `providers.Self()` or `providers.Object()` workarounds

---

## Test Command

```bash
cd /work/ElectronicsInventory/backend
python -m pytest tests/ -v
```

All 1144 tests were passing at the end of the initial migration attempt.
