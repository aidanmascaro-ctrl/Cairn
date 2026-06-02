# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mandatory — Read This Every Session

1. **Read `IMPLEMENTATION_PLAN.md` first.** It contains the current session's task list. Work top-to-bottom. Mark tasks done as you go.
2. **Read `spec.md` before designing any feature.** It is the high-level product truth.
3. **Read the relevant file in `specs/*.md` before building that feature.** Each ACTIVE stage has its own spec (`overview`, `ingest`, `classify`). Specs for deferred stages 3-8 live in `docs/archive/specs/` — do not implement from them.
4. **Read `fix_plan.md` if it exists.** It tracks known bugs, placeholder implementations, and outstanding TODOs across sessions.
5. **Read `AGENT.md` if it exists.** It contains self-discovered build/run learnings — keep it brief and up to date.

---

## Harness Engineering

**Agent = Model + Harness.** The model reasons. The harness is everything else — what the model sees, what it can do, how consistently it performs. This file + specs + plans + sensors are the harness.

### Guides (feedforward — shape reasoning before it starts)

- `CLAUDE.md` — operational rules; read every session
- `spec.md` — product truth; read before any feature work
- `specs/*.md` — per-stage detailed specs
- `IMPLEMENTATION_PLAN.md` — session task list, disposable
- `fix_plan.md` — living bug/TODO list across sessions
- `AGENT.md` — self-discovered build and run commands

### Sensors (feedback — inject corrections back into context)

- **TypeScript compiler errors** (`npm run typecheck`) → fix before proceeding, never `any`-cast away
- **pyrefly / mypy errors** (`pyrefly check`) → fix before proceeding
- **ESLint / Prettier** (`npm run lint`) → address warnings before marking a task complete
- **ruff** (`ruff check`) → Python lint, same rule
- **Test failures** (Vitest, pytest) → diagnose root cause, never skip or stub
- **Resolve API response validation** → after writing markers/bins/metadata, read them back and confirm
- **LLM-as-judge** → pipe Claude outputs (soundbite selections, B-roll matches) back through a second Claude call to score confidence before showing the user

### The Loop-Back Rule

Always look for opportunities to feed plugin output back through the model for evaluation. This is the single highest-leverage harness pattern.

- After writing markers → subagent reads timeline, verifies placement
- After soundbite selection → judge subagent scores confidence, flags weak picks
- After B-roll matching → relevance subagent drops matches below threshold
- After generating ffmpeg commands → dry-run validation before executing

### Meta-Reasoning Rule

Before answering any complex question or writing any significant Claude prompt: identify at least two ways the current approach could be wrong, then address them. Especially important in `src/orchestrator/prompts/` where prompt mistakes compound silently.

---

## Taste Invariants — NEVER VIOLATE

These are hard constraints that take precedence over any task instruction. The user's footage and workflow are at stake.

1. **NEVER modify the user's source media files.** Read-only guarantee. ffprobe, ffmpeg `-c copy`, frame extraction to temp — never re-encode or overwrite source.
2. **NEVER delete clips from the user's filesystem.** The organise stage _moves_ files; it does not delete. Never use `os.remove`, `fs.unlink`, or `shutil.rmtree` on user media paths.
3. **NEVER call the Resolve Scripting API from multiple processes simultaneously.** The API is not thread-safe. Serialise all Resolve calls through a single worker.
4. **NEVER embed API keys in code.** Keys come from OS keychain (via `keytar`) or user settings UI. Never `.env` committed to git. Never hardcoded.
5. **NEVER implement placeholder, stub, or "minimal" logic.** Ship full implementations per spec, or flag incomplete work in `fix_plan.md` and skip the feature. Claude's bias toward placeholders is the #1 silent failure mode.
6. **ALWAYS write metadata to sidecar JSON before writing to Resolve.** Sidecar first = idempotent re-ingest, debuggable state, recoverable from corruption.
7. **ALWAYS handle ambiguous inputs by flagging for user review.** Never silently decide on the user's behalf when confidence is low.
8. **NEVER upload user media to any cloud service without explicit per-project opt-in.** Audio sent to AssemblyAI requires user consent per project; default to local Whisper for NDA'd work.

