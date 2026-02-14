# Template Change Workflow

## Overview

This document describes the workflow for making changes to the ModernAppTemplate. Because this is a Copier-based template project, changes follow a specific pattern:

1. Edit files in `template/` (never `test-app/` directly)
2. Regenerate `test-app/` from the template
3. Run the test suite against the regenerated app
4. Update the changelog for downstream app migration
5. Optionally sync changes to downstream apps

This workflow can be used for both attended (interactive) and unattended (autonomous) work.

## Key Constraints

**Critical Rules:**

- **Never edit `test-app/` directly** — it is generated output
- **Always regenerate after template changes** — test-app must reflect current template state
- **Maintain the changelog** — all changes must be documented for downstream apps
- **Tests live outside test-app** — in `/work/ModernAppTemplate/backend/tests/`

**Template File Types:**

| Extension | Processing |
|-----------|------------|
| `.jinja` | Processed by Copier, extension stripped, variables substituted |
| Other | Copied as-is to generated output |

## Workflow Steps

### 0. Establish Feature Directory

Before starting work, create a feature directory for planning documents:

```
docs/features/<FEATURE_NAME>/
```

Where `<FEATURE_NAME>` is a snake_case identifier (e.g., `metrics_redesign`, `s3_api_update`).

```bash
mkdir -p docs/features/<FEATURE_NAME>/
```

**Document Paths:**
- Change Brief: `docs/features/<FEATURE_NAME>/change_brief.md`
- Plan: `docs/features/<FEATURE_NAME>/plan.md`
- Plan Review: `docs/features/<FEATURE_NAME>/plan_review.md`
- Code Review: `docs/features/<FEATURE_NAME>/code_review.md`
- Execution Report: `docs/features/<FEATURE_NAME>/execution_report.md`

### 1. Write the Change Brief

Describe the work at a functional level. Examples:

- "Add S3 file metadata retrieval method to S3Service."
- "Refactor metrics so services own their own Prometheus counters."
- "Add CORS configuration to settings."

For bug fixes, include reproduction steps. If confidence is low that the brief adequately describes the change, clarify with the user before proceeding.

Write the brief to: `docs/features/<FEATURE_NAME>/change_brief.md`

### 2. Create a Plan

Use the `plan-writer` agent to create a detailed implementation plan.

**Before invoking the agent:**

1. Extract explicit requirements from the user's prompt
2. Build a User Requirements Checklist
3. Include the checklist in the agent prompt

```
Use the Task tool with the plan-writer agent to create a plan for the change
described in docs/features/<FEATURE_NAME>/change_brief.md.

This is a template project. The plan must account for:
- All code changes go in template/ directory (not test-app/)
- Jinja files (.jinja) vs plain Python files
- Regeneration of test-app after changes
- Test updates in /work/ModernAppTemplate/backend/tests/
- Changelog entry for downstream app migration

Write the plan to: docs/features/<FEATURE_NAME>/plan.md

**User Requirements Checklist** (include verbatim in section 1a of the plan):
- [ ] <requirement 1>
- [ ] <requirement 2>
- [ ] ...
```

### 3. Review the Plan

Use the `plan-reviewer` agent to validate the plan:

```
Use the Task tool with the plan-reviewer agent to review the plan at
docs/features/<FEATURE_NAME>/plan.md.

Pay special attention to:
- Template vs test-app file locations
- Jinja syntax correctness
- Test coverage requirements
- Changelog migration instructions

Write the review to: docs/features/<FEATURE_NAME>/plan_review.md
```

**Iteration Rule:** If the review suggests substantial changes, update the plan and re-review until it passes without major concerns.

### 4. Execute the Plan

#### 4.1 Code Implementation

Use the `code-writer` agent for significant changes:

```
Launch the code-writer agent with the plan at docs/features/<FEATURE_NAME>/plan.md.

CRITICAL REMINDERS for template projects:
- Edit files in template/ directory only
- Use proper Jinja syntax for .jinja files
- Do NOT edit test-app/ directly
```

For minor changes, you may implement directly without an agent.

#### 4.2 Regenerate test-app

After ANY template change, regenerate test-app:

