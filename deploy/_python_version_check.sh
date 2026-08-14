#!/bin/bash
# Shared Python version gate for install / update scripts.
# Requires Python >= 3.10 (Apple CLT often ships 3.9).
#
# Usage: source this file, then:
#   applehasync_require_python "$PYTHON_BIN"
#   PYTHON_BIN="$(applehasync_resolve_python "${PYTHON_BIN:-}")"
#
applehasync_python_version() {
  local py="$1"
  "$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true
}

applehasync_python_ok() {
  local py="$1"
  local ver major minor
  [[ -n "$py" ]] || return 1
  if ! command -v "$py" >/dev/null 2>&1 && [[ ! -x "$py" ]]; then
    return 1
  fi
  ver="$(applehasync_python_version "$py")"
  [[ -n "$ver" ]] || return 1
  IFS=. read -r major minor <<<"$ver"
  (( major > 3 || (major == 3 && minor >= 10) ))
}

# Prefer explicit PYTHON_BIN, then install venv, Homebrew 3.12/3.11/3.10, then PATH.
# Prints the chosen interpreter path on stdout. Returns 1 if none suitable.
applehasync_resolve_python() {
  local preferred="${1:-}"
  local install_dir="${2:-}"
  local cand brew_prefix
  local -a candidates=()

  if [[ -n "$preferred" ]]; then
    candidates+=("$preferred")
  fi
  if [[ -n "$install_dir" && -x "$install_dir/.venv/bin/python" ]]; then
    candidates+=("$install_dir/.venv/bin/python")
  fi
  if command -v brew >/dev/null 2>&1; then
    for formula in python@3.12 python@3.13 python@3.11 python@3.10; do
      brew_prefix="$(brew --prefix "$formula" 2>/dev/null || true)"
      if [[ -n "$brew_prefix" ]]; then
        candidates+=("$brew_prefix/bin/python3.12")
        candidates+=("$brew_prefix/bin/python3.13")
        candidates+=("$brew_prefix/bin/python3.11")
        candidates+=("$brew_prefix/bin/python3.10")
        candidates+=("$brew_prefix/bin/python3")
      fi
    done
  fi
  candidates+=(
    python3.13
    python3.12
    python3.11
    python3.10
    python3
  )

  for cand in "${candidates[@]}"; do
    [[ -n "$cand" ]] || continue
    if applehasync_python_ok "$cand"; then
      # Prefer absolute path when possible
      if [[ -x "$cand" ]]; then
        echo "$cand"
      else
        command -v "$cand"
      fi
      return 0
    fi
  done
  return 1
}

applehasync_require_python() {
  local py="${1:-python3}"
  if ! command -v "$py" >/dev/null 2>&1 && [[ ! -x "$py" ]]; then
    echo "ERROR: Python not found ($py)." >&2
    echo "Install Python 3.10+ (recommended: brew install python@3.12), then re-run." >&2
    echo "  Or: https://www.python.org/downloads/" >&2
    return 1
  fi
  local ver
  ver="$(applehasync_python_version "$py")"
  if [[ -z "$ver" ]]; then
    echo "ERROR: could not read version from $py" >&2
    return 1
  fi
  local major minor
  IFS=. read -r major minor <<<"$ver"
  if (( major < 3 || (major == 3 && minor < 10) )); then
    echo "ERROR: appleHAsync requires Python 3.10 or newer (found $py → $ver)." >&2
    echo "Apple Command Line Tools often provide 3.9, which is not supported." >&2
    echo "Install a newer Python, then re-run with PYTHON_BIN pointing at it, e.g.:" >&2
    echo "  brew install python@3.12" >&2
    echo "  PYTHON_BIN=\$(brew --prefix python@3.12)/bin/python3.12 ./deploy/install-mac-agent.sh" >&2
    return 1
  fi
  echo "    Python: $py ($ver)"
  return 0
}