---

## One Item Per Loop

When working from `IMPLEMENTATION_PLAN.md`, work one task at a time, top-to-bottom. Mark done. Don't parallelise across tasks — parallelise _within_ a task via subagents where appropriate. If Ralph goes off-track, narrow back to strictly one item.

### Subagent Usage

- **Parallel subagents** for: codebase search (`ripgrep`), per-clip metadata extraction, per-clip vision tagging, per-clip sidecar JSON writes
- **Single subagent** for: Resolve API calls, build, test, git operations, ffmpeg transcoding (CPU/GPU-bound)
- **Primary context window operates as a scheduler** — dispatch expensive allocation work to subagents rather than filling the main window

### Don't Assume Not Implemented

Before implementing anything, search the codebase with subagents. Failure mode: Claude greps, misses a case, reimplements, creates drift. "Search before create" is a standing rule.

---

## Data Architecture — Resolve-first, sidecar-backed

### The Rule in One Sentence

> Every piece of generated metadata lives in two synchronised places: Resolve (for editor workflow) and sidecar JSON (for re-ingest, debugging, and plugin state).

### What Lives Where

> Active today: clip classification, clip colour, and API keys. The transcript,
> visual-tags, match-ID, notes, soundbite-marker and project-brief rows are
> **deferred** (stages 3-8, see `docs/archive/`).

| Data Type                           | Resolve           | Sidecar JSON              | Never                 |
| ----------------------------------- | ----------------- | ------------------------- | --------------------- |
| Clip classification (A/B/Ambiguous) | Keywords field    | `<clip>.json`             | —                     |
| Clip colour                         | SetClipColor      | — (visual only)           | —                     |
| Transcript                          | Subtitle track    | Full JSON with timestamps | —                     |
| B-roll visual tags                  | Keywords field    | Full tag set + confidence | —                     |
| B-roll match IDs                    | Shot field        | Full match data           | —                     |
| B-roll notes                        | Description field | —                         | —                     |
| Soundbite markers                   | Timeline markers  | Marker audit log          | —                     |
| Project brief                       | —                 | Brief JSON                | Resolve fields        |
| API keys                            | —                 | OS keychain only          | `.env`, code, sidecar |

### Sidecar JSON Location

`<project>/04_EXPORT/.cairn/<clip-id>.json` — hidden folder alongside the organised structure. Re-ingest reads these first; APIs are only called when sidecar is missing or stale.

---

## Project

**Cairn** — a DaVinci Resolve Workflow Integration Plugin. Organises footage, classifies A-roll vs B-roll (local KNN on face + motion), colour-codes the media pool, and builds a layered assembly timeline. Never cuts the video itself. Transcription, visual tagging, soundbite identification and B-roll matching are **deferred post-v1** (scope reset `966ce94`; specs in `docs/archive/`). See `spec.md` for the full product specification.

**Owner:** Aidan Mascaro, Perth WA, freelance videographer (@aidanmascaro).
**Coding level:** intermediate — can read and reason about code, prefers not to edit directly. Claude Code is responsible for all code. Explanations in plain English. Never ask the owner to edit code manually.

**Long-term vision:** personal tool today → sellable plugin for freelance videographers tomorrow. Architecture must support BYOK (Bring Your Own Key) multi-user distribution from day one.

---

## Tech Stack

- **Plugin shell:** Electron (DaVinci Resolve Workflow Integration Plugin, sandboxed model)
- **UI:** Svelte + Tailwind CSS
- **Orchestration:** Node.js (TypeScript strict mode)
- **Workers:** Python 3.11+ (bundled with plugin via PyInstaller)
- **Resolve APIs:** JavaScript (`WorkflowIntegration.node`) + Python (Scripting API)
- **Vision / maths (current):** OpenCV (`opencv-python-headless`) + numpy for the classify stage; ffmpeg/ffprobe (vendored, `assets/bin/`) for probing + frame extraction
- **AI APIs (deferred stages only):** AssemblyAI Universal-2 (transcription), Anthropic Claude (brief, selection, vision) — not called by any current worker; keys are accepted in Settings for when those stages return
- **Local fallbacks (deferred):** faster-whisper (transcription)
- **Type checkers:** TypeScript (strict), pyrefly (Python)
- **Linters:** ESLint + Prettier (JS/TS), ruff (Python)
- **Testing:** Vitest (JS/TS), pytest (Python)
- **Secrets:** keytar (OS keychain wrapper)