```bash
cd /work/ModernAppTemplate/backend
rm -rf test-app
poetry run copier copy . test-app --trust \
  -d project_name=test-app \
  -d project_description="Test application" \
  -d author_name="Test Author" \
  -d author_email="test@example.com" \
  -d use_database=true \
  -d use_oidc=true \
  -d use_s3=true \
  -d use_sse=true \
  -d workspace_name=TestApp
cd test-app && echo "# Test App" > README.md && poetry install
```

#### 4.3 Verification Checkpoint

Run verification from the test-app directory:

```bash
cd /work/ModernAppTemplate/backend/test-app

# Run template test suite (primary validation)
poetry run pytest ../tests/ -v

# Run linting on generated code
poetry run ruff check ../template/ .

# Run type checking (if configured)
poetry run mypy . --ignore-missing-imports
```

**Checklist:**
- [ ] All template tests pass (currently ~140 tests)
- [ ] Ruff linting passes
- [ ] Type checking passes (if applicable)
- [ ] Review git diff for unexpected changes
- [ ] New tests added for new functionality

### 5. Requirements Verification

Verify all checklist items from the plan were implemented:

```
Use the Task tool with the Explore agent to verify implementation.

Read the User Requirements Checklist from section 1a of
docs/features/<FEATURE_NAME>/plan.md.

For EACH checklist item, find concrete evidence in the template/ directory
and tests/ directory that proves it was implemented.

Write the verification report to:
docs/features/<FEATURE_NAME>/requirements_verification.md
```

If any items fail, address gaps and re-verify.

### 6. Code Review

Use the `code-reviewer` agent:

```
Use the Task tool with the code-reviewer agent to review the changes.

Plan location: docs/features/<FEATURE_NAME>/plan.md
Review output: docs/features/<FEATURE_NAME>/code_review.md

Review unstaged changes in the template/ directory.

Pay attention to:
- Jinja syntax correctness in .jinja files
- Consistency with existing patterns
- Test coverage adequacy
- Proper error handling
```

Resolve ALL identified issues (BLOCKER, MAJOR, and MINOR) before proceeding.

### 7. Update Changelog

**Required for all changes.** Add an entry to `changelog.md`:

```markdown
## YYYY-MM-DD

### <Brief title of change>

**What changed:** <Description of what was changed and why>

**Migration steps:**
1. <Step 1 for downstream apps>
2. <Step 2>
...
```

The changelog is essential for downstream apps to update from the template. Include:

- What files were added/changed
- Any API changes (method signatures, parameters)
- Container/DI changes
- Required manual steps for apps updating from the template

### 8. Create Execution Report

Write a summary report to `docs/features/<FEATURE_NAME>/execution_report.md`:

```markdown
# Execution Report: <Feature Name>

## Status
DONE | DONE-WITH-CONDITIONS | INCOMPLETE | ABORTED

## Summary
<What was accomplished>

## Files Changed
- template/common/... — <description>
- tests/... — <description>

## Verification Results
- Template tests: X passed
- Ruff: <pass/fail>
- Mypy: <pass/fail>

## Changelog Entry
Added to changelog.md: Yes/No

## Outstanding Work
<Any remaining items or "None">

## Notes for Downstream Apps
<Any special considerations>
```

### 9. Tag the Release

After all template tests pass and the changelog is updated, create a new version tag:

```bash
# Find the latest tag
git tag --list --sort=-v:refname | head -1

# Create the next tag (bump by 0.1)
git tag v0.X
```

Tags are required for `copier update` to work — downstream apps reference the template version via the `_commit` field in `.copier-answers.yml`.

### 10. Update Downstream Apps

Use `copier update` to propagate changes to all downstream apps. **Never manually copy template-owned files** — this bypasses Copier's conflict resolution and `_skip_if_exists` protections.

```bash
cd /work/<ProjectName>/backend
poetry run copier update --trust --defaults
```

After the update:

1. **Fix new ruff findings** in app-owned files (copier only updates template-owned files):
   ```bash
   ruff check --fix --unsafe-fixes .
   ```
