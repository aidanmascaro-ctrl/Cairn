# specs/ingest.md — Stage 1: Ingest

## Purpose

Take a user-dropped folder and end with:

1. A filesystem laid out according to the template (`01_VIDEOS/A_CAM`, `01_VIDEOS/B_CAM`, `02_PHOTOS`, `03_ASSETS`, `04_EXPORT`)
2. A Resolve project with bins mirroring that structure
3. Every clip imported into the correct bin, with extracted Sony XML metadata (lens, camera, timecode) attached

No AI, no transcription, no classification. This stage proves the plugin can reliably talk to Resolve and handle real footage without destroying it.

## Inputs

**Primary:** one filesystem path dropped onto the plugin's drop zone — a folder or a mounted drive.

**Routing triggers** (from `organise.py`):

- **Mode A — Raw Sony card dump**: contains `DCIM/` or `M4ROOT/`. Detected by `is_sony_card()`.
- **Mode B — Already-organised**: contains `01_VIDEOS/` or `02_PHOTOS/`. Detected by `already_organised()`.
- **Mode C — Loose or mixed**: random media files, no template. Detected by _absence_ of the above + presence of files with extensions in `VIDEO_EXTS ∪ PHOTO_EXTS`.

**Secondary signals:**

- Optional `--name` override (passed from the UI drop-zone dialog)
- Sony `MEDIAPRO.XML` or sibling XML for date + camera metadata
- User setting: "Generate proxies on import" (off by default)

**Forbidden inputs:**

- Source paths inside `/System/` or Windows system paths — refuse with clear error
- Drives mounted read-only — detect early, refuse before moving
- Folders with active Resolve locks (`<project>.drp.lock`) — surface a "Resolve has this project open" error

## Outputs

**On disk:**

- Folder renamed to `YYMMDD <shoot name>` if not already date-prefixed
- Template subfolders created: `01_VIDEOS/A_CAM`, `01_VIDEOS/B_CAM`, `02_PHOTOS`, `03_ASSETS`, `04_EXPORT`
- Files _moved_ (never deleted, never copied + deleted) to the right subfolder
- Empty Sony shell folders (`DCIM`, `M4ROOT`, `PRIVATE`, etc.) removed when empty
- `04_EXPORT/.cairn/` hidden folder created, empty, ready for sidecars

**In Resolve:**

- Project opened (or created) with name matching the shoot folder name
- Media Pool bin tree:
  - `Cairn/`
    - `01_VIDEOS/A_CAM/` (clips)
    - `01_VIDEOS/B_CAM/` (empty; reserved)
    - `02_PHOTOS/` (clips)
    - `03_ASSETS/` (clips)
- Each imported clip has extended metadata set via `SetMetadata`:
  - `Camera` (e.g. "Sony α7S III")
  - `Lens` (from XML)
  - `Timecode Start` (from XML)
  - `Shoot Date` (from folder prefix)
  - `Cairn Stage` = `"ingested"`
- If the proxy toggle is on: Resolve proxy generation kicked off (Optimised Media at ¼ res, ProRes 422 Proxy preset)

**Sidecars:**

- One `<project>/04_EXPORT/.cairn/<clip-id>.json` per clip, with:
  ```json
  {
    "schema_version": 1,
    "clip_id": "<hash of absolute path at ingest time>",
    "source_path": "<absolute path>",
    "shoot_folder": "<absolute path of the shoot root>",
    "bin_path": "01_VIDEOS/A_CAM",
    "kind": "video" | "photo" | "asset",
    "metadata": {
      "camera": "…",
      "lens": "…",
      "timecode_start": "…",
      "shoot_date": "251125"
    },
    "stages_complete": ["ingest"]
  }
  ```

## Algorithm

### 1. Pre-flight

- Resolve Scripting API: confirm project open, confirm write access via `GetMediaPool()`.
- Dropped path: resolve to absolute; verify exists, writable, not a system path.
- Determine mode via `is_sony_card()`, `already_organised()`, or fall-through to Mode C.

### 2. Organise (port of `organise.py`)

Direct port, keeping the following invariants from the original script:

- `VIDEO_EXTS`, `PHOTO_EXTS`, `ASSET_EXTS`, `ASSET_NAMES` — sets verbatim.
- `SONY_SHELLS` — Sony folder names to remove when empty.
- `TEMPLATE_DIRS` — subdir list to create.
- Rename logic: if folder name doesn't start with 6 digits, prepend date read from Sony XML (falls back to today). `--name` override applies in both "no date" and "has date" cases.
- File collection: `collect_files()` walks the tree, bucketing by extension + known location (`CLIP/` XMLs stay with videos, `THMBNL/` to assets).
- Move: `shutil.move` with collision-safe renaming (`name_1.ext`, `name_2.ext`). **Never `shutil.copy` + delete** — move is atomic where possible.
- Empty-shell cleanup: `remove_empty_shells()` runs last.

