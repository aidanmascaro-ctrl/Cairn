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

- **Status:** MEDIUM items resolved 2026-10-27; LOW items open
- **Opened:** 2026-10-27
- **Area:** `src/workers/selects*.py`, `src/ui/selects/`, `src/workers/bridge.py`
- **Symptom / gap:** found by an independent review before the first Selects commit; none fixed yet.
  - MEDIUM — a build that places nothing reports success: `selects_timeline.py` ~456-487 returns `ok: True` when `AppendToTimeline` gives `[]`/`None`, and `select_count`/`piece_count` come from the plan, not the read-back (spec §5 says read-back).
  - MEDIUM — camera-motion thresholds look ~4x too sensitive: metrics now store pan/tilt/zoom/twist per second, but `CAMERA_AXIS_SCALE` / `CAMERA_STATIC_THRESHOLD` (`selects_score.py` ~80) are still commented and sized per-sample. Confirm the intended unit.
  - MEDIUM — corrupt saved-tuning warning is dropped on the live path: `bridge.py` `_settings()` uses `load_settings()` and discards the warning from `load_settings_with_warning()`.
  - MEDIUM — rating keys disagree between Python `f"{x:.2f}"` and JS `toFixed(2)` on exact .125/.375/.625/.875 ties, so those segments show as unrated in `SelectsClipList`.
  - LOW — `specs/selects.md` §2 table still lists the old formulas/thresholds; fps only warned, not set, when the project already has timelines; video-only clips raise a false "not linked to audio" warning; a failed build leaves its clip markers (and an empty timeline if `_create_timeline` throws); fixed 180 s build timeout in `main.js`.
- **Resolved (MEDIUM):**
  - Build success now comes from the read-back: if Resolve places no video the build fails and the empty timeline is removed; `select_count` / `piece_count` are counted on the timeline, and markers go only on selects that landed.
  - Camera axes use one per-second `CAMERA_AXIS_SCALE` in `selects_score.py`, which `selects_metrics` now imports for `camera_stillness` — the units can't drift apart again. Stored measurements are unchanged (same numbers), so nothing needs re-analysing.
  - `bridge.py` passes no settings when the panel sends none, so `rescore` loads the saved tuning itself and returns its warning; builds, learn context and evaluate carry it too.
  - Every segment carries a `key` made by Python (`selects_score.rating_key`); the UI uses it instead of formatting with `toFixed`.
- **Next step:** the LOW items above.

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

- **Status:** resolved 2026-10-27 — `dist/` chowned back to the user, rebuilt, plugin reinstalled in symlink mode (source edits now only need a Resolve restart)
- **Opened:** Session 2 / 2026-04-25
- **Area:** packaging / `dist/` — built UI assets
- **Symptom / gap:** `dist/` was made root-owned by a prior `sudo`-level install run. All ingest fixes (reclassification, empty-shell removal, bin structure) were written against the source files but `dist/` has never been rebuilt with them. The live plugin is running stale compiled output.
- **Next step:** `sudo chown -R $(whoami) dist && npm run build && sudo bash packaging/install-dev-plugin.sh`, restart Resolve, re-run ingest to verify all three open items below.

### Bin structure in Resolve media pool unverified

- **Status:** resolved 2026-10-27 (Offload layout) — live ingest from inside the plugin of `Lexar 4TB/NORTHWIND MEDIA/261015 2 harvest dinner` built `Master/Cairn/Footage/FX30` (22) and `.../vid` (45), matching the clips on disk exactly. So the bridge's scripting connection works from the plugin sandbox. The old `01_VIDEOS/...` layout (non-Offload folders) is still untested live.
- **Opened:** Session 2 / 2026-04-25
- **Area:** `src/workers/resolve_import.py`
- **Symptom / gap:** `resolve_import.py` was updated to use `_find_or_create()` per-bin approach (creating `<shoot>/01_VIDEOS/A_CAM`, `/C_CAM`, `/02_PHOTOS` bins in Resolve Master), but this was not tested live before the session ended. Prior versions imported clips flat into Master.
- **MCP verified 2026-04-29:** `mp.AddSubFolder(parent, name)` (two-arg form on MediaPool) and `mp.SetCurrentFolder` both confirmed working via MCP test on Resolve Studio 21.0.0b.20. Nested path `Master/_cairn_test/01_VIDEOS/A_CAM` created successfully and cleaned up.
- **MCP re-verified 2026-06-07 on Resolve Studio 21.0.0.48:** ran the _real_ `resolve_api.find_or_create_bin` + `SetCurrentFolder` against the live media pool under `Master/_cairn_test` — bins created, idempotent (no duplicate bins on re-call), `SetCurrentFolder` truthy, fully cleaned up. The proper import path now used is `bridge.py handle_ingest → resolve_api.ingest_into_resolve` (builds `Master/Cairn/01_VIDEOS/...`). See the Resolved entry below.
- **Next step (only remaining unknown):** confirm the bridge worker's Python scripting connection holds _from inside the Electron plugin sandbox_ — run ingest live in the plugin and check the `cairn:health` `scripting_ok` flag. If it returns false in-plugin, the bridge cannot reach Resolve from a plugin child and we need to solve that one boundary (env propagation), but the import logic itself is verified.

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

