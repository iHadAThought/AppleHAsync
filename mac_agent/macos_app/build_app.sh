#!/bin/bash
# Build ~/Applications/appleHAsync.app (or APP_DIR) with in-process Python launcher.
set -euo pipefail

ROOT="${APPLE_HASYNC_ROOT:-}"
if [[ -z "$ROOT" ]]; then
  ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi
APP_DIR="${APP_DIR:-$HOME/Applications/appleHAsync.app}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: Python not found at $PYTHON_BIN (create venv first)" >&2
  exit 1
fi

echo "==> Building appleHAsync.app at $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$SRC_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"

# Capture venv site-packages so the Mach-O launcher can import deps in-process
SITE_PACKAGES="$("$PYTHON_BIN" -c '
import sysconfig
from pathlib import Path
p = Path(sysconfig.get_path("purelib"))
print(p)
')"
BASE_PREFIX="$("$PYTHON_BIN" -c 'import sys; print(sys.base_prefix)')"
cat >"$APP_DIR/Contents/Resources/runtime.env" <<EOF
APPLE_HASYNC_ROOT=${ROOT}
APPLE_HASYNC_SITE_PACKAGES=${SITE_PACKAGES}
VIRTUAL_ENV=${ROOT}/.venv
EOF

INCLUDE="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
LIBDIR="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")')"
LDVERSION="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("LDVERSION") or sysconfig.get_config_var("VERSION"))')"

CFLAGS="-I${INCLUDE}"
LDFLAGS=""
if [[ -n "$LIBDIR" ]]; then
  LDFLAGS+=" -L${LIBDIR}"
fi

# Framework Python (Apple / python.org)
FW="$("$PYTHON_BIN" -c '
import pathlib, sys
exe = pathlib.Path(sys.executable).resolve()
for p in [exe, *exe.parents]:
    if p.name == "Python.framework":
        print(p)
        break
')"

if [[ -n "$FW" && -d "$FW" ]]; then
  VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  LDFLAGS+=" -F$(dirname "$FW") -framework Python"
  CFLAGS+=" -I$FW/Versions/$VER/Headers"
elif [[ -n "$LIBDIR" ]]; then
  LDFLAGS+=" -lpython${LDVERSION}"
else
  CFG="$("$PYTHON_BIN" -c 'import sys,shutil; print(shutil.which("python3-config") or shutil.which(f"python{sys.version_info.major}.{sys.version_info.minor}-config") or "")')"
  if [[ -z "$CFG" ]]; then
    echo "ERROR: cannot find libpython / Python.framework to link launcher" >&2
    exit 1
  fi
  # shellcheck disable=SC2046
  CFLAGS+=" $($CFG --includes)"
  # shellcheck disable=SC2046
  LDFLAGS+=" $($CFG --ldflags --embed 2>/dev/null || $CFG --ldflags)"
fi

echo "    site-packages: $SITE_PACKAGES"
echo "    base prefix:   $BASE_PREFIX"
echo "    compile CFLAGS=$CFLAGS"
echo "    compile LDFLAGS=$LDFLAGS"
# shellcheck disable=SC2086
clang -O2 -Wall -Wextra -o "$APP_DIR/Contents/MacOS/appleHAsync" \
  "$SRC_DIR/launcher.c" $CFLAGS $LDFLAGS

chmod 755 "$APP_DIR/Contents/MacOS/appleHAsync"

# Ad-hoc sign so macOS treats it as a stable app identity
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true

# Register with Launch Services for display name resolution
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP_DIR" 2>/dev/null || true

echo "==> Built $APP_DIR"
echo "    id: app.iHadAThought.appleHAsync"
echo "    name: appleHAsync"
