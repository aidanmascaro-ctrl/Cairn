# Cairn — Product Specification (v1)

> _A cairn is a stack of stones left on a trail by someone who walked it first, marking the way for whoever comes next._

## What Cairn is

A DaVinci Resolve Workflow Integration Plugin that does the tedious pre-edit work — organising footage, classifying A-roll vs B-roll, transcribing dialogue, tagging B-roll visually, identifying soundbites, matching B-roll to story beats — and leaves markers throughout your project so the editor (you) can focus on the creative work of shaping the edit.

## The one-line goal

Drag a shoot folder onto Cairn → get a fully organised, transcribed, tagged, and annotated Resolve project where every usable moment is identified and matched, ready for you to assemble creatively.

## Core principle

Cairn identifies, it does not assemble. It leaves markers, not cuts. The plugin does the logging, transcribing, and tagging; you decide what the edit actually is. Think of it as a supercharged logging assistant with a recommendation engine, not an editor.

---

## Scope (current vs deferred)

> **As of the `966ce94` scope reset ("refactor: scope reset — strip stages 3-8, rebuild classifier as duration+face"), the SHIPPING product is a focused 4-stage pipeline. Stages 3–8 of the original vision below are DEFERRED post-v1 — their specs and LLM prompts are preserved in `docs/archive/`. Do not treat the deferred sections as active build targets.**

**Shipping today (4 stages):**

1. **Ingest** (`src/workers/ingest.py`) — organise a shoot folder (YYMMDD rename from Sony XML, TEMPLATE tree `01_VIDEOS/{A,B,C}_CAM` + `02_PHOTOS` + `03_ASSETS` + `04_EXPORT`, bucket files, idempotent re-ingest), import into Resolve bins, write sidecar JSON.
2. **Classify** (`src/workers/classify.py`) — A-roll vs B-roll vs Ambiguous via a **local KNN classifier** (k=5, confidence ≥ 0.70) over two local features: `face_ratio` (OpenCV Haar cascade across 5 sampled frames) and `motion_score` (mean normalised frame-to-frame pixel diff). 100% offline; no network and no AI APIs. classifier version `v3-knn-face-motion`.
3. **Colour** (inline in `src/workers/resolve_api.py`) — set clip colour A-ROLL→Blue, B-ROLL→Green, AMBIGUOUS→Yellow via `SetClipColor`; write Resolve custom metadata "Cairn Classification" + `AddFlag`.
4. **Build Assembly Timeline** (`src/workers/timeline.py`) — read classified clips from sidecars, infer modal FPS/resolution from the media pool, create a uniquely named timeline, append A-roll to V1/A1 then B-roll to V2/A2 via a track-lock protocol, skip AMBIGUOUS clips.

**Deferred post-v1 (stripped in `966ce94`, specs in `docs/archive/`):** transcribe (AssemblyAI Universal-2 + faster-whisper), vision (Claude frame tagging), brief (conversational project brief), soundbites (Claude soundbite selection + Resolve markers), matching (literal + thematic B-roll matching), and the sidebar cockpit / story map.

**AI APIs at runtime today: NONE.** Classification is entirely local (OpenCV + ffmpeg). AssemblyAI and Anthropic Claude remain dependencies of the deferred stages only; the AssemblyAI/Anthropic API-key fields in Settings are kept for those future stages.

> The remainder of this document is the original full-vision specification. The stages marked **DEFERRED (post-v1)** below are not part of the current build — they are retained as design intent.

---

## The pipeline

### Stage 1 — Ingest (powered by your existing organise.py)

The plugin handles all folder reorganisation internally — no external tool required. The drop-handler detects one of three input states and routes accordingly:

- **Mode A — Raw Sony card dump** (has `DCIM/` or `M4ROOT/`): plugin runs the organise logic, creates the template structure, then imports
- **Mode B — Already-organised folder** (has `01_VIDEOS/` etc.): skip organise, import directly
- **Mode C — Loose or mixed folder** (random media files, no structure): detect file types, organise into the template, then import

Your `organise.py` ports into the plugin as `ingest.py`. Its existing `is_sony_card()` and `already_organised()` detection logic drives the routing. The file-type buckets (VIDEO_EXTS, PHOTO_EXTS, ASSET_EXTS) extend naturally to Mode C handling.

Other details:

- **Proxy generation**: optional toggle, off by default
- Creates Resolve bins mirroring your `01_VIDEOS/A_CAM`, `02_PHOTOS`, `03_ASSETS` structure
- Reads the same Sony XML metadata your script already parses — extended to pull lens, camera, timecode into the clip metadata panel
- Never deletes source files (preserves the safety guarantee from your original script)

