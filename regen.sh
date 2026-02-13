#!/usr/bin/env bash
# Regenerate test-app from the template and copy domain files.
#
# Usage:
#   cd /work/ModernAppTemplate/backend
#   bash regen.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Removing old test-app..."
rm -rf test-app

echo "==> Running copier copy..."
poetry run copier copy . test-app --trust --defaults \
  -d project_name=test-app \
  -d project_description="Test application" \
  -d author_name="Test Author" \
  -d author_email="test@example.com" \
  -d repo_url="https://github.com/test/test-app.git" \
  -d image_name="registry:5000/test-app" \
  -d backend_port=5000 \
  -d use_database=true \
  -d use_oidc=true \
  -d use_s3=true \
  -d use_sse=true

echo "==> Copying domain files from test-app-domain..."
cp test-app-domain/app/startup.py test-app/app/startup.py
cp test-app-domain/app/services/container.py test-app/app/services/container.py
cp test-app-domain/app/exceptions.py test-app/app/exceptions.py
cp test-app-domain/app/consts.py test-app/app/consts.py
cp test-app-domain/app/app_config.py test-app/app/app_config.py
cp -r test-app-domain/app/models/* test-app/app/models/
cp test-app-domain/app/schemas/item_schema.py test-app/app/schemas/
cp test-app-domain/app/services/item_service.py test-app/app/services/
cp test-app-domain/app/api/items.py test-app/app/api/
cp -r test-app-domain/tests/* test-app/tests/
mkdir -p test-app/alembic/versions
cp test-app-domain/alembic/versions/001_create_items.py test-app/alembic/versions/
echo "# Test App" > test-app/README.md

echo "==> Copying .env.test..."
cp .env.test test-app/.env.test

echo "==> Installing dependencies..."
cd test-app && poetry install -q

echo ""
echo "Done. Run tests with:"
echo "  cd test-app"
echo "  python -m pytest ../tests/ -v      # Mother project tests (infrastructure)"
echo "  python -m pytest tests/ -v          # Domain tests (Items CRUD)"
