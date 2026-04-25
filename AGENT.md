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
- **`ImportMedia([folder_path])` imports flat (no bins):** both JS and Python `mp.ImportMedia(["/path/to/folder"])` imports files into the current folder without creating sub-bins. To create proper bin hierarchy, you must use `AddSubFolder` + `SetCurrentFolder` + `ImportMedia(file_paths_only)` — one import call per bin. See `src/workers/resolve_import.py`.
- **Python Scripting API for bin creation:** Python API requires "Preferences → General → External scripting: Local" to be enabled. Spawn as a subprocess from Node via `resolve_import.py`; kill with `SIGKILL` after 15 s timeout if it hangs.
- **`AddSubFolder` is on MediaPool, NOT MediaPoolFolder:** Use `mp.AddSubFolder(parentFolder, name)`. Calling `folder.AddSubFolder(name)` returns `None` (attribute doesn't exist on folder objects) and throws `TypeError: 'NoneType' object is not callable`.
- **External drives + rglob:** `folder.rglob("*")` and `send2trash` can hang indefinitely on large external SSDs (Samsung Evo 4TB confirmed). All Python bridge calls are wrapped in a 45 s `Promise.race` timeout in `main.js`.

## Python workers

- **System Python:** 3.14.3 at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`. CLAUDE.md target is 3.11+; 3.14 is ahead. Flag in `fix_plan.md` if `silero-vad`, `faster-whisper`, or `pyrefly` wheels aren't published for 3.14 — we may need to pin to 3.12 via `pyenv` or ship our own runtime via PyInstaller.
- _Populate further once `src/workers/` has its first module under `pyrefly check` + `ruff`._

## Packaging

_TBD — Session 10._

---

## Platform-specific gotchas

_Add here when a command behaves differently on macOS vs Windows, or when a Resolve API returns unexpected values on one OS._

- **Resolve Developer examples path** is `Library/Application Support/Blackmagic Design/...` at the system level on macOS. Windows equivalent (per Blackmagic docs) is `%ProgramData%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\` — verify on first Windows bring-up.