### Stage 2 — Classify A-roll vs B-roll

> **Current implementation (post-`966ce94`):** classification is a **local KNN classifier** (k=5, confidence ≥ 0.70) over two purely-local features — `face_ratio` (OpenCV Haar cascade across 5 sampled frames) and `motion_score` (mean normalised frame-to-frame pixel diff). Audio is **intentionally not used**; there is **no Silero VAD**. A fallback rule (`face_ratio ≥ 0.40 ⇒ A-ROLL`) applies only when there are fewer than 5 ground-truth examples in `~/.cairn/ground_truth.json`. Low KNN confidence ⇒ AMBIGUOUS (user review). Runs fully offline, no network, no AI APIs. classifier version `v3-knn-face-motion`. The sidecar carries a `classification` block plus `classification_meta` `{duration_sec, face_ratio, face_frames, frames_sampled, motion_score, classifier_version}`.

- The KNN classifier runs locally on every clip
- Each clip tagged in Resolve metadata ("Cairn Classification") as `A-ROLL`, `B-ROLL`, or `AMBIGUOUS`
- **Clip colour set in the media pool** for instant visual category identification (see Stage 3 — Colour):
  - A-ROLL → Blue
  - B-ROLL → Green
  - AMBIGUOUS → Yellow
- Ambiguous clips (low KNN confidence — e.g. your studio walkthrough with some dialogue + ambient) surface in a review panel for you to confirm — one click each, no friction

### Stage 3 — Transcribe A-roll

> **DEFERRED (post-v1)** — stripped in scope reset `966ce94`; spec preserved in `docs/archive/`. Not built in the current 4-stage pipeline. No transcription runs at runtime today.

- AssemblyAI Universal-2 by default (word-level timestamps + speaker diarization)
- Local Whisper fallback available for NDA'd client work
- Transcript attached to each clip as a Resolve subtitle track (readable in the clip preview)
- JSON sidecar saved so re-ingests don't re-bill you

### Stage 4 — Index B-roll visually

> **DEFERRED (post-v1)** — stripped in scope reset `966ce94`; spec preserved in `docs/archive/`. Not built in the current 4-stage pipeline. No Claude vision tagging runs at runtime today.

- 5 representative frames per B-roll clip extracted via ffmpeg (first, 25%, 50%, 75%, end)
- Frames sent to Claude vision → structured tags returned
- Tags written to clip metadata: subjects, actions, shot type (wide/medium/close/macro), mood, environment, colour palette, time of day

### Stage 5 — Project brief (conversational)

> **DEFERRED (post-v1)** — stripped in scope reset `966ce94`; spec preserved in `docs/archive/`. Not built in the current 4-stage pipeline.

- Chat panel opens in the plugin after indexing completes
- Claude asks about the project: subject, platforms, tone, target durations, angle
- Brief stored as structured JSON that drives all downstream matching logic
- Brief is editable later — you can refine and re-run matching without re-transcribing

### Stage 6 — Identify soundbites (A-roll)

> **DEFERRED (post-v1)** — stripped in scope reset `966ce94`; spec preserved in `docs/archive/`. Not built in the current 4-stage pipeline. No soundbite markers are written at runtime today.

- Claude reads the transcript + brief → identifies strong, usable soundbites
- Each becomes a **Resolve timeline marker** placed on the source clip at the exact in/out timecode of the bite
- Markers colour-coded by soundbite type:
  - **Green** → origin / story
  - **Red** → emotional peak
  - **Blue** → factual / informational
  - **Yellow** → hook / attention-grabber
  - (additional types mapped to remaining Resolve marker colours as needed)
- Marker note contains:
  - A label (e.g. "Origin story — when she first touched clay at 19")
  - Why it's usable
  - A short extract of the transcript for quick scanning

### Stage 7 — Match B-roll to soundbites (literal + thematic)

> **DEFERRED (post-v1)** — stripped in scope reset `966ce94`; spec preserved in `docs/archive/`. Not built in the current 4-stage pipeline.

- For each identified soundbite, Claude suggests B-roll matches in two flavours:
  - `[LITERAL]` — visual directly references what's being said (she says "clay" → shot of clay)
  - `[THEMATIC]` — visual serves the emotional / conceptual beat (she says "freedom" → wide studio golden-hour shot)
