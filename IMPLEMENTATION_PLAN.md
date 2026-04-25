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

---

## Session 3: Stage 1 Close-out + Stage 2 — Classify

### Pre-flight: close out Stage 1 open items

- [ ] Rebuild plugin (dist/ was root-owned from prior sudo install):
      `sudo chown -R $(whoami) dist && npm run build && sudo bash packaging/install-dev-plugin.sh`
- [ ] Restart Resolve; open a project; run ingest on an already-organised folder that has `01_VIDEOS/A_CAM`, `01_VIDEOS/C_CAM`, `02_PHOTOS` populated
- [ ] Verify **bin structure** appears in media pool: `<shoot>/01_VIDEOS/A_CAM/`, `/C_CAM/`, `/02_PHOTOS/` — this tests `resolve_import.py`'s `_find_or_create()` approach
- [ ] Verify **Photomator Edit Documents** (`DSC00816`-style extension-less files) land in `02_PHOTOS` — confirms fix in `ingest.py`
- [ ] Verify **empty shell folders** (`DCIM`, `M4ROOT`, `drone`) are trashed — confirms `send2trash` cross-volume fix
- [ ] If any Stage 1 item still fails, add a narrow targeted fix and retest; update `fix_plan.md`
- [ ] Once all three confirmed, mark them Resolved in `fix_plan.md`

### Task 1: Python dependency check for Silero VAD

- [ ] Test `silero-vad` install on Python 3.14: `pip install silero-vad`
  - If wheel missing for 3.14: install `pyenv`, create 3.12 venv, pin `CAIRN_PYTHON` env var; document in `AGENT.md`
  - Confirm `torch` and `onnxruntime` also install cleanly (silero-vad pulls them as deps)
- [ ] Confirm vendored ffmpeg at `assets/bin/ffmpeg` is present and executable
  - If absent: download a macOS static ffmpeg build and place at `assets/bin/ffmpeg`; update `.gitignore` to exclude binaries > 50 MB but keep the path declaration in `AGENT.md`
- [ ] Add `silero-vad` to `src/workers/requirements.txt`
- [ ] Smoke-test: `python3 -c "import silero_vad; print('ok')"` inside the target Python env

### Task 2: `src/workers/classify.py`

Full implementation per `specs/classify.md`. No placeholders.

- [ ] Constants block at top (single source of truth for tunable values):

  ```python
  AROLL_THRESHOLD  = 0.60   # speech_ratio >= this → A-ROLL
  BROLL_THRESHOLD  = 0.05   # speech_ratio <= this → B-ROLL
  VAD_THRESHOLD    = 0.5    # silero confidence threshold
  SAMPLING_RATE    = 16_000
  MODEL_NAME       = "silero_vad"
  MODEL_VERSION    = "v4"
  ```

- [ ] `extract_audio(clip_path: str, tmp_dir: Path) -> Path`: runs vendored ffmpeg
      `-ac 1 -ar 16000 -f wav -vn -y`; raises `AudioExtractionError` on non-zero exit;
      `try/finally` guarantees temp file cleanup even on exception
- [ ] `run_vad(wav_path: Path) -> dict`: loads silero model (cached after first call),
      returns `{speech_ratio: float, speech_segments: list[tuple], model_version: str}`
- [ ] `classify_clip(speech_ratio: float) -> str`: returns one of `A-ROLL`, `B-ROLL`, `AMBIGUOUS`
- [ ] `classify_all(clip_paths: list[str], sidecar_root: str) -> Iterator[dict]`: generator,
      skips already-classified clips, yields one progress dict per clip (file, label, speech_ratio)
- [ ] Idempotency: read sidecar, skip if `"classify" in stages_complete`
- [ ] Sidecar update: merge `{classification, vad, stages_complete}` then call `sidecar.write_clip()`
- [ ] No network calls. No Resolve calls. Pure local Python.

### Task 3: Extend `resolve_api.py` for classify writes

- [ ] Add `VALID_CLIP_COLORS` set — the full list of SetClipColor valid values for Resolve
      (Blue, Cyan, Green, Yellow, Red, Pink, Purple, Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream, Orange — confirm against Resolve Python API reference via context7)
- [ ] `set_clip_color(clip: Any, color: str) -> None`: validates against `VALID_CLIP_COLORS`;
      raises `ValueError` with the full valid-values list if invalid; calls `clip.SetClipColor(color)`
