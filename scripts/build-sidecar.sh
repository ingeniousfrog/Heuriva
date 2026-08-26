#!/usr/bin/env bash
# Build the Heuriva Python sidecar binary for Tauri externalBin.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  TARGET="$(rustc -vV | awk '/^host:/{print $2}')"
fi

OUT_DIR="$ROOT/desktop/src-tauri/binaries"
mkdir -p "$OUT_DIR"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

"$PYTHON" -m pip install -q --index-url https://pypi.org/simple 'pyinstaller>=6,<7'
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name "heuriva-sidecar" \
  --paths "$ROOT/src" \
  --hidden-import heuriva \
  --hidden-import heuriva.cli \
  --collect-all heuriva \
  "$ROOT/packaging/heuriva_sidecar.py"

SRC="$ROOT/dist/heuriva-sidecar"
if [[ ! -f "$SRC" && -f "$ROOT/dist/heuriva-sidecar.exe" ]]; then
  SRC="$ROOT/dist/heuriva-sidecar.exe"
fi
if [[ ! -f "$SRC" ]]; then
  echo "sidecar binary not found under dist/" >&2
  exit 1
fi

DEST="$OUT_DIR/heuriva-sidecar-${TARGET}"
if [[ "$SRC" == *.exe ]]; then
  DEST="${DEST}.exe"
fi
cp "$SRC" "$DEST"
chmod +x "$DEST" || true
echo "Wrote $DEST"
