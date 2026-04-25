# IMPLEMENTATION PLAN — Session 1: Project Bootstrap

## Session Goal

Establish repository structure, harness documents, per-stage specs, and plugin skeleton so Session 2 can begin implementing Stage 1 (ingest) against a working Electron + Python bridge.

---

## How This Works (Ralph Loop)

Every session starts by loading this file. Work top-to-bottom. Mark tasks done as you go.
At session end: archive completed items, carry forward anything unfinished, regenerate if stale.

**Guides (feedforward):** CLAUDE.md → spec.md → specs/\*.md → this file
**Sensors (feedback):** TypeScript compiler, pyrefly, ESLint, ruff, Vitest, pytest, LLM-as-judge

---

## Tasks

### Task 1: Repo scaffolding

- [x] `git init` in project root
- [x] Create folder tree matching `Project Structure` in CLAUDE.md:
  - `src/ui/`, `src/orchestrator/`, `src/orchestrator/prompts/`, `src/workers/`
  - `specs/`, `tests/node/`, `tests/python/`
  - `assets/bin/`, `packaging/`
- [x] Place `CLAUDE.md`, `spec.md`, `IMPLEMENTATION_PLAN.md` at root
- [x] Create empty `fix_plan.md` with heading stub
- [x] Create empty `AGENT.md` with heading stub
- [x] Create `.gitignore` covering: `node_modules/`, `__pycache__/`, `.env*`, `dist/`, `build/`, `assets/bin/ffmpeg*` (binaries vendored but heavy)
- [x] Copy the user's `organise.py` to the project root as reference (will later port to `src/workers/ingest.py`)
- [x] Initial commit: `chore: harness scaffolding and docs`

### Task 2: Decompose spec.md into /specs/\*.md

The single `spec.md` is the product overview. Each pipeline stage needs its own detailed spec so Claude Code only loads what it needs per task. Each spec file must include: Purpose, Inputs, Outputs, Algorithm/Prompt, Sensors/Validations, Open Questions.

- [x] `specs/overview.md` — distilled from spec.md: visual system, distribution architecture, out-of-scope, architecture diagram
- [x] `specs/ingest.md` — three input modes, port of `organise.py`, Resolve bin creation, XML metadata extraction, proxy toggle
- [x] `specs/classify.md` — Silero VAD thresholds, clip colour mapping, ambiguous review panel
- [x] `specs/transcribe.md` — AssemblyAI request/response schemas, Whisper fallback, subtitle track format, JSON sidecar schema
- [x] `specs/vision.md` — frame extraction timing strategy (first, 25%, 50%, 75%, end), Claude vision prompt, structured tag output schema
- [x] `specs/brief.md` — conversation flow, question order, brief JSON schema, editable-brief mechanics
- [x] `specs/soundbites.md` — selection prompt with meta-reasoning block, marker colour mapping (green/red/blue/yellow), marker note format, in/out timecode precision
- [x] `specs/matching.md` — literal vs thematic distinction, prompt design, Keywords/Shot/Description write strategy, confidence thresholds
- [x] `specs/sidebar.md` — UI wireframe, story-map component, click-to-jump Resolve API call, B-roll preview panel
- [x] Cross-check: every stage in spec.md has a matching specs/\*.md file
- [x] Commit: `docs: decompose spec.md into per-stage specs`

### Task 3: Electron plugin skeleton

- [x] Clone `VilleOlof/Toolbox` to `/tmp/` as reference (do not vendor — study only)
- [x] Create `manifest.xml` with plugin ID `com.aidanmascaro.cairn`, display name `Cairn`, version `0.0.1`
- [x] Create `package.js` (Electron app entry) — implemented as `package.json` + `src/orchestrator/main.js` (standard Electron entry); manifest `FilePath` points to the main.js
- [x] Create `src/ui/index.html` with a blank Svelte shell
- [x] Install the sandboxed Electron model per Blackmagic's 2024+ recommendation — `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, bridged via `contextBridge.exposeInMainWorld('cairn', …)` in `src/orchestrator/preload.js`; uses `WorkflowIntegration.InitializePromise` (SamplePromisePlugin reference, not the CompatibleSamplePlugin legacy one)
- [x] Verify: plugin appears under `Workspace > Workflow Integrations > Cairn` — confirmed. Note: Resolve scans system-level path only; install at `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/Cairn/` (display-name folder, not plugin ID)
- [x] Proof-of-life: plugin window opens and renders — confirmed by user
- [x] Commit: `feat: plugin skeleton loads in Resolve`
- [x] Tag: `v0.0.1`

