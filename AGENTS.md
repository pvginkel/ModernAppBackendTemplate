# Backend Template Project Guidelines

This is a Copier-based backend template that consolidates common patterns from multiple Flask applications.

## Sandbox Environment

- This repository is bind-mounted into `/work/backend` inside a container.
- The `.git` directory is mapped read-only, so staging or committing must happen outside the sandbox.
- When you need to commit, provide the user with the exact commands to run.
- The container includes poetry and the standard Python toolchain.

## Project Structure

```
/work/backend/
├── template/           # Copier template source (Jinja2 files)
├── test-app/           # Generated test application (DO NOT edit directly)
├── tests/              # Test suite for template validation
├── copier.yml          # Copier configuration
└── pyproject.toml      # Dev dependencies (copier, pytest)
```

## Critical Rules

### 1. Never Edit test-app Directly
The `test-app/` directory is generated from the template. All changes must be made in `template/` and then regenerated:

```bash
cd /work/backend
poetry run copier copy . test-app --force
```

### 2. Always Regenerate After Template Changes
After any change to files in `template/`, regenerate test-app and verify tests pass:

```bash
cd /work/backend
poetry run copier copy . test-app --force
cd test-app && poetry install
poetry run pytest ../tests/ -v
```

### 3. Tests Live Outside test-app
Tests are in `/work/backend/tests/`, not inside test-app. This allows testing the generated output without the tests being part of the template itself.

Run tests using test-app's poetry environment:
```bash
cd /work/backend/test-app
poetry run pytest ../tests/ -v
```

### 4. SQLite for Testing
Tests use in-memory SQLite. The `test_settings` fixture must call `settings.set_engine_options_override({})` because SQLite doesn't support pool options.

### 5. Template Syntax
- Files ending in `.jinja` are processed by Copier and have the extension stripped
- Use `{{ variable }}` for Copier variables (from copier.yml answers)
- Use `{% if condition %}` for conditional sections
- Don't over-engineer Jinja templates - only use conditionals where truly needed

## Common Patterns

### Adding a New Common Module
1. Create the module in `template/common/`
2. If it needs DI, add provider to `template/common/core/container.py.jinja`
3. Regenerate test-app
4. Add tests to `/work/backend/tests/`
5. Verify all tests pass

### Exception Handling
All exceptions are in `common/core/errors.py`:
- `BusinessLogicException` - base class
- `RecordNotFoundException` - 404
- `ResourceConflictException` - 409
- `InvalidOperationException` - 409

Use `@handle_api_errors` decorator on API endpoints.

### Transaction Rollback
Set `db_session.info["needs_rollback"] = True` when an error occurs. The teardown handler checks this flag.

### Correlation ID
Every request gets a correlation ID (from `X-Request-ID` header or generated). Access via `get_request_id()`. All error responses include `request_id`.

## Reference Apps

These apps use the patterns consolidated in this template:
- `/work/ElectronicsInventory/backend/` - Most comprehensive, has AI/task features
- `/work/IoTSupport/backend/` - OIDC auth, MQTT
- `/work/ZigbeeControl/backend/` - Simplest, good reference
- `/work/DHCPApp/backend/` - Basic CRUD

## Commit Guidelines

When template changes are ready to commit:
1. Regenerate test-app first
2. Run full test suite
3. Provide the user with git commands to run outside the container:
   - Stage both template changes and regenerated test-app
   - Use descriptive commit messages explaining the template change
