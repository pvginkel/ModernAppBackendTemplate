# Template Changelog

This changelog tracks all changes to the template that affect downstream apps. Each entry includes migration instructions for updating apps from the template.

See `CLAUDE.md` for instructions on how to use this changelog when updating apps.

---

<!-- Add new entries at the top, below this line -->

## 2026-02-13

### Add service layer files (infrastructure services and DI container scaffold)

**What changed:** Added the service layer files extracted from Electronics Inventory. These provide the infrastructure services that all generated apps share, plus a scaffold DI container for app-specific customization.

Template-maintained service files (overwritten on `copier update`):
- `template/app/services/__init__.py` - Empty services package
- `template/app/services/health_service.py` - Health check callback registry (healthz/readyz/drain)
- `template/app/services/metrics_service.py` - Background polling service for Prometheus metrics
- `template/app/services/task_service.py` - Background task management with SSE progress updates
- `template/app/services/base_task.py` - Abstract base classes for background tasks (BaseTask, BaseSessionTask)
- `template/app/services/sse_connection_manager.py` - SSE Gateway token mapping and event delivery (always included)
- `template/app/services/auth_service.py` - JWT validation with JWKS discovery (use_oidc)
- `template/app/services/oidc_client_service.py` - OIDC authorization code flow with PKCE (use_oidc)
- `template/app/services/s3_service.py` - S3-compatible storage operations (use_s3)
- `template/app/services/cas_image_service.py` - CAS thumbnail generation and image processing (use_s3)
- `template/app/services/frontend_version_service.py` - Frontend version SSE notifications (use_sse)
- `template/app/services/diagnostics_service.py` - Request/query performance profiling (use_database)

App-maintained scaffold (skip_if_exists, generated once):
- `template/app/services/container.py.jinja` - DI container with infrastructure providers; app adds domain providers

Also changed:
- `template/app/utils/temp_file_manager.py` - Reordered constructor params to put `lifecycle_coordinator` first; added defaults for `base_path` ("/tmp/app-temp") and `cleanup_age_hours` (24.0) so the scaffold container works without app-specific config

**Migration steps:**
1. Copy all new service files from template into your app's `app/services/` directory
2. Review your existing `app/services/container.py` - the scaffold is a starting point; your container should already have these infrastructure providers plus your domain-specific ones
3. If your `TempFileManager` usage passes `base_path` and `cleanup_age_hours` as positional args, update to use keyword arguments since the parameter order changed (lifecycle_coordinator is now first)
4. The `sse_connection_manager.py` and `base_task.py` are always included regardless of feature flags (TaskService depends on them)

## 2026-02-13

### Add schemas, build/deploy, alembic, scripts, and test infrastructure files

**What changed:** Added the remaining template files extracted from Electronics Inventory:

Schema files:
- `template/app/schemas/__init__.py` - Schema package init
- `template/app/schemas/health_schema.py` - Health check response schema (Pydantic)
- `template/app/schemas/task_schema.py` - Task status, events, progress schemas (Pydantic)
- `template/app/schemas/sse_gateway_schema.py` - SSE Gateway callback/send schemas (Pydantic)
- `template/app/schemas/upload_document.py` - Document upload schemas (use_s3, generic - no EI model dependency)

Build/deploy files:
- `template/run.py` - Development/production server entry point (Waitress + Flask debug)
- `template/.gitignore` - Standard Python/Flask gitignore
- `template/Dockerfile.jinja` - Multi-stage Docker build with feature-flagged system deps
- `template/Jenkinsfile.jinja` - Jenkins CI/CD pipeline with template variables
- `template/pyproject.toml.jinja` - Poetry project config with feature-flagged dependencies (skip_if_exists)
- `template/.env.example.jinja` - Environment variable documentation grouped by feature flag (skip_if_exists)

Alembic files (use_database only):
- `template/alembic.ini.jinja` - Alembic configuration with templated DB URL
- `template/alembic/env.py` - Alembic environment (offline/online migrations, test connection reuse)
- `template/alembic/script.py.mako` - Migration script template
- `template/alembic/versions/.gitkeep` - Empty versions directory

Scripts:
- `template/scripts/args.sh.jinja` - Shared variables (project name, ports) with template variables
- `template/scripts/build.sh` - Docker build script
- `template/scripts/dev-server.sh` - Development server restart loop
- `template/scripts/dev-sse-gateway.sh` - SSE Gateway development restart loop
- `template/scripts/initialize-sqlite-database.sh` - SQLite database initialization
- `template/scripts/push.sh` - Docker push to registry
- `template/scripts/run.sh` - Docker run script
- `template/scripts/stop.sh` - Docker stop script
- `template/scripts/testing-server.sh` - Testing server (generic, no EI references)

Test infrastructure:
- `template/tests/__init__.py` - Test package init
- `template/tests/conftest_infrastructure.py.jinja` - Infrastructure fixtures with feature-flagged sections (database clone pattern, OIDC mocks, SSE server, S3 checks)
- `template/tests/conftest.py` - Scaffold that imports infrastructure fixtures (skip_if_exists)

EI-specific code removed:
- `upload_document.py`: Replaced `AttachmentType` model import with generic `str | None`
- `pyproject.toml`: Removed openai, anthropic, celery, beautifulsoup4, validators, reportlab, types-beautifulsoup4 dependencies
- `conftest_infrastructure.py`: Removed AI/Mouser/document settings from `_build_test_app_settings`
- `testing-server.sh`: Replaced "Electronics Inventory" with generic "backend"
- `Dockerfile`: Changed from PyPy to CPython 3.12, removed jiter/openai patches

**Migration steps:**
1. These are new files - no migration needed for existing downstream apps
2. For new apps generated from the template, all files are created automatically
3. Existing apps should:
   - Compare their `pyproject.toml` against the template and ensure infrastructure dependencies match
   - Adopt `conftest_infrastructure.py` pattern: import infrastructure fixtures in `conftest.py`
   - Move from custom Dockerfile to the template Dockerfile pattern if not already using it
   - Replace EI-specific `upload_document.py` imports with generic types if using S3

## 2026-02-13

### Add core application files (app factory, config, CLI, exceptions, database)

**What changed:** Added the core application layer files extracted from Electronics Inventory:

- `template/app/__init__.py.jinja` - Flask application factory with feature-flagged sections for database, OIDC, S3, and SSE
- `template/app/app.py` - Custom Flask App class with typed container attribute
- `template/app/config.py.jinja` - Two-layer configuration (Environment + Settings) with feature-flagged field groups
- `template/app/cli.py.jinja` - CLI commands (upgrade-db, load-test-data) with feature-flagged database sections
- `template/app/consts.py.jinja` - Project constants scaffold (skip_if_exists)
- `template/app/app_config.py` - App-specific settings scaffold (skip_if_exists)
- `template/app/startup.py` - Hook functions scaffold (skip_if_exists)
- `template/app/exceptions.py` - Base exception classes scaffold (skip_if_exists)
- `template/app/extensions.py` - Flask-SQLAlchemy initialization (use_database only)
- `template/app/database.py` - Database operations: migrations, health checks, upgrade (use_database only)
- `template/app/models/__init__.py` - Empty models scaffold (skip_if_exists, use_database only)

EI-specific code removed: dashboard_metrics, sync_master_data_from_setup, SetupService import, InsufficientQuantityException, CapacityExceededException, DependencyException.

**Migration steps:**
1. These are new files - no migration needed for existing downstream apps
2. For new apps generated from the template, all files are created automatically
3. Existing apps should compare their `app/__init__.py`, `app/config.py`, `app/cli.py` against these templates and adopt the hook-based pattern if not already using it