---

## Commands

```bash
# Node / Electron
npm install           # Install Node deps
npm run dev           # Dev mode (plugin window against running Resolve)
npm run build         # Production build (signed installers)
npm run lint          # ESLint
npm run typecheck     # tsc --noEmit
npm run test          # Vitest
npm run package       # Build .dmg + .exe installers

# Python workers
cd src/workers
pip install -r requirements.txt
ruff check .          # Lint
pyrefly check .       # Type check
pytest                # Tests
```

---

## Project Structure

```
cairn/
├── CLAUDE.md                      # This file — read every session
├── spec.md                        # Product truth — read before features
├── IMPLEMENTATION_PLAN.md         # Current session tasks
├── fix_plan.md                    # Living bug/TODO list
├── AGENT.md                       # Self-updated build/run learnings
├── pytest.ini                     # pytest rootdir + testpaths
├── organise.py                    # v1 standalone CLI (ported into ingest.py)
├── specs/                         # ACTIVE per-stage specs only
│   ├── overview.md                # Architecture, visual system, out-of-scope
│   ├── ingest.md                  # Stage 1 — organise + import
│   └── classify.md                # Stage 2 — KNN (face + motion) + clip colour
├── docs/
│   └── archive/                   # DEFERRED stages 3-8 (not active) — see its README
│       ├── specs/                 # transcribe, vision, brief, soundbites, matching, sidebar
│       └── prompts/               # the 7 orphaned LLM prompts
├── manifest.xml                   # Plugin registration
├── src/
│   ├── ui/                        # Svelte UI (App, DropZone, ClassifyPanel,
│   │                              #   ClassifyInspector, Settings, TimelinePanel)
│   ├── orchestrator/              # Node orchestration
│   │   ├── main.js                # Electron main + IPC + inline Resolve JS API
│   │   ├── preload.js             # contextBridge trust boundary
│   │   ├── python-bridge.ts       # typed Node↔Python stdin/stdout protocol
│   │   └── prompts/               # Claude prompts (only .gitkeep until AI stages return)
│   └── workers/                   # Python workers
│       ├── bridge.py              # IPC dispatcher (entry point)
│       ├── ingest.py              # Stage 1 — organise + Resolve import
│       ├── classify.py            # Stage 2 — KNN face + motion (local, no network)
│       ├── resolve_api.py         # Resolve scripting + clip colour writes
│       ├── resolve_import.py      # legacy import fallback (slated for removal)
│       ├── timeline.py            # Stage 4 — build assembly timeline
│       └── sidecar.py             # Sidecar JSON read/write
├── tests/
│   ├── node/                      # Vitest
│   └── python/                    # pytest
├── tools/                         # trainer.js (KNN labelling), enrich_gt.py
├── assets/                        # ffmpeg/ffprobe binaries, icons
└── packaging/                     # PyInstaller spec, electron-builder, installers
```

---

## MCP Servers Available in Claude Code Sessions

| MCP                   | Prefix                      | What to use it for                                                |
| --------------------- | --------------------------- | ----------------------------------------------------------------- |
| **Filesystem**        | `mcp__Filesystem__*`        | Read/write project files, scaffold directories                    |
| **Desktop Commander** | `mcp__Desktop_Commander__*` | Shell access, running tests, git                                  |
| **context7**          | `mcp__context7__*`          | Up-to-date docs for Electron, Svelte, Resolve API, AssemblyAI SDK |
| **GitHub**            | `mcp__github__*`            | Issues, PRs, releases                                             |
| **DaVinci Resolve**   | `mcp__resolve__*`           | Dev-time Resolve verification; NOT available to plugin at runtime |

### Using the DaVinci Resolve MCP

**This is the most powerful dev-time tool in the harness.** Use it to verify every Resolve operation before asking the user to test live. The new `mcp__resolve__*` server (replacing the older `mcp__davinci-resolve__*`) exposes a flat tool surface (~200 tools, all prefixed `resolve_*`) with much richer coverage than before.

