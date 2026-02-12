# Copier Template Approach

This document captures everything learned from the first template extraction attempt, including decisions made during review. It serves as the guide for the next iteration.

## Goal

A Copier template that generates self-contained Flask backend projects. Each project is plain Python with no runtime dependency on the template. Template updates via `copier update` with three-way merge.

## Architecture

### Everything under `app/`

No `common/` directory. Template-maintained and app-maintained files coexist in the same `app/` tree. Ownership is tracked via `_skip_if_exists` in copier.yml and documentation.

### Hook-based contract via `startup.py`

The app customizes behavior through hooks in `app/startup.py` (app-owned, `_skip_if_exists`):

- `create_container()` — builds the DI container with app-specific providers
- `register_blueprints()` — registers domain resource blueprints on the `/api` blueprint
- `register_error_handlers()` — registers app-specific Flask error handlers
- `register_cli_commands(cli_app)` — registers app-specific CLI commands (Click)
- `post_migration_hook(app)` — runs after database migrations (called by CLI)
- `load_test_data_hook(app)` — loads test fixtures (called by CLI)

### Feature flags

Four boolean flags control file inclusion and conditional code sections:

| Flag | Controls |
|------|----------|
| `use_database` | SQLAlchemy, Alembic, migrations, pool diagnostics |
| `use_oidc` | OIDC authentication (BFF pattern with JWT cookies) |
| `use_s3` | S3 storage, CAS endpoints, image processing |
| `use_sse` | Server-Sent Events via SSE Gateway |

Files behind flags are excluded entirely via copier.yml `_exclude`. Files with conditional sections use `.jinja` suffix. **Minimize Jinja usage** — prefer plain Python files with runtime conditionals or separate files per feature over Jinja conditionals.

### Two config layers

The app has two independent configuration models:

1. **Template Settings** (`config.py`, template-owned) — infrastructure config (database, OIDC, S3, SSE, core Flask settings). Uses `Environment` for env var loading and `Settings` as clean Pydantic model.
2. **App Settings** (`app_config.py`, app-owned, `_skip_if_exists`) — domain-specific config. The app defines its own Pydantic settings model loaded from env vars. The container holds both.

This avoids the conflict of a template-owned Pydantic model that the app needs to extend.

### Constants file

Static metadata like API title and description go in `consts.py` (app-owned, `_skip_if_exists`) rather than config.py or Jinja templates. This eliminates the need for `.jinja` on files that only need the project name/description. Any file that would only be Jinja because of project name/description should read from consts.py instead.

---

## File Ownership Model

### Template-maintained (overwritten by `copier update`)

All infrastructure code. Developers should not edit these:

- `app/__init__.py` — application factory
- `app/app.py` — App class
- `app/config.py` — infrastructure Settings
- `app/cli.py` — CLI entry point (Click-based, calls hooks for extensibility)
- `app/api/__init__.py` — API blueprint setup
- `app/api/health.py` — health endpoints (uses HealthService callback registry)
- `app/api/metrics.py` — Prometheus metrics endpoint
- `app/api/oidc_hooks.py` — OIDC before/after request hooks (excluded when `use_oidc=false`)
- `app/api/auth.py` — OIDC auth endpoints
- `app/api/sse.py` — SSE Gateway callbacks
- `app/api/cas.py` — CAS blob serving
- `app/services/` — all infrastructure services
- `app/utils/` — all utilities
- `app/schemas/` — infrastructure schemas (health, task, SSE gateway)
- `run.py` — server entry point
- `Dockerfile` — container build
- `Jenkinsfile` — CI pipeline
- `scripts/` — shell scripts
- `alembic/env.py`, `alembic/script.py.mako` — migration config

### App-maintained (`_skip_if_exists` — generated once, never overwritten)

- `app/startup.py` — all hook implementations
- `app/services/container.py` — DI container with infrastructure + app providers
- `app/exceptions.py` — base + app-specific exceptions
- `app/consts.py` — project constants (API title, description)
- `app/app_config.py` — app-specific settings
- `app/models/__init__.py` — model imports for Alembic
- `pyproject.toml` — dependencies (app manages after initial generation)
- `tests/conftest.py` — imports infrastructure fixtures, adds app fixtures
- `.env.example` — environment variable documentation

