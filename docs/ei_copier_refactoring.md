# ElectronicsInventory Refactorings for Copier Template

These refactorings should be done in EI before the next template extraction. Each makes the EI code cleaner independently, and collectively they make the template boundary obvious.

## Priority order

Refactorings are ordered by dependency — later items may depend on earlier ones.

---

## 1. Replace `flask_log_request_id` with custom correlation ID

**Why:** `flask_log_request_id` imports `_app_ctx_stack` from Flask, removed in Flask 2.3+. It silently breaks correlation ID tracking under Flask 3.x, causing error responses to crash instead of returning JSON with `correlationId`.

**What to do:**

1. Remove `flask-log-request-id` from pyproject.toml
2. Replace `app/utils/__init__.py` with:

```python
"""Utility functions and helpers."""

import uuid

from flask import g, has_request_context, request


def get_current_correlation_id() -> str | None:
    """Get the current request's correlation ID."""
    if not has_request_context():
        return None
    return getattr(g, "correlation_id", None)


def _init_request_id(app):
    """Register before_request handler to set correlation ID."""

    @app.before_request
    def set_request_id():
        g.correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())


def ensure_request_id_from_query(request_id: str | None) -> None:
    """Set correlation ID from query parameter for SSE streams."""
    if request_id and has_request_context():
        g.correlation_id = request_id
```

3. In `app/__init__.py`, replace:
```python
from flask_log_request_id import RequestID
RequestID(app)
```
with:
```python
from app.utils import _init_request_id
_init_request_id(app)
```

4. Update any imports of `current_request_id()` to use `get_current_correlation_id()` (or alias).

**Verification:** Run EI test suite. All error responses should include `correlationId`.

---

## 2. Rename services for clarity

**Why:** Generic names are ambiguous. The template will have many services; names should indicate their domain.

**Renames:**

| Current | New | File rename |
|---------|-----|-------------|
| `ConnectionManager` | `SSEConnectionManager` | `connection_manager.py` -> `sse_connection_manager.py` |
| `ImageService` | `CasImageService` | `image_service.py` -> `cas_image_service.py` |
| `VersionService` | `FrontendVersionService` | `version_service.py` -> `frontend_version_service.py` |

**What to do:**

For each rename:
1. Rename the file
2. Rename the class
3. Update all imports (grep for old name across entire codebase)
4. Update DI container provider names
5. Update any string references (logging, metrics labels)

**Verification:** Run EI test suite. All tests pass.

---

## 3. Extract OIDC hooks from `app/api/__init__.py`

**Why:** `api/__init__.py` contains ~85 lines of OIDC-specific code (`before_request` auth, `after_request` cookie refresh, auth_bp registration). This should be a self-contained module.

**What to do:**

1. Create `app/api/oidc_hooks.py` with a single entry point:

```python
def register_oidc_hooks(api_bp: Blueprint) -> None:
    """Register OIDC authentication hooks on the API blueprint."""

    @api_bp.before_request
    @inject
    def before_request_authentication(...):
        ...

    @api_bp.after_request
    @inject
    def after_request_set_cookies(...):
        ...

    # Register auth blueprint
    from app.api.auth import auth_bp
    api_bp.register_blueprint(auth_bp)
```

2. Simplify `app/api/__init__.py` to:

```python
from flask import Blueprint

api_bp = Blueprint("api", __name__, url_prefix="/api")

# OIDC hooks (conditional in template, always present in EI)
from app.api.oidc_hooks import register_oidc_hooks
register_oidc_hooks(api_bp)
```

**Verification:** Run EI test suite. All auth tests pass.

---

## 4. Create HealthService with callback registry

**Why:** `health.py` hardcodes database checks. A callback registry lets features register their own health checks, eliminating Jinja conditionals in the template.

**What to do:**

1. Create `app/services/health_service.py`:

```python
class HealthService:
    def __init__(self, lifecycle_coordinator):
        self.lifecycle_coordinator = lifecycle_coordinator
        self._healthz_checks: list[tuple[str, Callable]] = []
        self._readyz_checks: list[tuple[str, Callable]] = []

    def register_healthz(self, name: str, check: Callable[[], dict]) -> None:
        self._healthz_checks.append((name, check))

    def register_readyz(self, name: str, check: Callable[[], dict]) -> None:
        self._readyz_checks.append((name, check))

    def check_healthz(self) -> tuple[dict, int]:
        result = {"status": "alive", "ready": True}
        for name, check in self._healthz_checks:
            result[name] = check()
        return result, 200

    def check_readyz(self) -> tuple[dict, int]:
        if self.lifecycle_coordinator.is_shutting_down():
            return {"status": "shutting down", "ready": False}, 503

        result = {"status": "ready", "ready": True}
        all_ok = True
        for name, check in self._readyz_checks:
            check_result = check()
            result[name] = check_result
            if not check_result.get("ok", True):
                all_ok = False

        if not all_ok:
            result["ready"] = False
            return result, 503
        return result, 200
```

