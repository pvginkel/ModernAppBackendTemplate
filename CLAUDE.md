# Backend Template (Submodule)

This is the Copier-based backend template that generates self-contained Flask backend projects. Each generated project is plain Python with no runtime dependency on the template.

For shared documentation (architecture, workflows, sync process), see the **parent repo's `docs/`** directory.

## Sandbox Environment

- This repository is a submodule of `ModernAppTemplate`, mounted at `/work/ModernAppTemplate/backend` inside the container.
- Git operations (staging, committing, tagging) work normally.
- uv and the standard Python toolchain live in the `modern-app` tool container: prefix commands with `cexec modern-app`.

## uv Projects

This repo and `test-app/` are separate uv projects, each with its own `.venv`. **Always use `uv run`** to run commands.

```bash
# Correct:
cd /work/ModernAppTemplate/backend/test-app && uv run pytest tests/ -v

# WRONG:
cd /work/ModernAppTemplate/backend/test-app && python -m pytest tests/ -v
```

This repo's own project holds the pinned copier that `regen.sh` and every app's `copier update` run (`uv run --project /work/ModernAppTemplate/backend copier ...`).

## Project Structure

```
backend/
├── copier.yml          # Copier configuration and feature flags
├── template/           # Copier template source (Jinja2 files)
├── test-app/           # Generated test application (DO NOT edit directly)
├── test-app-domain/    # Hand-written domain files copied into test-app after generation
├── tests/              # Mother project test suite (infrastructure tests)
├── pyproject.toml      # Dev dependencies (copier, pinned), uv project
├── uv.lock
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
uv run pytest ../tests/ -v      # Mother project tests (infrastructure)
uv run pytest tests/ -v          # Domain tests (Items CRUD)
```

Both suites need PostgreSQL, and the mother suite needs two more things from outside this repo:

- **PostgreSQL on `localhost:5432`** — the server the suites create their databases on, user and password `postgres`. The KubeCoder environment provides it as the `postgres` sidecar; `TEST_DATABASE_ADMIN_URL` points the suites elsewhere.
- **S3 on `localhost:9000`** — `.env.test` points there with `s3storage` credentials. The KubeCoder environment provides it as the `s3storage` service, a RustFS sidecar (see the parent repo's `.kubecoder/config.yaml`).
- **The SSE Gateway** — the `ssegateway` npm package, resolved from the frontend template's `test-app/node_modules` (`../frontend/test-app`). Run the frontend `regen.sh` once first, or set `SSE_GATEWAY_NODE_DIR` to any directory whose `node_modules` holds it.

### 5. PostgreSQL for Testing
The suites run on PostgreSQL; nothing in pytest uses SQLite. `template/tests/conftest_infrastructure.py.jinja` builds one template database per run with the Alembic revisions, in production's locale (ICU `und`, `C`), and clones it:

- **Rollback tier (default):** a session-scoped `app` on `<run>_main`. Each test's `session` runs on savepoints inside a transaction that is rolled back at teardown; `container` and `client` hand that session to services and requests. `commit()` and `rollback()` are real, only `close()` is a no-op.
- **`committed_app`:** an app on a clone of its own, with real commits, for anything a second connection must see.

The autouse `session_app_isolation` gives each test the session app's DI wiring back and undoes provider overrides a test leaves behind. What services keep in memory lives for the run, so a test that changes such state must undo it (`monkeypatch` does).

The mother suite runs on the same fixtures: `tests/conftest_infrastructure.py` is a symlink to `test-app/tests/conftest_infrastructure.py`, so it needs a regenerated `test-app/`, and `tests/conftest.py` adds only the SSE integration fixtures.

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
- `README.md`, `.dockerignore`, `Dockerfile` — generated once
- `app/models/__init__.py` — model imports for Alembic
- `pyproject.toml` — dependencies
- `tests/conftest.py` — test fixtures
- `tests/test_smoke.py` — liveness smoke test, so a fresh app has a test to collect
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
uv run pytest ../tests/ -v      # Mother project
uv run pytest tests/ -v          # Domain
```

## Reference App

Template patterns are extracted from: `/work/ElectronicsInventory/backend/`