- **Connection check:** `resolve_get_version` + `resolve_get_product_name` — call at session start.
- **Before any Resolve API change:** verify current state with `resolve_list_bins`, `resolve_list_timelines`, `resolve_get_project_settings`, `resolve_get_timeline_info`.
- **After any bin/import operation:** confirm structure with `resolve_list_bins` and `resolve_search_clips` for path-based lookups.
- **After classify write:** confirm with `resolve_get_clip_info(clip_id)` (returns metadata + clip colour) or `resolve_inspect_item(clip_id)`.
- **After timeline assembly:** confirm with `resolve_get_timeline_info` (track counts) and `resolve_list_clips_on_track(track_index, track_type)`.
- **Marker writes (Stage 6 soundbites):** `resolve_add_marker_at` / `resolve_add_markers` / `resolve_list_markers` — these are now first-class tools.
- **Pipeline-relevant bonus tools now available:** `resolve_clip_transcribe` (server-side AssemblyAI alternative for Stage 3), `resolve_detect_scene_cuts` (supplement classification), `resolve_smart_reframe`, `resolve_auto_sync_audio`, `resolve_build_from_markers` (assemble Stage 7 directly from marker timeline). Treat these as harness verification helpers, not as runtime substitutes — the shipped plugin must remain self-contained.
- **Testing bin creation:** create under `Master/_cairn_test`, verify, delete with `resolve_delete_bins([id])` — never pollute user project with test data.
- **Confirmed enabled:** External Scripting is enabled on this machine (MCP works ↔ scripting works ↔ `resolve_import.py` subprocess will also connect successfully).

### Using context7

Critical for this project — the Resolve Scripting API has poorly indexed docs and the JS API is worse. Pull fresh docs before writing any Resolve call. Same for Electron packaging.

### Using Desktop Commander

Use for running `npm test`, `pytest`, `ruff check`, `pyrefly check`, ffmpeg commands in the assets folder, and git operations. Never for touching user media — only inside the project repo.

---

## Skills Available in Claude Code Sessions

| Skill               | Trigger              | Use for                                                                 |
| ------------------- | -------------------- | ----------------------------------------------------------------------- |
| `frontend-design`   | Svelte UI work       | Plugin panels, drop zone, brief chat, sidebar cockpit                   |
| `mcp-builder`       | MCP surfaces         | If we later expose plugin functions via MCP to other tools              |
| `skill-creator`     | New project skills   | Domain skills specific to this plugin (e.g. Resolve marker conventions) |
| `security-review`   | Key handling, auth   | Before shipping any release — audit for key exposure                    |
| `verification-loop` | Pre-release          | Full end-to-end test on the sample project                     |
| `simplify`          | After large features | Post-Stage review sweeps                                                |

---

## Environment Variables (never committed)

| Variable             | Purpose                                         |
| -------------------- | ----------------------------------------------- |
| `ANTHROPIC_API_KEY`  | User-provided, stored via keytar                |
| `ASSEMBLYAI_API_KEY` | User-provided, stored via keytar                |
| `CAIRN_LOG_LEVEL`    | dev mode logging (default `info`)               |
| `CAIRN_SIDECAR_ROOT` | Override for testing (`.cairn` folder location) |

Never commit `.env.local`, `.env.development`, or any file with API keys.

---

## Development Notes

- Resolve Studio only — no free-tier support. Mac + Windows only (no Linux).
- Plugin ID: `com.aidanmascaro.cairn` — must match `manifest.xml` and `WorkflowIntegration.Initialize()`.
- Python workers communicate with Node via stdin/stdout JSON protocol. One JSON object per line.
- ffmpeg binary ships inside the plugin bundle at `assets/bin/ffmpeg` — do not rely on system `ffmpeg`.
- API keys flow: user enters in Settings UI → stored in OS keychain via keytar → retrieved per-request by orchestrator → passed to worker via env var for that subprocess.
- Sidecar JSON schema versioned: `{"schema_version": 1, ...}`. Bump on breaking change, migrate on load.
- The `organise.py` script at the project root of v1 is the user's original script — ported to `src/workers/ingest.py` for the plugin.
- Resolve marker colours (API-accepted values): Blue, Cyan, Green, Yellow, Red, Pink, Purple, Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream. Clip colours are a different set — check `SetClipColor` valid values.

