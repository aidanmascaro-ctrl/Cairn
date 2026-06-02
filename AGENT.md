# AGENT.md

Self-discovered build, run, and Resolve quirk learnings. Kept brief. Updated as new commands, pitfalls, or environment-specific gotchas are found — so future sessions don't re-learn them.

## Format

Short entries grouped by area. No essays. If something needs a paragraph of context, put it in the relevant `specs/*.md` and link from here.

---

## Build / Run

- **Install deps:** `npm install` (first run takes ~3 min on first setup — Electron is a large download).
- **Build UI:** `npm run build:ui` → emits `dist/index.html` + `dist/assets/*.js`.
- **Typecheck:** `npm run typecheck`.
- **Package type mismatch** — `package.json` is `"type": "commonjs"` so Electron `main.js` can use `require`. Vite config + ESM-only plugins live in `.mts` (`vite.config.mts`). Do not flip the root to `"type": "module"` — it will break the Electron main.
- **Svelte preprocess** — configured inline inside `vite.config.mts` (no separate `svelte.config.js`). Adding one requires `.mjs` and a `type: module` trick; not worth it.

## Resolve Workflow Integration

- **Sandboxed model is the way forward.** Reference: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/Examples/SamplePromisePlugin/`. Uses `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` with a preload + `contextBridge`.
- **`WorkflowIntegration.node` lives _inside_ the plugin folder** — main.js does `require('./WorkflowIntegration.node')`. For dev, the install script copies it from Blackmagic's SamplePromisePlugin install path.
- **API style:** async promises. `WorkflowIntegration.InitializePromise(pluginId)` → `WorkflowIntegration.GetResolvePromise()` → `resolve.GetProjectManager()` → etc. All methods return promises.
- **Manifest schema:** `<BlackmagicDesign><Plugin><Id/><Name/><Version/><Description/><FilePath/></Plugin></BlackmagicDesign>`. `FilePath` is relative to the plugin folder root.
- **Install location (macOS) — system path only:** Resolve only scans `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/`. The `~/Library/...` user-level path is NOT scanned (confirmed by absence from menu after user-level install). Use folder display name e.g. `Cairn/`, not plugin ID — matches the Snap Captions convention observed on this machine.
- **Dev install script:** `packaging/install-dev-plugin.sh`. Symlinks (default) or copies (`--copy`) plugin files into the user plugin folder. Run after every UI source change or use `npm run dev` against a Vite dev server + `--no-build`.
- **Studio vs Free:** the Developer/Examples folder ships only with DaVinci Resolve Studio. Free-tier users cannot run Workflow Integration plugins — this is a Resolve limitation, not ours. (Matches spec.md: Studio only.)
- **JS WorkflowIntegration API limitations (confirmed):** The JS API only exposes `GetProjectManager`, `GetCurrentProject`, `GetName`, `GetCurrentTimeline`, `GetMediaPool`, and `ImportMedia(filePaths)`. It does NOT expose `GetRootFolder`, `AddSubFolder`, `GetSubFolderList`, or `SetCurrentFolder` — those are Python Scripting API only.
- **`ImportMedia([folder_path])` imports flat (no bins):** both JS and Python `mp.ImportMedia(["/path/to/folder"])` imports files into the current folder without creating sub-bins. To create proper bin hierarchy, you must use `AddSubFolder` + `SetCurrentFolder` + `ImportMedia(file_paths_only)` — one import call per bin. See `src/workers/resolve_import.py` (LEGACY one-shot fallback, slated for removal — the live import path is now `ingest.py` + `resolve_api.py`; keep these bin-creation learnings, they apply to both).
- **Python Scripting API for bin creation:** Python API requires "Preferences → General → External scripting: Local" to be enabled. Spawn as a subprocess from Node via `resolve_import.py`; kill with `SIGKILL` after 15 s timeout if it hangs.
- **`AddSubFolder` is on MediaPool, NOT MediaPoolFolder:** Use `mp.AddSubFolder(parentFolder, name)`. Calling `folder.AddSubFolder(name)` returns `None` (attribute doesn't exist on folder objects) and throws `TypeError: 'NoneType' object is not callable`.
- **External drives + rglob:** `folder.rglob("*")` and `send2trash` can hang indefinitely on large external SSDs (Samsung Evo 4TB confirmed). All Python bridge calls are wrapped in a 45 s `Promise.race` timeout in `main.js`.
- **`mp.AddSubFolder(parent, name)` is on MediaPool, not the folder.** The `folder.AddSubFolder(name)` form returns None / errors; the `MediaPool.AddSubFolder(parent_folder, name)` two-arg form is the working signature in current Resolve versions. Documented in `resolve_import.py::_find_or_create`.
- **Stage 4 timeline assembly (`timeline.py`):** `MediaPool.CreateEmptyTimeline(name)` makes the new timeline current automatically; `mp.AppendToTimeline(clips)` lands video on V1, audio on A1. To put B-roll on V2/A2: call `timeline.AddTrack("video")` + `AddTrack("audio")` to create the destination tracks, then `timeline.SetTrackLock("video", 1, True)` + `SetTrackLock("audio", 1, True)` so the next AppendToTimeline routes to V2/A2. Unlock V1/A1 afterwards. Both AddTrack and SetTrackLock are wrapped in `contextlib.suppress` since older Resolve versions may not expose them.
- **Project framerate is global** — `project.SetSetting("timelineFrameRate", "23.976")` works only if no timeline yet exists at a different fps. After the first timeline, the project rate is locked. `timeline.py` records a warning when SetSetting returns False and proceeds with whatever fps the project has.

## Python workers

- **Current deps (`src/workers/requirements.txt`):** `send2trash`, `opencv-python-headless`, `numpy`. That's it. Classification is 100% local (OpenCV Haar cascade for faces + numpy frame-diff for motion). NO `silero-vad`, `faster-whisper`, `assemblyai`, or `anthropic` at runtime today — those are DEFERRED (post-v1) deps for the stripped transcribe/vision/soundbite stages (see `docs/archive/`).
- **ffmpeg / ffprobe are VENDORED** at `assets/bin/` — do not rely on system `ffmpeg`. Workers shell out to the vendored binaries for frame sampling and probe.
- **System Python:** 3.14.3 at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`. CLAUDE.md target is 3.11+; 3.14 is ahead. The three current deps publish 3.14 wheels fine. If a DEFERRED dep (e.g. `silero-vad`/`faster-whisper` and their ONNX/ctranslate2 stacks) is reintroduced later, re-check wheel availability for 3.14 and flag in `fix_plan.md` — may need pinning to 3.12 via `pyenv`.
- _Populate further once `src/workers/` has its first module under `pyrefly check` + `ruff`._