2. Register database health checks during app startup:

```python
health_service = container.health_service()

# In create_app, after db init:
def check_db_health():
    connected = check_db_connection()
    pending = get_pending_migrations() if connected else []
    return {
        "connected": connected,
        "migrations_pending": len(pending),
        "ok": connected and not pending,
    }

health_service.register_readyz("database", check_db_health)
```

3. Simplify `app/api/health.py` to delegate to HealthService:

```python
@health_bp.route("/readyz")
@inject
def readyz(health_service=Provide[ServiceContainer.health_service]):
    result, status = health_service.check_readyz()
    return jsonify(result), status
```

4. Add HealthService to the DI container.

**Verification:** Run EI test suite. Health endpoint responses should be identical.

---

## 5. Create `consts.py` for project metadata

**Why:** Eliminates Jinja templating in files that only need the project name/description. Files read from consts.py at runtime instead.

**What to do:**

1. Create `app/consts.py`:

```python
"""Project constants."""

PROJECT_NAME = "ElectronicsInventory"
PROJECT_DESCRIPTION = "Electronics inventory management system"
API_TITLE = "ElectronicsInventory API"
API_DESCRIPTION = "Electronics inventory management system"
```

2. Update `app/utils/spectree_config.py` to read from consts:

```python
from app.consts import API_TITLE, API_DESCRIPTION

api = SpecTree(
    backend_name="flask",
    title=API_TITLE,
    description=API_DESCRIPTION,
    ...
)
```

3. Update any other files that reference the project name as a hardcoded string.

**Verification:** API docs should show the same title/description.

---

## 6. Create separate AppSettings for domain config

**Why:** The template's `config.py` handles infrastructure settings. EI has domain-specific settings (AI provider, Mouser API, document processing). These should be in a separate model so the template can own `config.py`.

**What to do:**

1. Create `app/app_config.py`:

```python
"""Application-specific configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # AI / LLM
    AI_PROVIDER: str = Field(default="ollama")
    AI_MODEL: str = Field(default="llama3")
    OPENAI_API_KEY: str | None = Field(default=None)
    # ... all EI-specific settings
```

2. Load in container and make available:

```python
# In container.py:
app_config = providers.Singleton(AppSettings)
```

3. Move all EI-specific settings out of the main `Settings` class.
4. Services that need EI-specific config take `AppSettings` as a dependency.

**Verification:** Run EI test suite. All settings should resolve correctly.

---

## 7. Switch CLI to Click

**Why:** Click integrates with Flask's CLI system and supports extensibility via hooks more naturally than argparse.

**What to do:**

1. Rewrite `app/cli.py` using Click:

```python
import click
from app import create_app

@click.group()
@click.pass_context
def cli(ctx):
    """Project CLI."""
    ctx.ensure_object(dict)
    ctx.obj["app"] = create_app(skip_background_services=True)

@cli.command()
@click.option("--recreate", is_flag=True)
@click.option("--yes-i-am-sure", is_flag=True)
@click.pass_context
def upgrade_db(ctx, recreate, yes_i_am_sure):
    """Apply database migrations."""
    ...

@cli.command()
@click.option("--yes-i-am-sure", is_flag=True)
@click.pass_context
def load_test_data(ctx, yes_i_am_sure):
    """Recreate database and load test data."""
    ...

def main():
    # Register app-specific commands via hook
    from app.startup import register_cli_commands
    register_cli_commands(cli)

    cli()
```

2. Add `register_cli_commands` hook to `app/startup.py`:

```python
def register_cli_commands(cli):
    """Register app-specific CLI commands."""
    from app.cli_commands.sync_mouser import sync_mouser
    cli.add_command(sync_mouser)
```

3. Update `pyproject.toml`:
```toml
[tool.poetry.scripts]
cli = "app.cli:main"
```

**Verification:** All CLI commands work as before. `cli --help` shows all commands.

---

## 8. Remove database reset from testing API

**Why:** Database reset via HTTP is a testing convenience that doesn't belong in the template. The CLI handles it. The ResetLock is unnecessary for CLI-only reset.

**What to do:**

1. Remove from `app/api/testing.py`:
   - `/api/testing/reset` endpoint
   - Import of `TestResetResponseSchema`

2. Remove from `app/services/testing_service.py`:
   - `reset_database()` method
   - `is_reset_in_progress()` method
   - `ResetLock` dependency in constructor

3. Remove `app/utils/reset_lock.py` entirely (if only used by testing_service reset).

4. Remove `TestResetResponseSchema` from `app/schemas/testing.py`.

