#!/usr/bin/env bash
# Regenerate the test-apps from the template and copy domain files.
#
# Two test-apps:
#   test-app             every flag on (use_database, use_s3, use_oidc,
#                        use_sse), with the Items domain from test-app-domain.
#                        Tracked; the mother suite runs on its fixtures, and
#                        the root test-app copies it.
#   test-app-no-storage  use_database=false and use_s3=false, as DHCPApp,
#                        ZigbeeControl and FieldnotesApp run; use_oidc and
#                        use_sse on, as all three have them. A fresh render with
#                        no domain (test-app-domain needs the database) and no
#                        .env.test (those apps have none), so its suite is the
#                        template's own and runs from the environment alone, as
#                        in CI. The root's test-app-no-storage copies it.
#                        Gitignored: tracking it would repeat test-app's regen
#                        churn on every template commit.
#
# Usage:
#   cd /work/ModernAppTemplate/backend
#   bash regen.sh                        # both test-apps
#   bash regen.sh test-app-no-storage    # only the named ones
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# The sidecar's uv cache and /work are different mounts, so hardlinks fail.
export UV_LINK_MODE=copy

# regen <dir> <use_database> <use_s3>
regen() {
  local app="$1" database="$2" s3="$3"

  echo "==> [$app] Removing old test-app..."
  rm -rf "$app"

  echo "==> [$app] Running copier copy..."
  uv run --locked copier copy . "$app" --trust --defaults --vcs-ref HEAD \
    -d project_name=test-app \
    -d project_description="Test application" \
    -d author_name="Test Author" \
    -d author_email="test@example.com" \
    -d backend_port=5000 \
    -d use_database="$database" \
    -d use_oidc=true \
    -d use_s3="$s3" \
    -d use_sse=true

  if [ "$database" = true ]; then
    echo "==> [$app] Copying domain files from test-app-domain..."
    cp test-app-domain/app/startup.py "$app/app/startup.py"
    cp test-app-domain/app/services/container.py "$app/app/services/container.py"
    cp test-app-domain/app/exceptions.py "$app/app/exceptions.py"
    cp test-app-domain/app/consts.py "$app/app/consts.py"
    cp test-app-domain/app/app_config.py "$app/app/app_config.py"
    cp -r test-app-domain/app/models/* "$app/app/models/"
    cp test-app-domain/app/schemas/item_schema.py "$app/app/schemas/"
    cp test-app-domain/app/services/item_service.py "$app/app/services/"
    cp test-app-domain/app/api/items.py "$app/app/api/"
    cp -r test-app-domain/tests/* "$app/tests/"
    mkdir -p "$app/alembic/versions"
    cp test-app-domain/alembic/versions/001_create_items.py "$app/alembic/versions/"

    echo "==> [$app] Copying .env.test..."
    cp .env.test "$app/.env.test"
  fi

  echo "==> [$app] Installing dependencies..."
  (cd "$app" && uv sync -q)
}

apps=("$@")
if [ ${#apps[@]} -eq 0 ]; then
  apps=(test-app test-app-no-storage)
fi

for app in "${apps[@]}"; do
  case "$app" in
    test-app) regen test-app true true ;;
    test-app-no-storage) regen test-app-no-storage false false ;;
    *) echo "Unknown test-app: $app (expected test-app or test-app-no-storage)" >&2; exit 2 ;;
  esac
done

echo ""
echo "Done. Run tests in test-app with:"
echo "  uv run pytest ../tests/ -v      # Mother project tests (infrastructure)"
echo "  uv run pytest tests/ -v          # Domain tests (Items CRUD)"
echo "and in test-app-no-storage with:"
echo "  uv run pytest tests/ -v          # The template's own tests"
