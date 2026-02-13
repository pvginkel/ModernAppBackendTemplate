# Remaining Work Before EI Backport

## Overview

The template generates a working Flask app with all four feature flags validated. 58 tests pass (52 infrastructure, 6 domain). This document covers what's left before Electronics Inventory can consume the template via `copier update`.

---

## 1. Generic S3 Naming

Replace "MinIO" references with generic "S3" throughout the template.

**Files to update:**
- `template/tests/conftest_infrastructure.py.jinja` — error messages in `pytest_configure` and `_assert_s3_available`
- `CLAUDE.md` — feature flag table says "S3/MinIO storage"
- Any error messages or comments referencing MinIO specifically

**Scope:** Small, find-and-replace.

---

## 2. Missing Template Files

These EI infrastructure files should be extracted into the template. They ship with every generated app.

### 2a. Task API (`app/api/tasks.py`)

Task status, cancel, and remove endpoints. Every app using the task system needs these. Currently in EI's `startup.py` as a domain blueprint, but it's pure infrastructure — it only talks to `TaskService`.

**Source:** `/work/ElectronicsInventory/backend/app/api/tasks.py` (58 lines)
**Target:** `template/app/api/tasks.py`
**Registration:** Should be registered in `create_app()` (template-owned), not `startup.py` (app-owned). Guarded by flask_env check or always available.

### 2b. Common Response Schemas (`app/schemas/common.py`)

`ErrorResponseSchema`, `SuccessResponseSchema`, `MessageResponseSchema`, `PaginationMetaSchema`. Used by many EI domain endpoints for Spectree validation.

**Source:** `/work/ElectronicsInventory/backend/app/schemas/common.py` (56 lines)
**Target:** `template/app/schemas/common.py`
**Decision:** Template-owned. Every app will need these for Spectree API validation.

### 2c. Testing Service (`app/services/testing_service.py`)

Generates deterministic content (PNG images, PDF fixture, HTML pages) for Playwright tests. Used by testing_content endpoints.

**Source:** `/work/ElectronicsInventory/backend/app/services/testing_service.py` (163 lines)
**Target:** `template/app/services/testing_service.py`
**Dependencies:** Pillow (PIL), bundled `app/assets/fake-pdf.pdf`
**Feature flag:** `use_s3` (only useful when you have CAS/attachments to test)

### 2d. Testing Content Endpoints (`app/api/testing_content.py`)

Serves deterministic images, PDFs, and HTML for Playwright fixtures. Protected by `reject_if_not_testing()`.

**Source:** `/work/ElectronicsInventory/backend/app/api/testing_content.py` (93 lines)
**Target:** `template/app/api/testing_content.py`
**Schemas:** `template/app/schemas/testing_content.py` (24 lines)
**Feature flag:** `use_s3` (pairs with testing_service)

### 2e. PDF Test Asset

Bundled fake PDF for `TestingService.get_pdf_fixture()`.

**Source:** `/work/ElectronicsInventory/backend/app/assets/fake-pdf.pdf`
**Target:** `template/app/assets/fake-pdf.pdf`
**Feature flag:** `use_s3` (pairs with testing_service)

---

## 3. Mother-Project-Only Test Infrastructure

These files are needed for the mother project's test suite but should NOT be generated into every downstream app. They live in `/work/ModernAppTemplate/backend/tests/` (or alongside the mother project tests) and are never part of the template.

### 3a. SSE Integration Test Helpers

SSE client and SSE Gateway process manager. Used by integration tests that exercise real SSE streaming through the gateway.

**Source files:**
- `/work/ElectronicsInventory/backend/tests/integration/sse_client_helper.py` (121 lines)
- `/work/ElectronicsInventory/backend/tests/integration/sse_gateway_helper.py` (238 lines)

**Implication for template:** The SSE integration fixtures in `conftest_infrastructure.py.jinja` (`sse_server`, `sse_client_factory`, `sse_gateway_server`, `background_task_runner`) currently reference these helpers. Those fixtures must be **removed from `conftest_infrastructure.py.jinja`** and moved into the mother project's conftest instead. Downstream apps that want SSE integration tests can add them to their own conftest.

