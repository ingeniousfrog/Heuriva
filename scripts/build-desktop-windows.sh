#!/usr/bin/env bash
# Best-effort Windows desktop build (run on Windows with Rust + Node + MSVC).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
./scripts/build-sidecar.sh x86_64-pc-windows-msvc || ./scripts/build-sidecar.sh
cd desktop
npm install
npm run tauri build -- --target x86_64-pc-windows-msvc || npm run tauri build
echo "Windows build attempted. Check desktop/src-tauri/target/*/bundle/nsis or msi."