5. Update DI container — `TestingService` no longer needs `ResetLock`.

6. Update any Playwright tests that call `/api/testing/reset` to use CLI instead, or keep the endpoint in EI's app-specific code if Playwright still needs it.

**Note:** If Playwright tests still need HTTP-based reset, move the endpoint and its logic into EI's app-specific code (registered via `register_blueprints` hook). It just shouldn't be in the template.

**Verification:** Run EI test suite. CLI commands still work. Playwright tests updated if needed.

---

## 9. Split testing.py into focused blueprints

**Why:** `testing.py` mixes content fixtures, log streaming, SSE testing, and database reset into one file. After removing database reset (item 8), split the remaining concerns.

**What to do:**

Each of these becomes app-specific (not in the template). Move to EI's app code:

1. **Content fixtures** — `app/api/testing_content.py` (image, PDF, HTML generation)
2. **Log streaming** — `app/api/testing_logs.py` (SSE log stream)
3. **SSE testing** — `app/api/testing_sse.py` (task events, deployment triggers)

Each blueprint:
- Has its own `before_request` checking `is_testing`
- Has its own schema file if needed
- Is registered in EI's `register_blueprints()` hook

The template provides only the `is_testing` check utility and the `RouteNotAvailableException`.

**Verification:** All testing endpoints still work. Schemas split accordingly.

---

## 10. Split testing schemas

**Why:** Follows from splitting testing.py (item 9).

**What to do:**

Split `app/schemas/testing.py` into:
- `app/schemas/testing_content.py` — `ContentImageQuerySchema`, `ContentHtmlQuerySchema`
- `app/schemas/testing_sse.py` — `DeploymentTriggerRequestSchema`, `DeploymentTriggerResponseSchema`, `TaskEventRequestSchema`, `TaskEventResponseSchema`
- `app/schemas/testing_common.py` — `TestErrorResponseSchema`, `TestResetResponseSchema` (if kept)

Or just inline small schemas in the blueprint files if they're only used once.

**Verification:** All testing endpoint validation still works.

---

## 11. Split conftest into infrastructure + app

**Why:** The template needs to own infrastructure fixtures (app factory, client, session, OIDC mocking) while the app needs to add domain fixtures. If conftest.py is template-owned, the app can't extend it.

**What to do:**

1. Create `tests/conftest_infrastructure.py` with all infrastructure fixtures:
   - `clear_prometheus_registry` (autouse)
   - `_build_test_settings()`
   - `test_settings`
   - `template_connection`
   - `app`
   - `client`, `runner`
   - `container`, `session`
   - OIDC fixtures (`mock_oidc_discovery`, `mock_jwks`, `generate_test_jwt`, `oidc_app`, `oidc_client`)

2. Simplify `tests/conftest.py` to:

```python
"""Test configuration."""

# Import all infrastructure fixtures
from tests.conftest_infrastructure import *  # noqa: F401, F403

# App-specific fixtures below:

@pytest.fixture
def sample_component(session):
    ...
```

3. Verify pytest discovers all fixtures correctly.

**Verification:** Run EI test suite. All fixtures resolve.

---

## 12. Delete `run-integration-test.sh`

**Why:** No longer used.

**What to do:** Delete `scripts/run-integration-test.sh`.

---

## Summary

| # | Refactoring | Files affected | Risk |
|---|-------------|---------------|------|
| 1 | Replace flask_log_request_id | utils/__init__.py, __init__.py, pyproject.toml | Low — drop-in replacement |
| 2 | Rename services | 3 service files + all imports | Low — mechanical rename |
| 3 | Extract OIDC hooks | api/__init__.py, new oidc_hooks.py | Low — move code |
| 4 | Create HealthService | new health_service.py, health.py, container.py, __init__.py | Medium — new abstraction |
| 5 | Create consts.py | new consts.py, spectree_config.py | Low — extract constants |
| 6 | Separate AppSettings | new app_config.py, config.py, container.py, services | Medium — restructure config |
| 7 | Switch CLI to Click | cli.py, startup.py, pyproject.toml | Medium — rewrite CLI |
| 8 | Remove reset from testing API | testing.py, testing_service.py, reset_lock.py | Medium — may affect Playwright |
| 9 | Split testing blueprints | testing.py -> 3 files | Low — move code |
| 10 | Split testing schemas | testing.py schemas -> 2-3 files | Low — move code |
| 11 | Split conftest | conftest.py -> conftest.py + conftest_infrastructure.py | Low — restructure |
| 12 | Delete run-integration-test.sh | scripts/ | None |

**Suggested execution order:** 1, 2, 5, 12 (quick wins), then 3, 4 (health/OIDC extraction), then 6, 7 (config/CLI restructure), then 8, 9, 10 (testing cleanup), then 11 (conftest split). Run the full test suite after each refactoring.
