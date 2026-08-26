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
- `Release desktop` builds macOS Apple Silicon + Windows installers on `v*` tags and uploads a GitHub Release.

```bash
git tag v1.0.1
git push origin v1.0.1
```

Unsigned / ad-hoc builds are expected until Apple/Windows signing secrets are configured.

### First-open notes (unsigned)

**macOS:** drag Heuriva → Applications, then right-click → Open. If Gatekeeper says the app is “damaged”:

```bash
xattr -cr /Applications/Heuriva.app
```

**Windows:** if SmartScreen blocks the setup `.exe`, choose **More info** → **Run anyway**.

## Notes

- Unsigned / ad-hoc local distribution is expected for v1.x.
- Gatekeeper (macOS) / SmartScreen (Windows) may warn on first open.
- Config and SQLite stay under `~/.heuriva` (same as CLI).
- DMG background / NSIS sidebar assets live under `desktop/src-tauri/images/` (`scripts/generate-dmg-background.py`).
