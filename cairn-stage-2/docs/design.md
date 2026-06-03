# Resolve Auto-Cut Harness — Design

**Date:** 2026-06-02
**Status:** Design approved, pending spec review → implementation plan
**Owner:** Aidan Mascaro
**Type:** Experimental side project. **NOT** part of shipped Cairn.

---

## 1. What this is

A **live Claude-Code harness** that drives DaVinci Resolve via the `mcp__davinci-resolve__*`
MCP server to do what shipped Cairn deliberately does *not*: take raw footage + a deliverable
brief and **assemble an A-roll + B-roll rough cut** for the editor to refine.

"Harness" means: **no new plugin code ships.** Claude Code, in an interactive session, is the
runtime. It orchestrates local CLI tools (`ffmpeg`, `faster-whisper`) and the Resolve MCP, and
does the *reasoning* steps (soundbite selection, B-roll tagging, matching) in-session. The
deliverable of this project is a **repeatable runbook + thin Python helpers**, not an Electron
feature.

### Relationship to shipped Cairn (read this)

The April-2026 scope reset established that the *product* Cairn is a frictionless 4-step logger
that **identifies but never assembles** ("leaves markers, not cuts"). This harness intentionally
goes further — it cuts and assembles. That is **not** a reversal of the product direction. It is
a sandbox for probing how far the Resolve connections can be pushed. Findings may later inform
Cairn, but nothing here is committed to the shipped plugin without a separate, explicit decision.

- All of it lives under **`cairn-stage-2/`** — a self-contained sibling track. It must never
  edit `../src/` (the plugin), `../spec.md`, or `../IMPLEMENTATION_PLAN.md`.
- Its own docs (`cairn-stage-2/docs/`), helper scripts (`cairn-stage-2/helpers/`), plan, and
  sidecar cache (`cairn-stage-2/.cache/`, git-ignored) all stay inside this directory.
- See `cairn-stage-2/README.md` for the boundary declaration.

---

## 2. Core property: zero external API keys

The entire loop runs on tools already present on this machine plus Claude's in-session reasoning.
**No `ANTHROPIC_API_KEY` or `ASSEMBLYAI_API_KEY` is required** (confirmed: no Cairn keychain entry
exists, and none is needed).

| Pipeline step              | Engine                                                        |
| -------------------------- | ------------------------------------------------------------- |
| Transcribe A-roll          | `faster-whisper` 1.2.1 (local, word-level timestamps)         |
| "See" B-roll               | `ffmpeg` frame extraction → Claude views frames via Read tool |
| Classify A-roll vs B-roll  | Speech presence (Whisper/VAD) + frame glance + reasoning      |
| Select soundbites          | Claude reasoning over transcripts against the brief           |
| Tag + match B-roll         | Claude reasoning over frame tags + soundbites                 |
| Cut & assemble             | Resolve MCP (`set_mark_in_out` → `append_to_timeline`)        |
| Render draft (optional)    | Resolve MCP render pipeline                                   |

---

## 3. Pipeline

```
[raw clips] → 1.Input → 2.Classify → 3.Transcribe → 4.Brief
                                                      ↓
            8.Refine ← 7.Assemble ← 6.Tag+Match ← 5.Select (★ approval gate)
```

### Stage 1 — Input
**Primary input (v1): an existing Resolve timeline.** The operator points the harness at a
timeline (e.g. the current "timeline 6"); the harness reads the clips already laid on it across
all video tracks and resolves each clip's `File Path` via MCP (`media_pool_item.get_clip_property`).
Bin / shoot-folder inputs are deferred to a later iteration. No source media is touched.

- **Output:** clip manifest `[{clip_id, name, file_path, fps, resolution, duration}]`.

### Stage 2 — Classify A-roll vs B-roll
Per clip, decide A-roll (talking head) / B-roll (no or low speech) / Ambiguous. Signals: speech
ratio from a fast Whisper/VAD pass + a single mid-clip frame glance (face / static interview
framing). Ambiguous clips are flagged for one-click operator confirmation.

- **Output:** label per clip, written to sidecar JSON.
- **Checkpoint:** operator confirms ambiguous clips.

### Stage 3 — Transcribe A-roll
`faster-whisper` on each A-roll file → word-level timestamped transcript. **Single-speaker
assumption in v1 — no diarization.** Cached to sidecar JSON keyed by clip-id + file mtime so
re-runs are free and idempotent.

- **Output:** `{clip_id, words:[{text, start, end}], segments:[...]}` in sidecar.