### Template-maintained test infrastructure

- `tests/conftest_infrastructure.py` — infrastructure fixtures (app factory, client, session, OIDC mocking). Template-owned, overwritten on update. The app's `tests/conftest.py` imports from this.

---

## Copier Configuration

### Template variables

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `project_name` | str | required | Package name, CLI name, Docker image |
| `project_description` | str | "A Flask backend application" | Used in generated consts.py |
| `author_name` | str | required | pyproject.toml author |
| `author_email` | str | required | pyproject.toml author |
| `repo_url` | str | required | Git repository URL for Jenkinsfile |
| `image_name` | str | `registry:5000/{{ project_name }}` | Docker image name for Jenkinsfile |
| `backend_port` | int | 5000 | Server port for Dockerfile EXPOSE and run.py default |
| `use_database` | bool | true | SQLAlchemy + Alembic |
| `use_oidc` | bool | false | OIDC authentication |
| `use_s3` | bool | false | S3/MinIO storage |
| `use_sse` | bool | false | Server-Sent Events |

Note: `workspace_name` was removed — `repo_url` and `image_name` are the correct independent parameters.

### `_skip_if_exists` files

```yaml
_skip_if_exists:
  - app/startup.py
  - app/services/container.py
  - app/exceptions.py
  - app/consts.py
  - app/app_config.py
  - app/models/__init__.py
  - pyproject.toml
  - tests/conftest.py
  - .env.example
```

### `_exclude` rules

Feature-flagged files use Jinja expressions in `_exclude`:

```yaml
_exclude:
  # Database
  - "{% if not use_database %}app/extensions.py{% endif %}"
  - "{% if not use_database %}app/database.py{% endif %}"
  - "{% if not use_database %}app/services/diagnostics_service.py{% endif %}"
  - "{% if not use_database %}app/utils/pool_diagnostics.py{% endif %}"
  - "{% if not use_database %}app/utils/empty_string_normalization.py{% endif %}"
  - "{% if not use_database %}alembic.ini{% endif %}"
  - "{% if not use_database %}alembic/{% endif %}"

  # OIDC
  - "{% if not use_oidc %}app/api/auth.py{% endif %}"
  - "{% if not use_oidc %}app/api/oidc_hooks.py{% endif %}"
  - "{% if not use_oidc %}app/services/auth_service.py{% endif %}"
  - "{% if not use_oidc %}app/services/oidc_client_service.py{% endif %}"
  - "{% if not use_oidc %}app/utils/auth.py{% endif %}"

  # S3
  - "{% if not use_s3 %}app/api/cas.py{% endif %}"
  - "{% if not use_s3 %}app/services/s3_service.py{% endif %}"
  - "{% if not use_s3 %}app/services/cas_image_service.py{% endif %}"
  - "{% if not use_s3 %}app/utils/cas_url.py{% endif %}"
  - "{% if not use_s3 %}app/utils/image_processing.py{% endif %}"
  - "{% if not use_s3 %}app/utils/mime_handling.py{% endif %}"
  - "{% if not use_s3 %}app/schemas/upload_document.py{% endif %}"

  # SSE (sse_connection_manager.py and sse_gateway_schema.py always included — TaskService depends on them)
  - "{% if not use_sse %}app/api/sse.py{% endif %}"
  - "{% if not use_sse %}app/services/frontend_version_service.py{% endif %}"
  - "{% if not use_sse %}app/utils/sse_utils.py{% endif %}"
  - "{% if not use_sse %}app/utils/log_capture.py{% endif %}"
```

---

## Key Design Decisions

### 1. OIDC hooks extracted from `api/__init__.py`

The OIDC `before_request`/`after_request` hooks (~85 lines) live in `app/api/oidc_hooks.py`, excluded when `use_oidc=false`. The API `__init__.py` calls `register_oidc_hooks(api_bp)` conditionally. This keeps the API init clean.

### 2. HealthService with callback registry

Health checks use a `HealthService` with callback hook lists for both `healthz` and `readyz`. Features register their checks during startup:

```python
health_service.register_readyz("database", check_db_health)
health_service.register_readyz("s3", check_s3_health)
```

