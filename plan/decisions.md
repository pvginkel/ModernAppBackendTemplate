# Template Design Decisions

This document tracks decisions made when consolidating patterns from the four apps (DHCPApp, ElectronicsInventory, IoTSupport, ZigbeeControl) into a unified template.

## Format

Each decision follows this structure:
- **Context**: What needed to be decided
- **Options**: How each app does it (with file references)
- **Decision**: What the template uses
- **Reasoning**: Why this choice was made

---

## Decisions

### 1. Entry Point and Runner Pattern

**Context**: How to structure the application entry point (`run.py`) and server startup.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `run.py` | Complex: Daemon thread for Waitress, shutdown coordinator integration, TransLogger for access logs |
| IoTSupport | `run.py` | Simple: Direct `app.run()` or `serve()` based on FLASK_ENV |
| ZigbeeControl | `run.py` | Simple with validation: Validates FLASK_ENV, conditional waitress import |
| DHCPApp | `app.py` | Module-level app creation, simple dev/prod switch |

**Decision**: Template provides `common.run(AppContainer)` function that:
1. Accepts the app container class as parameter
2. Handles logging setup
3. Creates app via `create_app()`
4. Manages dev/prod server selection
5. Integrates graceful shutdown coordinator
6. Uses daemon thread pattern for production (from ElectronicsInventory)

**Reasoning**:
- ElectronicsInventory's pattern is most production-ready with graceful shutdown
- Encapsulating in `common.run()` keeps app's `run.py` to a one-liner
- Daemon thread approach allows shutdown coordinator to control exit

---

### 2. Configuration Class Structure

**Context**: How to structure the settings/configuration class.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/config.py` | Pydantic Settings v2, 60+ fields, env file resolution, properties for SQLAlchemy |
| IoTSupport | `app/config.py` | Pydantic Settings v2, 40+ fields, production validation method |
| ZigbeeControl | `app/__init__.py` | Direct `os.environ.get()` calls, no config class |
| DHCPApp | `config.py` | Simple class with class attributes, `get_config()` factory |

**Decision**: Template uses Pydantic Settings v2 with:
1. `CommonSettings` base class in `common/core/settings.py`
2. Apps extend with their own `Settings(CommonSettings)` class
3. `@lru_cache` on `get_settings()` for singleton behavior
4. Feature-specific fields conditionally included via Jinja

**Reasoning**:
- Pydantic provides validation, type safety, and .env file support
- Inheritance allows apps to add fields without modifying common code
- IoTSupport's `validate_production_config()` pattern is app-specific, not in base

---

### 3. Flask App Class

**Context**: Whether to use a custom Flask subclass.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/app.py` | `class App(Flask)` with typed `container` attribute |
| IoTSupport | `app/app.py` | Identical to ElectronicsInventory |
| ZigbeeControl | `app/__init__.py` | Plain `Flask` instance |
| DHCPApp | `app/__init__.py` | `class DHCPApp(Flask)` with service attributes |

**Decision**: Template provides `common.core.flask_app.App` class with typed `container` attribute.

**Reasoning**:
- Type hints for container improve IDE support and catch errors
- Consistent pattern across EI and IoT
- DHCPApp's service attributes approach is replaced by container pattern

---

### 4. Dependency Injection Container

**Context**: How to manage service dependencies.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/services/container.py` | `dependency-injector` DeclarativeContainer, 60+ providers |
| IoTSupport | `app/services/container.py` | `dependency-injector` DeclarativeContainer, 20+ providers |
| ZigbeeControl | `app/__init__.py` | `app.extensions` dict |
| DHCPApp | `app/__init__.py` | Direct attributes on app instance |

**Decision**: Template uses `dependency-injector` with:
1. `CommonContainer` base class with infrastructure services
2. Apps extend with `AppContainer(CommonContainer)`
3. Wiring done in app factory

**Reasoning**:
- `dependency-injector` provides clean DI with Flask integration
- Inheritance model matches settings pattern
- ZigBee/DHCP patterns don't scale to larger apps

---

### 5. Error Handling

**Context**: How to handle errors in API endpoints.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/utils/error_handling.py` | `@handle_api_errors` decorator, handles ValidationError, IntegrityError, custom exceptions |
| IoTSupport | `app/utils/error_handling.py` | Similar decorator (likely copied from EI) |
| ZigbeeControl | N/A | No centralized error handling |
| DHCPApp | `app/__init__.py` | Flask `@app.errorhandler()` decorators |

