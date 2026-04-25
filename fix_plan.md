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

- **Status:** open
- **Opened:** Session 2 / 2026-04-25
- **Area:** `src/workers/resolve_import.py`
- **Symptom / gap:** `resolve_import.py` was updated to use `_find_or_create()` per-bin approach (creating `<shoot>/01_VIDEOS/A_CAM`, `/C_CAM`, `/02_PHOTOS` bins in Resolve Master), but this was not tested live before the session ended. Prior versions imported clips flat into Master.
- **Next step:** After rebuild above, run ingest on an already-organised folder; verify Resolve media pool shows the nested bin structure, not flat clips in Master.

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