### Stage 4 — Brief
Operator provides the deliverable spec: platform / aspect (e.g. 9:16), target duration, topic /
angle, tone. Stored as `brief.json`. Drives selection and matching.

### Stage 5 — Select soundbites ★ APPROVAL GATE
Claude reads transcripts against the brief, selects the strongest segments with **exact in/out
timecodes**, and presents an ordered "radio edit" (segment text + source TC + rationale).
**No cutting happens until the operator approves this list.** This is the single most important
human checkpoint.

- **Output:** ordered soundbite list `[{clip_id, in_tc, out_tc, text, beat, why}]`.

### Stage 6 — Tag + match B-roll
`ffmpeg` extracts representative frames from each B-roll clip; Claude views them and assigns tags
(subjects, action, shot type, mood). Each soundbite gets matched B-roll: `[LITERAL]` (visual
references the words) and/or `[THEMATIC]` (visual serves the beat).

- **Output:** B-roll tags + per-soundbite match list in sidecar.

### Stage 7 — Assemble
Build a **brand-new timeline** at the brief's resolution/fps. Source originals are never modified.
Selected A-roll soundbites are placed on V1/A1 via per-clip mark-in/out + append; matched B-roll
on V2 over the relevant lines.

- **Output:** new Resolve timeline (e.g. `CAIRN_AUTOCUT_<brief>_<n>`).
- **Reversible:** deleting the timeline fully undoes the assembly.

### Stage 8 — Refine
Operator trims in Resolve. Optionally Claude renders a draft via the MCP render pipeline.

---

## 4. Safety invariants (inherited from Cairn)

1. **Never modify or delete source media.** ffprobe / read-only frame extraction to temp only.
2. **Never overwrite the operator's existing timelines.** Always create a new one.
3. **Serialise all Resolve calls** through the single MCP server (no concurrent Resolve access).
4. **Sidecar JSON first**, then Resolve writes — idempotent, debuggable, recoverable.
5. The whole assembly is **reversible** (it is just a new timeline).

---

## 5. Verified capabilities (live probe, 2026-06-02)

- MCP connected: **DaVinci Resolve Studio 21.0.0b.33**, Edit page.
- Current project timeline "6": 3 video / 5 audio tracks, ~70 clips (Sony `C31xx` + `DJI` drone).
- Footage: **vertical 4K (2160×3840), 25fps** — a 9:16 social shoot ("Melville Open Studios").
- Clip `File Path` is readable → direct file access for Whisper/ffmpeg confirmed.
- Transcription MCP surface: `media_pool_item.transcribe_audio(clip_id)` +
  `clear_transcription(clip_id)`; clip props `Transcription` / `Transcription Status` exist.
- Assembly surface: `timeline.set_mark_in_out`, `media_pool.create_timeline_from_clips`,
  `media_pool.append_to_timeline`, full `render` pipeline.
- Local tooling: `ffmpeg`/`ffprobe` (Homebrew), `faster-whisper 1.2.1` importable.

## 6. Known limitations / risks

- **Resolve native transcription read-back is unverified** (granularity, timecode mapping). →
  Mitigated by using Whisper-on-the-file instead; native path deferred unless Whisper proves
  insufficient.
- This MCP is the **older `mcp__davinci-resolve__*` server**, *not* the newer `mcp__resolve__*`
  described in CLAUDE.md — so `resolve_clip_transcribe` / `resolve_detect_scene_cuts` helpers are
  **not available**. All capability claims in this doc are against the older server's surface.
- Frame-accurate subclip placement on V2 (B-roll alignment to specific spoken lines) needs a
  proven frame-math path (sec → frame via clip FPS). To validate early in implementation.
- Whisper accuracy on noisy / accented / multi-speaker A-roll may need a larger model; cost is
  time, not money.

## 7. Resolved decisions (2026-06-02)

1. **Input:** an existing Resolve **timeline** is the v1 primary entry. Bin / folder deferred.
2. **Helpers:** thin Python scripts live in `cairn-stage-2/helpers/`, orchestrated by Claude + MCP.
3. **Sidecar/cache:** harness-local at `cairn-stage-2/.cache/` (git-ignored), not Cairn's `.cairn/`.
4. **Speakers:** single-speaker assumption in v1; **no diarization**.

## 8. Explicitly out of scope (v1 of the harness)

- Shipping any of this into the Cairn Electron plugin.
- Music / SFX, colour, titles, captions burn-in.
- Multicam sync.
- Fully autonomous cutting with no approval gate (the Stage 5 gate is mandatory).
- Cloud transcription (AssemblyAI) — local Whisper only for the experiment.
