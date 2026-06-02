# specs/overview.md — Cairn Architecture Overview

> Distilled from `spec.md`. This file is the cross-cutting reference for every
> stage. Read it alongside the per-stage spec you're implementing.
>
> **Scope (post 966ce94 reset):** the shipping product is the **4-stage**
> pipeline below. Stages 3–8 of the original vision (transcribe, vision, brief,
> soundbites, matching, sidebar cockpit) are **DEFERRED post-v1** — their specs
> are preserved under `docs/archive/specs/` and must not be treated as active
> goals.

## Purpose

Establish the non-stage-specific invariants: visual system, data flow, process
topology, distribution architecture, out-of-scope items. Every stage spec
assumes these as given and does not re-state them.

## The pipeline at a glance

**Shipping today (4 stages):**

```
Stage 1  INGEST     organise shoot folder → Resolve bins + clip import + sidecar
Stage 2  CLASSIFY   KNN over face_ratio + motion_score → A-roll / B-roll / Ambiguous
Stage 3  COLOUR     media-pool clip colour (inline in resolve_api on classify write)
Stage 4  TIMELINE   build assembly timeline — A-roll → V1/A1, B-roll → V2/A2
```

Each stage is independently useful — the plugin can ship after any stage and
still save the user time.

**Deferred post-v1 (stripped in the scope reset; specs in `docs/archive/`):**

```
TRANSCRIBE   AssemblyAI / Whisper → subtitle track + JSON sidecar
VISION       frame extraction → Claude → structured tags
BRIEF        chat panel → structured project brief JSON
SOUNDBITES   Claude selection → Resolve markers at source timecode
MATCHING     literal + thematic B-roll → Keywords/Shot/Description
SIDEBAR      story-map cockpit, click-to-jump, B-roll preview
```

## Process topology

```
Resolve Studio
 └── Workflow Integration: Cairn
      └── Electron (Svelte UI)
           └── Node orchestrator (src/orchestrator/main.js, CommonJS)
                ├── Resolve JS API (in-process, inline in main.js today)
                └── Python worker (long-lived child process,
                    JSON-per-line stdin/stdout via src/orchestrator/python-bridge.ts)
                     └── src/workers/bridge.py  (IPC dispatcher) imports:
                          ├── ingest.py        (Stage 1 — organise + import)
                          ├── classify.py      (Stage 2 — KNN face + motion, local)
                          ├── resolve_api.py   (Resolve scripting + clip colour)
                          ├── resolve_import.py (legacy import fallback — to be removed)
                          ├── timeline.py      (Stage 4 — assembly timeline)
                          └── sidecar.py       (read/write sidecar JSON)
```

**No network workers today.** Classification is fully local (OpenCV + ffmpeg).
The AssemblyAI / Anthropic clients belong to deferred stages and are not spawned
by any current worker.

