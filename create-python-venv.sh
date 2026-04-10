#!/usr/bin/env bash
# Create a Python virtual environment matching this repo's setup:
#   <python> -m venv .venv
#   pip install --upgrade pip
#   pip install -r requirements.txt
# Optional: pip install -r requirements-dev.txt
#
# Interpreter: reads .python-version (e.g. 3.10.20) and picks the matching
# python3.N binary on PATH (python3.10 for 3.10.x). Override with PYTHON=/path/to/python.
#
# Run from any project root (or copy this script there). The venv is created
# next to requirements.txt in the directory containing this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${SCRIPT_DIR}/.venv"
PYTHON_VERSION_FILE="${SCRIPT_DIR}/.python-version"

# macOS: ensure Python 3.10.x via Homebrew when .python-version requests 3.10.x (PYTHON overrides).
ensure_macos_python310() {
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  [[ -z "${PYTHON:-}" ]] || return 0

  local spec=""
  if [[ -f "$PYTHON_VERSION_FILE" ]]; then
    IFS= read -r spec < "$PYTHON_VERSION_FILE" || spec=""
    spec="${spec%%[[:space:]]*}"
  fi
  [[ "$spec" =~ ^3\.10\. ]] || return 0

  local have_310=false
  if command -v python3.10 >/dev/null 2>&1; then
    local mm
    mm="$(python3.10 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || mm=""
    [[ "$mm" == "3.10" ]] && have_310=true
  fi
  [[ "$have_310" == true ]] && return 0

  if ! command -v brew >/dev/null 2>&1; then
    cat <<'EOF' >&2
error: Python 3.10.x is required but python3.10 was not found, and Homebrew is not installed.

Install Homebrew from https://brew.sh/ , then run this script again. It will run: brew install python@3.10

Or set PYTHON to the path to a Python 3.10.x interpreter.
EOF
    exit 1
  fi

  echo "Installing Python 3.10 via Homebrew: brew install python@3.10"
  brew install python@3.10

  if ! command -v python3.10 >/dev/null 2>&1; then
    echo "error: brew install python@3.10 finished but python3.10 is still not on PATH. Ensure $(brew --prefix 2>/dev/null)/bin is on PATH." >&2
    exit 1
  fi
}

ensure_macos_python310

# Resolve which python to use: PYTHON env, then .python-version -> python3.N, else python3.
resolve_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s' "$PYTHON"
    return
  fi
  if [[ -f "$PYTHON_VERSION_FILE" ]]; then
    local spec
    IFS= read -r spec < "$PYTHON_VERSION_FILE" || spec=""
    spec="${spec%%[[:space:]]*}"
    if [[ "$spec" =~ ^3\.([0-9]+) ]]; then
      local minor="${BASH_REMATCH[1]}"
      local candidate="python3.${minor}"
      if command -v "$candidate" >/dev/null 2>&1; then
        printf '%s' "$candidate"
        return
      fi
      echo "error: ${PYTHON_VERSION_FILE} requests ${spec} but '${candidate}' was not found on PATH." >&2
      echo "Install that Python (e.g. pyenv install ${spec}) or set PYTHON to the interpreter." >&2
      exit 1
    fi
  fi
  printf '%s' "python3"
}

PYTHON_BIN="$(resolve_python)"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: Python interpreter not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ -f "$PYTHON_VERSION_FILE" ]]; then
  want="$(tr -d '[:space:]' < "$PYTHON_VERSION_FILE")"
  got="$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  if [[ -n "$want" && "$want" != "$got" ]]; then
    echo "warning: .python-version wants ${want} but ${PYTHON_BIN} is ${got}" >&2
  fi
fi
WITH_DEV=false
FORCE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Creates .venv under the script directory and installs dependencies from
requirements.txt (same flow as this repository's README).

Options:
  --with-dev   Also install requirements-dev.txt if present
  --force      Remove an existing .venv and recreate it
  -h, --help   Show this help

Environment:
  PYTHON       Use this interpreter instead of the one implied by .python-version

Example:
  ./create-python-venv.sh
  ./create-python-venv.sh --with-dev
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-dev) WITH_DEV=true ;;
    --force)    FORCE=true ;;
    -h|--help)  usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

REQ="${SCRIPT_DIR}/requirements.txt"
if [[ ! -f "$REQ" ]]; then
  echo "error: requirements.txt not found at ${REQ}" >&2
  exit 1
fi

if [[ -d "$VENV_PATH" ]]; then
  if [[ "$FORCE" != true ]]; then
    echo "error: ${VENV_PATH} already exists. Remove it or pass --force." >&2
    exit 1
  fi
  echo "Removing existing ${VENV_PATH}"
  rm -rf "$VENV_PATH"
fi

echo "Creating virtual environment with ${PYTHON_BIN}: ${VENV_PATH}"
"$PYTHON_BIN" -m venv "$VENV_PATH"

echo "Upgrading pip"
"$VENV_PATH/bin/pip" install --upgrade pip

echo "Installing requirements from requirements.txt"
"$VENV_PATH/bin/pip" install -r "$REQ"

if [[ "$WITH_DEV" == true ]]; then
  DEV_REQ="${SCRIPT_DIR}/requirements-dev.txt"
  if [[ -f "$DEV_REQ" ]]; then
    echo "Installing development requirements from requirements-dev.txt"
    "$VENV_PATH/bin/pip" install -r "$DEV_REQ"
  else
    echo "warning: --with-dev set but requirements-dev.txt not found; skipping" >&2
  fi
fi

echo
echo "Done. Activate with:"
echo "  source ${VENV_PATH}/bin/activate"
echo "Or run Python directly:"
echo "  ${VENV_PATH}/bin/python ..."
