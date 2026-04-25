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

- [ ] Create minimal Settings panel in UI (Svelte component)
- [ ] Two fields: `ANTHROPIC_API_KEY`, `ASSEMBLYAI_API_KEY` (both password-masked)
- [ ] Wire to `keytar` — write on save, read on plugin start
- [ ] Validate keys on save: test call to each API, show ✅ or ❌ next to field
- [ ] Settings persist across plugin restarts
- [ ] Commit: `feat: settings panel with keychain storage`

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

## Carry-Forward / Backlog

- Stage 1 implementation (ingest — port of organise.py, Resolve bin creation) — Session 2
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
