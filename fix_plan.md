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

### Selects: live Claude learning call not yet run

- **Status:** open
- **Opened:** Session 12 / 2026-10-27
- **Area:** `src/orchestrator/selects-learn.js`
- **Symptom / gap:** The loop is unit-tested with a fake client, but the one live attempt failed authentication: the `ANTHROPIC_API_KEY` in the shell environment is rejected (401). The real request shape (structured output + `fallbacks: "default"`) has not been confirmed against the API.
- **Next step:** Enter a valid key in Cairn Settings, rate 6+ selects, press Learn my taste, and confirm a proposal comes back.

### Selects: can't re-tune while analysis is running

- **Status:** open (by design for now)
- **Opened:** Session 12 / 2026-10-27
- **Area:** `src/workers/bridge.py` main loop
- **Symptom / gap:** The single Python worker runs a streaming handler to completion before reading the next request, so re-score calls wait until analysis finishes. The panel disables tuning while analysing.
- **Next step:** If editors want to tune mid-analysis, run analysis in a worker thread inside the bridge and keep the Resolve lock rules intact.

### Settings hands raw API keys to the renderer (pre-existing)

- **Status:** open
- **Opened:** Session 12 / 2026-10-27 (found by review; predates the Selects stage)
- **Area:** `src/orchestrator/main.js` `cairn:keys:get`, `src/ui/Settings.svelte`
- **Symptom / gap:** `keys:get` returns the decrypted Anthropic and AssemblyAI keys to the web page. The Selects learning call itself keeps the key in the main process.
- **Next step:** Return `{ hasAnthropic, hasAssemblyai }` only and make `keys:set` write-only; Settings shows "saved" instead of the key.

### Selects: code review findings (2026-10-27, pre-commit review)

- **Status:** open
- **Opened:** 2026-10-27
- **Area:** `src/workers/selects*.py`, `src/ui/selects/`, `src/workers/bridge.py`
- **Symptom / gap:** found by an independent review before the first Selects commit; none fixed yet.
  - MEDIUM — a build that places nothing reports success: `selects_timeline.py` ~456-487 returns `ok: True` when `AppendToTimeline` gives `[]`/`None`, and `select_count`/`piece_count` come from the plan, not the read-back (spec §5 says read-back).
  - MEDIUM — camera-motion thresholds look ~4x too sensitive: metrics now store pan/tilt/zoom/twist per second, but `CAMERA_AXIS_SCALE` / `CAMERA_STATIC_THRESHOLD` (`selects_score.py` ~80) are still commented and sized per-sample. Confirm the intended unit.
  - MEDIUM — corrupt saved-tuning warning is dropped on the live path: `bridge.py` `_settings()` uses `load_settings()` and discards the warning from `load_settings_with_warning()`.
  - MEDIUM — rating keys disagree between Python `f"{x:.2f}"` and JS `toFixed(2)` on exact .125/.375/.625/.875 ties, so those segments show as unrated in `SelectsClipList`.
  - LOW — `specs/selects.md` §2 table still lists the old formulas/thresholds; fps only warned, not set, when the project already has timelines; video-only clips raise a false "not linked to audio" warning; a failed build leaves its clip markers (and an empty timeline if `_create_timeline` throws); fixed 180 s build timeout in `main.js`.
- **Next step:** fix the four MEDIUM items before relying on Selects, starting with the build-success and motion-unit ones.

### Review notes outside Selects (2026-10-27, LOW)

- **Status:** open
- **Opened:** 2026-10-27
- **Area:** various
- **Symptom / gap:**
  - `keys:get` (`main.js` ~502) returns plaintext API keys to the renderer; return a "set" flag / masked value instead. Storage uses `safeStorage`, not the `keytar` CLAUDE.md names.
  - Selects bridge accepts any directory from the renderer (`bridge.py` `_shoot()` only checks `is_dir()`); limit to folders the user picked or the Offload inbox.
  - `ingest.py` `run_offload` falls back to today's date when the manifest date is missing — a low-confidence value written to Date Recorded; leave it blank instead.
  - Offload camera-folder counts are spread into the progress event (`**resolve_counts`); a folder named `type`/`event`/`total` would clash. Nest them.
  - Leftover hard-coded colours after the restyle: `ClassifyPanel .review-item`, `DropZone .pick-btn`, `.offload-inbox`/`.sort-btn` blues.
  - `packaging/build-mac.sh` silently skips a missing node module (`if [[ -d … ]]`); make it fail the build.
  - Offload: `DriveIndex.copies(of:)` fingerprints every same-size candidate (slower scans); Sort is disabled while any camera folder is unchecked, including Gyroflow-only folders.
  - Resolve media paths and sidecars go stale if Offload re-sorts a shoot after it was ingested (`Handoff.relocate` only fixes the manifest and inbox).
- **Next step:** pick off as convenient; none block current use.

### Selects review items deferred

- **Status:** open
- **Opened:** Session 12 / 2026-10-27
- **Area:** Selects stage
- **Symptom / gap:**
  - Bridge replies aren't checked against the Zod schemas on the live path (`main.js` is CommonJS and can't load `python-bridge.ts`); the renderer trusts the Python shapes, which the tests pin.
  - A stalled read from a network or card volume can block one ffmpeg worker with no watchdog.
  - A select containing any force-included sample is exempt from "most per clip" (matches Cutdown's documented behaviour).
  - Sliders reset to centre by double-click only; there is no `aria-valuetext`.
  - Security review (LOW): any existing directory is accepted as a shoot and gets `04_EXPORT/.cairn/selects.json` written into it. Check the path against folders the user picked in the main process.
- **Next step:** Revisit when packaging for other editors.

### One unexplained pytest failure (did not recur)

- **Status:** open (watch)
- **Opened:** Session 12 / 2026-10-27
- **Area:** `tests/python/test_resolve_api.py`
- **Symptom / gap:** One run reported 4 failures in `test_resolve_api.py` while 8 calibration processes were importing `src/workers` modules. Eleven later full runs (three under the same load) passed.
- **Next step:** If it recurs, capture the traceback; suspect concurrent `__pycache__` writes or a leaked `resolve_api._lock`.

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

## Resolved

_No entries yet._
