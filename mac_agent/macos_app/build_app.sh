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
print(Path(sysconfig.get_path("purelib")))
')"
BASE_PREFIX="$("$PYTHON_BIN" -c 'import sys; print(sys.base_prefix)')"
cat >"$APP_DIR/Contents/Resources/runtime.env" <<EOF
APPLE_HASYNC_ROOT=${ROOT}
APPLE_HASYNC_SITE_PACKAGES=${SITE_PACKAGES}
VIRTUAL_ENV=${ROOT}/.venv
EOF

# Resolve include / link flags. Handles:
# - python.org / Homebrew Python.framework
# - Apple CLT Python3.framework (sysconfig LIBDIR often points at a missing Xcode path)
# - plain libpython*.dylib under base_prefix
LINK_META="$("$PYTHON_BIN" - <<'PY'
import os
import shutil
import sys
import sysconfig
from pathlib import Path

base = Path(sys.base_prefix).resolve()
exe = Path(sys.executable).resolve()
ver = f"{sys.version_info.major}.{sys.version_info.minor}"
ldversion = sysconfig.get_config_var("LDVERSION") or sysconfig.get_config_var("VERSION") or ver
include = Path(sysconfig.get_path("include") or "")

fw = None
for p in (exe, *exe.parents, base, *base.parents):
    if p.name in ("Python.framework", "Python3.framework") and (p / "Versions").is_dir():
        fw = p
        break

def first_existing(paths):
    for p in paths:
        if p is None:
            continue
        path = Path(p)
        if path.is_file() or path.is_dir():
            return path
    return None

libdir_cfg = sysconfig.get_config_var("LIBDIR") or ""
libdir = first_existing(
    [
        libdir_cfg,
        base / "lib",
        base / f"lib/python{ver}/config-{ldversion}-darwin",
        fw / "Versions" / ver / "lib" if fw else None,
    ]
)

dylib = first_existing(
    [
        (libdir / f"libpython{ldversion}.dylib") if libdir else None,
        (libdir / f"libpython{ver}.dylib") if libdir else None,
        base / "lib" / f"libpython{ldversion}.dylib",
        base / "lib" / f"libpython{ver}.dylib",
        fw / "Versions" / ver / "lib" / f"libpython{ldversion}.dylib" if fw else None,
        fw / "Versions" / ver / "Python3" if fw and fw.name == "Python3.framework" else None,
        fw / "Versions" / ver / "Python" if fw and fw.name == "Python.framework" else None,
    ]
)

fw_headers = None
if fw is not None:
    for cand in (
        fw / "Versions" / ver / "Headers",
        fw / "Headers",
        include,
    ):
        if cand and Path(cand).is_dir():
            fw_headers = Path(cand)
            break

cflags = []
ldflags = []
mode = ""

if include.is_dir():
    cflags.append(f"-I{include}")

if fw is not None and fw_headers is not None:
    mode = "framework"
    fw_parent = fw.parent
    fw_link_name = "Python3" if fw.name == "Python3.framework" else "Python"
    cflags.append(f"-I{fw_headers}")
    ldflags.extend(
        [
            f"-F{fw_parent}",
            f"-framework {fw_link_name}",
            f"-Wl,-rpath,{fw_parent}",
        ]
    )
elif dylib is not None and dylib.suffix == ".dylib":
    mode = "dylib"
    lib_dir = dylib.parent
    # Prefer -lpythonX.Y when the soname matches; else link the full path.
    stem = dylib.name
    if stem.startswith("lib") and stem.endswith(".dylib"):
        short = stem[3 : -len(".dylib")]  # python3.9
        ldflags.extend([f"-L{lib_dir}", f"-l{short}", f"-Wl,-rpath,{lib_dir}"])
    else:
        ldflags.extend([str(dylib), f"-Wl,-rpath,{lib_dir}"])
    if not include.is_dir() and (base / "include").is_dir():
        cflags.append(f"-I{base / 'include'}")
else:
    cfg = shutil.which("python3-config") or shutil.which(f"python{ver}-config")
    if not cfg:
        print("ERROR: cannot locate Python.framework / Python3.framework / libpython", file=sys.stderr)
        print(f"  base_prefix={base}", file=sys.stderr)
        print(f"  LIBDIR(sysconfig)={libdir_cfg!r} exists={os.path.isdir(libdir_cfg)}", file=sys.stderr)
        sys.exit(1)
    mode = "python-config"
    import subprocess

    cflags.append(
        subprocess.check_output([cfg, "--includes"], text=True).strip()
    )
    try:
        ldflags.append(
            subprocess.check_output([cfg, "--ldflags", "--embed"], text=True).strip()
        )
    except subprocess.CalledProcessError:
        ldflags.append(subprocess.check_output([cfg, "--ldflags"], text=True).strip())

# Flatten for the shell (quoted values — unquoted eval breaks on multiple -I/-L flags)
import shlex

print("MODE=" + shlex.quote(mode))
print("CFLAGS=" + shlex.quote(" ".join(cflags)))
print("LDFLAGS=" + shlex.quote(" ".join(ldflags)))
print("FW=" + shlex.quote(str(fw) if fw else ""))
print("DYLIB=" + shlex.quote(str(dylib) if dylib else ""))
print("LIBDIR=" + shlex.quote(str(libdir) if libdir else ""))
PY
)"

# shellcheck disable=SC2086
eval "$LINK_META"

if [[ -z "${CFLAGS:-}" || -z "${LDFLAGS:-}" ]]; then
  echo "ERROR: failed to resolve Python link flags" >&2
  echo "$LINK_META" >&2
  exit 1
fi

echo "    site-packages: $SITE_PACKAGES"
echo "    base prefix:   $BASE_PREFIX"
echo "    link mode:     $MODE"
[[ -n "${FW:-}" ]] && echo "    framework:     $FW"
[[ -n "${DYLIB:-}" ]] && echo "    dylib:         $DYLIB"
echo "    compile CFLAGS=$CFLAGS"
echo "    compile LDFLAGS=$LDFLAGS"

# shellcheck disable=SC2086
clang -O2 -Wall -Wextra -o "$APP_DIR/Contents/MacOS/appleHAsync" \
  "$SRC_DIR/launcher.c" $CFLAGS $LDFLAGS

chmod 755 "$APP_DIR/Contents/MacOS/appleHAsync"

if ! otool -L "$APP_DIR/Contents/MacOS/appleHAsync" >/dev/null 2>&1; then
  echo "ERROR: built launcher is not a valid Mach-O binary" >&2
  exit 1
fi

# Ad-hoc sign so macOS treats it as a stable app identity
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true

# Register with Launch Services for display name resolution
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP_DIR" 2>/dev/null || true

echo "==> Built $APP_DIR"
echo "    id: app.iHadAThought.appleHAsync"
echo "    name: appleHAsync"
