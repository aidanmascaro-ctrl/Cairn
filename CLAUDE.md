# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mandatory — Read This Every Session

1. **Read `IMPLEMENTATION_PLAN.md` first.** It contains the current session's task list. Work top-to-bottom. Mark tasks done as you go.
2. **Read `spec.md` before designing any feature.** It is the high-level product truth.
3. **Read the relevant file in `specs/*.md` before building that feature.** Each pipeline stage has its own detailed spec.
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
2. **NEVER delete clips from the user's filesystem.** The organise stage *moves* files; it does not delete. Never use `os.remove`, `fs.unlink`, or `shutil.rmtree` on user media paths.
3. **NEVER call the Resolve Scripting API from multiple processes simultaneously.** The API is not thread-safe. Serialise all Resolve calls through a single worker.
4. **NEVER embed API keys in code.** Keys come from OS keychain (via `keytar`) or user settings UI. Never `.env` committed to git. Never hardcoded.
5. **NEVER implement placeholder, stub, or "minimal" logic.** Ship full implementations per spec, or flag incomplete work in `fix_plan.md` and skip the feature. Claude's bias toward placeholders is the #1 silent failure mode.
6. **ALWAYS write metadata to sidecar JSON before writing to Resolve.** Sidecar first = idempotent re-ingest, debuggable state, recoverable from corruption.
7. **ALWAYS handle ambiguous inputs by flagging for user review.** Never silently decide on the user's behalf when confidence is low.
8. **NEVER upload user media to any cloud service without explicit per-project opt-in.** Audio sent to AssemblyAI requires user consent per project; default to local Whisper for NDA'd work.

---

## One Item Per Loop

When working from `IMPLEMENTATION_PLAN.md`, work one task at a time, top-to-bottom. Mark done. Don't parallelise across tasks — parallelise *within* a task via subagents where appropriate. If Ralph goes off-track, narrow back to strictly one item.

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

| Data Type | Resolve | Sidecar JSON | Never |
|---|---|---|---|
| Clip classification (A/B/Ambiguous) | Keywords field | `<clip>.json` | — |
| Clip colour | SetClipColor | — (visual only) | — |
| Transcript | Subtitle track | Full JSON with timestamps | — |
| B-roll visual tags | Keywords field | Full tag set + confidence | — |
| B-roll match IDs | Shot field | Full match data | — |
| B-roll notes | Description field | — | — |
| Soundbite markers | Timeline markers | Marker audit log | — |
| Project brief | — | Brief JSON | Resolve fields |
| API keys | — | OS keychain only | `.env`, code, sidecar |

### Sidecar JSON Location
`<project>/04_EXPORT/.cairn/<clip-id>.json` — hidden folder alongside the organised structure. Re-ingest reads these first; APIs are only called when sidecar is missing or stale.

---

## Project

**Cairn** — a DaVinci Resolve Workflow Integration Plugin. Organises footage, classifies A-roll vs B-roll, transcribes, tags B-roll visually, identifies soundbites, matches B-roll to story beats. Never cuts the video itself. See `spec.md` for the full product specification.

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
- **AI APIs:** AssemblyAI Universal-2 (transcription), Anthropic Claude (brief, selection, vision)
- **Local fallbacks:** faster-whisper (transcription), ffmpeg (frame extraction, probe)
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
├── specs/
│   ├── overview.md                # Architecture, visual system, out-of-scope
│   ├── ingest.md                  # Stage 1 — organise + import
│   ├── classify.md                # Stage 2 — VAD + clip colour
│   ├── transcribe.md              # Stage 3 — AssemblyAI + Whisper
│   ├── vision.md                  # Stage 4 — frame extraction + tagging
│   ├── brief.md                   # Stage 5 — conversational brief
│   ├── soundbites.md              # Stage 6 — soundbite identification + markers
│   ├── matching.md                # Stage 7 — B-roll match (literal + thematic)
│   └── sidebar.md                 # Stage 8 — UI cockpit
├── manifest.xml                   # Plugin registration
├── package.js                     # Plugin entry point
├── src/
│   ├── ui/                        # Svelte UI
│   ├── orchestrator/              # Node orchestration + API calls
│   │   ├── resolve-bridge.ts      # Resolve JS API wrapper
│   │   ├── python-bridge.ts       # Node↔Python stdin/stdout protocol
│   │   └── prompts/               # ALL Claude prompts live here
│   └── workers/                   # Python workers
│       ├── ingest.py              # Ported from organise.py
│       ├── classify.py            # Silero VAD
│       ├── transcribe.py          # AssemblyAI + Whisper
│       ├── vision.py              # Claude vision client
│       ├── resolve_api.py         # Python side of Resolve API
│       └── sidecar.py             # Sidecar JSON read/write
├── tests/
│   ├── node/                      # Vitest
│   └── python/                    # pytest
├── assets/                        # ffmpeg binary, icons, sample data
└── packaging/                     # Electron-builder config, signing certs
```

---

## MCP Servers Available in Claude Code Sessions

| MCP | Prefix | What to use it for |
|---|---|---|
| **Filesystem** | `mcp__Filesystem__*` | Read/write project files, scaffold directories |
| **Desktop Commander** | `mcp__Desktop_Commander__*` | Shell access, running tests, git |
| **context7** | `mcp__context7__*` | Up-to-date docs for Electron, Svelte, Resolve API, AssemblyAI SDK |
| **GitHub** | `mcp__github__*` | Issues, PRs, releases |

### Using context7
Critical for this project — the Resolve Scripting API has poorly indexed docs and the JS API is worse. Pull fresh docs before writing any Resolve call. Same for Electron packaging.

### Using Desktop Commander
Use for running `npm test`, `pytest`, `ruff check`, `pyrefly check`, ffmpeg commands in the assets folder, and git operations. Never for touching user media — only inside the project repo.

---

## Skills Available in Claude Code Sessions

| Skill | Trigger | Use for |
|---|---|---|
| `frontend-design` | Svelte UI work | Plugin panels, drop zone, brief chat, sidebar cockpit |
| `mcp-builder` | MCP surfaces | If we later expose plugin functions via MCP to other tools |
| `skill-creator` | New project skills | Domain skills specific to this plugin (e.g. Resolve marker conventions) |
| `security-review` | Key handling, auth | Before shipping any release — audit for key exposure |
| `verification-loop` | Pre-release | Full end-to-end test on the sample project |
| `simplify` | After large features | Post-Stage review sweeps |

---

## Environment Variables (never committed)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | User-provided, stored via keytar |
| `ASSEMBLYAI_API_KEY` | User-provided, stored via keytar |
| `CAIRN_LOG_LEVEL` | dev mode logging (default `info`) |
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
