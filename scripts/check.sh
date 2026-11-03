#!/usr/bin/env bash
#
# Project gate: lint + format + types + tests with coverage.
# Usage: bash scripts/check.sh
# Exits 0 only when every step passes.  Works on plain Linux (and Windows via
# Git Bash) with python3 installed; no network access is required.

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found" >&2
  exit 1
fi

run() {
  local module="$1"
  shift
  if "$PYTHON" -c "import $module" >/dev/null 2>&1; then
    "$PYTHON" -m "$module" "$@"
  elif command -v "$module" >/dev/null 2>&1; then
    "$module" "$@"
  else
    echo "error: $module is not installed (pip install -e '.[dev]')" >&2
    exit 1
  fi
}

echo "==> lint (ruff check)"
run ruff check .

echo "==> format (ruff format --check)"
run ruff format --check .

echo "==> types (mypy)"
run mypy

echo "==> tests (pytest + coverage >= 85%)"
run pytest tests/ --cov=audio_censor --cov-report=term-missing --cov-fail-under=85

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "note: ffmpeg is not installed, integration tests were skipped" >&2
fi

echo "==> all checks passed"
