# Cairn — Product Specification (v1)

> _A cairn is a stack of stones left on a trail by someone who walked it first, marking the way for whoever comes next._

## What Cairn is

A DaVinci Resolve Workflow Integration Plugin that does the tedious pre-edit work — organising footage, classifying A-roll vs B-roll, transcribing dialogue, tagging B-roll visually, identifying soundbites, matching B-roll to story beats — and leaves markers throughout your project so the editor (you) can focus on the creative work of shaping the edit.

## The one-line goal

Drag a shoot folder onto Cairn → get a fully organised, transcribed, tagged, and annotated Resolve project where every usable moment is identified and matched, ready for you to assemble creatively.

## Core principle

Cairn identifies, it does not assemble. It leaves markers, not cuts. The plugin does the logging, transcribing, and tagging; you decide what the edit actually is. Think of it as a supercharged logging assistant with a recommendation engine, not an editor.

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

- Silero VAD runs locally on every clip (~1-2 seconds per file)
- Each clip tagged in Resolve metadata as `A-ROLL`, `B-ROLL`, or `AMBIGUOUS`
- **Clip colour set in the media pool** for instant visual category identification:
  - A-ROLL → Blue
  - B-ROLL → Green
  - AMBIGUOUS → Yellow
  - PHOTOS → Purple
  - ASSETS → Tan
- Ambiguous clips (e.g. your studio walkthrough with some dialogue + ambient) surface in a review panel for you to confirm — one click each, no friction

### Stage 3 — Transcribe A-roll

- AssemblyAI Universal-2 by default (word-level timestamps + speaker diarization)
- Local Whisper fallback available for NDA'd client work
- Transcript attached to each clip as a Resolve subtitle track (readable in the clip preview)
- JSON sidecar saved so re-ingests don't re-bill you

### Stage 4 — Index B-roll visually

- 5 representative frames per B-roll clip extracted via ffmpeg (first, 25%, 50%, 75%, end)
- Frames sent to Claude vision → structured tags returned
- Tags written to clip metadata: subjects, actions, shot type (wide/medium/close/macro), mood, environment, colour palette, time of day

### Stage 5 — Project brief (conversational)

- Chat panel opens in the plugin after indexing completes
- Claude asks about the project: subject, platforms, tone, target durations, angle
- Brief stored as structured JSON that drives all downstream matching logic
- Brief is editable later — you can refine and re-run matching without re-transcribing

### Stage 6 — Identify soundbites (A-roll)

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

- For each identified soundbite, Claude suggests B-roll matches in two flavours:
  - `[LITERAL]` — visual directly references what's being said (she says "clay" → shot of clay)
  - `[THEMATIC]` — visual serves the emotional / conceptual beat (she says "freedom" → wide studio golden-hour shot)
- Metadata written across three Resolve clip fields for different workflow needs:
  - **Keywords** → visual tags (filterable): `hands, wheel, clay, warm-light, close-up`
  - **Shot** → match IDs (sortable column in list view): `origin_story, passion_quote`
  - **Description** → full notes (free-text detail): `Literal match for origin_story — close-up hands shaping clay on wheel, warm tungsten key light. Also thematic match for passion_quote.`
- Matching entry appended to the A-roll soundbite marker's note: `Matching B-roll: clip_042 (literal), clip_081 (thematic)`

### Stage 8 — Plugin sidebar (your cockpit)

A persistent panel inside the plugin showing:

- **Story map**: all identified soundbites, filterable by type
- **Click-to-jump**: click any soundbite → Resolve playhead moves to that clip at that timecode
- **B-roll matches panel**: when a soundbite is selected, matched B-roll preview thumbnails appear
- **Stats**: X A-roll clips indexed, Y soundbites identified, Z B-roll clips tagged, average match count per bite

---

## What you actually get in Resolve

Nothing is cut. Nothing is assembled. What exists:

1. Organised bin structure matching your folder convention
2. Every clip tagged `A-ROLL` / `B-ROLL` / `AMBIGUOUS` in the metadata
3. A-roll clips carry full transcripts as subtitle tracks, speakers labelled
4. A-roll clips carry colour-coded markers at every identified soundbite, with notes
5. B-roll clips carry visual tags + match associations in their metadata
6. A plugin sidebar giving you a story map and one-click navigation

You open Resolve like normal, scrub through the markers to find the bites you love, drag them to a new timeline, and pull B-roll from the matched suggestions. **Nothing about your creative process changes — the logging, transcribing, and tagging is just already done.**

---

## The visual system — two independent colour signals

Two colour dimensions operate at different levels, giving you instant visual meaning without overlap:

**Media pool clip colour** = what _kind_ of clip it is

- Blue = A-ROLL, Green = B-ROLL, Yellow = AMBIGUOUS, Purple = PHOTOS, Tan = ASSETS

**Timeline marker colour** (on A-roll clips only) = what _type of story beat_ a soundbite is

- Green = origin/story, Red = emotional peak, Blue = factual, Yellow = hook

The clip colour is a property of the clip in the media pool; the marker colour applies inside the clip's timeline. They never collide. Scanning the media pool gives you category-at-a-glance; opening an A-roll clip gives you story-structure-at-a-glance.