- [ ] `get_clip_by_path(media_pool: Any, file_path: str) -> Any | None`: look up a MediaPoolItem
      by its `File Path` property, searching `A_CAM`, `B_CAM`, `C_CAM` bins
- [ ] `classify_write_resolve(media_pool: Any, clip_path: str, label: str) -> bool`:
      acquires lock, finds clip, writes Keywords = label, sets clip colour; returns False if clip not found (log warning, don't raise)
- [ ] Add to `ingest_into_resolve` lock already present — no concurrent classify + ingest calls possible

### Task 4: Bridge extension for classify streaming

- [ ] `bridge.py`: add `handle_classify` streaming handler
  - Payload: `{path: str, dry_run?: bool}` — path is the organised shoot folder
  - Per-clip: yields `{event: "clip_classified", file, label, speech_ratio}`
  - On completion: yields `{event: "classify_complete", a_roll: N, b_roll: N, ambiguous: N}`
- [ ] `python-bridge.ts`: add `classify` outbound type, `ClassifyProgress` + `ClassifyResult` inbound types
- [ ] `main.js`: add `cairn:classify` IPC handler — spawns via `callPython`, streams progress to renderer, writes Resolve clips via `classify_write_resolve` (through a second IPC round-trip so Resolve writes stay in the main process)

  **Note:** Resolve API calls happen inside `main.js` (JS WorkflowIntegration) or via `resolve_import.py`-style subprocess, not inside `bridge.py`. Architecture: Python classifies + writes sidecars → sends results → Node side calls `classify_write_resolve` for each clip. Serialise all Resolve writes through the existing `resolveApiQueue` if one exists, or guard with a mutex.

- [ ] `preload.js`: expose `cairn.classify({path, onProgress})`
- [ ] `types.ts`: add `ClassifyPayload`, `ClassifyProgressEvent`, `ClassifyResult`

### Task 5: Classify panel UI (`src/ui/ClassifyPanel.svelte`)

- [ ] Trigger button: "Classify clips" (enabled only after ingest is done for a folder)
- [ ] Progress bar + per-clip status line (clip name, label badge A/B/AMBIGUOUS, speech_ratio %)
- [ ] Summary on completion: "12 A-roll / 34 B-roll / 3 Ambiguous"
- [ ] **Ambiguous review sub-panel**: list of AMBIGUOUS clips with:
  - Filename
  - speech_ratio percentage
  - Thumbnail (from ffmpeg frame at 50% — `assets/bin/ffmpeg -ss <mid> -i <clip> -frames:v 1 -q:v 5 -y /tmp/thumb-<id>.jpg`)
  - Two buttons: "A-roll ▲" / "B-roll ▼"
  - User confirmation: updates sidecar `{classification, user_confirmed: true}` + Resolve via `classify_write_resolve`
- [ ] Add `<ClassifyPanel>` to `App.svelte` below the DropZone card; shown once `ingestResult` is non-null

### Task 6: pytest suite for classify

- [ ] `tests/python/test_classify.py`:
  - **Audio extraction**: mock ffmpeg, assert temp file path and cleanup
  - **VAD sanity**: feed a 1-second sine wave (speech-like) → `speech_ratio > 0`; feed silence → `speech_ratio == 0.0`
  - **Threshold logic**: parameterised tests for boundary values (0.04 → B-ROLL, 0.05 → B-ROLL, 0.06 → AMBIGUOUS, 0.60 → A-ROLL, 0.61 → A-ROLL)
  - **Idempotency**: clip with `"classify" in stages_complete` is skipped (no ffmpeg call)
  - **No-network assertion**: test harness blocks outbound with `pytest-httpserver` or socket mock; classify must pass
  - **Sidecar merge**: assert `stages_complete` gains `"classify"` without losing `"ingest"`
- [ ] All tests `tmp_path`-scoped

### Task 7: Sensors green + commit

- [ ] `ruff check src/workers/classify.py` → 0 errors
- [ ] `pyrefly check src/workers/classify.py` → 0 errors
- [ ] `pytest tests/python/test_classify.py` → all green
- [ ] `npm run typecheck && npm run lint && npm run test` → all green
- [ ] Update `AGENT.md`: record silero VAD model cache location, ffmpeg path, any Resolve `SetClipColor` valid-values finding
- [ ] Commit: `feat: Stage 2 classify — Silero VAD, clip colour, ambiguous review`
- [ ] Tag: `v0.2.0`

---

## Session 3 Success Criteria

1. Run classify on an organised shoot folder → every video clip gets A-ROLL/B-ROLL/AMBIGUOUS label
2. Resolve media pool shows clip colours: Blue for A-roll, Green for B-roll, Yellow for ambiguous
3. Ambiguous clips appear in the review panel with thumbnails; A/B buttons update Resolve + sidecar live
4. Sidecar JSON updated with `vad` block and `"classify"` in `stages_complete`
5. All sensors green

---

## Session 4: Stage 3 — Transcribe (high-level)

See `specs/transcribe.md` for full detail.

### Session 4 tasks (expand into sub-tasks at Session 4 start)

- [ ] `src/workers/transcribe.py`: AssemblyAI SDK client, speaker labels, word-level JSON → sidecar shape
- [ ] SRT writer: `utterances[]` → `04_EXPORT/.cairn/subtitles/<clip-id>.srt`
- [ ] `resolve_api.py` extension: `attach_subtitle(clip, srt_path)` calling `clip.AddSubTitle`
- [ ] `faster-whisper` fallback (gated on `cloud_transcription_allowed: false` in brief; NDA path)
- [ ] Bridge handler `handle_transcribe`; IPC `cairn:transcribe`
- [ ] `TranscribePanel.svelte`: per-clip progress, cost estimate before batch (AssemblyAI minutes), speaker labelling UI
- [ ] Idempotency: skip if sidecar has matching `provider` + `provider_model`
- [ ] Bill-safety test: two consecutive runs → exactly one API call (mock AssemblyAI client)
- [ ] Commit: `feat: Stage 3 transcribe — AssemblyAI + Whisper fallback, subtitle track`; tag `v0.3.0`

### Pre-Session 4 decision needed

- Confirm `clip.AddSubTitle(path)` is the correct Python API call for attaching an SRT as a subtitle track — verify via DaVinci Resolve scripting docs before the session starts.

---

## Session 5: Stage 4 — Vision (high-level)

See `specs/vision.md` for full detail.

### Session 5 tasks

- [ ] `src/workers/vision.py`: ffmpeg 5-frame extraction, Claude vision API call via `tag_broll` tool, tag validation, frame cleanup
- [ ] Prompt: `src/orchestrator/prompts/vision_tag.md` (meta-reasoning block mandatory)
- [ ] `resolve_api.py` extension: `set_keywords(clip, keywords_str)` — write to `Keywords` field via `SetMetadata`
- [ ] Bridge handler + IPC + UI panel with cost estimate (tokens × clips)
- [ ] LLM-as-judge loop-back: 10% sample re-scored, low-confidence tags surfaced in UI
- [ ] Local vision fallback (NDA path) — if significant lift, defer to `fix_plan.md` and ship cloud-only
- [ ] Commit: `feat: Stage 4 vision — 5-frame extraction, Claude tagging, Resolve keywords`; tag `v0.4.0`

### Pre-Session 5 decisions

- Tool use (`tag_broll`) vs JSON-in-text: default to tool use.
- Frame count scaling for long clips: start at 5 flat; revisit after sample shoot.

---

## Session 6: Stage 5 — Project Brief (high-level)

See `specs/brief.md` for full detail.

### Session 6 tasks

- [ ] `src/orchestrator/prompts/brief_system.md` and `brief_finalise.md`
- [ ] `BriefPanel.svelte`: conversational chat UI (message list, input box), live JSON progress ("4/9 fields"), Edit / Start Over modes
- [ ] Node orchestration: `cairn:brief:message` IPC → Claude `messages.create` with `update_brief_draft` + `finalise_brief` tools → returns updated draft JSON
- [ ] Write `brief.json` to `04_EXPORT/.cairn/brief.json`
- [ ] LLM-as-judge on finalisation: second call scores accuracy of extraction
- [ ] Gate Stages 6–7 on `brief.json` with `finalised: true`
- [ ] Commit: `feat: Stage 5 brief — conversational brief panel`; tag `v0.5.0`

### Architecture note

Brief can be filled **in parallel** with Stages 1–4 (the brief chat doesn't depend on transcripts or vision tags being complete). Open the brief panel once ingest is done; only Stages 6–7 are gated on both indexing + brief being complete.

---

## Session 7: Stage 6 — Soundbites (high-level)

See `specs/soundbites.md` for full detail.

### Session 7 tasks

- [ ] `src/orchestrator/prompts/soundbite_select.md` (meta-reasoning block + beat-type defs)
- [ ] `src/orchestrator/prompts/soundbite_judge.md`
- [ ] Node orchestration: for each A-roll clip, read transcript + brief → Claude `propose_soundbites` tool → judge pass → write markers
- [ ] `resolve_api.py` extension: `add_clip_marker(clip, frame, color, name, note, duration, custom_data)` and `clear_cairn_markers(clip)` (by customData prefix)
- [ ] Frame conversion: `sec → frame` using clip FPS from `GetClipProperty("FPS")`
- [ ] Sidecar: soundbites array written before Resolve marker calls
- [ ] Beat-type → colour mapping validated at call time
- [ ] `SoundbitesPanel.svelte`: progress + review panel for `judge_score < 0.6` flagged bites
- [ ] Commit: `feat: Stage 6 soundbites — selection, judge pass, Resolve markers`; tag `v0.6.0`

### Pre-Session 7 verification needed

- Confirm `MediaPoolItem.AddMarker(frameId, color, name, note, duration, customData)` signature in Python API — check via context7 before coding. If customData not supported, find the correct overload.

---

## Session 8: Stage 7 — B-roll Matching (high-level)

See `specs/matching.md` for full detail.

### Session 8 tasks

- [ ] `src/orchestrator/prompts/match_broll.md` (meta-reasoning block, literal/thematic defs)
- [ ] `src/orchestrator/prompts/match_judge.md`
- [ ] Build B-roll library summary: one line per clip from vision sidecar tags
- [ ] For each soundbite: Claude `propose_matches` tool → judge pass → drop `judge_score < 0.6`
- [ ] `resolve_api.py` extension: `set_shot_field(clip, value)` + `set_description_field(clip, value)` — confirm Resolve field names (`Shot`, `Description`) via Python API reference
- [ ] A-roll marker note append: `MATCHES:` line, preserving existing marker content
- [ ] Sidecars: B-roll + A-roll updated before any Resolve write
- [ ] `MatchingPanel.svelte`: per-soundbite coverage, zero-match bites flagged, rejected-match log
- [ ] Commit: `feat: Stage 7 matching — literal/thematic B-roll to soundbite pairing`; tag `v0.7.0`

### Resolve API risk to resolve early

- `MediaPoolItem.SetMetadata("Shot", …)` — confirm `Shot` is a writable field in the scripting API. If not, use a custom metadata key and document the fallback in `AGENT.md`.

---

## Session 9: Stage 8 — Sidebar Cockpit (high-level)

See `specs/sidebar.md` for full detail.

### Session 9 tasks

- [ ] `SidebarPanel.svelte`: story map component showing all soundbites (by beat colour), B-roll match badges, click-to-jump
- [ ] Click-to-jump → `cairn.resolve.jumpToClip(clip_id, frame)` IPC → `GetCurrentTimeline().SetCurrentVideoClipIndex()` or equivalent
- [ ] B-roll preview panel: hover/click shows matched clips with their tags and thumbnail
- [ ] Project state indicator: which stages complete for current project, what's pending
- [ ] Commit: `feat: Stage 8 sidebar — story map cockpit UI`; tag `v0.8.0`

---

## Session 10: Packaging + Code Signing (high-level)

### Session 10 tasks

- [ ] Mac: `electron-builder` `.dmg` with notarisation; Apple Developer certificate + notary service
- [ ] Windows: `electron-builder` `.exe` with code signing; EV cert or standard cert (decide at Session 10 start)
- [ ] PyInstaller bundle: `pyinstaller classify.py transcribe.py …` into a single `workers` binary shipped inside the Electron `resources/` folder; update `getPythonPath()` to point at bundled binary first
- [ ] Update auto-updater config (if using `electron-updater`)
- [ ] Smoke-test: install on a clean macOS machine (no Python, no Node) — plugin must load and full pipeline must run
- [ ] Commit: `chore: packaging + installers`; tag `v1.0.0-rc1`

---

## Carry-Forward / Backlog

- Stage 1 close-out (bin structure live test, rebuild) — Session 3 pre-flight ← current
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