**External dependency:** `sse_gateway_helper.py` references `/work/SSEGateway/scripts/run-gateway.sh`. Needs to be configurable.

### 3b. Testing SSE Helpers

Version deployment triggers and fake task event sending. Extracted from EI's `app/api/testing_sse.py` and `app/schemas/testing_sse.py` as test-only utilities rather than deployed API endpoints.

**Source files:**
- `/work/ElectronicsInventory/backend/app/api/testing_sse.py` (163 lines)
- `/work/ElectronicsInventory/backend/app/schemas/testing_sse.py` (91 lines)

**Target:** Refactored into test helpers in the mother project, not HTTP endpoints.

### 3c. Test Task Implementations

DemoTask, FailingTask, LongRunningTask — needed to exercise the task service in tests.

**Source files:**
- `/work/ElectronicsInventory/backend/tests/test_tasks/test_task.py` (101 lines)

**Target:** `/work/ModernAppTemplate/backend/tests/test_tasks/`

### 3d. Testing Utilities (`tests/testing_utils.py`)

`StubLifecycleCoordinator` and `TestLifecycleCoordinator` — stubs for unit testing services that depend on the lifecycle coordinator.

**Source:** `/work/ElectronicsInventory/backend/tests/testing_utils.py` (97 lines)
**Target:** `/work/ModernAppTemplate/backend/tests/testing_utils.py`

---

## 4. Infrastructure Tests to Move from EI into Mother Project

These EI tests exercise template-owned infrastructure. They should become mother project tests in `/work/ModernAppTemplate/backend/tests/` and be **removed from EI** after the backport.

The mother project currently has 52 tests covering: app factory, config, correlation ID, error handling, health API, metrics API. The following would expand coverage significantly.

### Already in mother project (keep as-is)
- `test_app_factory.py` (6 tests)
- `test_config.py` (16 tests)
- `test_correlation_id.py` (3 tests)
- `test_error_handling.py` (7 tests)
- `test_health_api.py` (12 tests)
- `test_metrics_api.py` (7 tests)

### To add from EI

**Database infrastructure:**
- `test_database_upgrade.py` — database upgrade helpers
- `test_empty_string_normalization.py` — SQLAlchemy empty-to-NULL normalization
- `test_transaction_rollback.py` — Flask error handler and session teardown

**Services:**
- `test_s3_service.py` — S3Service CRUD operations
- `test_cas_image_service.py` — CAS image service (thumbnail generation, etc.)
- `test_task_service.py` — task lifecycle (start, cancel, status, cleanup)
- `test_task_api.py` — task API endpoints (status, cancel, remove)
- `test_task_schemas.py` — TaskEvent, TaskStatus, TaskInfo schema validation
- `test_base_task.py` — BaseTask abstract class and ProgressHandle
- `test_sse_connection_manager.py` — SSE connection lifecycle
- `test_metrics_service.py` — metrics service polling
- `test_lifecycle_coordinator.py` — lifecycle event sequencing
- `test_temp_file_manager.py` — temp file storage and cleanup
- `services/test_diagnostics_service.py` — request diagnostics

**Auth (OIDC):**
- `services/test_auth_service.py` — AuthService JWT validation
- `services/test_oidc_client_service.py` — OIDC client service
- `api/test_auth_endpoints.py` — auth API endpoints (login, callback, logout, self)
- `api/test_auth_middleware.py` — OIDC before_request hook
- `utils/test_auth_utils.py` — auth utility functions

**SSE:**
- `test_sse_api.py` — SSE Gateway callback API
- `test_sse_client_helper.py` — SSE client helper parsing

**Integration (SSE Gateway):**
- `integration/test_version_stream_baseline.py` — version stream via SSE Gateway
- `integration/test_task_stream_baseline.py` — task stream via SSE Gateway
- `integration/test_sse_gateway_tasks.py` — task streaming via SSE Gateway
- `integration/test_sse_gateway_version.py` — version streaming via SSE Gateway

**Other:**
- `test_request_parsing.py` — query parameter parsing utilities
- `test_graceful_shutdown_integration.py` — graceful shutdown sequence
- `test_cli.py` — CLI command handlers
- `api/test_testing.py` — testing API endpoint guards

