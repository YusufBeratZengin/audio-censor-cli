#!/usr/bin/env bash
#
# Project gate: lint + format + types + tests with coverage.
# Usage: bash scripts/check.sh
# Exits 0 only when every step passes.
#
# The script is self-contained:
#   * picks python3 (or $PYTHON) and finds the dev tools through the module
#     namespace, PATH, the user's ~/.local/bin and a local .venv;
#   * if a tool is still missing it installs just that tool into the current
#     interpreter (or the user site when the interpreter is read-only).
#     Set CHECK_NO_INSTALL=1 to disable that and fail fast instead.
#   * RUFF, MYPY and PYTEST may point at explicit executables.
#
# Tests never touch the network; only the one-time tool bootstrap above can.

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON" ] || ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: python3 not found (set PYTHON to an interpreter)" >&2
  exit 1
fi

has_module() {
  "$PYTHON" -c "import $1" >/dev/null 2>&1
}

# resolve <module> [<pip package> ...]
# Fills the TOOL array with the command that runs <module>.
TOOL=()
resolve() {
  local module="$1"
  shift
  local env_name override dir
  env_name="$(printf '%s' "$module" | tr '[:lower:]' '[:upper:]')"
  override="${!env_name:-}"

  TOOL=()
  if [ -n "$override" ]; then
    if command -v "$override" >/dev/null 2>&1; then
      TOOL=("$override")
      return 0
    fi
    echo "error: $env_name=$override is not executable" >&2
    return 1
  fi

  if has_module "$module"; then
    TOOL=("$PYTHON" -m "$module")
    return 0
  fi
  if command -v "$module" >/dev/null 2>&1; then
    TOOL=("$module")
    return 0
  fi
  # ruff/mypy wheels are standalone binaries: a copy in ~/.local/bin works even
  # when the active interpreter cannot import the module (different Python).
  for dir in "$HOME/.local/bin" "$(dirname "$(command -v "$PYTHON")")" ".venv/bin"; do
    if [ -x "$dir/$module" ]; then
      TOOL=("$dir/$module")
      return 0
    fi
  done

  if [ "${CHECK_NO_INSTALL:-0}" != "1" ] && [ "$#" -gt 0 ]; then
    echo "note: $module not found, installing: $*" >&2
    if "$PYTHON" -m pip install --quiet "$@" >/dev/null 2>&1 ||
      "$PYTHON" -m pip install --quiet --user "$@" >/dev/null 2>&1; then
      if has_module "$module"; then
        TOOL=("$PYTHON" -m "$module")
        return 0
      fi
      if command -v "$module" >/dev/null 2>&1; then
        TOOL=("$module")
        return 0
      fi
    fi
    echo "warning: automatic install of $module failed" >&2
  fi

  echo "error: $module is not installed" >&2
  echo "       run: $PYTHON -m pip install -e '.[dev]'" >&2
  return 1
}

echo "==> lint (ruff check)"
resolve ruff ruff || exit 1
"${TOOL[@]}" check .

echo "==> format (ruff format --check)"
resolve ruff ruff || exit 1
"${TOOL[@]}" format --check .

echo "==> types (mypy)"
resolve mypy mypy || exit 1
"${TOOL[@]}"

echo "==> tests (pytest + coverage >= 85%)"
resolve pytest pytest pytest-cov || exit 1
# ruff/mypy/pytest may come from ~/.local/bin, i.e. a different interpreter than
# $PYTHON; ask the resolved pytest itself whether the coverage plugin is loaded.
pytest_has_cov() {
  # Capture first: `pytest --help | grep -q` would SIGPIPE under `pipefail`.
  local help
  help="$("${TOOL[@]}" --help 2>/dev/null || true)"
  case "$help" in
    *--cov*) return 0 ;;
    *) return 1 ;;
  esac
}
if ! pytest_has_cov; then
  if [ "${CHECK_NO_INSTALL:-0}" != "1" ] && [ "${TOOL[0]}" = "$PYTHON" ]; then
    echo "note: pytest-cov not found, installing it" >&2
    "$PYTHON" -m pip install --quiet pytest-cov >/dev/null 2>&1 || true
  fi
  if ! pytest_has_cov; then
    echo "error: pytest-cov is not installed (needed for the coverage gate)" >&2
    echo "       run: $PYTHON -m pip install -e '.[dev]'" >&2
    exit 1
  fi
fi
"${TOOL[@]}" tests/ --cov=audio_censor --cov-report=term-missing --cov-fail-under=85

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "note: ffmpeg is not installed, integration tests were skipped" >&2
fi

echo "==> all checks passed"
