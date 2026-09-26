# Backend Template (Submodule)

This is the Copier-based backend template that generates self-contained Flask backend projects. Each generated project is plain Python with no runtime dependency on the template.

For shared documentation (architecture, workflows, sync process), see the **parent repo's `docs/`** directory.

## Sandbox Environment

- This repository is a submodule of `ModernAppTemplate`, mounted at `/work/ModernAppTemplate/backend` inside the container.
- Git operations (staging, committing, tagging) work normally.
- The container includes poetry and the standard Python toolchain.

## Poetry Virtual Environments

Each project in the container has its own Poetry virtualenv. **Always use `poetry run`** to run commands.

```bash
# Correct:
cd /work/ModernAppTemplate/backend && poetry run pytest tests/ -v

# WRONG:
cd /work/ModernAppTemplate/backend && python -m pytest tests/ -v
```

## Project Structure

```
backend/
├── copier.yml          # Copier configuration and feature flags
├── template/           # Copier template source (Jinja2 files)
├── test-app/           # Generated test application (DO NOT edit directly)
├── test-app-domain/    # Hand-written domain files copied into test-app after generation
├── tests/              # Mother project test suite (infrastructure tests)
├── pyproject.toml      # Dev dependencies (copier, pytest)
├── regen.sh            # Regeneration script
└── .env.test           # Test environment overrides
```

## Critical Rules

### 1. Never Edit test-app Directly
`test-app/` is generated from the template. All changes go in `template/`, then regenerate.

### 2. test-app-domain Contains App-Specific Files
`test-app-domain/` has hand-written domain files (Item model, CRUD API, migration, tests) that get copied into `test-app/` after Copier generation.

### 3. Always Regenerate After Template Changes
```bash
cd /work/ModernAppTemplate/backend
bash regen.sh
```

### 4. Run Both Test Suites
```bash
cd /work/ModernAppTemplate/backend/test-app
poetry run pytest ../tests/ -v      # Mother project tests (infrastructure)
poetry run pytest tests/ -v          # Domain tests (Items CRUD)
```

### 5. SQLite for Testing
Tests use in-memory SQLite with the template cloning pattern (`sqlite3.Connection.backup()`).

### 6. Minimize Jinja Usage
- Prefer plain Python files with separate files per feature over Jinja conditionals
- Use `consts.py` (app-owned) for project name/description instead of Jinja
- Reserve `.jinja` for files with substantial conditional sections

## File Ownership

### Template-maintained (overwritten by `copier update`)
Infrastructure code. See `docs/copier_approach.md` in the parent repo for the full list.

### App-maintained (`_skip_if_exists` — generated once, never overwritten)
- `app/startup.py` — hook implementations
- `app/services/container.py` — DI container
- `app/exceptions.py` — base + app-specific exceptions
- `app/consts.py` — project constants
- `app/app_config.py` — app-specific settings
- `app/models/__init__.py` — model imports for Alembic
- `pyproject.toml` — dependencies
- `tests/conftest.py` — test fixtures
- `.env.example` — environment documentation
- `Dockerfile`

## Feature Flags

| Flag | Controls |
|------|----------|
| `use_database` | SQLAlchemy, Alembic, migrations, pool diagnostics |
| `use_oidc` | OIDC authentication (BFF pattern with JWT cookies) |
| `use_s3` | S3 storage, CAS endpoints, image processing |
| `use_sse` | Server-Sent Events via SSE Gateway |

## Hook Contract

The app customizes behavior through hooks in `app/startup.py`:
- `create_container()` — builds the DI container
- `register_blueprints(api_bp, app)` — registers domain blueprints
- `register_root_blueprints(app)` — registers blueprints directly on Flask app
- `register_error_handlers(app)` — registers app-specific error handlers
- `register_cli_commands(cli_app)` — registers Click CLI commands
- `post_migration_hook(app)` — runs after database migrations
- `load_test_data_hook(app)` — loads test fixtures

## Known Gotchas

### Pytest Conftest Discovery
Both `tests/conftest.py` (mother project) and `test-app/tests/conftest.py` (domain) share the module name `tests.conftest`. Always run them separately:
```bash
cd test-app
poetry run pytest ../tests/ -v      # Mother project
poetry run pytest tests/ -v          # Domain
```

## Reference App

Template patterns are extracted from: `/work/ElectronicsInventory/backend/`
