#!/usr/bin/env bash
#
# Project gate: lint + format + types + tests with coverage.
# Usage: bash scripts/check.sh
# Exits 0 only when every step passes.
#
# The script is self-contained:
#   * picks python3 (or $PYTHON) and finds the dev tools through the module
#     namespace, PATH, the user's ~/.local/bin and a local .venv;
#   * if a tool is still missing it installs just that tool -- at the version
#     pinned in requirements-dev.txt, the same file CI installs -- into the
#     current interpreter (or the user site when the interpreter is read-only).
#     Set CHECK_NO_INSTALL=1 to disable that and fail fast instead.
#   * RUFF, MYPY and PYTEST may point at explicit executables.
#   * FFmpeg and FFprobe must be available (PATH or $FFMPEG/$FFPROBE); the
#     render tests need them and the coverage gate depends on those tests.
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

# FFmpeg is not optional: without it ~50 render tests are skipped and the
# coverage gate cannot pass. FFMPEG/FFPROBE may point at binaries that are not
# on PATH; their directory is prepended so the test suite's shutil.which()
# sees them too.
for tool_var in FFMPEG FFPROBE; do
  tool_path="${!tool_var:-}"
  if [ -n "$tool_path" ]; then
    if [ ! -x "$tool_path" ]; then
      echo "error: $tool_var=$tool_path is not an executable file" >&2
      exit 1
    fi
    # Keep the shell-native form (/c/... under Git Bash): a bare "C:\\..."
    # entry would be split at its colon by PATH.  MSYS translates PATH when it
    # launches a native Windows Python, so the tests find the binaries too.
    PATH="$(cd "$(dirname "$tool_path")" && pwd):$PATH"
  fi
done
export PATH

# GUI installers (winget, Chocolatey, Scoop, the zip builds) update the machine
# PATH but not the environment of a shell that was already open, so a plain
# `bash scripts/check.sh` can miss a perfectly good FFmpeg.  Look in the usual
# directories before failing; $FFMPEG/$FFPROBE above still take precedence.
if ! command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG_CANDIDATES=(
    "${LOCALAPPDATA:-}/Microsoft/WinGet/Links"
    "/c/ProgramData/chocolatey/bin"
    "${USERPROFILE:-}/scoop/shims"
    "$HOME/scoop/shims"
    "/c/ffmpeg/bin"
    "/opt/homebrew/bin"
    "/usr/local/bin"
  )
  for candidate in "${FFMPEG_CANDIDATES[@]}"; do
    [ -n "$candidate" ] && [ -d "$candidate" ] || continue
    if [ -x "$candidate/ffmpeg" ] || [ -x "$candidate/ffmpeg.exe" ]; then
      PATH="$candidate:$PATH"
      export PATH
      echo "note: using FFmpeg from $candidate (not on the inherited PATH)" >&2
      break
    fi
  done
fi

for tool_name in ffmpeg ffprobe; do
  if ! command -v "$tool_name" >/dev/null 2>&1; then
    echo "error: $tool_name was not found on PATH" >&2
    echo "       install FFmpeg (https://ffmpeg.org/download.html) and retry," >&2
    echo "       or set FFMPEG=/path/to/ffmpeg and FFPROBE=/path/to/ffprobe." >&2
    echo "       The suite skips every render test without it, so the coverage" >&2
    echo "       gate cannot pass." >&2
    exit 1
  fi
done
if ! ffmpeg -version >/dev/null 2>&1; then
  echo "error: ffmpeg is on PATH but does not run" >&2
  exit 1
fi

has_module() {
  "$PYTHON" -c "import $1" >/dev/null 2>&1
}

# The one pin file CI installs.  When this script has to bootstrap a missing
# tool it installs the *pinned* version from here, so a green local gate means
# the same Ruff/MyPy/pytest as the CI job instead of whatever is newest today.
REQUIREMENTS="requirements-dev.txt"

# requirement_pin <pip package> -> exact version, or nothing when unpinned.
requirement_pin() {
  local package="$1" pin=""
  if [ -f "$REQUIREMENTS" ]; then
    pin="$(grep -E "^[[:space:]]*$package==" "$REQUIREMENTS" | head -n 1 | cut -d= -f3)"
  fi
  printf '%s' "$pin"
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
    local package pin
    local -a specs=()
    for package in "$@"; do
      pin="$(requirement_pin "$package")"
      if [ -n "$pin" ]; then
        specs+=("$package==$pin")
      else
        specs+=("$package")
      fi
    done
    echo "note: $module not found, installing: ${specs[*]}" >&2
    if "$PYTHON" -m pip install --quiet "${specs[@]}" >/dev/null 2>&1 ||
      "$PYTHON" -m pip install --quiet --user "${specs[@]}" >/dev/null 2>&1; then
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
    cov_spec="pytest-cov"
    cov_pin="$(requirement_pin pytest-cov)"
    if [ -n "$cov_pin" ]; then
      cov_spec="pytest-cov==$cov_pin"
    fi
    echo "note: pytest-cov not found, installing $cov_spec" >&2
    "$PYTHON" -m pip install --quiet "$cov_spec" >/dev/null 2>&1 || true
  fi
  if ! pytest_has_cov; then
    echo "error: pytest-cov is not installed (needed for the coverage gate)" >&2
    echo "       run: $PYTHON -m pip install -e '.[dev]'" >&2
    exit 1
  fi
fi
# The project root must be importable even when pytest is a standalone script
# bound to a different interpreter: such a launcher does not put the CWD on
# sys.path, so `import audio_censor` and subprocess `-m audio_censor` calls
# would fail without an editable install.
#
# Native Windows Python needs a drive-letter path here; an inherited MSYS style
# PYTHONPATH (colon separated, /c/...) is not usable, so it is replaced rather
# than appended to.
PROJECT_ROOT="$PWD"
if command -v cygpath >/dev/null 2>&1; then
  PROJECT_ROOT="$(cygpath -w "$PWD")"
fi
PYTHONPATH="$PROJECT_ROOT" \
  "${TOOL[@]}" tests/ --cov=audio_censor --cov-report=term-missing --cov-fail-under=85

echo "==> all checks passed"
