#!/bin/bash
# Shared Python version gate for install / update scripts.
# Requires Python >= 3.10 (Apple CLT often ships 3.9).
#
# Usage: source this file, then:
#   applehasync_require_python "$PYTHON_BIN"
#
applehasync_require_python() {
  local py="${1:-python3}"
  if ! command -v "$py" >/dev/null 2>&1 && [[ ! -x "$py" ]]; then
    echo "ERROR: Python not found ($py)." >&2
    echo "Install Python 3.10+ (recommended: brew install python@3.12), then re-run." >&2
    echo "  Or: https://www.python.org/downloads/" >&2
    return 1
  fi
  local ver
  ver="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
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