---

## Git Workflow

- Commit only when explicitly asked, or when a task in `IMPLEMENTATION_PLAN.md` is fully complete and checks pass
- Commit messages: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`)
- Tag releases at the end of each completed stage: `v0.1.0` after Stage 1, `v0.2.0` after Stage 2, etc.
- Never `git push` without explicit user instruction
- Never `git reset --hard` or `git rebase -i` without confirmation

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:

- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
- Rebuild the full graph (with semantic extraction) only when docs/specs change: `/graphify . --update`
- Query the graph to find cross-cutting concerns before touching shared code: `/graphify query "resolve api"`, `/graphify path "classify" "timeline"`

---

## Everything Claude Code (ECC) — Agent & Quality Gate Integration

ECC agents and rules are installed globally at `~/.claude/agents/` and `~/.claude/rules/`. Use them proactively — do not wait to be asked.

### Mandatory Agent Usage

| Trigger                                        | Agent to use                                                            |
| ---------------------------------------------- | ----------------------------------------------------------------------- |
| After writing or editing any TypeScript/Svelte | `typescript-reviewer`                                                   |
| After writing or editing any Python worker     | `python-reviewer`                                                       |
| Before any commit                              | `security-reviewer`                                                     |
| When a build or typecheck fails                | `build-error-resolver`                                                  |
| When implementing a new feature or test        | `tdd-guide`                                                             |
| When making architectural decisions            | `architect`                                                             |
| After editing any Python worker                | `silent-failure-hunter` — catches swallowed errors in IPC/Resolve calls |
| After significant perf work (classify, ingest) | `performance-optimizer` — ffprobe batching, KNN memory                  |
| When harness feels slow or noisy               | `harness-optimizer` — tune `.claude/settings.json` hooks                |

### Key Skills Available

| Skill                | When to invoke                                                                                                           |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `/tdd-workflow`      | Writing new workers, handlers, or UI logic                                                                               |
| `/security-review`   | Before any release or after touching key storage/IPC                                                                     |
| `/backend-patterns`  | Designing new Python worker interfaces                                                                                   |
| `/e2e-testing`       | Adding Playwright tests for UI flows                                                                                     |
| `/verification-loop` | Pre-release full pipeline smoke test                                                                                     |
| `/graphify`          | After large refactors to rebuild the knowledge graph                                                                     |
| `/video-editing`     | When writing FFmpeg commands, batch cut scripts, or Whisper pipelines — contains exact patterns for the tools Cairn uses |
| `/eval-harness`      | When writing capability evals for the KNN classifier or LLM-as-judge loops — formalises pass@k and regression tracking   |

### Slash Commands Available

| Command                                          | When to use                                                                                  |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| `/code-review`                                   | Before any PR — runs 4 parallel agents with ≥80 confidence filtering, posts GitHub comment   |
| `/commit`                                        | Quick conventional commit with auto-generated message                                        |
| `/revise-claude-md`                              | End of session — captures learnings from current session into CLAUDE.md                      |
| `/ralph-loop "task" --completion-promise "DONE"` | Long autonomous fix cycles (e.g. "run pytest and fix all failures. Output DONE when green.") |

### Quality Gates (auto-wired in `.claude/settings.json`)

These run automatically — do not bypass them:

- **On every TypeScript/Svelte edit** → ESLint auto-fix
- **On every Python edit** → Ruff auto-fix
- **At session end** → `npm run typecheck` + `pyrefly check src/workers/`

If any gate fails, fix it before marking the task complete. Never `// @ts-ignore` or `# type: ignore` to silence — diagnose and fix the root cause.

### TypeScript Language Server (typescript-lsp)

The typescript-lsp plugin is installed globally. Use **go-to-definition** and **find-references** to navigate the IPC contract between `src/ui/types.ts`, `src/orchestrator/python-bridge.ts`, and `src/orchestrator/main.js` before editing shared types — it prevents the silent-breakage pattern where a type change doesn't surface until runtime.
