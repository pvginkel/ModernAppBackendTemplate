# Backend Template Project Guidelines

This is a Copier-based backend template that consolidates common patterns from multiple Flask applications.

## Sandbox Environment

- This repository is bind-mounted into `/work/ModernAppTemplate/backend` inside a container.
- The `.git` directory is mapped read-only, so staging or committing must happen outside the sandbox.
- When you need to commit, provide the user with the exact commands to run.
- The container includes poetry and the standard Python toolchain.

## Project Structure

```
/work/ModernAppTemplate/backend/
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
cd /work/ModernAppTemplate/backend
poetry run copier copy . test-app --force
```

### 2. Always Regenerate After Template Changes
After any change to files in `template/`, regenerate test-app and verify tests pass:

```bash
cd /work/ModernAppTemplate/backend
poetry run copier copy . test-app --force
cd test-app && poetry install
poetry run pytest ../tests/ -v
```

### 3. Tests Live Outside test-app
Tests are in `/work/ModernAppTemplate/backend/tests/`, not inside test-app. This allows testing the generated output without the tests being part of the template itself.

Run tests using test-app's poetry environment:
```bash
cd /work/ModernAppTemplate/backend/test-app
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
4. Add tests to `/work/ModernAppTemplate/backend/tests/`
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

## Development Workflow

When developing common modules, follow this workflow to keep template and apps in sync:

### 1. Make Changes in the Right Place

**If fixing a bug or adding a feature to common modules:**
- Edit the template files in `template/common/`
- For non-Jinja files (pure Python), these can be directly synced to apps

**If the change is app-specific:**
- Edit only in the app's directory (e.g., `/work/ZigbeeControl/backend/app/`)

### 2. Regenerate test-app

After any template change:
```bash
cd /work/ModernAppTemplate/backend
rm -rf test-app
copier copy template test-app --trust \
  -d project_name=test-app \
  -d project_description="Test application" \
  -d author_name="Test Author" \
  -d author_email="test@example.com" \
  -d use_database=true \
  -d use_oidc=true \
  -d use_s3=true \
  -d use_sse=true
cd test-app && echo "# Test App" > README.md && poetry install
```

### 3. Run Template Tests

```bash
cd /work/ModernAppTemplate/backend/test-app
python -m pytest ../tests/ -v      # Template test suite (107 tests)
python -m pytest tests/ -v          # Generated app tests
```

### 4. Sync to Real Apps (if applicable)

For non-Jinja common modules, sync changes to apps like ZigbeeControl:
```bash
# Compare files
diff template/common/auth/oidc.py /work/ZigbeeControl/backend/common/auth/oidc.py

# Copy if needed (for pure Python files only)
cp template/common/auth/oidc.py /work/ZigbeeControl/backend/common/auth/oidc.py
```

### 5. Run App Tests

```bash
cd /work/ZigbeeControl/backend
python -m pytest tests/ -v
```

### 6. Verify All Test Suites Pass

Before considering work complete, all three must pass:
- [ ] Template tests (`/work/ModernAppTemplate/backend/tests/`) - 107 tests
- [ ] Generated test-app tests (`/work/ModernAppTemplate/backend/test-app/tests/`) - 2 tests
- [ ] Real app tests (e.g., `/work/ZigbeeControl/backend/tests/`) - 31 tests

### Quick Reference

| Change Type | Edit Location | Then Do |
|-------------|---------------|---------|
| Common module (pure Python) | `template/common/` | Regenerate test-app, sync to apps, run all tests |
| Common module (Jinja) | `template/common/*.jinja` | Regenerate test-app, manually update apps, run all tests |
| App-specific code | App's `app/` directory | Run app tests only |
| Test fixtures | `template/tests/conftest.py.jinja` | Regenerate test-app, run all tests |

## Reference Apps

These apps use the patterns consolidated in this template:
- `/work/ElectronicsInventory/backend/` - Most comprehensive, has AI/task features
- `/work/IoTSupport/backend/` - OIDC auth, MQTT
- `/work/ZigbeeControl/backend/` - Simplest, good reference
- `/work/DHCPApp/backend/` - Basic CRUD

## Version Tracking in Apps

When an app is updated from the template, the `_commit` field in `.copier-answers.yml` must be manually updated to track which template version was used.

**After syncing template changes to an app:**
1. Get the current HEAD commit hash of the template repository:
   ```bash
   cd /work/ModernAppTemplate/backend
   git rev-parse HEAD
   ```
2. Update the app's `.copier-answers.yml`:
   ```yaml
   _commit: <commit-hash-from-step-1>
   ```

This allows tracking which template version an app is based on, making future updates easier to reason about.

**Note:** Always commit template changes first before recording the commit hash in apps, otherwise the hash will reference uncommitted work.

## Changelog

All template changes must be documented in `changelog.md` at the repository root. This changelog is essential for tracking what changed and helping apps update from the template.

### Maintaining the Changelog

When making changes to the template, add an entry to `changelog.md` with:

1. **Date** - When the change was made
2. **What changed and why** - A clear description of the change and its purpose
3. **Migration instructions** - Specific steps apps need to take to adopt the change

**Entry format:**
```markdown
## YYYY-MM-DD

### <Brief title of change>

**What changed:** <Description of what was changed and why>

**Migration steps:**
1. <Step 1>
2. <Step 2>
...
```

### Using the Changelog When Updating an App

When updating an app from the template:

1. **Find the app's current version:**
   ```bash
   grep "_commit:" /path/to/app/.copier-answers.yml
   ```

2. **View changelog entries added since that version:**
   ```bash
   cd /work/ModernAppTemplate/backend
   git diff <template-commit-hash-from-app>..HEAD -- changelog.md
   ```
   This shows exactly which changelog entries were added since the app's last update.

3. **Follow the migration steps** in each new changelog entry to update the app

4. **Update the app's `_commit`** to the new HEAD hash after completing all migrations

## Commit Guidelines

When template changes are ready to commit:
1. Regenerate test-app first
2. Run full test suite
3. Provide the user with git commands to run outside the container:
   - Stage both template changes and regenerated test-app
   - Use descriptive commit messages explaining the template change
