# specs/sidebar.md — Stage 8: Plugin sidebar (the cockpit)

## Purpose

A persistent panel inside the plugin that gives the editor a story map of the whole shoot: every identified soundbite, filterable; click-to-jump to the Resolve playhead; matched B-roll thumbnails on selection; live project stats. This is where the editor spends most of their time once indexing is done — it replaces "scroll the media pool" and "scrub a 40-min interview" with "point at the soundbite you want".

## Purpose-as-user-flow

```
Open Cairn sidebar
  → Story map: 12 soundbites listed, coloured by beat type
  → Filter: "emotional-peak only" → 3 bites remain
  → Click one: Resolve playhead jumps to that clip at that timecode, playback starts
  → Sidebar shows 3 matched B-roll thumbnails below the bite
  → Preview one, like it, drag it to the open timeline
  → Move to next bite. Repeat.
```

## Inputs

- All sidecars for the current project (`04_EXPORT/.cairn/*.json`, plus `brief.json`)
- Live Resolve state: current project, current timeline, current playhead position (via Resolve JS API)
- User actions from the UI (click, filter, preview)

## Outputs

Read-heavy surface; minimal writes. Writes are triggered by user actions:

- **User marks a bite as "used"** → sidecar `"user_state.used": true`
- **User rejects a B-roll match** → match's `"user_rejected": true` in sidecar (for telemetry / future preference learning)
- **User edits a marker label in the sidebar** → sidecar updated, Resolve marker note rewritten through the serial worker

## UI layout (wireframe, Svelte)

```
┌──────────────────────────────────────────────────────┐
│  Cairn                                 ⚙ Settings     │
├──────────────────────────────────────────────────────┤
│  ▸ 251125 sample                                     │
│    Stats: 12 A-roll · 47 B-roll · 12 bites · ⌀ 2.8  │
│                                                      │
│  [ All ] [ Origin ] [ Peak ] [ Factual ] [ Hook ] … │
│                                                      │
│  STORY MAP                                           │
│  ┌────────────────────────────────────────────────┐ │
│  │ 🟢 Origin story — first touched clay at 19     │ │
│  │    clip: A_CAM_005 · 00:12:40 → 00:12:58      │ │
│  │    JUDGE: 0.91                                 │ │
│  ├────────────────────────────────────────────────┤ │
│  │ 🔴 Emotional peak — losing first kiln          │ │
│  │    clip: A_CAM_012 · 00:24:02 → 00:24:21      │ │
│  │    JUDGE: 0.87                                 │ │
│  ├────────────────────────────────────────────────┤ │
│  │ 🔵 Factual — trained at Edinburgh 2014         │ │
│  │    clip: A_CAM_005 · 00:03:10 → 00:03:18      │ │
│  │    JUDGE: 0.94                                 │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  SELECTED: Origin story                              │
│  Matches:                                            │
│   LIT  clip_042  ━━━━  0.91 · close hands on wheel  │
│   LIT  clip_113  ━━━   0.83 · clay being wedged     │
│   THM  clip_081  ━━    0.72 · wide studio golden    │
│                                                      │
│  [ Preview ]  [ Mark bite as used ]  [ Reject ]     │
└──────────────────────────────────────────────────────┘
```

## Components

- **`<StoryMap>`** — list of soundbite rows, virtualised for large projects
- **`<SoundbiteRow>`** — colour dot, label, clip name + TC, judge score chip
- **`<FilterBar>`** — beat-type chips + search box (searches `label`, `extract`)
- **`<MatchPanel>`** — literal + thematic matches for the selected bite, with thumbnails and rationale
- **`<Stats>`** — project-level counts
- **`<ReviewPanel>`** (modal) — flagged ambiguous clips (Stage 2) and flagged low-judge bites (Stage 6)
- **`<Settings>`** (modal) — API keys, per-project privacy toggles, brief edit

## Click-to-jump behaviour

When a soundbite row is clicked:

```typescript
// Node side, through the single Resolve worker:
await resolveBridge.run("jump_to_bite", {
  clipId: bite.source_clip_id,
  frame: bite.in_frame,
});

// Python / resolve_api.py:
def jump_to_bite(clip_id, frame):
    mp_item = find_media_pool_item(clip_id)
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    # If the clip is in the current timeline, seek there:
    if timeline and clip_in_timeline(mp_item, timeline):
        timeline.SetCurrentTimecode(tc_for_frame(frame, timeline_fps))
    else:
        # Otherwise open the source clip in the Source Viewer:
        project.OpenSourceClipInViewer(mp_item)
        mp_item.SetClipProperty("Current Timecode", tc_for_frame(frame, clip_fps))
```

