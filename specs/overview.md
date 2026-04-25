# specs/overview.md — Cairn Architecture Overview

> Distilled from `spec.md`. This file is the cross-cutting reference for every stage. Read it alongside the per-stage spec you're implementing.

## Purpose

Establish the non-stage-specific invariants: visual system, data flow, process topology, distribution architecture, out-of-scope items. Every stage spec assumes these as given and does not re-state them.

## The pipeline at a glance

```
Stage 1  INGEST       organise.py logic → Resolve bins + clip import
Stage 2  CLASSIFY     Silero VAD → A-roll / B-roll / Ambiguous + clip colour
Stage 3  TRANSCRIBE   AssemblyAI (or Whisper) → subtitle track + JSON sidecar
Stage 4  VISION       5 frames → Claude → structured tags in Keywords
Stage 5  BRIEF        Chat panel → structured project brief JSON
Stage 6  SOUNDBITES   Claude selection → Resolve markers at source timecode
Stage 7  MATCHING     Literal + thematic B-roll → Keywords/Shot/Description
Stage 8  SIDEBAR      Story map UI, click-to-jump, B-roll preview
```

Each stage is independently useful — the plugin can ship after any stage and still save the user time.

## Process topology

```
Resolve Studio
 └── Workflow Integration: Cairn
      └── Electron (Svelte UI)
           └── Node orchestrator (TypeScript strict)
                ├── Resolve JS API (in-process)
                ├── AssemblyAI API (network)
                ├── Anthropic API (network)
                └── Python workers (child processes, JSON-per-line stdin/stdout)
                     ├── ingest.py      (organise logic)
                     ├── classify.py    (Silero VAD)
                     ├── transcribe.py  (AssemblyAI / Whisper)
                     ├── vision.py      (frame extraction + Claude vision)
                     ├── resolve_api.py (Resolve Python scripting)
                     └── sidecar.py     (read/write sidecar JSON)
```

**Serialisation rule (taste invariant #3):** all Resolve Scripting API calls flow through a single worker. The API is not thread-safe. Node may fan out to multiple Python workers for CPU-bound work (frame extraction, VAD) but every Resolve mutation goes through one queue.

## The two-colour visual system

Two colour dimensions operate at different levels. They never collide.

**Media pool clip colour** = what _kind_ of clip it is:

| Category  | Colour |
| --------- | ------ |
| A-ROLL    | Blue   |
| B-ROLL    | Green  |
| AMBIGUOUS | Yellow |
| PHOTOS    | Purple |
| ASSETS    | Tan    |

**Timeline marker colour** (on A-roll clips only) = what _type of story beat_ a soundbite is:

| Beat type                | Colour                           |
| ------------------------ | -------------------------------- |
| Origin / story           | Green                            |
| Emotional peak           | Red                              |
| Factual / informational  | Blue                             |
| Hook / attention-grabber | Yellow                           |
| (additional types)       | remaining Resolve marker colours |

Resolve's accepted marker colours (from CLAUDE.md): Blue, Cyan, Green, Yellow, Red, Pink, Purple, Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream. Clip colours are a different enumerated set — per-stage specs must validate their chosen names against `SetClipColor` before writing.

## Data rule — Resolve-first + sidecar-backed

Every piece of generated metadata lives in two synchronised places:

1. **Resolve** — for editor workflow (Keywords, Shot, Description, Subtitle tracks, Markers, Clip colour)
2. **Sidecar JSON** — for re-ingest, debugging, recovery, and plugin state

**Write order is always sidecar first, then Resolve.** This keeps re-ingest idempotent: the sidecar is the canonical record; Resolve is a projection of it. If the plugin crashes mid-write, the sidecar still exists and the next run reconciles.

Sidecar location: `<project>/04_EXPORT/.cairn/<clip-id>.json`. Schema versioned: `{"schema_version": 1, ...}`.

| Data                           | Resolve field    | Sidecar                   | Never                 |
| ------------------------------ | ---------------- | ------------------------- | --------------------- |
| Classification (A/B/Ambiguous) | Keywords         | `<clip>.json`             | —                     |
| Clip colour                    | SetClipColor     | — (visual only)           | —                     |
| Transcript                     | Subtitle track   | Full JSON + timestamps    | —                     |
| Visual tags                    | Keywords         | Full tag set + confidence | —                     |
| Match IDs                      | Shot             | Full match data           | —                     |
| Free-text notes                | Description      | —                         | —                     |
| Soundbite markers              | Timeline markers | Marker audit log          | —                     |
| Project brief                  | —                | `brief.json`              | Resolve fields        |
| API keys                       | —                | OS keychain via keytar    | `.env`, code, sidecar |

## Distribution architecture (shipping to other editors)

- **BYOK** for AssemblyAI + Anthropic — users enter keys in plugin Settings.
- **Cloud-first defaults** (AssemblyAI Universal-2, Claude) for reliability.
- **Local fallbacks** (faster-whisper for transcription, optional local vision) for NDA work. Turned on via per-project opt-in.
- **Pluggable backend** — transcription and vision providers behind a clean interface so open-source contributions can swap providers.
- **Config location**: `~/Library/Application Support/Cairn/` (Mac), `%APPDATA%\Cairn\` (Windows). Plugin updates never wipe settings.
- **Installers**: `.dmg` (Mac, signed + notarised), `.exe` (Windows, signed). Apple Developer account ~$100/year.
- **Telemetry off by default.** User privacy > product metrics.
- **No lock-in**: all metadata in standard formats (JSON sidecars, Resolve native fields) so users can extract their work if they leave.

## Out-of-scope for v1

The following are explicit non-goals. Do not implement or propose them.

- Assembled rough-cut timelines
- Reel candidate timelines
- Any automated cutting / sequencing / assembly
- Resolve's built-in AI tools (IntelliScript, Magic Mask, SuperScale) — v2
- Music / SFX suggestions
- Colour grading
- Captions / titles as output
- Multi-project batch mode
- Multicam sync across A_CAM and B_CAM

## Sensors (system-wide)

These apply to every stage and are run automatically by the harness:

- **TypeScript strict compile** — never `any`-cast away errors
- **pyrefly** — Python type check, blocks on errors
- **ESLint + Prettier** — JS/TS lint
- **ruff** — Python lint; no TODO comments in `main` (only in `fix_plan.md`)
- **Vitest / pytest** — unit + integration
- **Resolve API read-back** — after every mutation, re-read and confirm
- **LLM-as-judge** — second Claude call to score confidence on any first-Claude selection output
- **Meta-reasoning rule** — in any prompt file under `src/orchestrator/prompts/`, the prompt must identify at least two ways its approach could be wrong and address them in-prompt

## Open questions (cross-cutting)

- Where exactly does the plugin bundle Python? PyInstaller + codesign on Mac is the plan, but verify the sandboxed Electron model permits spawning the PyInstaller binary. Investigate during Task 3.
- How is `ffmpeg` located at runtime? Decision: ship vendored binary at `assets/bin/ffmpeg` (macOS) + `assets/bin/ffmpeg.exe` (Windows). Setup script downloads on first dev install. Binary path resolved via `app.getAppPath()`-relative.
- Single worker vs. per-stage worker? Decision pending benchmark — start with one long-lived `bridge.py` process that dispatches by message `type`; split later if Resolve API latency becomes a scheduling problem.
- Sidecar schema migration strategy? v1 assumption: migrator runs on load if `schema_version` < current; define on first breaking change.
