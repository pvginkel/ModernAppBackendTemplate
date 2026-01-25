#!/bin/bash
# Re-apply the copier template to update common modules

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$PROJECT_DIR/.copier-answers.yml" ]; then
    echo "Error: $PROJECT_DIR/.copier-answers.yml not found" >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec poetry run copier copy --answers-file .copier-answers.yml --force ../../ModernAppTemplate/backend/template .