**Decision**: Template provides `@handle_api_errors` decorator in `common/core/errors.py`:
1. Handles Pydantic ValidationError → 400
2. Handles SQLAlchemy IntegrityError → 409/400 (if database enabled)
3. Handles custom BusinessLogicException hierarchy
4. Generic exception → 500 with logging
5. Includes correlation ID in responses

**Reasoning**:
- Decorator pattern is cleaner than Flask error handlers for API-specific errors
- Flask error handlers still used for 404/500 at app level
- Correlation ID useful for debugging

---

### 6. Spectree/OpenAPI Configuration

**Context**: How to configure OpenAPI documentation.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/utils/spectree_config.py` | Global `api` variable, `configure_spectree()` function |
| IoTSupport | `app/utils/spectree_config.py` | Identical pattern |
| ZigbeeControl | `app/__init__.py` | Inline SpecTree creation in app factory |

**Decision**: Template uses EI/IoT pattern:
1. `common/core/spectree.py` with global `api` and `configure_spectree(app, title, version)`
2. App passes title/version/description during configuration

**Reasoning**:
- Global `api` allows imports in route modules for decorators
- Separate configuration function keeps app factory clean

---

### 7. Metrics Service

**Context**: How to implement Prometheus metrics.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/services/metrics_service.py` | Full service with Protocol, background updater thread, 50+ metrics, shutdown integration |
| IoTSupport | `app/api/metrics.py` | Basic endpoint using `prometheus_client.generate_latest()` |
| ZigbeeControl | N/A | No metrics |
| DHCPApp | N/A | No metrics |

**Decision**: Template provides minimal `MetricsService` in `common/metrics/service.py`:
1. Protocol class for abstraction
2. Basic metrics: shutdown state, task execution, SSE events (if enabled)
3. Background updater infrastructure (apps add their own metrics updates)
4. `/metrics` endpoint

**Reasoning**:
- App-specific metrics (inventory counts, etc.) belong in app code
- Common infrastructure metrics (shutdown, tasks, SSE) are template-provided
- Protocol allows apps to extend with their own implementation

---

### 8. Health Endpoints

**Context**: How to implement health check endpoints for Kubernetes.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/api/health.py` | `/health/readyz` (checks DB, migrations, shutdown), `/health/healthz`, `/health/drain` |
| IoTSupport | `app/api/health.py` | Similar but simpler |
| ZigbeeControl | N/A | No health endpoints |
| DHCPApp | N/A | No health endpoints |

**Decision**: Template provides `common/health/` module:
1. `/health/healthz` - Always 200 (liveness)
2. `/health/readyz` - Checks shutdown state, optionally DB connection (readiness)
3. Database checks only if `use_database` enabled

**Reasoning**:
- Kubernetes requires both probes
- Drain endpoint is EI-specific (manual shutdown trigger), not included in base

---

### 9. Database Session Management

**Context**: How to manage SQLAlchemy sessions per request.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/__init__.py` | `@app.teardown_request` commits/rollbacks based on exception, uses `needs_rollback` flag in session info |
| IoTSupport | `app/__init__.py` | Identical pattern |

**Decision**: Template (when `use_database=true`) provides session management in app factory:
1. `ContextLocalSingleton` provider for db_session
2. `@app.teardown_request` handler for commit/rollback
3. `needs_rollback` flag pattern for explicit rollback marking

**Reasoning**:
- Pattern is identical in both DB-using apps
- ContextLocalSingleton ensures request-scoped sessions

---

### 10. Task Service and SSE Broadcasting

**Context**: How to handle background tasks and their relationship to SSE.

**Options**:

| App | File | Approach |
|-----|------|----------|
| ElectronicsInventory | `app/services/task_service.py` | TaskService with ConnectionManager dependency for broadcasting |

**Decision**: Template decouples tasks from SSE:
1. `TaskService` depends on `BroadcasterProtocol`
2. When SSE enabled: `ConnectionManager` implements protocol
3. When SSE disabled: `NullBroadcaster` provides no-op implementation
4. Tasks always available, broadcasts are silent without SSE

**Reasoning**:
- User requirement: tasks unconditionally available
- Protocol pattern allows graceful degradation
- Apps can run tasks without real-time updates

---
