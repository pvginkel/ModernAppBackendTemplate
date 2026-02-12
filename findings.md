# Template Review Findings

## Verbatim copies from EI

**Not fully verbatim.** The background agents were instructed to copy "as-is" and verified with diff, but there are some known deviations:
- `app/utils/__init__.py` — completely rewritten to replace `flask_log_request_id` with custom correlation ID code (Flask 3.x incompatibility fix)
- `testing_service.py` — made into `.jinja` with conditional database sections
- `__init__.py` (app factory) — heavily reworked with Jinja conditionals
- `config.py` — restructured with Jinja feature flag sections
- `cli.py` — restructured with Jinja conditionals

For the pure-copy files (services, utils, schemas) — the agents reported verbatim copies verified by diff. I did not independently re-verify every file after the agents ran. **I can do a batch diff against EI if you want certainty.**

---

## Pip package?

No, I don't think so. The explicit design goal was "no runtime dependency on the template." Making a pip package would reintroduce the coupling problem. The template files are meant to be fully owned by the generated project. `copier update` with three-way merge is the update mechanism.

There's a philosophical case for extracting truly stable, zero-configuration utilities (lifecycle coordinator, error handler builder, correlation ID) into a shared package. But the cost is version pinning headaches, and the benefit is small since these files rarely change. I'd recommend keeping things as-is.

---

## `api/__init__.py.jinja` — OIDC should be extracted

**Agreed.** The `before_request` auth hook + `after_request` cookie refresh + auth_bp registration is ~85 lines of OIDC-specific code mixed into the API init. This should be a separate file like `app/api/oidc_hooks.py` (excluded when `use_oidc=false`) that exports a single `register_oidc_hooks(api_bp)` function. The `__init__.py` just calls it conditionally:

```python
{% if use_oidc %}
from app.api.oidc_hooks import register_oidc_hooks
register_oidc_hooks(api_bp)
{% endif %}
```

This keeps `__init__.py` clean and makes the OIDC stuff self-contained.

---

## `health.py.jinja` — callback-based health checks

**Agreed in principle.** The current `readyz` hardcodes database checks. A callback registry would let features register their own checks:

```python
# In create_app or container setup:
health_registry.register("database", check_db_health)
health_registry.register("s3", check_s3_health)

# readyz just iterates:
checks = health_registry.run_all()
```

**Question:** How far do you want to take this? A simple list of `Callable[[], dict]` callbacks on the container/app is straightforward. A full plugin system with priorities and timeouts is probably overkill. My instinct says a simple `HealthCheckRegistry` class with a `register(name, callback)` method and a `run_all()` method that aggregates results.

---

## `testing.py.jinja` — split into separate blueprints

**Agreed.** This file mixes several unrelated concerns:
1. **Content fixtures** (image/pdf/html) — always needed
2. **Database reset** — `use_database` only (and you want to remove it)
3. **Log streaming** — `use_sse` only
4. **SSE testing** (task events, deployment triggers) — `use_sse` only

Each should be its own blueprint in its own file, conditionally included. The `check_testing_mode` guard can be a shared utility or each blueprint registers it independently.

---

## `models/__init__.py.jinja` — why is it Jinja?

**It shouldn't be.** Looking at the content, it's just a scaffold comment with no Jinja syntax. It became `.jinja` by mistake. Should be plain `__init__.py`, excluded entirely when `use_database=false` (which it already is via the `app/models/` exclusion rule).

---

## `schemas/testing.py` — not templated

**Correct observation.** It contains schemas for both always-present features (content fixtures) and SSE-only features (deployment triggers, task events). If we split `testing.py.jinja` into separate blueprints, the schemas should split too — each blueprint gets its own schema file.

---

## Renames

All agreed, straightforward:
- `ConnectionManager` -> `SSEConnectionManager`
- `ImageService` -> `CasImageService`
- `VersionService` -> `FrontendVersionService`

These are purely template-internal names. We rename the files and classes, update all imports. No feature-flag implications.

---

## `testing_service.py.jinja` — split into multiple parts

