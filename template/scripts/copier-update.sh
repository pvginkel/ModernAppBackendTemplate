#!/bin/bash
# Re-apply the copier template to update common modules

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$PROJECT_DIR/.copier-answers.yml" ]; then
    echo "Error: $PROJECT_DIR/.copier-answers.yml not found" >&2
    exit 1
fi

# Extract _src_path from answers file
SRC_PATH=$(grep '^_src_path:' "$PROJECT_DIR/.copier-answers.yml" | sed 's/^_src_path: *//')

if [ -z "$SRC_PATH" ]; then
    echo "Error: _src_path not found in .copier-answers.yml" >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec poetry run copier copy \
    --answers-file .copier-answers.yml \
    --trust \
    --defaults \
    --overwrite \
    --skip app/__init__.py \
    --skip app/config.py \
    --skip app/container.py \
    --skip app/api/__init__.py \
    --skip app/models/__init__.py \
    --skip tests/conftest.py \
    --skip tests/test_health.py \
    --skip scripts/args.sh \
    --skip Jenkinsfile \
    "$SRC_PATH" .