## Packaging

- **Mac DMG**: `bash packaging/build-mac.sh` — stages plugin folder, codesigns binaries with hardened runtime + `packaging/entitlements.mac.plist`, creates drag-install DMG (Cairn → symlink to Resolve plugin dir), notarizes via `xcrun notarytool`. Requires `APPLE_DEVELOPER_ID`, `APPLE_TEAM_ID`, `APPLE_ID`, `APPLE_APP_PASSWORD` env vars. Set `CAIRN_SKIP_NOTARIZE=1` for local builds without certs.
- **Windows NSIS**: `npm run package:win` — electron-builder with `packaging/electron-builder.json5`. Installs to `%ProgramData%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\Cairn`. Uses `packaging/installer.nsh` to override `$INSTDIR`. Requires `WIN_CERT_PATH` + `WIN_CERT_PASSWORD`.
- **Python bundle**: `npm run package:python` — runs PyInstaller against `packaging/workers.spec`, outputs `packaging/dist/workers` (Mac) or `packaging/dist/workers.exe` (Win). Must run in the same Python env that has the current deps (`send2trash`, `opencv-python-headless`, `numpy`) + `pyinstaller`. (DEFERRED post-v1: when transcribe/vision are reintroduced, this env will also need `assemblyai`/`anthropic`/`faster-whisper` etc.)
- **Runtime path resolution**: `getWorkersPath()` in `main.js` checks for `resources/workers[.exe]` (production) then `packaging/dist/workers[.exe]` (dev build). Falls back to Python script + system Python if neither is present.
- **Entitlements**: `cs.disable-library-validation` required for PyInstaller's bundled third-party dylibs (e.g. OpenCV). Apple-approved for Python-based bundles. (DEFERRED post-v1: `cs.allow-unsigned-executable-memory` was needed for ONNX Runtime JIT / ctranslate2 from the stripped Silero VAD + faster-whisper stages — re-add only if those are reintroduced.)
- **SetCurrentTimecode for click-to-jump**: Uses source timecode (`in_tc` from sidecar) — best-effort; works for cameras that start at 00:00:00:00. Cameras with absolute timecode may not seek exactly. Documented as v1 limitation.

---

## DaVinci Resolve MCP (dev sessions only)

- **What it is:** `mcp__resolve__*` tools are available to Claude Code during dev sessions (this replaces the older `mcp__davinci-resolve__*` server). They connect to Resolve via the same Python Scripting API the plugin uses (requires External Scripting enabled). They are **NOT available to the Cairn Electron plugin at runtime** — the plugin uses `resolve_api.py` (and the legacy `resolve_import.py` fallback, slated for removal).
- **Why it matters:** Live Resolve verification without user screenshots. Test bin creation, clip colours, timeline operations, marker writes, and media pool state directly during the session.
- **Confirmed working (2026-04-29):** Nested bin creation under `Master/shoot/01_VIDEOS` works correctly via the old `mcp__davinci-resolve__*` server. Migrate the verification calls to the new flat-namespace `mcp__resolve__*` tools on next live test.
- **External Scripting confirmed enabled** on this machine — MCP connects successfully → `resolve_import.py` subprocess will also connect successfully.
- **Key tools (new flat namespace):**
  - Connection: `resolve_get_version`, `resolve_get_product_name`
  - Bins / media pool: `resolve_create_bin`, `resolve_list_bins`, `resolve_import_media`, `resolve_import_folder_to_media_pool`, `resolve_delete_bins`, `resolve_search_clips`
  - Clip metadata + colour: `resolve_get_clip_info`, `resolve_set_clip_color`, `resolve_set_clip_metadata`, `resolve_inspect_item`
  - Timeline: `resolve_list_timelines`, `resolve_create_empty_timeline`, `resolve_add_track`, `resolve_set_track_locked`, `resolve_get_timeline_info`, `resolve_list_clips_on_track`
  - Markers (DEFERRED post-v1 — soundbite markers stripped in scope reset 966ce94; see `docs/archive/`): `resolve_add_marker_at`, `resolve_list_markers`, `resolve_delete_marker_at`
  - Project: `resolve_get_project_settings`, `resolve_load_project`, `resolve_save_project`
- **Test pattern:** Create test bins under `Master/_cairn_test`, verify, delete with `resolve_delete_bins([id])`. Never import media into a user's live project during testing.

## Platform-specific gotchas

_Add here when a command behaves differently on macOS vs Windows, or when a Resolve API returns unexpected values on one OS._

- **Resolve Developer examples path** is `Library/Application Support/Blackmagic Design/...` at the system level on macOS. Windows equivalent (per Blackmagic docs) is `%ProgramData%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\` — verify on first Windows bring-up.