The health blueprint delegates to HealthService. No Jinja needed in health.py.

### 3. Testing endpoints are app-owned

Content fixture endpoints (image/pdf/html) are domain-specific. The template does NOT include testing blueprints or testing services. Apps implement their own testing endpoints if needed for Playwright. The template only provides the `is_testing` check utility.

Database reset is NOT exposed via HTTP. It lives in the CLI only.

### 4. No ResetLock in template

The reset lock was only used by the database reset HTTP endpoint. With reset moved to CLI-only, the lock is unnecessary. The CLI is inherently single-process.

### 5. CLI uses Click

Click integrates with Flask's CLI system and is more idiomatic. The template provides database commands (upgrade-db, load-test-data) conditionally. The app extends via `register_cli_commands()` hook in startup.py.

### 6. Split conftest

- `tests/conftest_infrastructure.py` — template-owned, provides app/client/session/OIDC fixtures
- `tests/conftest.py` — app-owned (`_skip_if_exists`), imports infrastructure fixtures, adds domain fixtures

### 7. pyproject.toml is `_skip_if_exists`

Generated once with correct deps for the chosen feature flags. App manages it afterwards. Template dep changes are documented in the changelog for manual migration.

### 8. Renamed services for clarity

- `ConnectionManager` -> `SSEConnectionManager` (file: `sse_connection_manager.py`)
- `ImageService` -> `CasImageService` (file: `cas_image_service.py`)
- `VersionService` -> `FrontendVersionService` (file: `frontend_version_service.py`)

### 9. SSEConnectionManager and SSE gateway schema are always included

`TaskService` depends on `SSEConnectionManager` which imports `sse_gateway_schema`. Both files are included regardless of `use_sse`. Only the SSE blueprint, FrontendVersionService, sse_utils, and log_capture are SSE-conditional.

### 10. `requests` package is an explicit dependency

`SSEConnectionManager` imports `requests`. Even though it's available as a transitive dependency, it must be explicit in pyproject.toml.

### 11. Replace `flask_log_request_id`

The `flask_log_request_id` package is incompatible with Flask 3.x (imports removed `_app_ctx_stack`). Replace with custom implementation in `app/utils/__init__.py` using Flask's `g` object and `before_request` hook. This must be done in EI first.

### 12. Backend port is parameterized

`backend_port` copier variable (default 5000) used in `Dockerfile` EXPOSE and `run.py` default.

### 13. S3 test availability is a fixture, not a blocker

The generated conftest does NOT block all tests if S3 is unreachable. Instead, a `require_s3` fixture skips individual tests that need real S3. This allows domain tests to run without S3 infrastructure.

---

## What Worked Well

### Hook-based architecture
The three hooks in `startup.py` cleanly separate template infrastructure from app logic. The app factory (`__init__.py`) can be fully template-owned because all customization goes through hooks.

### test-app-domain pattern
Hand-written domain files in `test-app-domain/` copied into the generated `test-app/` after Copier runs. This gives a real working app to test against without polluting the template. The flow is:
1. `copier copy` generates scaffolding into `test-app/`
2. Domain files from `test-app-domain/` overwrite the scaffolds
3. `test-app/` is now a complete working app

### Mother project tests
Infrastructure tests in `tests/` (outside test-app) validate template infrastructure. They run from inside test-app's poetry environment: `cd test-app && python -m pytest ../tests/ -v`. This ensures the generated infrastructure works correctly.

### Flag combination validation
A script that generates projects with all key flag combinations and verifies `py_compile` succeeds on every `.py` file. Catches Jinja rendering errors, missing imports, and dependency issues across all combinations.

### SQLite template cloning for test isolation
A session-scoped fixture creates a "template" SQLite database, runs migrations once, then each test clones it via `sqlite3.Connection.backup()`. Fast and isolated.

### Prometheus registry clearing
An autouse fixture clears the Prometheus registry before and after each test. Without this, metric collectors from one test leak into the next.

## What Did Not Work Well

### Too many `.jinja` files
Files were made Jinja just because they had a project name reference or a single conditional. Prefer: separate files per feature, runtime conditionals, or reading from consts.py. Reserve Jinja for files with substantial conditional sections (config.py, __init__.py, cli.py).

