# fix_plan.md

Living list of known bugs, placeholder implementations, and outstanding TODOs that span sessions. Carried forward every session. Taste invariant #5 forbids shipping placeholder code in `main` — if a feature can't be implemented in full per spec, it is **skipped** and logged here.

## Format

Each entry:

```markdown
### <short title>

- **Status:** open | in-progress | resolved
- **Opened:** <session number / date>
- **Area:** <stage name or file path>
- **Symptom / gap:** <what's wrong or missing>
- **Next step:** <concrete action>
```

---

## Open

### Plugin dist/ rebuild required before Stage 1 can be fully verified

- **Status:** open
- **Opened:** Session 2 / 2026-04-25
- **Area:** packaging / `dist/` — built UI assets
- **Symptom / gap:** `dist/` was made root-owned by a prior `sudo`-level install run. All ingest fixes (reclassification, empty-shell removal, bin structure) were written against the source files but `dist/` has never been rebuilt with them. The live plugin is running stale compiled output.
- **Next step:** `sudo chown -R $(whoami) dist && npm run build && sudo bash packaging/install-dev-plugin.sh`, restart Resolve, re-run ingest to verify all three open items below.

### Bin structure in Resolve media pool unverified

- **Status:** in-progress
- **Opened:** Session 2 / 2026-04-25
- **Area:** `src/workers/resolve_import.py`
- **Symptom / gap:** `resolve_import.py` was updated to use `_find_or_create()` per-bin approach (creating `<shoot>/01_VIDEOS/A_CAM`, `/C_CAM`, `/02_PHOTOS` bins in Resolve Master), but this was not tested live before the session ended. Prior versions imported clips flat into Master.
- **MCP verified 2026-04-29:** `mp.AddSubFolder(parent, name)` (two-arg form on MediaPool) and `mp.SetCurrentFolder` both confirmed working via MCP test on Resolve Studio 21.0.0b.20. Nested path `Master/_cairn_test/01_VIDEOS/A_CAM` created successfully and cleaned up. API shape matches `resolve_import.py` — per-bin strategy is correct.
- **Next step:** Rebuild plugin (`sudo chown -R $(whoami) dist && npm run build:ui && sudo bash packaging/install-dev-plugin.sh`), restart Resolve, run ingest end-to-end to confirm clips land in nested bins.

### Photomator Edit Documents not moving to 02_PHOTOS

- **Status:** open
- **Opened:** Session 2 / 2026-04-25
- **Area:** `src/workers/ingest.py` — `collect_files()` and reclassification pass
- **Symptom / gap:** Photomator creates extension-less sidecar files alongside RAW files (e.g. `DSC00816` with no extension, Kind = "Photomator Edit Document"). Code routes extension-less files to the photos bucket and has a reclassification pass from `03_ASSETS → 02_PHOTOS`, but the live plugin hasn't been rebuilt yet to confirm this works.
- **Next step:** After rebuild, verify `DSC00816`-style files land in `02_PHOTOS`. If still failing, check whether `path.is_file()` returns False for these (may be opaque packages despite appearing as files in Get Info).

### Empty shell folders not being removed

- **Status:** open
- **Opened:** Session 2 / 2026-04-25
- **Area:** `src/workers/ingest.py` — `remove_empty_shells()`
- **Symptom / gap:** `DCIM`, `M4ROOT`, `drone`, and other source-camera folders remain after ingest. Root causes: (1) old code used `rmdir` which failed silently on non-empty dirs; (2) custom `_trash_dir` failed cross-device (external drive → `~/.Trash`). Fixed — now uses `send2trash` which handles cross-volume correctly. Awaiting rebuild confirmation.
- **Next step:** After rebuild, verify shell folders are trashed. If `send2trash` still fails, add stderr logging to surface the exception type.

### All ingest fixes unconfirmed in live plugin

- **Status:** open
- **Opened:** Session 2 / 2026-04-25
- **Area:** `src/workers/ingest.py` — DJI→C_CAM, XMP→02_PHOTOS, extension-less→02_PHOTOS passes
- **Symptom / gap:** All three reclassification fixes are code-complete and test-green but were written before the plugin could be rebuilt (`dist/` was root-owned from a prior `sudo` install). Live behaviour unconfirmed.
- **Next step:** `sudo chown -R $(whoami) dist && npm run build && sudo bash packaging/install-dev-plugin.sh`, restart Resolve, re-run ingest.

---

## 2026-06-02 — restructure pass follow-ups

