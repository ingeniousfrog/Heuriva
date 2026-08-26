# Heuriva desktop (Tauri 2)

Thin shell around the localhost Session UI.

- Spawns the bundled `heuriva-sidecar` (`heuriva serve`)
- Opens a WebView to `http://127.0.0.1:8766/`
- Kills the sidecar on exit
- Does **not** reimplement Runtime / SQLite logic in Rust

## Dev (Python path, no installer)

```bash
# terminal A
heuriva serve

# terminal B — optional Tauri chrome against running serve
cd desktop && npm install && npm run tauri dev
```

## Build installers

```bash
chmod +x scripts/*.sh
./scripts/build-desktop.sh          # macOS .dmg / .app (primary)
./scripts/build-desktop-windows.sh  # Windows best effort
```

Sidecar only:

```bash
./scripts/build-sidecar.sh
```

## GitHub Actions release

- `CI` runs pytest on push/PR to `main`.
- `Release desktop` builds macOS (arm64 + x86_64) and Windows installers on `v*` tags and uploads a GitHub Release.

```bash
git tag v1.0.0
git push origin v1.0.0
```

Unsigned / ad-hoc builds are expected until Apple/Windows signing secrets are configured.

## Notes

- Unsigned / ad-hoc local distribution is expected for v1.0.
- Gatekeeper (macOS) / SmartScreen (Windows) may warn on first open.
- Config and SQLite stay under `~/.heuriva` (same as CLI).