**Agreed.** Current responsibilities:
1. Database reset (you're removing this)
2. Fake image generation
3. PDF fixture loading
4. HTML fixture rendering
5. Reset lock management

Without database reset, what remains is content fixture generation — images, PDFs, HTML. These could stay in a single `ContentFixtureService` or be split further. **Question:** Do you want the content fixtures to remain as a service, or should each testing blueprint just have its own inline logic (they're small functions)?

---

## Removing database reset functionality

Understood. This removes:
- `reset_database()` from testing_service
- `/api/testing/reset` endpoint
- `TestResetResponseSchema`
- `ResetLock` dependency (still needed? probably not if reset is the only user)
- The `post_migration_hook` and `load_test_data_hook` from startup.py

**Question:** Do you also want to remove `post_migration_hook` and `load_test_data_hook` from the startup.py contract? They're called by the CLI's `upgrade-db` and `load-test-data` commands too. If you keep the CLI commands, the hooks stay. If you remove the CLI commands that call them, the hooks go too.

---

## `spectree_config.py.jinja` — move params to config

**Agreed.** Add `api_title` and `api_description` to `Settings` (with defaults derived from `project_name` and `project_description` at generation time). Then `spectree_config.py` becomes plain Python reading from the container's config. No more `.jinja`.

**Question:** Should `api_title`/`api_description` be environment variables, or just hardcoded defaults in the generated `config.py`? I'd lean toward hardcoded defaults since API metadata rarely changes per-environment.

---

## `__init__.py.jinja` — confirm unconditionally overwritten

**Confirmed.** `app/__init__.py` is NOT in `_skip_if_exists`. It's template-maintained and will be overwritten on `copier update`. The app never needs to edit it — all customization goes through the startup.py hooks.

---

## CLI rename to `cli`

Straightforward. Change `{{ project_name }}-cli = "app.cli:main"` to `cli = "app.cli:main"` in pyproject.toml.jinja.

---

## CLI extensibility — how does the app add commands?

**This is a real problem.** Currently `cli.py.jinja` is template-owned (overwritten on update), but it hardcodes all available commands. The app can't add domain-specific CLI commands without editing this file.

**Proposed solution:** Same hook pattern as startup.py. Add a `register_cli_commands(subparsers)` hook:

```python
# In cli.py (template-owned):
from app.startup import register_cli_commands
register_cli_commands(subparsers)

# In startup.py (app-owned):
def register_cli_commands(subparsers):
    from app.cli_commands.import_data import register
    register(subparsers)
```

For the database commands specifically — yes, they should move into a separate module (e.g., `app/cli/database.py`) that the template's cli.py conditionally imports. The startup hook then lets the app add its own commands alongside.

**Question:** Do you want the CLI to use `argparse` subparsers (current) or switch to Click (which Flask already bundles)? Click integrates better with Flask's CLI system and is more idiomatic.

---

## `config.py.jinja` and `conftest.py.jinja` — app extensibility

Same fundamental problem as CLI. These are template-owned but apps need to add their own settings and fixtures.

**For config.py:** The app can't add settings fields to a template-owned Pydantic model. Options:
1. **App-specific settings class** — the app defines `AppSettings(Settings)` that extends the template's `Settings`. But then the template's `Settings.load()` returns the wrong type.
2. **Settings hook** — the app registers extra fields at load time. Ugly.
3. **Separate app config** — the app has its own `app_config.py` with its own Pydantic model loaded from env vars, completely independent of the template's Settings. The container holds both.
4. **Make config.py `_skip_if_exists`** — app owns it entirely. Template updates to config require manual migration.

I think option 3 is cleanest — the template's `Settings` handles infrastructure, the app has its own `AppSettings` for domain config. No collision.

**For conftest.py:** Similar — the template conftest provides infrastructure fixtures (app, client, session, OIDC mocking). The app's tests add domain fixtures in their own conftest files or `conftest.py` in subdirectories. The template conftest being overwritten is fine as long as the app doesn't edit it. **Question:** Is the *template's* `tests/conftest.py.jinja` actually the generated app's `tests/conftest.py`? If so, apps will definitely want to edit it. Maybe split into `tests/conftest_infrastructure.py` (template-owned) imported by `tests/conftest.py` (app-owned, `_skip_if_exists`)?

---

## `exceptions.py.jinja` and `startup.py.jinja` — why templated?

**They shouldn't need Jinja.** Looking at the content:
- `exceptions.py` — no Jinja syntax at all. Pure Python. The `.jinja` suffix is wrong.
- `startup.py` — also no Jinja syntax. Pure Python scaffold with empty hooks.

Both are in `_skip_if_exists` so they're generated once. The `.jinja` extension means Copier processes them (stripping `.jinja`), but since there's no template syntax, they'd be identical as plain files. Should be renamed to drop `.jinja`.

---

## `fake-pdf.pdf` and testing_service — app-specific

**Agreed.** If we remove database reset and split the testing endpoints, what remains in the testing *service* is content fixture generation (images, PDFs, HTML). These are useful for Playwright tests but are arguably app-specific.

**Question:** Do you want to keep content fixture endpoints in the template at all? They're useful for any app that runs Playwright tests. If yes, a slimmed-down `ContentFixtureService` with just `create_fake_image()` could stay in the template, and the app adds PDF/HTML fixtures if needed. Or we could keep all three content types as template features since they're generic enough.

---

## Scripts folder is empty

**Yes, this is an oversight.** The EI scripts folder has 10 shell scripts. Most should be templated and copied:
- `run.sh`, `dev-server.sh`, `testing-server.sh` — start the app in various modes
- `build.sh`, `push.sh` — Docker build/push
- `args.sh` — shared variables (needs templating for project name)
- `dev-sse-gateway.sh` — SSE-specific (conditional)
- `initialize-sqlite-database.sh` — database-specific (conditional)
- `run-integration-test.sh`, `stop.sh` — general purpose

---

## `Jenkinsfile.jinja` — parameterize properly

**Agreed.** The current template uses `workspace_name` for the GitHub org and `project_name` for the repo, which is wrong — the repo URL structure is independent. Should add explicit `repo_url` and `image_name` parameters:

```yaml
repo_url:
  type: str
  help: "Git repository URL"

image_name:
  type: str
  help: "Docker image name (e.g., registry:5000/my-app)"
  default: "registry:5000/{{ project_name }}"
```

**Agree to remove `workspace_name`** — it conflates too many things. `repo_url` and `image_name` are the actual independent parameters.

---

## `pyproject.toml.jinja` — app needs to add dependencies

This is inherently conflicting: it's template-owned (to manage infrastructure deps) but apps need to add their own deps. Options:

1. **`_skip_if_exists`** — app owns it entirely. Template dep updates require manual migration from changelog. This is what most Copier templates do.
2. **Keep template-owned** — apps use `copier update` three-way merge. Poetry deps merge well in practice because each line is independent.
3. **Split** — a `pyproject.template.toml` for infrastructure deps that gets merged into `pyproject.toml` at generation time. Overly complex.

**I'd recommend option 1** (`_skip_if_exists`). The initial generation gives you the right deps for your feature flags. After that, the app manages its own pyproject.toml. Template changelog documents dep changes for manual updates. This matches how every other Copier/cookiecutter template works.

---

## Port not parameterized in `run.py` and `Dockerfile.jinja`

`run.py` reads `PORT` from env (default 5000). `Dockerfile.jinja` hardcodes `EXPOSE 5000`. Should add a `backend_port` template variable (default 5000) and use it in both places:
- `Dockerfile.jinja`: `EXPOSE {{ backend_port }}`
- `run.py` could stay reading from env since it's already flexible, or the default could be templated

---

## test-app-domain purpose

**Yes, exactly.** `test-app-domain/` contains hand-written domain files (Item model, CRUD API, migration, tests) that get copied into the generated `test-app/` after Copier runs. This gives us a real working app to test against. The flow is:

1. `copier copy` generates template scaffolding into `test-app/`
2. Domain files from `test-app-domain/` overwrite the scaffolds (startup.py, container.py, etc.)
3. Now `test-app/` is a complete working app with a real domain

**Question:** Would you prefer these domain files live somewhere else, or be structured differently? They could also be a separate Copier layer or just checked into `test-app/` directly with the scaffold files in `_skip_if_exists` handling the coexistence.

---

## More tests needed

**Agreed.** The current 19 mother project tests only cover Phase 1 infrastructure. Missing from the plan's Phase 2.5:
- `test_auth.py` — OIDC flow, `@public` decorator, `@allow_roles`, token refresh
- `test_oidc.py` — OIDC client service, endpoint discovery, token exchange
- `test_s3.py` — S3 operations, CAS URL generation, bucket management (needs S3 mocking or `require_s3` fixture)
- `test_sse_callback.py` — SSE Gateway callback handling
- `test_connection_manager.py` — Connection tracking, event delivery
- `test_transaction_rollback.py` — Session rollback on error
- `test_cli.py` — CLI command tests

The S3 tests specifically would need to either mock boto3 or use the `require_s3` fixture to skip when MinIO isn't available.

---

## Open questions

1. **Health check registry** — simple callback list, or do you want something more structured?
2. **Database reset removal scope** — also remove `post_migration_hook`/`load_test_data_hook` from startup.py, or keep them for CLI use?
3. **CLI framework** — stay with argparse or switch to Click?
4. **App settings** — option 3 (separate AppSettings class) sound right?
5. **Template conftest** — split into infrastructure (template-owned) + app conftest (`_skip_if_exists`)?
6. **Content fixtures** — keep in template or make app-specific?
7. **pyproject.toml** — `_skip_if_exists` (option 1)?
8. **Verbatim verification** — want me to run a batch diff of all pure-copy files against EI?