- Metadata written across three Resolve clip fields for different workflow needs:
  - **Keywords** → visual tags (filterable): `hands, wheel, clay, warm-light, close-up`
  - **Shot** → match IDs (sortable column in list view): `origin_story, passion_quote`
  - **Description** → full notes (free-text detail): `Literal match for origin_story — close-up hands shaping clay on wheel, warm tungsten key light. Also thematic match for passion_quote.`
- Matching entry appended to the A-roll soundbite marker's note: `Matching B-roll: clip_042 (literal), clip_081 (thematic)`

### Stage 8 — Plugin sidebar (your cockpit)

> **DEFERRED (post-v1)** — stripped in scope reset `966ce94`; spec preserved in `docs/archive/`. There is no full sidebar cockpit / story map UI in the current build; the shipped UI is the drop zone, classify panel/inspector, settings, and timeline panel.

A persistent panel inside the plugin showing:

- **Story map**: all identified soundbites, filterable by type
- **Click-to-jump**: click any soundbite → Resolve playhead moves to that clip at that timecode
- **B-roll matches panel**: when a soundbite is selected, matched B-roll preview thumbnails appear
- **Stats**: X A-roll clips indexed, Y soundbites identified, Z B-roll clips tagged, average match count per bite

---

## What you actually get in Resolve

> **Current build (post-`966ce94`):** items 1, 2, and the clip colours below ship today, plus an auto-built assembly timeline (A-roll on V1/A1, B-roll on V2/A2, AMBIGUOUS skipped). Items 3–6 are **DEFERRED (post-v1)** and describe the deferred transcribe/soundbite/match/sidebar stages.

What exists in the full vision:

1. Organised bin structure matching your folder convention _(shipping)_
2. Every clip tagged `A-ROLL` / `B-ROLL` / `AMBIGUOUS` in the metadata, with clip colour set in the media pool _(shipping)_
3. A-roll clips carry full transcripts as subtitle tracks, speakers labelled _(DEFERRED — post-v1)_
4. A-roll clips carry colour-coded markers at every identified soundbite, with notes _(DEFERRED — post-v1)_
5. B-roll clips carry visual tags + match associations in their metadata _(DEFERRED — post-v1)_
6. A plugin sidebar giving you a story map and one-click navigation _(DEFERRED — post-v1)_

> Note: unlike the deferred vision (which deliberately assembled nothing), the current build does produce one starting artefact — a named **assembly timeline** with A-roll and B-roll laid onto separate tracks for you to refine. AMBIGUOUS clips are intentionally left out of it.

You open Resolve like normal, scrub through the markers to find the bites you love, drag them to a new timeline, and pull B-roll from the matched suggestions. **Nothing about your creative process changes — the logging, transcribing, and tagging is just already done.**

---

## The visual system — two independent colour signals

Two colour dimensions operate at different levels, giving you instant visual meaning without overlap:

**Media pool clip colour** = what _kind_ of clip it is _(shipping)_

- Blue = A-ROLL, Green = B-ROLL, Yellow = AMBIGUOUS
- (Purple = PHOTOS, Tan = ASSETS were part of the original vision and are not set by the current classifier, which colours video clips only.)

**Timeline marker colour** (on A-roll clips only) = what _type of story beat_ a soundbite is

> **DEFERRED (post-v1)** — depends on the deferred soundbites stage (`966ce94`). No soundbite markers are written in the current build.

- Green = origin/story, Red = emotional peak, Blue = factual, Yellow = hook

The clip colour is a property of the clip in the media pool; the marker colour applies inside the clip's timeline. They never collide. Scanning the media pool gives you category-at-a-glance; opening an A-roll clip gives you story-structure-at-a-glance.

---

## The sample shoot, walked through

> **Current build covers steps 1–4 (ingest + classify + colour) plus an auto-built assembly timeline. Steps 5–10 are DEFERRED (post-v1) — transcribe, vision, brief, soundbites, matching, sidebar — stripped in `966ce94`.**