### Tests that stay in EI (domain-specific)
Everything related to: parts, boxes, locations, kits, sellers, types, inventory, shopping lists, pick lists, attachments, AI, dashboard, database constraints, domain fixtures, startup hooks.

---

## 5. Template Refactoring Required

Moving the SSE integration fixtures out of the template has a ripple effect:

### 5a. Slim down `conftest_infrastructure.py.jinja`

Remove the following fixtures from the template (they move to the mother project conftest):
- `_find_free_port()`
- `sse_server` fixture
- `background_task_runner` fixture
- `sse_client_factory` fixture
- `sse_gateway_server` fixture

This significantly simplifies `conftest_infrastructure.py.jinja` and addresses the earlier concern about its complexity.

### 5b. Mother project conftest gets SSE fixtures

The mother project's `tests/conftest.py` gains the SSE integration fixtures that were removed from the template. These reference the mother-project-only helpers from section 3.

### 5c. Downstream apps opt in

Downstream apps that want SSE integration tests add the fixtures and helpers to their own test suite. The template provides the SSE runtime infrastructure (connection manager, gateway schema, SSE utils) but not the test harness.

---

## 6. EI Refactorings (from `ei_copier_refactoring.md`)

Status of the 12 planned refactorings:

| # | Refactoring | Template Status | EI Status |
|---|-------------|-----------------|-----------|
| 1 | Replace flask_log_request_id | Done (custom impl) | **Needs backport** |
| 2 | Rename services (SSEConnectionManager, etc.) | Done | **Needs backport** |
| 3 | Extract OIDC hooks to oidc_hooks.py | Done | **Needs backport** |
| 4 | Create HealthService with callback registry | Done | **Needs backport** |
| 5 | Create consts.py for project metadata | Done | **Needs backport** |
| 6 | Create separate AppSettings | Done | **Needs backport** |
| 7 | Switch CLI to Click | Done | **Needs backport** |
| 8 | Remove database reset from testing API | Done (no HTTP reset) | **Needs backport** |
| 9 | Split testing.py into focused blueprints | Partial (see 2d) | **Needs backport** |
| 10 | Split testing schemas | Partial (see 2d) | **Needs backport** |
| 11 | Split conftest into infrastructure + app | Done | Already done in EI |
| 12 | Delete run-integration-test.sh | N/A | **Needs cleanup** |

**Key point:** All 12 refactorings were done in the template first. EI needs to adopt them before `copier update` will work cleanly. The backport is the mechanism — EI switches to the template and adjusts its domain code to match the new hook contract.

---

## 7. EI Backport Sequence

Once the template is complete (sections 1-5 above), the EI backport would proceed as:

### Phase 1: Prepare EI
1. Apply the 12 refactorings from `ei_copier_refactoring.md` to EI's codebase
2. Reorganize EI's test suite: move infrastructure tests out, keep only domain tests
3. Restructure EI's `conftest.py` to import from `conftest_infrastructure` and only add domain fixtures
4. Move `tasks_bp` from `startup.py` to template-owned registration
5. Ensure EI's `container.py` matches the template's hook contract

### Phase 2: Initial copier adoption
1. Run `copier copy` into a fresh directory with EI's feature flags (all true)
2. Copy EI's domain files (models, schemas, services, API endpoints, migrations, tests)
3. Verify EI's full test suite passes against the template infrastructure
4. Create `.copier-answers.yml` to track template version

### Phase 3: Verify and clean up
1. Remove infrastructure code from EI that's now template-owned
2. Remove infrastructure tests from EI that are now mother project tests
3. Verify `copier update` works (template changes flow to EI without conflicts)
4. Run EI's full test suite + Playwright suite

---

## 8. Resolved Decisions

1. **`app/schemas/common.py`** — Template-owned. Ships with every generated app.
2. **Document service extraction** — EI-specific. `document_service.py`, `download_cache_service.py`, `html_document_handler.py` stay in EI.
3. **SSE Gateway path** — Configurable, but default to `/work/SSEGateway/scripts/run-gateway.sh`.
4. **OpenAI runner** — EI-specific. `test_openai_runner.py` stays in EI.
