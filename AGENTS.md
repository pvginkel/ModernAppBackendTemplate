# Backend Template Project Guidelines

This is a Copier-based backend template that generates self-contained Flask backend projects. Each generated project is plain Python with no runtime dependency on the template.

## Sandbox Environment

- This repository is bind-mounted into `/work/ModernAppTemplate/backend` inside a container.
- Git operations (staging, committing, tagging) work normally inside the container.
- The container includes poetry and the standard Python toolchain.

## Poetry Virtual Environments

Each project in the container has its own Poetry virtualenv. **Always use `poetry run`** to run commands — never bare `python` or `pytest`, as the system `python` may resolve to a different project's venv or to no venv at all.

```bash
# Correct — uses the project's own venv:
cd /work/IoTSupport/backend && poetry run pytest tests/ -v

# WRONG — may use a different project's venv or system Python:
cd /work/IoTSupport/backend && python -m pytest tests/ -v
```

### Projects and their venvs

| Project | Path | Notes |
|---------|------|-------|
| ModernAppTemplate | `/work/ModernAppTemplate/backend` | Template repo. `regen.sh` runs `poetry install` in test-app. |
| test-app | `/work/ModernAppTemplate/backend/test-app` | Generated from template. Has its own venv with all template deps. |
| IoTSupport | `/work/IoTSupport/backend` | Downstream app. Has its own venv. |
| ElectronicsInventory | `/work/ElectronicsInventory/backend` | Downstream app. Has its own venv. |

### Why this matters

- `pyproject.toml` is app-owned (`_skip_if_exists`) — `copier update` does NOT modify it
- When enabling new feature flags (e.g., `use_s3=true`), copier adds the code files but **not** the dependencies — you must manually add them to the downstream app's `pyproject.toml` and run `poetry lock && poetry install`
- Each venv is isolated. A dependency being available in one project does not mean it exists in another. Always verify with `poetry run python -c "import <module>"` in the target project.

## Project Structure

```
/work/ModernAppTemplate/backend/
├── template/           # Copier template source (Jinja2 files)
├── test-app/           # Generated test application (DO NOT edit directly)
├── test-app-domain/    # Hand-written domain files copied into test-app after generation
├── tests/              # Mother project test suite (infrastructure tests)
├── copier.yml          # Copier configuration and feature flags
├── pyproject.toml      # Dev dependencies (copier, pytest)
├── copier_approach.md  # Complete guide for template extraction (READ THIS FIRST)
├── ei_copier_refactoring.md  # EI refactorings needed before next extraction
└── findings.md         # Review findings from first iteration
```

## Key Documentation

Before making changes, read these documents:

- **`copier_approach.md`** — Architecture decisions, file ownership model, what worked/didn't, implementation sequence, full file inventory. This is the primary reference for template work.
- **`ei_copier_refactoring.md`** — Refactorings needed in EI before re-extracting the template.
- **`findings.md`** — Review findings and discussion from the first iteration.

## Critical Rules

### 1. Never Edit test-app Directly
`test-app/` is generated from the template. All changes go in `template/`, then regenerate.

### 2. test-app-domain Contains App-Specific Files
`test-app-domain/` has hand-written domain files (Item model, CRUD API, migration, tests) that get copied into `test-app/` after Copier generation. These files fill in the `_skip_if_exists` scaffolds with a real working domain.

### 3. Always Regenerate After Template Changes
After any change to `template/` or `copier.yml`, run the regeneration script:

```bash
cd /work/ModernAppTemplate/backend
bash regen.sh
```

This script removes old test-app, runs `copier copy`, copies domain files from `test-app-domain/`, copies `.env.test`, and runs `poetry install`.

### 4. Run Both Test Suites
```bash
cd /work/ModernAppTemplate/backend/test-app
poetry run pytest ../tests/ -v      # Mother project tests (infrastructure)
poetry run pytest tests/ -v          # Domain tests (Items CRUD)
```

### 5. SQLite for Testing
Tests use in-memory SQLite with the template cloning pattern (`sqlite3.Connection.backup()`). The `test_settings` fixture must call `settings.set_engine_options_override({})` because SQLite doesn't support pool options.