## Security follow-ups (repo went public 2026-11-06)

- [ ] **Plan an Electron upgrade.** Electron 30 is out of support and `npm audit` flags it. It is a dev dependency here, but the jump to 45 is breaking and must be validated against Resolve's plugin host. `electron-builder` needs a major bump too.
- [ ] **Clear the remaining dev-tool audit findings.** Production dependencies are clean; 36 findings remain in dev tooling (`vite` has no direct fix yet).
- [ ] **Re-enable ASAR with integrity** in `packaging/electron-builder.json5` once Electron is upgraded.
- [ ] **Add a strict CSP to `src/ui/index.html`.**
- [ ] **Extend path validation.** Apply `ingest.py`'s `preflight()` checks to the classify and build-timeline handlers.

---

## Resolved

### DaVinci import did nothing / showed an error after ingest

- **Status:** resolved (code) — pending one live in-plugin confirmation (see bin-structure item above)
- **Opened / fixed:** Session / 2026-06-07
- **Area:** `src/orchestrator/main.js` (`cairn:ingest` handler), `src/workers/resolve_import.py`
- **Root cause:** main.js disabled the working import path and used a broken fallback. It sent `resolve: false` to the bridge, so the proper bin-tree import (`bridge.py handle_ingest → resolve_api.ingest_into_resolve`) never ran. It then always fell through to `importFolderToResolve`, which spawned `resolve_import.py` as a **second OS process opening a second Resolve Scripting connection** — a Taste Invariant #3 violation (the scripting API is not process-safe). Inside the Electron plugin sandbox that second connection hangs/errors; the only thing that ever ran was a flat, bin-less JS `ImportMedia` (or nothing). A plain-Terminal subprocess connects fine (verified under Python 3.14), which is why the failure is sandbox/concurrency-specific, not a Python-version or scripting-disabled problem.
- **Fix:** route the whole import through the single bridge worker — `main.js` now passes `resolve: true` and lets `handle_ingest` do org + the proper Cairn bin-tree import in one streamed call. Deleted `importFolderToResolve` / `importViaResolveScript` (the second-process spawn) and the dead `scanDir`/`VIDEO_EXTS`/`PHOTO_EXTS`. Handler now throws on a non-`ingest_complete` terminal message so a bridge error surfaces in the UI instead of a hollow "done". `resolve_import.py` tombstoned (inert) so the wrong pattern can't be re-introduced.
- **Verification:** new `tests/python/test_resolve_api.py` (bin tree, routing, dedup, JPEG-stills, no-project guard) — 5 pass; full suite 119 pass; eslint/tsc/vitest/ruff/pyrefly clean; live MCP test of the real `resolve_api` bin calls on Resolve 21.0.0.48.

## Offload handoff (Cairn side) — open items · 2026-10-26

Offload itself moved to its own repo on 2026-11-10 (its open items went to its `fix_plan.md`). The contract between the two is `specs/offload-handoff.md`.

- [x] **Cairn side of the hand-off** — DropZone lists "Ready from Offload" (polls `cairn:offload:inbox` every 10s + on focus); Sort runs `cairn:ingest` on the shoot and `cairn:offload:done` drops the entry.
- [x] **Layout clash** — `ingest.run()` detects `<shoot>/.cairn/offload.json` and imports **in place** (no rename/moves). Resolve bins: `Cairn/Footage/<Camera>` per camera folder + `Cairn/Photos` (`resolve_api.ingest_offload_into_resolve`). Clip sidecars go in `<shoot>/.cairn/` (`sidecar.sidecar_root`); classify scans `Footage/*/`. Tests: `tests/python/test_offload_ingest.py`.
- [x] **Offload Resolve import live-tested** (2026-10-26, Resolve 21.1.0.14): 2 real clips → `Master/Cairn/Footage/FX3` + `.../A7S3`, 1 clip each; re-run imported 0 (dedup OK). Test bins deleted after.
- [x] **Clip metadata silently dropped (all ingests, not just Offload).** Fixed 2026-10-27. Ingest now writes `Camera Type` / `Date Recorded` / `Lens Type` (Resolve fields), and Cairn's own keys (`Cairn Stage`, `Cairn Classification`) go through `SetThirdPartyMetadata` — `SetMetadata` refuses any non-Resolve field name, so classify's A/B label was being dropped too. `set_clip_metadata` returns the refused keys; the bridge streams a `metadata_refused` event shown as ⚠ in the ingest log. Offload bins get their camera folder name as Camera Type. Verified live on Resolve 21.1 with the real function. Re-ingest also fills **blank** fields on clips already in the bin (`_import_and_tag`), never overwriting an editor's value or a later Cairn Stage; backfilled the 67 clips of `261015 2 harvest dinner` live (22 FX30 + 45 vid, 0 re-imported).
- [ ] **Timeline build on Offload shoots** — `timeline.py` untested with the Footage/<Camera> layout.