### Task 4: Node ↔ Python bridge

- [x] Create `src/orchestrator/python-bridge.ts` — spawns Python subprocess, JSON-per-line stdin/stdout protocol, message types typed with Zod schemas
- [x] Create `src/workers/bridge.py` — receives JSON messages, dispatches to handler by `type` field, responds with JSON
- [x] Implement an echo test: Node sends `{"type": "echo", "payload": "hello"}` → Python returns it with a timestamp → Node confirms roundtrip
- [x] Wire echo test to a temporary button in the plugin UI for visual confirmation
- [x] Test: `npm run test` includes a node-side integration test that spawns Python and exercises the echo (5/5 pass)
- [x] Commit: `feat: Node↔Python bridge with typed JSON protocol`

### Task 5: Install sensors

- [x] `tsconfig.json` with strict mode, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`
- [x] `eslint.config.mjs` + `.prettierrc` — no `any`, consistent imports, Svelte + TS flat config
- [x] `pyrefly` configured for `src/workers/` — 0 errors confirmed
- [x] `ruff.toml` configured — strict rule set, auto-fixed bridge.py
- [x] Husky pre-commit hook with `lint-staged` — ESLint+Prettier on JS/TS, ruff on Python
- [x] GitHub Actions CI stub at `.github/workflows/ci.yml`
- [x] Commit: `chore: install feedforward + feedback sensors`

### Task 6: Settings and keychain

- [x] Create minimal Settings panel in UI (Svelte component)
- [x] Two fields: `ANTHROPIC_API_KEY`, `ASSEMBLYAI_API_KEY` (both password-masked)
- [x] Wire to `keytar` — write on save, read on plugin start
- [x] Validate keys on save: test call to each API, show ✅ or ❌ next to field
- [x] Settings persist across plugin restarts
- [x] Commit: `feat: settings panel with keychain storage`

---

## Success Criteria for Session 1

By end of session, this must be true:

1. Repo is initialised, all harness docs in place
2. `specs/*.md` exist for all eight pipeline stages
3. Plugin loads in Resolve and can read the project name
4. Node↔Python bridge passes an echo test via CI
5. All sensors (typecheck, lint, tests) run green on `main`
6. User can enter API keys in Settings and they're validated + stored

Session 2 begins with: "Implement Stage 1 (ingest) per `specs/ingest.md`" — nothing else.

---

## Session 2: Stage 1 — Ingest

### Task 1: `src/workers/ingest.py`

- [x] Port `organise.py` verbatim (VIDEO_EXTS, PHOTO_EXTS, ASSET_EXTS, SONY_SHELLS, TEMPLATE_DIRS, SKIP_NAMES)
- [x] Mode detection: `is_sony_card()`, `already_organised()`, fall-through Mode C
- [x] Extended XML metadata extraction: camera, lens, timecode_start, shoot_date (beyond read_sony_date)
- [x] Progress events: emit `{type:progress,event:file_moved,data:{...}}` to stdout per file
- [x] Dry-run mode: `--dry-run` flag skips moves and writes, returns plan only
- [x] Pre-flight: verify path exists, writable, not system path, not read-only mount
- [x] Sidecar write: calls `sidecar.write_clip()` for every clip before Resolve import
- [x] Returns structured JSON result: renamed folder, files moved, metadata map, clip list

### Task 2: `src/workers/sidecar.py`

- [x] `write_clip(sidecar_root, clip_id, record)` — writes `<root>/<clip-id>.json` (schema_version 1)
- [x] `read_clip(sidecar_root, clip_id)` — reads and returns dict, None if missing
- [x] `clip_id(absolute_path)` = `sha256(str(path))[:16]`
- [x] Creates `04_EXPORT/.cairn/` if absent; never overwrites without merging stages_complete

### Task 3: `src/workers/resolve_api.py`

- [x] `get_resolve()` — imports DaVinciResolveScript, returns Resolve handle; graceful ImportError
- [x] `find_or_create_bin(folder, name)` — idempotent; checks existing subfolders first
- [x] `import_clips(media_pool, bin_folder, paths)` — calls `ImportMedia`, returns clip list
- [x] `set_clip_metadata(clip, meta_dict)` — calls `SetMetadata` for each key
- [x] Single-process lock: raises if called concurrently from multiple threads
- [x] Full bin tree creation: Cairn / 01_VIDEOS / A_CAM, B_CAM / 02_PHOTOS / 03_ASSETS

### Task 4: Extend bridge protocol for streaming progress

- [x] Add `progress` inbound message type to `python-bridge.ts` (InboundMessageSchema)
- [x] Add `stream(msg, onProgress)` method to `PythonBridge` — resolves on final message, calls callback for progress events
- [x] `bridge.py`: support streaming handlers that yield multiple messages per request ID

### Task 5: Wire `ingest` through bridge

- [x] Add `ingest` outbound call type to `python-bridge.ts`
- [x] Add `ingest_complete` inbound response type
- [x] `bridge.py`: add `handle_ingest` streaming handler
- [x] `main.js`: add `cairn:ingest` IPC handler using `bridge.stream()`
- [x] `preload.js`: expose `ingest(payload, onProgress)`
- [x] `types.ts`: add `IngestPayload`, `IngestResult`, `ProgressEvent`

### Task 6: Drop zone UI

- [x] `src/ui/DropZone.svelte` — drag-and-drop zone + folder picker button
- [x] Progress display: file count, current file, spinner → ✓ done
- [x] Wire to `cairn.ingest()` IPC call
- [x] Add to `App.svelte` (above Settings)

### Task 7: pytest suite

- [x] `tests/python/test_ingest.py` — mode detection, file collection, rename logic, dry-run, metadata extraction, empty-shell cleanup (poisoned dir test), idempotency
- [x] All tests use `tmp_path` fixture, no filesystem side-effects outside tmp
- [x] `ruff check` and `pyrefly check` green on all new Python files

### Task 8: Sensors green + commit

- [x] `npm run typecheck`, `npm run lint`, `npm run test` green
- [x] `python3 -m ruff check src/workers/` and `python3 -m pyrefly check src/workers/` green
- [x] `python3 -m pytest tests/python/` green
- [x] Commit: `feat: Stage 1 ingest — organise, Resolve import, sidecar`
- [x] Tag: `v0.1.0`

---

## Session 2 Success Criteria

1. Drop a Sony card dump onto the plugin — folder renamed, template created, clips moved
2. Resolve opens with Cairn bin tree and imported clips with metadata
3. Sidecar JSON files present at `04_EXPORT/.cairn/<clip-id>.json` before Resolve import
4. All sensors green on `main`

---

## Carry-Forward / Backlog

- Stage 1 implementation (ingest — port of organise.py, Resolve bin creation) — Session 2 ✓ (this session)
- Stage 2 (classify — Silero VAD integration) — Session 3
- Stage 3 (transcribe — AssemblyAI client + Whisper fallback) — Session 4
- Stage 4 (vision — frame extraction + Claude vision tagging) — Session 5
- Stage 5 (brief — conversational panel) — Session 6
- Stage 6 (soundbites — selection + markers) — Session 7
- Stage 7 (matching — literal + thematic B-roll pairing) — Session 8
- Stage 8 (sidebar — cockpit UI) — Session 9
- Packaging + code signing (Mac notarisation, Windows cert) — Session 10

---

## Rules for Every Session

1. Read `spec.md` and the relevant `specs/*.md` before designing any feature
2. One task `in_progress` at a time
3. Resolve-first + sidecar JSON — never store plugin state elsewhere
4. All Claude prompts live in `src/orchestrator/prompts/` — never inline in routes or components
5. Never implement placeholders — full implementations or flag in `fix_plan.md`
6. Never modify source media files under any circumstances
7. Commit only when explicitly asked or a task is fully complete with sensors green
8. Update `AGENT.md` when you discover a new build/run command or Resolve quirk — keep it brief