### 6. Minimize Jinja Usage
- Files ending in `.jinja` are processed by Copier (extension stripped)
- Prefer plain Python files with separate files per feature over Jinja conditionals
- Use `consts.py` (app-owned) for project name/description instead of Jinja
- Reserve `.jinja` for files with substantial conditional sections (config.py, __init__.py, cli.py)

## File Ownership

### Template-maintained (overwritten by `copier update`)
Infrastructure code. Developers should not edit these. See `copier_approach.md` for the full list.

### App-maintained (`_skip_if_exists` — generated once, never overwritten)
- `app/startup.py` — hook implementations (blueprints, error handlers, CLI commands)
- `app/services/container.py` — DI container with infrastructure + app providers
- `app/exceptions.py` — base + app-specific exceptions
- `app/consts.py` — project constants (API title, description)
- `app/app_config.py` — app-specific settings (separate from template Settings)
- `app/models/__init__.py` — model imports for Alembic
- `pyproject.toml` — dependencies (app manages after initial generation)
- `tests/conftest.py` — imports infrastructure fixtures, adds app fixtures
- `.env.example` — environment variable documentation

## Feature Flags

| Flag | Controls |
|------|----------|
| `use_database` | SQLAlchemy, Alembic, migrations, pool diagnostics, diagnostics service |
| `use_oidc` | OIDC authentication (BFF pattern with JWT cookies) |
| `use_s3` | S3 storage, CAS endpoints, image processing |
| `use_sse` | Server-Sent Events via SSE Gateway |

## Common Patterns

### Exception Handling
Base exceptions in `app/exceptions.py` (app-owned):
- `BusinessLogicException` — base class
- `RecordNotFoundException` — 404
- `ResourceConflictException` — 409
- `InvalidOperationException` — 409

Error handlers in `app/utils/flask_error_handlers.py` (template-owned) convert these to JSON responses with `correlationId`.

### Correlation ID
Every request gets a correlation ID from the `X-Request-ID` header (or auto-generated UUID). Access via `get_current_correlation_id()` from `app.utils`. All error responses include `correlationId`.

### Transaction Rollback
Set `g.needs_rollback = True` when an error occurs. The `teardown_request` handler checks this flag.

### Hook Contract
The app customizes behavior through hooks in `app/startup.py`:
- `create_container()` — builds the DI container
- `register_blueprints(api_bp, app)` — registers domain blueprints
- `register_error_handlers(app)` — registers app-specific error handlers
- `register_cli_commands(cli_app)` — registers Click CLI commands
- `post_migration_hook(app)` — runs after database migrations
- `load_test_data_hook(app)` — loads test fixtures

## Reference App

Template patterns are extracted from:
- `/work/ElectronicsInventory/backend/` — primary source

## Changelog

All template changes must be documented in `changelog.md`. Each entry includes:
1. Date
2. What changed and why
3. Migration steps for downstream apps

Apps track which template version they're on via `_commit` in `.copier-answers.yml`.

## Known Gotchas

### Pytest Conftest Discovery
Both `tests/conftest.py` (mother project) and `test-app/tests/conftest.py` (domain) share the module name `tests.conftest`. This means:

- **You cannot run both test suites in a single pytest invocation.** Doing so causes `ImportPathMismatchError` because pytest finds two files claiming to be `tests.conftest`.
- **Always run them separately:**
  ```bash
  cd test-app
  poetry run pytest ../tests/ -v      # Mother project (infrastructure)
  poetry run pytest tests/ -v          # Domain (Items CRUD)
  ```
- Both conftest files re-export from `tests.conftest_infrastructure` (the generated infrastructure fixtures in test-app). This works because tests run from inside `test-app/`, so `tests.conftest_infrastructure` resolves to `test-app/tests/conftest_infrastructure.py`.
- Do NOT duplicate fixtures in conftest files. Import from `conftest_infrastructure` and add domain-specific fixtures only.

## Commit Guidelines

1. Regenerate test-app and run full test suite
2. Stage both template changes and regenerated test-app
3. Commit with a descriptive message

## Updating Downstream Apps

When template changes need to propagate to a downstream app (e.g., EI's `backend-new/`), **always use `copier update`**. Never manually copy template-owned files into the target app — this bypasses Copier's conflict resolution, skips `_skip_if_exists` protections, and creates drift that's hard to track.

If `copier update` fails because `.copier-answers.yml` is not committed, commit it first. Never manually copy template-owned files — always use `copier update`.