Resolve's exact API names require verification — see Open Questions. Whatever the final calls, they route through the serial worker.

## Data loading strategy

- On plugin open: read all sidecars, build in-memory project state (Svelte store).
- File watcher (`chokidar` Node side) on `04_EXPORT/.cairn/` — re-reads affected sidecars on change so running pipelines update the sidebar live without a manual refresh.
- Caching: thumbnails cached in `04_EXPORT/.cairn/thumbs/` (extracted via ffmpeg first time viewed). Second view is instant.

## Filters

- **Beat type chips** (multi-select): origin, peak, factual, hook, quote, process, humour, reflection, technical, tension, resolution
- **Search**: fuzzy over `label` + `extract` + `why_usable`
- **"Show only flagged"**: bites with `[REVIEW]` or `judge_score < 0.6`
- **"Show only used"**: bites with `user_state.used: true`
- **"Uncovered bites"**: bites with zero B-roll matches

## Sensors / Validations

- **Live state consistency** — when a sidecar changes on disk, the UI must reflect it within ~500 ms. Integration test: write a sidecar update via a script → assert store updates.
- **Click-to-jump verification** — after a jump call, read back the Resolve playhead; assert within ±1 frame of target.
- **Thumbnail freshness** — thumbnail cache keyed by clip-id + mtime; stale caches regenerated.
- **No heavy work on the UI thread** — sidecar parsing, thumbnail extraction, all off-thread (Node orchestrator, Python workers).
- **Vitest tests** for every Svelte component (render, filter logic, selection).
- **Playwright-style smoke test** on the sidebar loaded against a fixture project — one flow (open → filter → click → match panel populates).

### Taste-invariant cross-checks

- #3 (serial Resolve): every user-triggered Resolve call routes through the single worker.
- #4 (no hardcoded keys): Settings modal is the only place keys appear; keys read via keytar IPC, never embedded.
- #5 (no placeholders): rows for bites that failed to load show an error state with the actual reason, not a blank "..."
- #6 (sidecar first): any user action that changes state writes sidecar first, then tells Resolve.

## Keyboard shortcuts

Power-user feel — keep it tight:

| Shortcut | Action                                 |
| -------- | -------------------------------------- |
| `J / K`  | Prev / next soundbite                  |
| `Enter`  | Jump Resolve playhead to selected bite |
| `M`      | Mark selected bite as used             |
| `R`      | Open review panel                      |
| `/`      | Focus search                           |
| `1..9`   | Filter by beat-type chip (by position) |
| `Esc`    | Clear filter / close modal             |

## Open questions

- **Exact Resolve jump API** — the scripting docs are thin. Options to try: `timeline.SetCurrentTimecode()`, `project.SetCurrentTimeline()`, `OpenSourceClipInViewer` (may not exist on all versions). Spike during Session 9, record findings in `AGENT.md`.
- **Thumbnails from Resolve vs ffmpeg** — Resolve has `GetClipProperty("Thumbnail")` but returns nothing useful in some versions. ffmpeg extraction is slower on first view but reliable. Decision: ffmpeg, cache.
- **Sidebar inside plugin vs separate panel** — Blackmagic's Workflow Integration renders a single Electron window. Can we dock it or must it float? If floating only, is that OK UX? Spike early.
- **Undo for user actions** — marking "used" by accident should be undoable. v1: simple `⌘Z` reverts the last sidecar edit. Out of scope for v1 core but log in `fix_plan.md` if it causes frustration in testing.
- **Multi-select** — select multiple bites + action on all? (e.g. "Mark all factual bites as used"). v2. Not v1.
- **Performance at scale** — 47 B-roll clips is small. What about 500? Virtualised list + paginated sidecar reads. Test at 500-clip synthetic project.
- **Drag-drop from sidebar to timeline** — ideal UX but Resolve's timeline isn't a DOM surface. Likely impossible without an "Add to timeline" button that drops the clip at the playhead. v1: button, not drag.