**Extensions for Mode C (loose folder):** same bucket logic; template created from scratch; files moved in.

**Extension for the plugin**: emit a structured progress report (JSON per file moved) over stdout so the Node orchestrator can drive the UI progress bar.

### 3. XML metadata extraction

Extend the existing `read_sony_date()` logic into a full metadata extractor:

```python
{
  "camera": "<from //Device @modelName>",
  "lens": "<from //LensUnitMetadata @make + @model>",
  "timecode_start": "<from //LtcChangeTable/Ltc @value first frame>",
  "shoot_date": "<YYMMDD>"
}
```

Falls back cleanly when fields are missing (not every XML has lens metadata on older Sony bodies).

### 4. Resolve bin + clip import

Through `resolve_api.py` (single worker, serial):

```python
pm = resolve.GetProjectManager()
project = pm.GetCurrentProject() or pm.CreateProject(shoot_name)
mp = project.GetMediaPool()
root = mp.GetRootFolder()

# Create bin tree
cairn = find_or_create_bin(root, "Cairn")
videos = find_or_create_bin(cairn, "01_VIDEOS")
a_cam = find_or_create_bin(videos, "A_CAM")
# ... etc.

# Import clips
clips_a = mp.ImportMedia([str(p) for p in a_cam_files])
for clip, meta in zip(clips_a, metadata_map):
    clip.SetMetadata("Camera", meta["camera"])
    clip.SetMetadata("Lens", meta["lens"])
    clip.SetMetadata("Cairn Stage", "ingested")
```

Bin creation is idempotent — re-running against an already-organised folder finds existing bins instead of creating duplicates.

### 5. Sidecar write

For every imported clip, write `<project>/04_EXPORT/.cairn/<clip-id>.json` **before** returning success. Sidecar-first is the invariant (per `overview.md`).

`clip-id` = `sha256(absolute_path_at_ingest_time)[:16]`. Stable across re-ingests because the path doesn't change once organised.

### 6. Proxy generation (optional)

If setting enabled, call `project.SetSetting("videoMonitorFormat", …)` appropriately and kick off `Project.GenerateOptimizedMedia(clips)`. This runs async in Resolve; the plugin doesn't block on it.

## Sensors / Validations

- **Read-back after every bin and clip operation.** `GetMediaPool().GetRootFolder()` → walk the tree → confirm the bin we just created exists and the clip count matches.
- **Path safety check** before any move: refuse if destination is outside the original shoot folder's parent.
- **Dry-run mode** for tests: `--dry-run` flag prevents moves and writes, prints the plan.
- **No-op idempotency test**: running ingest twice on the same folder produces identical sidecars and does not re-import clips. Part of `tests/python/test_ingest.py`.
- **pyrefly** must pass on `src/workers/ingest.py`.
- **ruff**: strict; no TODOs.

### Taste-invariant cross-checks

- #1 (never modify source media): ingest uses `shutil.move` only. Verified by pytest that asserts no `os.remove`, `shutil.copy`, or ffmpeg invocations in the module.
- #2 (never delete user clips): `remove_empty_shells` only removes directories that pass the whitelist + pass `rmdir` (fails if non-empty). Verified by test with a poisoned file inside a "DCIM" folder.
- #3 (single Resolve process): `resolve_api.py` owns a lock; any call to a Resolve API method from another worker raises.
- #6 (sidecar before Resolve): unit test verifies sidecar exists on disk before the Resolve import call returns.

## Open questions

- **Already-imported clips in Resolve** — if the user re-ingests a folder already linked in Resolve, do we skip, re-link, or error? Initial plan: skip (detect via `GetMediaPoolItems()` path match). Confirm with the user.
- **Windows path handling** — `shutil.move` across drive letters is not atomic. Does this matter for user-perceived safety? Document in `AGENT.md` once tested on Windows.
- **Proxy format choice** — Resolve offers Optimised Media and true Proxy Media separately. Optimised Media is simpler; Proxy Media is what editors typically want on slow drives. Defer decision until first editor feedback.
- **Clip-id stability under rename** — if the user renames the shoot folder after ingest, sidecars still reference the old path. Solution: store `clip-id` based on filename + size + mtime instead of absolute path. Revisit if this becomes a problem.
- **What if the user drops an already-organised folder from another tool** (e.g. Kyno)? Current plan: trust their layout if it matches the template; otherwise Mode C. Confirm with the user on real Kyno output.