### Template-owned files that apps need to extend
`config.py`, `cli.py`, `conftest.py`, `pyproject.toml` — all template-owned but apps need to add their own content. Solution: split ownership (separate AppSettings, CLI hooks, conftest_infrastructure import, `_skip_if_exists` for pyproject.toml).

### S3 availability as a global blocker
`pytest_configure` in conftest hard-failed all tests if MinIO wasn't reachable. Domain tests that don't need S3 couldn't run. Solution: `require_s3` fixture that skips individual tests.

### ConnectionManager dependency chain
`TaskService` -> `SSEConnectionManager` -> `sse_gateway_schema`. When SSE files were excluded, the dependency chain broke. Solution: always include SSEConnectionManager and its schema.

### `flask_log_request_id` incompatibility
This third-party package uses Flask internals removed in Flask 3.x. It silently broke correlation ID tracking, causing error responses to crash instead of returning JSON. Must be replaced in EI first before template extraction.

### Monolithic testing.py
Testing endpoints mixed content fixtures, database reset, log streaming, and SSE testing into one file with heavy Jinja conditionals. Made the template complex and hard to maintain.

### No consts.py
Project name/description were injected via Jinja into multiple files. A consts.py read at runtime eliminates most Jinja usage.

---

## Implementation Sequence

### Phase 1: Core + Database

1. Create `copier.yml` with all variables and exclusion rules
2. Create core template files — app factory, config, CLI, error handlers, lifecycle, health, metrics, utilities
3. Create database-conditional files — extensions, database.py, migrations, pool diagnostics, diagnostics service
4. Create `consts.py` scaffold and `app_config.py` scaffold
5. Create test-app domain files in `test-app-domain/` (Item model, CRUD API, migration)
6. Create mother project tests (health, metrics, lifecycle, error handling, config, task service)
7. Generate test-app, copy domain files, run all tests
8. Validate flag combinations (all-true, db-only, minimal)

### Phase 2: OIDC + S3 + SSE

1. Add OIDC files — auth endpoints, auth service, OIDC client, oidc_hooks.py, auth utilities
2. Add S3 files — CAS endpoint, S3 service, CasImageService, image processing, MIME handling
3. Add SSE files — SSE callback endpoint, SSEConnectionManager, FrontendVersionService, SSE utils, log capture
4. Update test-app domain if needed
5. Add feature-specific mother project tests (auth, S3, SSE, connection manager)
6. Validate all flag combinations

### Phase 3: Validation

1. Generate projects with all key flag combinations, verify py_compile
2. Runtime smoke tests for minimal and db-only variants
3. Full test suite: mother project tests + test-app domain tests
4. Copy scripts from EI, template as needed

---

## Regeneration Workflow

```bash
cd /work/ModernAppTemplate/backend

# 1. Regenerate test-app
rm -rf test-app
poetry run copier copy . test-app --trust \
  -d project_name=test-app \
  -d project_description="Test application" \
  -d author_name="Test Author" \
  -d author_email="test@example.com" \
  -d repo_url="https://github.com/test/test-app.git" \
  -d image_name="registry:5000/test-app" \
  -d backend_port=5000 \
  -d use_database=true \
  -d use_oidc=true \
  -d use_s3=true \
  -d use_sse=true

# 2. Copy domain files
cp test-app-domain/app/startup.py test-app/app/startup.py
cp test-app-domain/app/services/container.py test-app/app/services/container.py
cp test-app-domain/app/exceptions.py test-app/app/exceptions.py
cp test-app-domain/app/consts.py test-app/app/consts.py
cp test-app-domain/app/app_config.py test-app/app/app_config.py
cp -r test-app-domain/app/models/* test-app/app/models/
cp test-app-domain/app/schemas/item_schema.py test-app/app/schemas/
cp test-app-domain/app/services/item_service.py test-app/app/services/
cp test-app-domain/app/api/items.py test-app/app/api/
cp -r test-app-domain/tests/* test-app/tests/
mkdir -p test-app/alembic/versions
cp test-app-domain/alembic/versions/001_create_items.py test-app/alembic/versions/
echo "# Test App" > test-app/README.md

# 3. Install and test
cd test-app && poetry install
python -m pytest ../tests/ -v      # Mother project tests
python -m pytest tests/ -v          # Domain tests
```