2. **Update `pyproject.toml`** if the template changed ruff version or config format (this file is `_skip_if_exists`, so copier won't touch it)
3. **Run the app's test suite** to verify nothing broke
4. **Check `ruff check .`** passes clean

Repeat for each downstream app (IoTSupport, ElectronicsInventory, etc.).

## Upstreaming a Fix from a Downstream App

When a bug is discovered and fixed in a downstream app (e.g., IoTSupport) and the fix is in template-owned code, it needs to be upstreamed to the template and then propagated to all other downstream apps.

### Steps

1. **Identify the fix**: Find the commit in the downstream app that fixes the issue. Verify it touches template-owned files (not app-specific code).

2. **Apply to the template**: Port the fix to the corresponding file(s) in `template/`. The downstream app may have app-specific imports or services — adapt as needed for the generic template.

3. **Regenerate and test**: Run `bash regen.sh`, then run both test suites:
   ```bash
   cd test-app
   python -m pytest ../tests/ -v      # Mother project tests
   python -m pytest tests/ -v          # Domain tests
   ruff check ../template/ .           # Lint check
   ```

4. **Update the changelog**: Add an entry in `changelog.md` describing the fix and migration steps.

5. **Commit and tag**: Commit the template changes and create a new version tag (bump by 0.1 from the latest tag).

6. **Update all downstream apps**: Run `copier update --trust --defaults` in each downstream project, fix any ruff findings, update `pyproject.toml` if needed, and run their test suites.

### Example

```
# 1. Check the fix in the downstream app
cd /work/IoTSupport/backend
git show HEAD -- app/api/auth.py

# 2. Apply to template
# Edit template/app/api/auth.py with the equivalent fix

# 3. Regenerate and test
cd /work/ModernAppTemplate/backend
bash regen.sh
cd test-app && python -m pytest ../tests/ -v && python -m pytest tests/ -v

# 4. Update changelog.md

# 5. Commit and tag
git add . && git commit -m "Fix auth/self endpoint"
git tag v0.X

# 6. Update downstream apps
cd /work/IoTSupport/backend && copier update --trust --defaults
cd /work/ElectronicsInventory/backend && copier update --trust --defaults
# Run tests in each...
```

## Quality Standards

Before considering work complete:

- [ ] All template tests pass (~140 tests)
- [ ] Ruff linting passes on template/ and test-app/
- [ ] test-app regenerated and functional
- [ ] Requirements verification passed (all checklist items confirmed)
- [ ] Code review completed with GO or GO-WITH-CONDITIONS
- [ ] All review issues resolved
- [ ] Changelog updated with migration instructions
- [ ] Execution report written
- [ ] No outstanding questions (or deferred to user with context)

## Related Documentation

- `CLAUDE.md` — Project instructions and critical rules
- `changelog.md` — Change history for downstream apps
- `copier.yml` — Template configuration and variables

## Example: Minor Bug Fix

```
1. Create feature directory:
   mkdir -p docs/features/fix_s3_error_handling/

2. Write change brief:
   "Fix S3Service to properly handle missing credentials error"

3. Create plan (may skip for trivial fixes):
   - Identify the bug location
   - Describe the fix
   - List tests to add/update

4. Implement in template/:
   Edit template/common/storage/s3_service.py

5. Regenerate test-app:
   rm -rf test-app && poetry run copier copy . test-app --trust ...

6. Run tests:
   cd test-app && poetry run pytest ../tests/ -v

7. Update changelog:
   Add entry describing the fix and migration (usually just "copy updated file")
```

## Example: New Feature

```
1. Create feature directory:
   mkdir -p docs/features/add_redis_cache/

2. Write detailed change brief

3. Use plan-writer agent to create comprehensive plan
   - New files needed
   - Container changes
   - Settings additions
   - Test requirements

4. Use plan-reviewer agent to validate

5. Use code-writer agent to implement

6. Regenerate and verify:
   - All tests pass
   - New feature works in test-app

7. Use code-reviewer agent to review

8. Update changelog with detailed migration steps:
   - New files to copy
   - Container.py changes
   - Settings.py additions
   - New dependencies

9. Create execution report
```