Items surfaced during the program analysis / scope-reset hygiene pass (post `966ce94`). The current pipeline is 4 stages: INGEST (`src/workers/ingest.py`) → CLASSIFY (`src/workers/classify.py`, KNN on `face_ratio` + `motion_score`, `classifier_version "v3-knn-face-motion"`) → COLOUR (inline in `src/workers/resolve_api.py`) → BUILD ASSEMBLY TIMELINE (`src/workers/timeline.py`). These follow-ups are intentionally NOT done in this pass; they are logged so a future session can pick them up.

### Packaging smoke test (validation gap — must run a real build)

- [ ] **Run a real PyInstaller build on the target arch and smoke-test the bundled binary.** `packaging/workers.spec` was corrected by reading (cv2 now pulled in via `collect_data_files`, `timeline` added to `hiddenimports`, phantom/removed modules deleted), but a spec edit CANNOT be validated by reading alone. Build the bundle and verify both `classify` and `build_timeline` commands work end-to-end inside the bundled binary (not just from source).
- [ ] **Confirm bundled binary size is acceptable now that `cv2` (opencv-python-headless) is included.** Record the size delta vs. the pre-cv2 bundle and confirm it is within distribution expectations.

### PR#2 — restructure (deferred from this hygiene pass)

- [ ] **Split `src/orchestrator/main.js` (861 lines).** Extract into `resolve-bridge.js` (the inline Resolve JS-API wrapping — currently inline in `main.js`, a known tech-debt item) + a `handlers/` directory. Note: there is currently NO `package.js` and NO `resolve-bridge.ts`.
- [ ] **Split `src/workers/resolve_api.py` (744 lines) and expose a public `resolve_lock()` contextmanager.** `src/workers/timeline.py` currently reaches into the private `_acquire_lock` / `_release_lock` — give it a public, supported locking contract instead. (Taste invariant #3: all Resolve calls must be serialised through a single worker — the lock is load-bearing.)
- [ ] **Extract `src/workers/constants.py`.** `VIDEO_EXTS` / `PHOTO_EXTS` / `SONY_SHELLS` / `TEMPLATE_DIRS` are copy-pasted across 4 files with a LIVE divergence: `ingest.py` includes `.xmp` but `resolve_api.py` and `resolve_import.py` do NOT, so Resolve import silently skips `.xmp` sidecars. Centralising fixes the divergence (and is a correctness bug, not just cleanup).
- [ ] **Extract `classify.py`'s `inspect_clip` into `src/workers/inspect.py`.**
- [ ] **Adopt the typed `PythonBridge` (`src/orchestrator/python-bridge.ts`) in `main.js` instead of the inline shim.** Prerequisites first: (a) close the Zod schema gaps for the `health_check` and `classify_inspect` messages, and (b) fix the `import.meta.url` `scriptPath` default that breaks under CommonJS `require()`.
- [ ] **Route the legacy `src/workers/resolve_import.py` fallback through `bridge.py`, then delete it.** As a separate one-shot process it can race the non-thread-safe Resolve Scripting API — a direct violation of taste invariant #3. It is already slated for removal.

### Latent correctness bugs (own fixes + tests — NOT part of the restructure)

- [ ] **`sidecar` `clip_id = sha256(absolute_path)[:16]` breaks idempotency on re-ingest.** When ingest renames the shoot folder, the absolute path changes, so the clip hashes to a new `clip_id` and re-ingest no longer matches the existing sidecar. Needs a path-stable identity (and a regression test).
- [ ] **`sidecar.write_clip` / `merge_clip` use non-atomic `Path.write_text` (truncate-then-write).** A crash mid-write corrupts the sidecar. Switch to tmp-file-then-`os.replace` (atomic). (Supports taste invariant #6: sidecar-first must be recoverable from corruption.)
- [ ] **Add unit coverage for production paths.** Create `tests/python/test_resolve_api.py` + `tests/python/test_bridge.py` — these production paths currently have ZERO unit coverage.

### Security / dependencies (separate follow-up)

- [ ] **Plan an Electron upgrade.** Electron 30 has 11 known HIGH CVEs. This is a breaking upgrade — validate the new version against Resolve's plugin host before shipping.
- [ ] **Re-enable ASAR with integrity.** `packaging/electron-builder.json5` currently sets `asar: false`; restore ASAR-with-integrity once the Electron upgrade is validated.
- [ ] **Add a strict CSP to `src/ui/index.html`.**
- [ ] **Extend path validation across all handlers.** `ingest.py`'s `preflight()` path validation is not applied to the `classify`, `build_timeline`, `classify_confirm`, and `classify_inspect` handlers in `src/workers/bridge.py` — extend it so every handler validates untrusted paths.

---

## Resolved

_No entries yet._
