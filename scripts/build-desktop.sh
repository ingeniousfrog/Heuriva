#!/usr/bin/env bash
# Build Heuriva desktop installers via Tauri (macOS .dmg primary).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

./scripts/build-sidecar.sh

cd "$ROOT/desktop"
if [[ ! -d node_modules ]]; then
  npm install
fi

# Ensure icons exist (generated placeholder if missing).
if [[ ! -f src-tauri/icons/icon.png || ! -f src-tauri/icons/icon.icns ]]; then
  "$ROOT/.venv/bin/python" -m pip install -q --index-url https://pypi.org/simple 'pillow>=10'
  "$ROOT/.venv/bin/python" "$ROOT/scripts/generate-desktop-icon.py"
fi

export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$ROOT/desktop/src-tauri/target}"
npm run tauri build "$@"

RELEASE_DIR="$ROOT/desktop/release"
mkdir -p "$RELEASE_DIR"
if [[ -d "$CARGO_TARGET_DIR/release/bundle/macos/Heuriva.app" ]]; then
  rm -rf "$RELEASE_DIR/Heuriva.app"
  cp -R "$CARGO_TARGET_DIR/release/bundle/macos/Heuriva.app" "$RELEASE_DIR/"
fi
if compgen -G "$CARGO_TARGET_DIR/release/bundle/dmg/"*.dmg > /dev/null; then
  cp -f "$CARGO_TARGET_DIR/release/bundle/dmg/"*.dmg "$RELEASE_DIR/"
fi
echo "Tauri build finished. Artifacts under $RELEASE_DIR and $CARGO_TARGET_DIR/release/bundle/"