---

## The sample shoot, walked through

1. Sony card dumped into a folder called `sample`. You drop it on the plugin.
2. Plugin runs your `organise.py` logic. Folder becomes `251125 sample/01_VIDEOS/A_CAM/...` etc.
3. Resolve opens. Bins created matching the folder tree. Clips imported (no proxies — your default).
4. VAD classifies: sitdown interview → `A-ROLL`. Studio walkthrough with light chatter → `AMBIGUOUS` (flagged for your review). Silent wheel shots → `B-ROLL`.
5. AssemblyAI transcribes the A-roll + the walkthrough clips you confirmed as A-roll. Subtitle tracks show her dialogue, "Speaker 1: Lauren" labelled.
6. Claude vision tags B-roll. Clips now have metadata like `hands on wheel, medium close-up, warm tungsten, clay-covered fingers, shallow DOF`.
7. Brief chat opens. You tell Claude: Lauren's a local ceramicist; one 60-90s IG Reel and one 2-3min YouTube cut; warm, intimate tone; focus on origin story and craft passion.
8. Claude identifies 12 candidate soundbites. Each gets a green marker (story), red marker (emotional peak), or blue marker (factual) at the correct source timecode. Notes read like _"Origin story — she started at 19 after trying pottery at uni. Strong emotional hook, usable as cold open."_
9. B-roll matching runs. Each soundbite marker's note lists matches: _"Matching B-roll: clip_042 (literal — close-up hands on wheel), clip_081 (thematic — wide studio golden hour)."_ B-roll clips get `MATCHES: origin_story, passion_quote` in their metadata.
10. Plugin sidebar shows the story map. You click the "origin story" soundbite → Resolve jumps there. You love it, drag to a new timeline. Sidebar shows the two suggested B-roll matches; you preview, pick one. Repeat.

**Estimated time saved on a project like this: the full logging pass — 2-3 hours — reduced to 20 minutes of supervised review. You still cut the edit yourself.**

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

- **BYOK (Bring Your Own Key)** for AssemblyAI + Anthropic API — users enter keys in plugin settings. No cloud infrastructure you have to run or pay for.
- **Cloud-first defaults** (AssemblyAI, Claude) for reliable cross-machine behaviour. Local Whisper + local vision model available as optional fallbacks for privacy-sensitive users.
- **Pluggable backend architecture** — swap transcription or vision provider via a clean interface. Futureproof, open-source friendly.
- **Config in user folder** (`~/Library/Application Support/Cairn/` on Mac, `%APPDATA%\Cairn\` on Windows) — plugin updates don't wipe settings.
- **Standard Electron installers** — `.dmg` for Mac, `.exe` for Windows. Signed certificates needed for Mac Gatekeeper (budget ~$100/year for an Apple Developer account).
- **Anonymous telemetry off by default** — respects user privacy, strong positioning whether you go open source or commercial.
- **No lock-in** — all metadata and sidecars stored in standard formats so users can extract their work if they leave the tool.

This shape means v1 ships with zero ongoing cost to you. You can decide on a business model (free, freemium, paid licence, open source) after seeing how it performs.

---

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────┐
│  Resolve Studio                                         │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Workspace → Workflow Integrations → Cairn  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Electron UI (Svelte)                        │ │ │
│  │  │  • Drop zone     • Brief chat                │ │ │
│  │  │  • Review panel  • Story map sidebar         │ │ │
│  │  └──────────────┬───────────────────────────────┘ │ │
│  │                 │                                  │ │
│  │                 ▼                                  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Node orchestrator                           │ │ │
│  │  │  • Spawns Python workers                     │ │ │
│  │  │  • Calls AssemblyAI / Claude APIs            │ │ │
│  │  │  • Talks to Resolve JS API                   │ │ │
│  │  └──────────────┬───────────────────────────────┘ │ │
│  │                 │                                  │ │
│  │                 ▼                                  │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Python workers (pluggable modules)          │ │ │
│  │  │  • ingest.py  (your organise.py, adapted)    │ │ │
│  │  │  • classify.py (Silero VAD)                  │ │ │
│  │  │  • transcribe.py (AssemblyAI / Whisper)      │ │ │
│  │  │  • vision.py (Claude / local CLIP)           │ │ │
│  │  │  • resolve_bridge.py (bins, markers, meta)   │ │ │
│  │  └──────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
          │                              │
          ▼                              ▼
    AssemblyAI API                Anthropic API
    (transcription)               (brief, selection, vision)
```

---

## First build milestone

Build sequence, each stage independently useful:

- **Stage 1**: Drop folder → organise (port your script) → import to Resolve with bin structure. No AI. Proves the plugin talks to Resolve reliably.
- **Stage 2**: Add VAD classification. Clips tagged A-ROLL / B-ROLL / AMBIGUOUS.
- **Stage 3**: Add AssemblyAI transcription. Subtitle tracks on A-roll clips.
- **Stage 4**: Add Claude vision tagging for B-roll.
- **Stage 5**: Add brief chat + soundbite identification + marker placement.
- **Stage 6**: Add B-roll matching + plugin sidebar.

You could ship after Stage 2 and already save hours of manual logging. The full v1 is Stages 1–6 together.