**Serialisation rule (taste invariant #3):** all Resolve Scripting API calls
flow through the single long-lived worker, which serialises them behind a
module lock. The API is not thread-safe. Node may fan out CPU-bound work
(frame extraction, face/motion) but every Resolve mutation goes through one queue.

## The two-colour visual system

Two colour dimensions operate at different levels. They never collide.

**Media pool clip colour** (Stage 3) = what _kind_ of clip it is:

| Category  | Colour |
| --------- | ------ |
| A-ROLL    | Blue   |
| B-ROLL    | Green  |
| AMBIGUOUS | Yellow |
| PHOTOS    | Purple |
| ASSETS    | Tan    |

**Timeline marker colour** (story-beat type on soundbites) — **DEFERRED** with
the soundbites stage; documented in `docs/archive/specs/soundbites.md`.

Resolve's accepted marker colours: Blue, Cyan, Green, Yellow, Red, Pink, Purple,
Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream. Clip colours are a
different enumerated set — per-stage specs must validate names against
`SetClipColor` before writing.

## Data rule — Resolve-first + sidecar-backed

Every piece of generated metadata lives in two synchronised places:

1. **Resolve** — for editor workflow (Keywords / metadata, Clip colour, and —
   for deferred stages — Subtitle tracks, Markers, Shot/Description fields)
2. **Sidecar JSON** — for re-ingest, debugging, recovery, and plugin state

**Write order is always sidecar first, then Resolve.** The sidecar is the
canonical record; Resolve is a projection of it. If the plugin crashes mid-write,
the sidecar still exists and the next run reconciles.

Sidecar location: `<project>/04_EXPORT/.cairn/<clip-id>.json`. Schema versioned:
`{"schema_version": 1, ...}`. (Clip id = `sha256(absolute_path)[:16]`.)

| Data                           | Resolve field                   | Sidecar                                  | Status   |
| ------------------------------ | ------------------------------- | ---------------------------------------- | -------- |
| Classification (A/B/Ambiguous) | `Cairn Classification` metadata | `classification` + `classification_meta` | active   |
| Clip colour                    | SetClipColor + AddFlag          | — (visual only)                          | active   |
| Transcript                     | Subtitle track                  | Full JSON + timestamps                   | deferred |
| Visual tags                    | Keywords                        | Full tag set + confidence                | deferred |
| Match IDs                      | Shot                            | Full match data                          | deferred |
| Free-text notes                | Description                     | —                                        | deferred |
| Soundbite markers              | Timeline markers                | Marker audit log                         | deferred |
| Project brief                  | —                               | `brief.json`                             | deferred |
| API keys                       | —                               | OS keychain / safeStorage                | active   |

## Distribution architecture (shipping to other editors)

- **BYOK** for AssemblyAI + Anthropic — users enter keys in plugin Settings.
  (Keys are accepted today but only exercised once the deferred AI stages return.)
- **Local-first today**: the shipping pipeline needs no cloud account — ingest,
  classify, colour and timeline all run locally with bundled ffmpeg + OpenCV.
- **Local fallbacks** (faster-whisper, local vision) planned for the deferred
  transcription/vision stages, toggled via per-project opt-in for NDA work.
- **Config location**: `~/Library/Application Support/Cairn/` (Mac),
  `%APPDATA%\Cairn\` (Windows). Plugin updates never wipe settings.
- **Installers**: `.dmg` (Mac, signed + notarised), `.exe` (Windows, signed).
- **Telemetry off by default.** User privacy > product metrics.
- **No lock-in**: all metadata in standard formats (JSON sidecars, Resolve native
  fields) so users can extract their work if they leave.

## Out-of-scope for v1

The following are explicit non-goals. Do not implement or propose them.

- **Editorial cutting / trimming / sequencing.** Stage 4 builds a _layered
  assembly_ (A-roll on V1, B-roll on V2) — it does **not** cut, trim, reorder, or
  produce a rough cut. Cairn identifies and assembles tracks; it does not edit.
- Reel candidate timelines
- Resolve's built-in AI tools (IntelliScript, Magic Mask, SuperScale) — v2
- Music / SFX suggestions
- Colour grading
- Captions / titles as output
- Multi-project batch mode
- Multicam sync across A_CAM and B_CAM

## Sensors (system-wide)

Run automatically by the harness:

- **TypeScript strict compile** — never `any`-cast away errors
- **pyrefly** — Python type check, blocks on errors
- **ESLint + Prettier** — JS/TS lint
- **ruff** — Python lint; no TODO comments in `main` (only in `fix_plan.md`)
- **Vitest / pytest** — unit + integration
- **Resolve API read-back** — after every mutation, re-read and confirm
- **Classifier eval harness** — held-out accuracy gate on KNN changes
- **Meta-reasoning rule** — any prompt file under `src/orchestrator/prompts/`
  must identify ≥2 ways its approach could be wrong and address them in-prompt
  (applies once the deferred LLM stages return)

## Open questions (cross-cutting)

- PyInstaller bundle: confirm the sandboxed Electron model permits spawning the
  bundled `workers` binary, and smoke-test that `classify` (cv2) and
  `build_timeline` actually run inside the bundle (see `fix_plan.md`).
- `ffmpeg`/`ffprobe` runtime location: vendored at `assets/bin/`, resolved
  bundled → Homebrew → PATH (see `classify.py` / `trainer.js`).
- Single long-lived `bridge.py` worker vs per-stage workers — currently one
  dispatcher; revisit if Resolve API latency becomes a scheduling problem.
- Sidecar schema migration: migrator runs on load when `schema_version` < current.