---

## Template File Inventory

### Always included (no feature flag)

**App core:**
- `app/__init__.py.jinja` — application factory (Jinja: feature conditionals)
- `app/app.py` — App class
- `app/config.py.jinja` — infrastructure Settings (Jinja: feature settings groups)
- `app/cli.py.jinja` — Click CLI (Jinja: database commands conditional)
- `app/consts.py` — project constants scaffold (`_skip_if_exists`)
- `app/app_config.py` — app settings scaffold (`_skip_if_exists`)
- `app/startup.py` — hook implementations scaffold (`_skip_if_exists`)
- `app/exceptions.py` — base exceptions scaffold (`_skip_if_exists`)

**API:**
- `app/api/__init__.py.jinja` — API blueprint (Jinja: OIDC hook registration)
- `app/api/health.py` — health endpoints (no Jinja, uses HealthService)
- `app/api/metrics.py` — Prometheus metrics

**Services:**
- `app/services/__init__.py` — empty
- `app/services/container.py` — DI container scaffold (`_skip_if_exists`)
- `app/services/health_service.py` — health check callback registry
- `app/services/metrics_service.py` — Prometheus metrics
- `app/services/task_service.py` — background task execution
- `app/services/base_task.py` — task base class
- `app/services/sse_connection_manager.py` — SSE connection tracking (always included, TaskService depends on it)

**Utilities:**
- `app/utils/__init__.py` — correlation ID tracking
- `app/utils/flask_error_handlers.py` — error handler registration
- `app/utils/lifecycle_coordinator.py` — startup/shutdown lifecycle
- `app/utils/spectree_config.py` — OpenAPI docs (reads from consts.py, no Jinja)
- `app/utils/temp_file_manager.py` — temp file cleanup
- `app/utils/request_parsing.py` — query parameter helpers
- `app/utils/text_utils.py` — text truncation
- `app/utils/url_utils.py` — URL parsing

**Schemas:**
- `app/schemas/__init__.py` — empty
- `app/schemas/health_schema.py` — health response
- `app/schemas/task_schema.py` — task status/events
- `app/schemas/sse_gateway_schema.py` — SSE Gateway types (always included)

**Build/deploy:**
- `pyproject.toml.jinja` — dependencies (`_skip_if_exists`)
- `run.py` — server entry (reads port from env, no Jinja needed if default in consts.py)
- `Dockerfile.jinja` — container build (Jinja: feature deps, port)
- `Jenkinsfile.jinja` — CI pipeline (Jinja: repo_url, image_name)
- `.gitignore` — standard ignores
- `.env.example.jinja` — env var documentation (`_skip_if_exists`)
- `scripts/` — shell scripts (some need Jinja for project name)

**Tests:**
- `tests/__init__.py` — empty
- `tests/conftest_infrastructure.py.jinja` — infrastructure fixtures (template-owned)
- `tests/conftest.py` — app fixture scaffold (`_skip_if_exists`)

### `use_database` files
- `app/extensions.py`
- `app/database.py`
- `app/models/__init__.py` (`_skip_if_exists`, no Jinja)
- `app/services/diagnostics_service.py`
- `app/utils/pool_diagnostics.py`
- `app/utils/empty_string_normalization.py`
- `alembic.ini.jinja`
- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/.gitkeep`

### `use_oidc` files
- `app/api/auth.py`
- `app/api/oidc_hooks.py`
- `app/services/auth_service.py`
- `app/services/oidc_client_service.py`
- `app/utils/auth.py`

### `use_s3` files
- `app/api/cas.py`
- `app/services/s3_service.py`
- `app/services/cas_image_service.py`
- `app/utils/cas_url.py`
- `app/utils/image_processing.py`
- `app/utils/mime_handling.py`
- `app/schemas/upload_document.py`

### `use_sse` files
- `app/api/sse.py`
- `app/services/frontend_version_service.py`
- `app/utils/sse_utils.py`
- `app/utils/log_capture.py`