1. Sony card dumped into a folder called `sample`. You drop it on the plugin. _(shipping)_
2. Plugin runs the `ingest.py` organise logic. Folder becomes `251125 sample/01_VIDEOS/A_CAM/...` etc. _(shipping)_
3. Resolve opens. Bins created matching the folder tree. Clips imported (no proxies — your default). _(shipping)_
4. The local KNN classifier (face + motion) classifies: sitdown interview → `A-ROLL`. Studio walkthrough with low face ratio and mixed motion → `AMBIGUOUS` (flagged for your review). Silent wheel shots → `B-ROLL`. Clips coloured Blue/Green/Yellow. _(shipping)_ Cairn then auto-builds an assembly timeline (A-roll on V1/A1, B-roll on V2/A2, AMBIGUOUS skipped). _(shipping)_
5. _(DEFERRED — post-v1)_ AssemblyAI transcribes the A-roll + the walkthrough clips you confirmed as A-roll. Subtitle tracks show her dialogue, "Speaker 1: Lauren" labelled.
6. _(DEFERRED — post-v1)_ Claude vision tags B-roll. Clips now have metadata like `hands on wheel, medium close-up, warm tungsten, clay-covered fingers, shallow DOF`.
7. _(DEFERRED — post-v1)_ Brief chat opens. You tell Claude: Lauren's a local ceramicist; one 60-90s IG Reel and one 2-3min YouTube cut; warm, intimate tone; focus on origin story and craft passion.
8. _(DEFERRED — post-v1)_ Claude identifies 12 candidate soundbites. Each gets a green marker (story), red marker (emotional peak), or blue marker (factual) at the correct source timecode. Notes read like _"Origin story — she started at 19 after trying pottery at uni. Strong emotional hook, usable as cold open."_
9. _(DEFERRED — post-v1)_ B-roll matching runs. Each soundbite marker's note lists matches: _"Matching B-roll: clip_042 (literal — close-up hands on wheel), clip_081 (thematic — wide studio golden hour)."_ B-roll clips get `MATCHES: origin_story, passion_quote` in their metadata.
10. _(DEFERRED — post-v1)_ Plugin sidebar shows the story map. You click the "origin story" soundbite → Resolve jumps there. You love it, drag to a new timeline. Sidebar shows the two suggested B-roll matches; you preview, pick one. Repeat.

**Original estimate (full vision): the full logging pass — 2-3 hours — reduced to 20 minutes of supervised review. You still cut the edit yourself.**

---

## Explicitly OUT of v1 scope

- Assembled rough-cut timelines (you rejected this — correctly)
- Reel candidate timelines
- Any automated cutting, sequencing, or assembly
- Resolve's built-in AI tools (IntelliScript, Magic Mask, SuperScale) — v2 via OS macro chaining
- Music / SFX suggestions
- Colour grading
- Captions / titles
- Multi-project batch mode
- Multicam sync across A_CAM and B_CAM of the same moment

---

## Distribution architecture (shipping to other editors)

Since this is going to a wider audience:

- **BYOK (Bring Your Own Key)** for AssemblyAI + Anthropic API — users enter keys in plugin settings. No cloud infrastructure you have to run or pay for. _(The API-key fields exist in Settings today, but no AI API is called at runtime in the current 4-stage build — these keys are for the DEFERRED post-v1 stages.)_
- **Cloud-first defaults** (AssemblyAI, Claude) for reliable cross-machine behaviour. Local Whisper + local vision model available as optional fallbacks for privacy-sensitive users. _(DEFERRED — post-v1; current classification is 100% local with no cloud calls and no AI APIs at all.)_
- **Pluggable backend architecture** — swap transcription or vision provider via a clean interface. Futureproof, open-source friendly. _(DEFERRED — post-v1; applies to the deferred transcribe/vision stages.)_
- **Config in user folder** (`~/Library/Application Support/Cairn/` on Mac, `%APPDATA%\Cairn\` on Windows) — plugin updates don't wipe settings.
- **Standard Electron installers** — `.dmg` for Mac, `.exe` for Windows. Signed certificates needed for Mac Gatekeeper (budget ~$100/year for an Apple Developer account).
- **Anonymous telemetry off by default** — respects user privacy, strong positioning whether you go open source or commercial.
- **No lock-in** — all metadata and sidecars stored in standard formats so users can extract their work if they leave the tool.

This shape means v1 ships with zero ongoing cost to you. You can decide on a business model (free, freemium, paid licence, open source) after seeing how it performs.

---

## Architecture at a glance

> **Diagram below reflects the current 4-stage build.** AssemblyAI/Anthropic API calls, the brief chat, and the story map sidebar are **DEFERRED (post-v1)** — shown dimmed in the original full-vision diagram that follows.

Current build:

```
┌─────────────────────────────────────────────────────────┐
│  Resolve Studio                                         │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Workspace → Workflow Integrations → Cairn  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Electron UI (Svelte)                        │ │ │
│  │  │  • DropZone      • ClassifyPanel/Inspector   │ │ │
│  │  │  • Settings      • TimelinePanel             │ │ │
│  │  └──────────────┬───────────────────────────────┘ │ │
│  │                 │                                  │ │
│  │                 ▼                                  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Node orchestrator (main.js, CommonJS)       │ │ │
│  │  │  • Spawns long-lived Python worker           │ │ │
│  │  │  • Resolve JS API wrapping is INLINE here    │ │ │
│  │  │    (tech debt; no separate resolve-bridge)   │ │ │
│  │  │  • python-bridge.ts: typed, Zod-validated    │ │ │
│  │  │  • NO AI API calls at runtime                │ │ │
│  │  └──────────────┬───────────────────────────────┘ │ │
│  │                 │  JSON-per-line stdin/stdout      │ │
│  │                 ▼                                  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Python workers (src/workers/)               │ │ │
│  │  │  • bridge.py  (IPC dispatcher)               │ │ │
│  │  │  • ingest.py  (ported organise logic)        │ │ │
│  │  │  • classify.py (local KNN: face + motion)    │ │ │
│  │  │  • resolve_api.py (bins, colour, metadata)   │ │ │
│  │  │  • timeline.py (assembly timeline build)     │ │ │
│  │  │  • sidecar.py (sidecar JSON read/write)      │ │ │
│  │  │  • resolve_import.py (LEGACY fallback, TBR)  │ │ │
│  │  └──────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
   No network. ffmpeg/ffprobe vendored at assets/bin/.
   requirements.txt: send2trash, opencv-python-headless, numpy.
```

Original full-vision diagram (the AI API layer and `transcribe.py` / `vision.py` / `resolve_bridge.py` are DEFERRED post-v1 / do not exist as named today):

```
┌─────────────────────────────────────────────────────────┐
│  Resolve Studio                                         │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Workspace → Workflow Integrations → Cairn  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Electron UI (Svelte)                        │ │ │
│  │  │  • Drop zone     • Brief chat (DEFERRED)     │ │ │
│  │  │  • Review panel  • Story map sidebar (DEFER) │ │ │
│  │  └──────────────┬───────────────────────────────┘ │ │
│  │                 │                                  │ │
│  │                 ▼                                  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Node orchestrator                           │ │ │
│  │  │  • Spawns Python workers                     │ │ │
│  │  │  • Calls AssemblyAI / Claude APIs (DEFERRED) │ │ │
│  │  │  • Talks to Resolve JS API                   │ │ │
│  │  └──────────────┬───────────────────────────────┘ │ │
│  │                 │                                  │ │
│  │                 ▼                                  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Python workers (pluggable modules)          │ │ │
│  │  │  • ingest.py  (your organise.py, adapted)    │ │ │
│  │  │  • classify.py (local KNN: face + motion)    │ │ │
│  │  │  • transcribe.py (AssemblyAI/Whisper) DEFER  │ │ │
│  │  │  • vision.py (Claude / local CLIP) DEFERRED  │ │ │
│  │  │  • resolve_api.py (bins, markers, meta)      │ │ │
│  │  └──────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
          │                              │
          ▼ (DEFERRED — post-v1)         ▼ (DEFERRED — post-v1)
    AssemblyAI API                Anthropic API
    (transcription)               (brief, selection, vision)
```

---

## First build milestone

Build sequence, each stage independently useful:

> **Current build (post-`966ce94`) = the first two milestones below, extended with clip colouring and an auto-built assembly timeline. The AI milestones (transcription, vision, brief/soundbites, matching/sidebar) are DEFERRED post-v1.**

- **Stage 1**: Drop folder → organise (ported `organise.py` logic) → import to Resolve with bin structure. No AI. Proves the plugin talks to Resolve reliably. _(shipping)_
- **Stage 2**: Add classification — a **local KNN classifier over face + motion features** (not VAD; audio is intentionally unused). Clips tagged and coloured A-ROLL / B-ROLL / AMBIGUOUS, then laid into an assembly timeline. _(shipping)_
- **Stage 3**: Add AssemblyAI transcription. Subtitle tracks on A-roll clips. _(DEFERRED — post-v1)_
- **Stage 4**: Add Claude vision tagging for B-roll. _(DEFERRED — post-v1)_
- **Stage 5**: Add brief chat + soundbite identification + marker placement. _(DEFERRED — post-v1)_
- **Stage 6**: Add B-roll matching + plugin sidebar. _(DEFERRED — post-v1)_

The current shipping product is Stages 1–2 (plus colour + assembly timeline) and already saves hours of manual logging. The full original v1 vision is Stages 1–6 together — Stages 3–6 are deferred post-v1 (specs in `docs/archive/`).
